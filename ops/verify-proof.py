#!/usr/bin/env python3
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

DB = Path.home() / ".local/state/synal/work.db"


def ordered(actual, required, label):
    pos = -1
    for item in required:
        try:
            pos = actual.index(item, pos + 1)
        except ValueError as exc:
            raise SystemExit(f"FAIL {label}: missing/out-of-order {item}; actual={actual}") from exc


def rows(db, sql, key):
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(sql, (key,)).fetchall()]


def index_by_id(rows_):
    return {int(r["id"]): r for r in rows_}


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-proof.py <work_key>")
    key = sys.argv[1]
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        claim = db.execute("SELECT * FROM work_claims WHERE work_key=?", (key,)).fetchone()
        current_events = rows(db, "SELECT id,event_type,detail,created_at FROM work_events WHERE work_key=? ORDER BY id", key)
        current_ledger = rows(db, "SELECT id,worker,transition,attempt,evidence,created_at FROM work_ledger WHERE work_key=? ORDER BY id", key)
        current_telemetry = rows(db, "SELECT id,metric,value,unit,detail,created_at FROM telemetry WHERE work_key=? ORDER BY id", key)
        proof_row = db.execute("SELECT proof_digest,proof_json,verified FROM proof_runs WHERE work_key=?", (key,)).fetchone()

    if not claim:
        raise SystemExit("FAIL missing claim")
    if dict(claim)["state"] != "COMPLETED":
        raise SystemExit(f"FAIL claim state={dict(claim)['state']}")
    if not proof_row:
        raise SystemExit("FAIL proof_runs row missing")
    if not bool(proof_row[2]):
        raise SystemExit("FAIL stored proof not verified")

    snapshot = json.loads(proof_row[1])
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    if digest != proof_row[0]:
        raise SystemExit(f"FAIL immutable snapshot digest mismatch stored={proof_row[0]} recomputed={digest}")

    snap_event_names = [x["event_type"] for x in snapshot["events"]]
    snap_ledger_names = [x["transition"] for x in snapshot["ledger"]]
    snap_metrics = {x["metric"] for x in snapshot["telemetry"]}
    ordered(snap_event_names, ["RECEIVED","SIGNATURE_VERIFIED","CLAIMED","EXECUTE_START","VALIDATED","COMPLETED"], "snapshot.events")
    ordered(snap_ledger_names, ["CLAIMED","EXECUTE_START","VALIDATED","SLEEP"], "snapshot.ledger")
    for required in ("signature_verified","wake","validation","completion"):
        if required not in snap_metrics:
            raise SystemExit(f"FAIL snapshot telemetry missing {required}")

    # Prove the immutable snapshot rows still exist unchanged in the final durable DB.
    current_event_index = index_by_id(current_events)
    current_ledger_index = index_by_id(current_ledger)
    current_telemetry_index = index_by_id(current_telemetry)
    for row in snapshot["events"]:
        if current_event_index.get(int(row["id"])) != row:
            raise SystemExit(f"FAIL event snapshot row changed/missing id={row['id']}")
    for row in snapshot["ledger"]:
        if current_ledger_index.get(int(row["id"])) != row:
            raise SystemExit(f"FAIL ledger snapshot row changed/missing id={row['id']}")
    for row in snapshot["telemetry"]:
        if current_telemetry_index.get(int(row["id"])) != row:
            raise SystemExit(f"FAIL telemetry snapshot row changed/missing id={row['id']}")

    final_event_names = [x["event_type"] for x in current_events]
    final_metrics = {x["metric"] for x in current_telemetry}
    if "READBACK_VERIFIED" not in final_event_names:
        raise SystemExit("FAIL final DB missing READBACK_VERIFIED")
    if "readback_verified" not in final_metrics:
        raise SystemExit("FAIL final telemetry missing readback_verified")

    print(json.dumps({
        "state":"VERIFIED",
        "work_key":key,
        "execution_proof_digest":digest,
        "immutable_snapshot_verified":True,
        "snapshot_rows_still_present":True,
        "claim_state":dict(claim)["state"],
        "execution_event_chain":snap_event_names,
        "execution_ledger_chain":snap_ledger_names,
        "final_readback_event_verified":True,
        "final_event_chain":final_event_names,
        "final_telemetry_metrics":sorted(final_metrics)
    }, indent=2))


if __name__ == "__main__":
    main()
