#!/usr/bin/env python3
"""Recovery sweep for estate event-continuity gaps.

Webhooks remain primary. This low-frequency persistent sweep is the recovery net for old/orphaned
work or repositories that did not deliver a usable event. It routes candidates only; it never marks
the underlying domain outcome complete.
"""
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

STATE = Path.home() / ".local/state/synal/sentry-sweep.db"
CONTROL_REPO = "TML-4PM/t4h-engineering-control-plane"
SENTRY_ISSUE = 37
MAX_NEW_ROUTES = 25
QUERIES = [
    'org:TML-4PM is:issue is:open "missing receipt"',
    'org:TML-4PM is:issue is:open "expected receipt"',
    'org:TML-4PM is:issue is:open "zero workflow runs"',
    'org:TML-4PM is:issue is:open "no workflow runs"',
    'org:TML-4PM is:issue is:open "runner unavailable"',
    'org:TML-4PM is:issue is:open billing Actions',
    'org:TML-4PM is:issue is:open "did not pick up"',
    'org:TML-4PM is:issue is:open "did not wake"',
    'org:TML-4PM is:issue is:open "no runtime receipt"',
    'org:TML-4PM is:issue is:open "downstream proof chain"',
]


def gh(*args):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "gh failed")
    return p.stdout


def init():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS routed(
          candidate_key TEXT PRIMARY KEY, source_url TEXT NOT NULL,
          source_updated_at TEXT, routed_comment_id INTEGER, routed_at INTEGER NOT NULL)""")
        db.commit()


def candidates():
    found = {}
    for query in QUERIES:
        raw = gh("api", "-X", "GET", "search/issues", "-f", f"q={query}", "-f", "per_page=100")
        payload = json.loads(raw or "{}")
        for item in payload.get("items", []):
            url = item.get("html_url", "")
            if not url:
                continue
            if url == f"https://github.com/{CONTROL_REPO}/issues/{SENTRY_ISSUE}":
                continue
            found[url] = {
                "url": url,
                "title": item.get("title", ""),
                "updatedAt": item.get("updated_at", ""),
                "number": item.get("number"),
            }
    return sorted(found.values(), key=lambda x: x.get("updatedAt", ""), reverse=True)


def route(item):
    url = item["url"]
    updated = item.get("updatedAt") or ""
    key = hashlib.sha256(f"{url}|{updated}".encode()).hexdigest()[:20]
    with sqlite3.connect(STATE) as db:
        if db.execute("SELECT 1 FROM routed WHERE candidate_key=?", (key,)).fetchone():
            return False
    body = f"""/action estate event-continuity candidate discovered by recovery sweep
<!-- t4h-event -->
work_key: github:event-continuity-sweep:{key}
target_worker: WKR-EVENT-SENTRY-001
source_item: {url}
source_updated_at: {updated}
requested_action: refresh source truth; determine whether expected event/wake/receipt chain is missing or stale; if continuity failure exists diagnose/repair or route WKR-RECOVER-001/domain owner; independently verify evidence; do not mark domain outcome complete from issue text alone
test_class: ESTATE_CONTINUITY_CANDIDATE
"""
    created = json.loads(gh("api", f"repos/{CONTROL_REPO}/issues/{SENTRY_ISSUE}/comments", "-f", f"body={body}"))
    cid = int(created["id"])
    rb = json.loads(gh("api", f"repos/{CONTROL_REPO}/issues/comments/{cid}"))
    if int(rb.get("id", 0)) != cid:
        raise RuntimeError("routing comment readback failed")
    with sqlite3.connect(STATE) as db:
        db.execute(
            "INSERT INTO routed(candidate_key,source_url,source_updated_at,routed_comment_id,routed_at) VALUES(?,?,?,?,?)",
            (key, url, updated, cid, int(time.time())),
        )
        db.commit()
    return True


def main():
    init()
    scanned = candidates()
    routed = 0
    skipped_existing = 0
    errors = []
    for item in scanned:
        if routed >= MAX_NEW_ROUTES:
            break
        try:
            if route(item):
                routed += 1
            else:
                skipped_existing += 1
        except Exception as exc:
            errors.append({"url": item.get("url"), "error": str(exc)[:300]})
    result = {
        "state": "COMPLETE" if not errors else "PARTIAL",
        "candidates_discovered": len(scanned),
        "new_routes": routed,
        "skipped_existing": skipped_existing,
        "batch_limit": MAX_NEW_ROUTES,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
