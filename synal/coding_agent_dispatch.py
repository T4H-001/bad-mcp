from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

KIMI_WORK_KEY = "github:t4h-remote-mcp-server-clean:pr-144:kimi-code-real"
KIMI_REPO = "TML-4PM/t4h-remote-mcp-server-clean"
KIMI_PR = 144


def is_kimi_work(work_key: str) -> bool:
    return work_key == KIMI_WORK_KEY


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def resolve_checkout(runtime: Any) -> Path:
    """Resolve a bounded checkout without assuming a permanent provider-specific path."""
    for env_name in ("T4H_KIMI_REPO_ROOT", "T4H_TARGET_REPO_ROOT"):
        value = os.environ.get(env_name)
        if value:
            root = Path(value).expanduser().resolve()
            if (root / "scripts/tools/verify-kimi-code.sh").is_file():
                return root
            raise RuntimeError(f"KIMI_CHECKOUT_INVALID:{env_name}:{root}")

    workspace = Path(os.environ.get("T4H_CODING_AGENT_WORKSPACE", str(runtime.STATE_DB.parent / "checkouts")))
    workspace.mkdir(parents=True, exist_ok=True)
    root = workspace / "t4h-remote-mcp-server-clean-pr144"

    if not (root / ".git").is_dir():
        clone = _run(["gh", "repo", "clone", KIMI_REPO, str(root), "--", "--filter=blob:none"], timeout=180)
        if clone.returncode != 0:
            raise RuntimeError("KIMI_CHECKOUT_CLONE_FAILED:" + clone.stderr.strip()[:1200])

    fetch = _run(["git", "fetch", "origin", f"pull/{KIMI_PR}/head"], cwd=root, timeout=120)
    if fetch.returncode != 0:
        raise RuntimeError("KIMI_CHECKOUT_FETCH_FAILED:" + fetch.stderr.strip()[:1200])
    checkout = _run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=root, timeout=60)
    if checkout.returncode != 0:
        raise RuntimeError("KIMI_CHECKOUT_CHECKOUT_FAILED:" + checkout.stderr.strip()[:1200])

    verifier = root / "scripts/tools/verify-kimi-code.sh"
    if not verifier.is_file():
        raise RuntimeError("KIMI_VERIFIER_MISSING_AFTER_CHECKOUT")
    return root


def run_kimi_verifier(runtime: Any) -> dict[str, Any]:
    root = resolve_checkout(runtime)
    verifier = root / "scripts/tools/verify-kimi-code.sh"
    completed = _run(["bash", str(verifier)], cwd=root, timeout=120)
    output = (completed.stdout + "\n" + completed.stderr).strip()
    result: dict[str, Any] = {
        "repository": KIMI_REPO,
        "pull_request": KIMI_PR,
        "checkout": str(root),
        "verifier": "scripts/tools/verify-kimi-code.sh",
        "exit_code": completed.returncode,
        "output_tail": output[-6000:],
    }
    for line in output.splitlines():
        if line.startswith("RECEIPT="):
            result["receipt_path"] = line.split("=", 1)[1].strip()
    if completed.returncode != 0:
        if "kimi executable not found" in output:
            raise RuntimeError("KIMI_EXECUTABLE_UNAVAILABLE_ON_RUNTIME_HOST")
        raise RuntimeError(f"KIMI_VERIFIER_FAILED:exit={completed.returncode}:{output[-1200:]}")
    return result


def _block_once(runtime: Any, work_key: str, url: str, worker: str, reason: str) -> None:
    attempt = runtime.attempt_for(work_key)
    with sqlite3.connect(runtime.STATE_DB) as db:
        db.execute(
            "UPDATE work_claims SET state='BLOCKED',lease_until=NULL,last_error=?,updated_at=? WHERE work_key=?",
            (reason[:2000], runtime.now(), work_key),
        )
        db.commit()
    runtime.event(work_key, "BLOCKED", reason)
    runtime.ledger(work_key, worker, "BLOCKED", attempt, reason)
    runtime.metric(work_key, "failure", 1, detail="BLOCKED")
    runtime.github_comment(
        url,
        f"<!-- t4h-receipt -->\nwork_key: {work_key}\nworker: {worker}\nstate: BLOCKED\n"
        f"attempt: {attempt}\nfailure_class: DEPENDENCY\nerror: {reason}\n"
        "completed_independent_work: source checkout + governed verifier dispatch path\n"
        "wake_condition: Kimi executable becomes available on an approved T4H runtime, or a compatible qualified runtime is selected\n"
        "next_state: WAIT_DEPENDENCY",
    )


def execute_kimi(work_key: str, url: str, body: str, worker: str, runtime: Any) -> None:
    """Execute the known Kimi qualification job deterministically.

    This intentionally bypasses the generic LLM fallback. A coding-agent
    qualification work key must execute its governed verifier, not be converted
    into a prose-generation request.
    """
    attempt = runtime.attempt_for(work_key)
    runtime.event(work_key, "EXECUTE_START", f"attempt={attempt};worker={worker};handler=kimi")
    runtime.ledger(work_key, worker, "EXECUTE_START", attempt, "handler=kimi")
    try:
        result = run_kimi_verifier(runtime)
        runtime.event(work_key, "KIMI_VERIFIER", json.dumps({"exit_code": result["exit_code"], "receipt_path": result.get("receipt_path")}, sort_keys=True))
        runtime.metric(work_key, "kimi_verifier_pass", 1)
        runtime.complete(work_key, url, worker, result, attempt)
    except RuntimeError as exc:
        reason = str(exc)
        if reason == "KIMI_EXECUTABLE_UNAVAILABLE_ON_RUNTIME_HOST":
            _block_once(runtime, work_key, url, worker, reason)
            return
        terminal = runtime.failure(work_key, url, worker, exc)
        if not terminal:
            runtime.event(work_key, "ROUTED_RECOVERY", runtime.RECOVERY_WORKER)
        return
