#!/usr/bin/env python3
"""Evaluate thread inactivity and route confirmed events through the existing GitHub->Synal ingress."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "runtime" / "thread_idle.py"
spec = importlib.util.spec_from_file_location("thread_idle", MODULE)
thread_idle = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = thread_idle
spec.loader.exec_module(thread_idle)

CONTROL_REPO = os.environ.get("T4H_CONTROL_REPO", "TML-4PM/t4h-engineering-control-plane")
THREAD_IDLE_ISSUE = int(os.environ.get("T4H_THREAD_IDLE_ISSUE", "60"))


def gh_comment(body: str) -> int:
    p = subprocess.run(
        ["gh", "api", f"repos/{CONTROL_REPO}/issues/{THREAD_IDLE_ISSUE}/comments", "-f", f"body={body}"],
        capture_output=True, text=True, timeout=30,
    )
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "GitHub event comment failed")
    cid = int(json.loads(p.stdout)["id"])
    rb = subprocess.run(
        ["gh", "api", f"repos/{CONTROL_REPO}/issues/comments/{cid}"],
        capture_output=True, text=True, timeout=30,
    )
    if rb.returncode or int(json.loads(rb.stdout).get("id", 0)) != cid:
        raise RuntimeError("GitHub event comment readback failed")
    return cid


def release_for_retry(db: sqlite3.Connection, event: dict) -> None:
    thread_key = f"{event['source_system']}:{event['source_native_id']}"
    db.execute(
        "UPDATE thread_activity SET emitted_revision=NULL,idle_confirmed_at=NULL,state='IDLE_CANDIDATE' WHERE thread_key=? AND content_revision=?",
        (thread_key, event["content_revision"]),
    )
    db.commit()


def main() -> None:
    db = thread_idle.connect(thread_idle.DEFAULT_STATE)
    events = thread_idle.evaluate_idle(db)
    routed = []
    errors = []
    for event in events:
        body = f"""/action confirmed inactive chat thread requires durable closeout
<!-- t4h-event -->
work_key: {event['work_key']}
target_worker: WKR-FINALISE-001
source_system: {event['source_system']}
source_native_id: {event['source_native_id']}
source_ref: {event['source_ref']}
content_revision: {event['content_revision']}
content_hash: {event['content_hash']}
last_material_activity_at: {event['last_material_activity_at']}
requested_action: snapshot authorised thread provenance; invoke OUTCOME-001 to reduce intent into durable outcomes; persist canonical closeout in governed Supabase; route unresolved executable outcomes under WKR-FINALISE-001; create Drive/Doc projection only when destination policy warrants it; independently verify readback; receipt and sleep
test_class: THREAD_IDLE_CONFIRMED
"""
        try:
            cid = gh_comment(body)
            routed.append({"work_key": event["work_key"], "comment_id": cid})
        except Exception as exc:
            release_for_retry(db, event)
            errors.append({"work_key": event["work_key"], "error": str(exc)[:300]})
    print(json.dumps({"events": len(events), "routed": routed, "errors": errors}, indent=2))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
