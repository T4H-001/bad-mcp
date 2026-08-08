#!/usr/bin/env python3
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

DB = Path.home() / ".local/state/synal/work.db"


def rows(db, sql, key):
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(sql, (key,)).fetchall()]


def ordered(actual, required, label):
    pos = -1
    for item in required:
        try:
            pos = actual.index(item, pos + 1)
        except ValueError as exc:
            raise SystemExit(f"FAIL {label}: missing/out-of-order {item}; actual={actual}") from exc


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-proof.py <work_key>")
    key = sys.argv[1]
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        claim = db.execute("SELECT * FROM work_claims WHERE work_key=?", (key,)).fetchone()
        if not claim:
            raise SystemExit("FAIL missing claim")
        events = rows(db, "SELECT id,event_type,detail,created_at FROM work_events WHERE work_key=? ORDER BY id", key)
        ledger = rows(db, "SELECT id,worker,transition,attempt,evidence,created_at FROM work_ledger WHERE work_key=? ORDER BY id", key)
        telemetry = rows(db, "SELECT id,metric,value,unit,detail,created_at FROM telemetry WHERE work_key=? ORDER BY id", key)
        proof = db.execute("SELECT proof_digest,verified FROM proof_runs WHERE work_key=?", (key,)).fetchone()
    event_names = [x["event_type"] for x in events]
    ledger_names = [x["transition"] for x in ledger]
    ordered(event_names, ["RECEIVED","SIGNATURE_VERIFIED","CLAIMED","EXECUTE_START","VALIDATED","COMPLETED"], "events")
    ordered(ledger_names, ["CLAIMED","EXECUTE_START","VALIDATED","SLEEP"], "ledger")
    metrics = {x["metric"] for x in telemetry}
    for required in ("signature_verified","wake","validation","completion"):
        if required not in metrics:
            raise SystemExit(f"FAIL telemetry missing {required}")
    if dict(claim)["state"] != "COMPLETED":
        raise SystemExit(f"FAIL claim state={dict(claim)['state']}")
    bundle = {"work_key": key, "claim": dict(claim), "events": events, "ledger": ledger, "telemetry": telemetry}
    digest = hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    if not proof:
        raise SystemExit("FAIL proof_runs row missing")
    if not bool(proof[1]):
        raise SystemExit("FAIL stored proof not verified")
    if digest != proof[0]:
        raise SystemExit(f"FAIL digest mismatch stored={proof[0]} recomputed={digest}")
    print(json.dumps({
        "state":"VERIFIED",
        "work_key":key,
        "proof_digest":digest,
        "claim_state":dict(claim)["state"],
        "event_chain":event_names,
        "ledger_chain":ledger_names,
        "telemetry_metrics":sorted(metrics)
    }, indent=2))


if __name__ == "__main__":
    main()
