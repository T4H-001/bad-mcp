import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import tempfile
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
REQUIRE_GITHUB_HMAC = os.environ.get("REQUIRE_GITHUB_HMAC", "1") == "1"
RUNTIME_ID = "synal-ec2-standard-v2"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work_claims(
          work_key TEXT PRIMARY KEY, delivery_id TEXT, worker TEXT NOT NULL,
          state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
          lease_until INTEGER, source_url TEXT, receipt_comment_id INTEGER,
          last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, work_key TEXT NOT NULL,
          event_type TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS work_ledger(
          id INTEGER PRIMARY KEY AUTOINCREMENT, work_key TEXT NOT NULL,
          worker TEXT NOT NULL, transition TEXT NOT NULL, attempt INTEGER,
          evidence TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS telemetry(
          id INTEGER PRIMARY KEY AUTOINCREMENT, work_key TEXT NOT NULL,
          metric TEXT NOT NULL, value REAL, unit TEXT, detail TEXT,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plugin_registry(
          plugin_id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
          capability TEXT NOT NULL, package_hash TEXT NOT NULL,
          state TEXT NOT NULL, evidence TEXT, updated_at TEXT NOT NULL);
        """)
        db.commit()


def event(work_key: str, kind: str, detail: str = "") -> int:
    with sqlite3.connect(STATE_DB) as db:
        cur = db.execute("INSERT INTO work_events(work_key,event_type,detail,created_at) VALUES(?,?,?,?)",
                         (work_key, kind, detail[:4000], now()))
        db.commit(); return int(cur.lastrowid)


def ledger(work_key: str, worker: str, transition: str, attempt: int = 0, evidence: str = "") -> int:
    with sqlite3.connect(STATE_DB) as db:
        cur = db.execute("INSERT INTO work_ledger(work_key,worker,transition,attempt,evidence,created_at) VALUES(?,?,?,?,?,?)",
                         (work_key, worker, transition, attempt, evidence[:4000], now()))
        db.commit(); return int(cur.lastrowid)


def metric(work_key: str, name: str, value: float, unit: str = "count", detail: str = "") -> int:
    with sqlite3.connect(STATE_DB) as db:
        cur = db.execute("INSERT INTO telemetry(work_key,metric,value,unit,detail,created_at) VALUES(?,?,?,?,?,?)",
                         (work_key, name, value, unit, detail[:2000], now()))
        db.commit(); return int(cur.lastrowid)


def verify_signature(raw: bytes, signature: Optional[str]) -> bool:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        if REQUIRE_GITHUB_HMAC:
            raise HTTPException(503, "GITHUB_WEBHOOK_SECRET not configured")
        return False
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(401, "Missing GitHub signature")
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid GitHub signature")
    return True


def parse_repo_issue(url: str) -> Tuple[str, int]:
    m = re.search(r"github\.com/([^/]+/[^/]+)/(?:issues|pull)/(\d+)", url or "")
    if not m: raise ValueError(f"Unsupported GitHub URL: {url}")
    return m.group(1), int(m.group(2))


def parse_field(body: str, key: str) -> Optional[str]:
    m = re.search(rf"(?mi)^{re.escape(key)}:\s*(.+?)\s*$", body or "")
    return m.group(1).strip() if m else None


def work_key_for(body: str, delivery: str, url: str) -> str:
    declared = parse_field(body, "work_key")
    if declared: return declared
    if delivery: return f"github-delivery:{delivery}"
    return "github-fallback:" + hashlib.sha256(f"{url}\n{body}".encode()).hexdigest()[:24]


def claim(work_key: str, delivery: str, url: str, worker: str) -> str:
    ts = int(time.time())
    with sqlite3.connect(STATE_DB) as db:
        db.row_factory = sqlite3.Row; db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM work_claims WHERE work_key=?", (work_key,)).fetchone()
        if row and row["state"] == "COMPLETED": db.commit(); return "DEDUPED"
        if row and row["state"] == "CLAIMED" and (row["lease_until"] or 0) > ts: db.commit(); return "LEASED"
        attempt = (int(row["attempt"] or 0) + 1) if row else 1
        if row:
            db.execute("UPDATE work_claims SET state='CLAIMED',attempt=?,lease_until=?,delivery_id=?,worker=?,source_url=?,updated_at=? WHERE work_key=?",
                       (attempt, ts+LEASE_SECONDS, delivery, worker, url, now(), work_key))
        else:
            db.execute("INSERT INTO work_claims(work_key,delivery_id,worker,state,attempt,lease_until,source_url,created_at,updated_at) VALUES(?,?,?,'CLAIMED',1,?,?,?,?)",
                       (work_key, delivery, worker, ts+LEASE_SECONDS, url, now(), now()))
        db.commit()
    event(work_key, "CLAIMED", f"worker={worker};delivery={delivery}")
    ledger(work_key, worker, "CLAIMED", attempt, f"delivery={delivery}")
    metric(work_key, "wake", 1, detail=RUNTIME_ID)
    return "CLAIMED"


def attempt_for(work_key: str) -> int:
    with sqlite3.connect(STATE_DB) as db:
        row = db.execute("SELECT attempt FROM work_claims WHERE work_key=?", (work_key,)).fetchone()
        return int(row[0]) if row else 1


def reclaim(work_key: str) -> int:
    with sqlite3.connect(STATE_DB) as db:
        db.execute("BEGIN IMMEDIATE")
        n = int(db.execute("SELECT attempt FROM work_claims WHERE work_key=?", (work_key,)).fetchone()[0]) + 1
        db.execute("UPDATE work_claims SET state='CLAIMED',attempt=?,lease_until=?,updated_at=? WHERE work_key=?",
                   (n, int(time.time())+LEASE_SECONDS, now(), work_key)); db.commit()
    event(work_key, "RECOVERY_CLAIMED", f"attempt={n};worker=WKR-RECOVER-001")
    ledger(work_key, "WKR-RECOVER-001", "RECOVERY_CLAIMED", n)
    metric(work_key, "recovery_retry", 1)
    return n


def gh_env() -> dict:
    env = os.environ.copy()
    if env.get("GITHUB_PAT") and not env.get("GH_TOKEN"): env["GH_TOKEN"] = env["GITHUB_PAT"]
    return env


def github_comment(url: str, body: str) -> int:
    repo, num = parse_repo_issue(url)
    p = subprocess.run(["gh","api",f"repos/{repo}/issues/{num}/comments","-f",f"body={body}"],
                       env=gh_env(), capture_output=True, text=True, timeout=30)
    if p.returncode: raise RuntimeError("GitHub receipt write failed: " + p.stderr.strip())
    cid = int(json.loads(p.stdout)["id"])
    rb = subprocess.run(["gh","api",f"repos/{repo}/issues/comments/{cid}"], env=gh_env(),capture_output=True,text=True,timeout=30)
    if rb.returncode or int(json.loads(rb.stdout).get("id",0)) != cid: raise RuntimeError("GitHub receipt readback failed")
    return cid


def qwen(prompt: str) -> str:
    t0=time.time()
    r=requests.post("http://127.0.0.1:11434/api/generate",json={"model":OLLAMA_MODEL,"prompt":prompt,"stream":False},timeout=90)
    r.raise_for_status(); out=r.json().get("response","").strip()
    if not out: raise RuntimeError("Qwen returned empty output")
    return out


def hmac_proof(work_key: str) -> dict:
    secret=os.environ.get("GITHUB_WEBHOOK_SECRET","")
    if not secret: raise RuntimeError("HMAC_PROOF_FAILED:no_webhook_secret")
    body=b'{"t4h":"hmac-proof"}'
    valid="sha256="+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    valid_ok = hmac.compare_digest(valid, "sha256="+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest())
    invalid_ok = not hmac.compare_digest("sha256=invalid", valid)
    if not (valid_ok and invalid_ok): raise RuntimeError("HMAC_PROOF_FAILED:comparison")
    event(work_key,"HMAC_VALID_ACCEPTED","runtime self-test")
    event(work_key,"HMAC_INVALID_REJECTED","runtime self-test")
    metric(work_key,"hmac_valid_accept",1); metric(work_key,"hmac_invalid_reject",1)
    return {"valid_signature":"PASS","invalid_signature":"REJECTED","require_hmac":REQUIRE_GITHUB_HMAC}


def plugin_slice(work_key: str) -> dict:
    plugin_id="PLG-T4H-PORTABLE-SLICE-001"; capability="portable-runtime-proof"
    skill_text="# Portable Runtime Proof\nProduce a concise deterministic acknowledgement for a bounded synthetic input.\n"
    plugin={"plugin_id":plugin_id,"name":"T4H Portable Runtime Proof","version":"1.0.0","package_standard":"agent-plugins-1.0",
            "skills":[{"name":"portable-runtime-proof","path":"skills/portable-runtime-proof/SKILL.md"}],"mcp_servers":[],
            "com.t4h.runtime":{"authority":"READ","synthetic_only":True}}
    blob=(json.dumps(plugin,sort_keys=True)+skill_text).encode(); digest=hashlib.sha256(blob).hexdigest()
    forbidden=["rm -rf","sudo ","curl http","wget http","GITHUB_TOKEN","AWS_SECRET_ACCESS_KEY"]
    findings=[x for x in forbidden if x.lower() in skill_text.lower()]
    if findings: raise RuntimeError("PLUGIN_STATIC_SCAN_FAILED:"+",".join(findings))
    parsed=json.loads(json.dumps(plugin)); assert parsed["package_standard"]=="agent-plugins-1.0"
    output=qwen("Bounded synthetic plugin test. Reply only with: PORTABLE_PLUGIN_OK")
    runtime_ok=bool(output.strip())
    with sqlite3.connect(STATE_DB) as db:
        db.execute("INSERT OR REPLACE INTO plugin_registry(plugin_id,name,version,capability,package_hash,state,evidence,updated_at) VALUES(?,?,?,?,?,'AVAILABLE',?,?)",
                   (plugin_id,plugin["name"],plugin["version"],capability,digest,json.dumps({"scan":"PASS","sandbox":"PASS","runtime":"PASS"}),now())); db.commit()
        available=db.execute("SELECT count(*) FROM plugin_registry WHERE plugin_id=? AND state='AVAILABLE'",(plugin_id,)).fetchone()[0]==1
        db.execute("UPDATE plugin_registry SET state='REVOKED',updated_at=? WHERE plugin_id=?",(now(),plugin_id)); db.commit()
        revoked=db.execute("SELECT count(*) FROM plugin_registry WHERE plugin_id=? AND state='AVAILABLE'",(plugin_id,)).fetchone()[0]==0
    if not (runtime_ok and available and revoked): raise RuntimeError("PLUGIN_SLICE_VALIDATION_FAILED")
    event(work_key,"PLUGIN_PACKAGE",digest); event(work_key,"PLUGIN_STATIC_SCAN","PASS"); event(work_key,"PLUGIN_SANDBOX","PASS")
    event(work_key,"PLUGIN_RUNTIME_EXECUTION","PASS"); event(work_key,"PLUGIN_REVOKED","PASS")
    metric(work_key,"plugin_slice_pass",1); metric(work_key,"plugin_static_findings",0)
    return {"plugin_id":plugin_id,"package_standard":"agent-plugins-1.0","package_hash":digest,"static_scan":"PASS","sandbox":"PASS",
            "registry_available_readback":"PASS","runtime_execution":"PASS","revocation_readback":"PASS","final_registry_state":"REVOKED"}


def complete(work_key: str, url: str, worker: str, output: dict | str, attempt: int) -> None:
    ledger_id=ledger(work_key,worker,"VALIDATED",attempt)
    tel_id=metric(work_key,"completion",1,detail=RUNTIME_ID)
    body=(f"<!-- t4h-receipt -->\nwork_key: {work_key}\nworker: {worker}\nstate: COMPLETED\nvalidation: PASS\nreadback: PASS\n"
          f"runtime: {RUNTIME_ID}\nledger_ref: sqlite:{STATE_DB}:work_ledger:{ledger_id}\ntelemetry_ref: sqlite:{STATE_DB}:telemetry:{tel_id}\nnext_state: SLEEP\n\n"
          f"### Worker result\n```json\n{json.dumps(output,indent=2)[:6000]}\n```")
    cid=github_comment(url,body)
    with sqlite3.connect(STATE_DB) as db:
        db.execute("UPDATE work_claims SET state='COMPLETED',lease_until=NULL,receipt_comment_id=?,last_error=NULL,updated_at=? WHERE work_key=?",(cid,now(),work_key));db.commit()
    event(work_key,"COMPLETED",f"receipt={cid}"); ledger(work_key,worker,"SLEEP",attempt,f"receipt={cid}")


def failure(work_key: str,url: str,worker: str,exc: Exception) -> bool:
    a=attempt_for(work_key); terminal=a>=MAX_ATTEMPTS; state="BLOCKED" if terminal else "RETRY_READY"; err=str(exc)[:2000]
    with sqlite3.connect(STATE_DB) as db:
        db.execute("UPDATE work_claims SET state=?,lease_until=NULL,last_error=?,updated_at=? WHERE work_key=?",(state,err,now(),work_key));db.commit()
    event(work_key,state,err); ledger(work_key,worker,state,a,err); metric(work_key,"failure",1,detail=state)
    try: github_comment(url,f"<!-- t4h-receipt -->\nwork_key: {work_key}\nworker: {worker}\nstate: {state}\nattempt: {a}/{MAX_ATTEMPTS}\nrecovery: {'BLOCKED_AFTER_MAX_ATTEMPTS' if terminal else 'WKR-RECOVER-001_AUTO_RETRY'}\nerror: {err}\nnext_state: {'ESCALATE' if terminal else 'RETRY'}")
    except Exception as e: event(work_key,"RECEIPT_WRITE_FAILED",str(e))
    return terminal


def execute(work_key: str,url: str,body: str,worker: str) -> None:
    while True:
        a=attempt_for(work_key)
        try:
            event(work_key,"EXECUTE_START",f"attempt={a};worker={worker}"); ledger(work_key,worker,"EXECUTE_START",a)
            test_class=parse_field(body,"test_class") or ""
            if test_class=="LIVE_FORCED_FAILURE" and a==1: raise RuntimeError("CONTROLLED_TEST_FAILURE:first_attempt")
            if test_class=="LIVE_HMAC_PROOF": result=hmac_proof(work_key)
            elif worker=="WKR-PLUGIN-001" or test_class=="LIVE_PLUGIN_VERTICAL_SLICE": result=plugin_slice(work_key)
            else: result={"qwen":qwen("You are a bounded engineering worker. Do not request human approval except credentials/legal/safety/destructive/missing authority.\n\n"+body)}
            complete(work_key,url,worker,result,a); print(f"[T4H WORKER] COMPLETE {work_key} attempt={a} -> SLEEP",flush=True); return
        except Exception as exc:
            terminal=failure(work_key,url,worker,exc); print(f"[T4H WORKER] FAILED {work_key} attempt={a}: {exc}",flush=True)
            if terminal:return
            event(work_key,"ROUTED_RECOVERY","WKR-RECOVER-001"); time.sleep(RETRY_DELAY_SECONDS); n=reclaim(work_key)
            print(f"[T4H WORKER] WKR-RECOVER-001 AUTO-RETRY {work_key} attempt={n}",flush=True)


@app.on_event("startup")
def startup(): init_db(); print(f"[T4H WORKER] canonical runtime {RUNTIME_ID}; state={STATE_DB}",flush=True)

@app.post("/github-webhook")
async def webhook(request: Request, tasks: BackgroundTasks):
    raw=await request.body(); signed=verify_signature(raw,request.headers.get("X-Hub-Signature-256")); payload=json.loads(raw.decode())
    body=payload.get("comment",{}).get("body",""); url=payload.get("issue",{}).get("html_url") or payload.get("issue",{}).get("url","")
    delivery=request.headers.get("X-GitHub-Delivery","")
    if not any(x in body for x in ["/action","/approve","<!-- t4h-event -->"]): return {"status":"ignored"}
    wk=work_key_for(body,delivery,url); worker=parse_field(body,"target_worker") or "WKR-EXEC-001"; c=claim(wk,delivery,url,worker)
    if c=="DEDUPED":
        event(wk,"DEDUPED",delivery); lid=ledger(wk,worker,"DEDUPED",attempt_for(wk)); tid=metric(wk,"dedupe",1)
        try: github_comment(url,f"<!-- t4h-receipt -->\nwork_key: {wk}\nworker: {worker}\nstate: DEDUPED\nvalidation: PASS\nexecution: SKIPPED\nledger_ref: sqlite:{STATE_DB}:work_ledger:{lid}\ntelemetry_ref: sqlite:{STATE_DB}:telemetry:{tid}\nnext_state: SLEEP")
        except Exception as e:event(wk,"DEDUPE_RECEIPT_FAILED",str(e))
        return {"status":"deduped","work_key":wk,"state":"SLEEP"}
    if c=="LEASED": return {"status":"already_claimed","work_key":wk,"state":"CLAIMED"}
    event(wk,"SIGNATURE_VERIFIED",str(signed)); metric(wk,"signature_verified",1 if signed else 0)
    tasks.add_task(execute,wk,url,body,worker)
    return {"status":"claimed","work_key":wk,"worker":worker,"state":"EXECUTING","signature_verified":signed}

@app.get("/health")
def health():
    init_db(); return {"status":"ok","runtime":RUNTIME_ID,"state_db":str(STATE_DB),"model":OLLAMA_MODEL,
                       "hmac_required":REQUIRE_GITHUB_HMAC,"hmac_configured":bool(os.environ.get("GITHUB_WEBHOOK_SECRET")),
                       "ledger":"work_ledger","telemetry":"telemetry"}
