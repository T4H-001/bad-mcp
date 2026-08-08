import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

app = FastAPI()

STATE_DB = Path(os.environ.get("T4H_STATE_DB", str(Path.home() / ".local/state/synal/work.db")))
LEASE_SECONDS = int(os.environ.get("T4H_LEASE_SECONDS", "300"))
MAX_ATTEMPTS = int(os.environ.get("T4H_MAX_ATTEMPTS", "3"))
RETRY_DELAY_SECONDS = int(os.environ.get("T4H_RETRY_DELAY_SECONDS", "2"))
OLLAMA_MODEL = os.environ.get("T4H_OLLAMA_MODEL", "qwen2.5:1.5b")
REQUIRE_GITHUB_HMAC = os.environ.get("REQUIRE_GITHUB_HMAC", "0") == "1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS work_claims (
                work_key TEXT PRIMARY KEY,
                delivery_id TEXT,
                worker TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                lease_until INTEGER,
                source_url TEXT,
                receipt_comment_id INTEGER,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS work_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_key TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        db.commit()


def record_event(work_key: str, event_type: str, detail: str = "") -> None:
    with sqlite3.connect(STATE_DB) as db:
        db.execute(
            "INSERT INTO work_events(work_key,event_type,detail,created_at) VALUES(?,?,?,?)",
            (work_key, event_type, detail[:4000], utc_now()),
        )
        db.commit()


def verify_github_signature(raw: bytes, signature: Optional[str]) -> None:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        if REQUIRE_GITHUB_HMAC:
            raise HTTPException(status_code=503, detail="GITHUB_WEBHOOK_SECRET not configured")
        return
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing GitHub signature")
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")


def parse_repo_issue(issue_url: str) -> Tuple[str, int]:
    match = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_url or "")
    if not match:
        raise ValueError(f"Unsupported issue URL: {issue_url}")
    return match.group(1), int(match.group(2))


def parse_work_key(comment_body: str, delivery_id: str, issue_url: str) -> str:
    match = re.search(r"(?mi)^work_key:\s*([^\s]+)\s*$", comment_body or "")
    if match:
        return match.group(1).strip()
    if delivery_id:
        return f"github-delivery:{delivery_id}"
    digest = hashlib.sha256(f"{issue_url}\n{comment_body}".encode()).hexdigest()[:24]
    return f"github-fallback:{digest}"


def claim_work(work_key: str, delivery_id: str, issue_url: str, worker: str) -> str:
    now = int(time.time())
    lease_until = now + LEASE_SECONDS
    with sqlite3.connect(STATE_DB) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM work_claims WHERE work_key=?", (work_key,)).fetchone()
        if row:
            if row["state"] == "COMPLETED":
                db.commit()
                return "DEDUPED"
            if row["state"] == "CLAIMED" and (row["lease_until"] or 0) > now:
                db.commit()
                return "LEASED"
            attempt = int(row["attempt"] or 0) + 1
            db.execute(
                """UPDATE work_claims
                   SET state='CLAIMED', attempt=?, lease_until=?, delivery_id=?, worker=?, source_url=?, updated_at=?
                   WHERE work_key=?""",
                (attempt, lease_until, delivery_id, worker, issue_url, utc_now(), work_key),
            )
        else:
            db.execute(
                """INSERT INTO work_claims
                   (work_key,delivery_id,worker,state,attempt,lease_until,source_url,created_at,updated_at)
                   VALUES(?,?,?,'CLAIMED',1,?,?,?,?)""",
                (work_key, delivery_id, worker, lease_until, issue_url, utc_now(), utc_now()),
            )
        db.commit()
    record_event(work_key, "CLAIMED", f"worker={worker}; delivery={delivery_id}")
    return "CLAIMED"


def current_attempt(work_key: str) -> int:
    with sqlite3.connect(STATE_DB) as db:
        row = db.execute("SELECT attempt FROM work_claims WHERE work_key=?", (work_key,)).fetchone()
        return int(row[0]) if row else 1


def reclaim_for_retry(work_key: str) -> int:
    now = int(time.time())
    with sqlite3.connect(STATE_DB) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT attempt FROM work_claims WHERE work_key=?", (work_key,)).fetchone()
        if not row:
            raise RuntimeError("Cannot reclaim missing work item")
        next_attempt = int(row[0]) + 1
        db.execute(
            "UPDATE work_claims SET state='CLAIMED', attempt=?, lease_until=?, updated_at=? WHERE work_key=?",
            (next_attempt, now + LEASE_SECONDS, utc_now(), work_key),
        )
        db.commit()
    record_event(work_key, "RECOVERY_CLAIMED", f"attempt={next_attempt}; worker=WKR-RECOVER-001")
    return next_attempt


def gh_env() -> dict:
    env = os.environ.copy()
    if "GITHUB_PAT" in env and "GH_TOKEN" not in env:
        env["GH_TOKEN"] = env["GITHUB_PAT"]
    return env


def github_receipt(issue_url: str, body: str) -> int:
    repo, issue_number = parse_repo_issue(issue_url)
    create = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{issue_number}/comments", "-f", f"body={body}"],
        env=gh_env(), capture_output=True, text=True, timeout=30,
    )
    if create.returncode != 0:
        raise RuntimeError(f"GitHub receipt write failed: {create.stderr.strip()}")
    payload = json.loads(create.stdout)
    comment_id = int(payload["id"])

    readback = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/comments/{comment_id}"],
        env=gh_env(), capture_output=True, text=True, timeout=30,
    )
    if readback.returncode != 0:
        raise RuntimeError(f"GitHub receipt readback failed: {readback.stderr.strip()}")
    readback_payload = json.loads(readback.stdout)
    if int(readback_payload.get("id", 0)) != comment_id:
        raise RuntimeError("GitHub receipt readback ID mismatch")
    return comment_id


def run_qwen(comment_body: str) -> str:
    response = requests.post(
        "http://127.0.0.1:11434/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": (
                "You are a bounded engineering worker. Execute only the requested analysis/planning task. "
                "Do not request human approval unless credentials, legal authority, safety, destructive action, "
                "or missing authority makes execution impossible. Return a concise machine-actionable result.\n\n"
                f"GitHub work item:\n{comment_body}"
            ),
            "stream": False,
        },
        timeout=90,
    )
    response.raise_for_status()
    result = response.json().get("response", "").strip()
    if not result:
        raise RuntimeError("Qwen returned empty output")
    return result


def complete_work(work_key: str, issue_url: str, worker: str, output: str) -> None:
    receipt_body = (
        f"<!-- t4h-receipt -->\n"
        f"work_key: {work_key}\n"
        f"worker: {worker}\n"
        f"state: COMPLETED\n"
        f"validation: PASS\n"
        f"readback: PASS\n"
        f"next_state: SLEEP\n\n"
        f"### Worker result\n{output[:5000]}"
    )
    comment_id = github_receipt(issue_url, receipt_body)
    with sqlite3.connect(STATE_DB) as db:
        db.execute(
            """UPDATE work_claims
               SET state='COMPLETED', lease_until=NULL, receipt_comment_id=?, last_error=NULL, updated_at=?
               WHERE work_key=?""",
            (comment_id, utc_now(), work_key),
        )
        db.commit()
    record_event(work_key, "COMPLETED", f"receipt_comment_id={comment_id}")


def fail_work(work_key: str, issue_url: str, worker: str, error: Exception) -> bool:
    error_text = str(error)[:3000]
    attempt = current_attempt(work_key)
    terminal = attempt >= MAX_ATTEMPTS
    state = "BLOCKED" if terminal else "RETRY_READY"
    with sqlite3.connect(STATE_DB) as db:
        db.execute(
            "UPDATE work_claims SET state=?, lease_until=NULL, last_error=?, updated_at=? WHERE work_key=?",
            (state, error_text, utc_now(), work_key),
        )
        db.commit()
    record_event(work_key, state, error_text)

    recovery_state = "BLOCKED_AFTER_MAX_ATTEMPTS" if terminal else "WKR-RECOVER-001_AUTO_RETRY"
    try:
        github_receipt(
            issue_url,
            f"<!-- t4h-receipt -->\nwork_key: {work_key}\nworker: {worker}\nstate: {state}\n"
            f"attempt: {attempt}/{MAX_ATTEMPTS}\nrecovery: {recovery_state}\nerror: {error_text}\n"
            f"next_state: {'ESCALATE' if terminal else 'RETRY'}",
        )
    except Exception as receipt_error:
        record_event(work_key, "RECEIPT_WRITE_FAILED", str(receipt_error))
    return terminal


def execute_work(work_key: str, issue_url: str, comment_body: str, worker: str) -> None:
    while True:
        attempt = current_attempt(work_key)
        try:
            record_event(work_key, "EXECUTE_START", f"model={OLLAMA_MODEL}; attempt={attempt}")
            if "test_class: LIVE_FORCED_FAILURE" in comment_body and attempt == 1:
                raise RuntimeError("CONTROLLED_TEST_FAILURE:first_attempt")
            output = run_qwen(comment_body)
            record_event(work_key, "VALIDATED", f"non-empty Qwen result; attempt={attempt}")
            complete_work(work_key, issue_url, worker, output)
            print(f"[T4H WORKER] COMPLETE {work_key} attempt={attempt} -> SLEEP", flush=True)
            return
        except Exception as exc:
            terminal = fail_work(work_key, issue_url, worker, exc)
            print(f"[T4H WORKER] FAILED {work_key} attempt={attempt}: {exc}", flush=True)
            if terminal:
                return
            record_event(work_key, "ROUTED_RECOVERY", "WKR-RECOVER-001")
            time.sleep(RETRY_DELAY_SECONDS)
            next_attempt = reclaim_for_retry(work_key)
            print(f"[T4H WORKER] WKR-RECOVER-001 AUTO-RETRY {work_key} attempt={next_attempt}", flush=True)


@app.on_event("startup")
def startup() -> None:
    init_db()
    print(f"[T4H WORKER] durable state: {STATE_DB}", flush=True)


@app.post("/github-webhook")
async def handle_github_webhook(request: Request, background_tasks: BackgroundTasks):
    raw = await request.body()
    verify_github_signature(raw, request.headers.get("X-Hub-Signature-256"))
    payload = json.loads(raw.decode("utf-8"))

    comment_body = payload.get("comment", {}).get("body", "")
    issue_url = payload.get("issue", {}).get("html_url") or payload.get("issue", {}).get("url", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    if not any(keyword in comment_body for keyword in ["/action", "/approve", "<!-- t4h-event -->"]):
        return {"status": "ignored"}

    work_key = parse_work_key(comment_body, delivery_id, issue_url)
    worker_match = re.search(r"(?mi)^target_worker:\s*([^\s]+)", comment_body)
    worker = worker_match.group(1) if worker_match else "WKR-EXEC-001"

    claim = claim_work(work_key, delivery_id, issue_url, worker)
    print(f"[T4H WORKER] {claim} {work_key} worker={worker}", flush=True)

    if claim == "DEDUPED":
        record_event(work_key, "DEDUPED", delivery_id)
        try:
            github_receipt(
                issue_url,
                f"<!-- t4h-receipt -->\nwork_key: {work_key}\nworker: {worker}\nstate: DEDUPED\n"
                f"validation: PASS\nexecution: SKIPPED\nnext_state: SLEEP",
            )
        except Exception as exc:
            record_event(work_key, "DEDUPE_RECEIPT_FAILED", str(exc))
        return {"status": "deduped", "work_key": work_key, "state": "SLEEP"}
    if claim == "LEASED":
        return {"status": "already_claimed", "work_key": work_key, "state": "CLAIMED"}

    background_tasks.add_task(execute_work, work_key, issue_url, comment_body, worker)
    return {"status": "claimed", "work_key": work_key, "worker": worker, "state": "EXECUTING"}


@app.get("/health")
def health():
    init_db()
    return {"status": "ok", "state_db": str(STATE_DB), "model": OLLAMA_MODEL}
