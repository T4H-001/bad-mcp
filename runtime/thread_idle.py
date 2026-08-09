#!/usr/bin/env python3
"""Provider-neutral thread activity register and inactivity evaluator.

This module does not scrape chat providers. It accepts authorised observations from adapters,
persists material activity locally, and emits deterministic idle candidates for the existing
Synal event/runtime to route.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_STATE = Path.home() / ".local/state/synal/thread-activity.db"
DEFAULT_POLICY = {
    "hot_idle_after_s": 3600,
    "normal_idle_after_s": 18000,
    "cold_idle_after_s": 172800,
    "recheck_before_commit_s": 300,
}


@dataclass(frozen=True)
class Observation:
    source_system: str
    source_native_id: str
    content_revision: str
    content_hash: str
    last_message_at: int
    last_material_activity_at: int
    source_ref: str = ""
    priority: str = "normal"

    @property
    def thread_key(self) -> str:
        return f"{self.source_system}:{self.source_native_id}"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS thread_activity(
        thread_key TEXT PRIMARY KEY,
        source_system TEXT NOT NULL,
        source_native_id TEXT NOT NULL,
        source_ref TEXT NOT NULL DEFAULT '',
        content_revision TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        first_seen_at INTEGER NOT NULL,
        last_message_at INTEGER NOT NULL,
        last_material_activity_at INTEGER NOT NULL,
        priority TEXT NOT NULL DEFAULT 'normal',
        idle_candidate_at INTEGER,
        idle_confirmed_at INTEGER,
        emitted_revision TEXT,
        state TEXT NOT NULL DEFAULT 'ACTIVE',
        updated_at INTEGER NOT NULL
    )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS idle_events(
        work_key TEXT PRIMARY KEY,
        thread_key TEXT NOT NULL,
        content_revision TEXT NOT NULL,
        event_type TEXT NOT NULL,
        emitted_at INTEGER NOT NULL,
        payload_json TEXT NOT NULL
    )"""
    )
    db.commit()
    return db


def record_observation(db: sqlite3.Connection, obs: Observation, now: int | None = None) -> str:
    now = int(now or time.time())
    row = db.execute(
        "SELECT content_revision,last_material_activity_at FROM thread_activity WHERE thread_key=?",
        (obs.thread_key,),
    ).fetchone()
    first_seen = now if row is None else db.execute(
        "SELECT first_seen_at FROM thread_activity WHERE thread_key=?", (obs.thread_key,)
    ).fetchone()[0]
    revision_changed = row is None or row[0] != obs.content_revision
    material_advanced = row is None or obs.last_material_activity_at > int(row[1])
    state = "ACTIVE" if revision_changed or material_advanced else db.execute(
        "SELECT state FROM thread_activity WHERE thread_key=?", (obs.thread_key,)
    ).fetchone()[0]
    clear_idle = revision_changed or material_advanced
    db.execute(
        """INSERT INTO thread_activity(
        thread_key,source_system,source_native_id,source_ref,content_revision,content_hash,
        first_seen_at,last_message_at,last_material_activity_at,priority,idle_candidate_at,
        idle_confirmed_at,emitted_revision,state,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(thread_key) DO UPDATE SET
          source_ref=excluded.source_ref,
          content_revision=excluded.content_revision,
          content_hash=excluded.content_hash,
          last_message_at=excluded.last_message_at,
          last_material_activity_at=excluded.last_material_activity_at,
          priority=excluded.priority,
          idle_candidate_at=CASE WHEN ? THEN NULL ELSE thread_activity.idle_candidate_at END,
          idle_confirmed_at=CASE WHEN ? THEN NULL ELSE thread_activity.idle_confirmed_at END,
          state=excluded.state,
          updated_at=excluded.updated_at
        """,
        (
            obs.thread_key, obs.source_system, obs.source_native_id, obs.source_ref,
            obs.content_revision, obs.content_hash, first_seen, obs.last_message_at,
            obs.last_material_activity_at, obs.priority, None, None, None, state, now,
            1 if clear_idle else 0, 1 if clear_idle else 0,
        ),
    )
    db.commit()
    return "UPDATED" if row else "CREATED"


def threshold_for(priority: str, policy: dict) -> int:
    if priority == "hot":
        return int(policy["hot_idle_after_s"])
    return int(policy["normal_idle_after_s"])


def evaluate_idle(db: sqlite3.Connection, now: int | None = None, policy: dict | None = None) -> list[dict]:
    now = int(now or time.time())
    policy = {**DEFAULT_POLICY, **(policy or {})}
    out: list[dict] = []
    rows = db.execute(
        """SELECT thread_key,source_system,source_native_id,source_ref,content_revision,content_hash,
        last_message_at,last_material_activity_at,priority,idle_candidate_at,idle_confirmed_at,
        emitted_revision,state FROM thread_activity"""
    ).fetchall()
    for row in rows:
        (
            thread_key, source_system, source_native_id, source_ref, revision, content_hash,
            last_message_at, last_material_at, priority, candidate_at, confirmed_at,
            emitted_revision, state,
        ) = row
        age = now - int(last_material_at)
        threshold = threshold_for(priority, policy)
        recovery = age >= int(policy["cold_idle_after_s"])
        if age < threshold:
            continue
        if candidate_at is None:
            db.execute(
                "UPDATE thread_activity SET idle_candidate_at=?,state='IDLE_CANDIDATE',updated_at=? WHERE thread_key=?",
                (now, now, thread_key),
            )
            continue
        if not recovery and now - int(candidate_at) < int(policy["recheck_before_commit_s"]):
            continue
        if emitted_revision == revision:
            continue
        work_key = "thread-idle:" + hashlib.sha256(
            f"{source_system}|{source_native_id}|{revision}|v1".encode()
        ).hexdigest()[:24]
        event = {
            "event_type": "THREAD_IDLE_CONFIRMED",
            "work_key": work_key,
            "target_worker": "WKR-FINALISE-001",
            "compiler": "OUTCOME-001",
            "source_system": source_system,
            "source_native_id": source_native_id,
            "source_ref": source_ref,
            "content_revision": revision,
            "content_hash": content_hash,
            "last_message_at": int(last_message_at),
            "last_material_activity_at": int(last_material_at),
            "idle_age_s": age,
            "recovery": recovery,
            "requested_action": "snapshot provenance; compile/reduce thread; persist canonical closeout; route unresolved outcomes; receipt/readback; sleep",
        }
        db.execute(
            "INSERT OR IGNORE INTO idle_events(work_key,thread_key,content_revision,event_type,emitted_at,payload_json) VALUES(?,?,?,?,?,?)",
            (work_key, thread_key, revision, event["event_type"], now, json.dumps(event, sort_keys=True)),
        )
        db.execute(
            "UPDATE thread_activity SET idle_confirmed_at=?,emitted_revision=?,state='QUIESCENT',updated_at=? WHERE thread_key=?",
            (now, revision, now, thread_key),
        )
        out.append(event)
    db.commit()
    return out


def parse_observation(raw: dict) -> Observation:
    required = ["source_system", "source_native_id", "content_revision", "last_message_at", "last_material_activity_at"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"missing required fields: {','.join(missing)}")
    digest = raw.get("content_hash") or hashlib.sha256(
        f"{raw['source_system']}|{raw['source_native_id']}|{raw['content_revision']}".encode()
    ).hexdigest()
    return Observation(
        source_system=str(raw["source_system"]),
        source_native_id=str(raw["source_native_id"]),
        source_ref=str(raw.get("source_ref", "")),
        content_revision=str(raw["content_revision"]),
        content_hash=str(digest),
        last_message_at=int(raw["last_message_at"]),
        last_material_activity_at=int(raw["last_material_activity_at"]),
        priority=str(raw.get("priority", "normal")),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_STATE))
    ap.add_argument("--observe", help="JSON observation file, or - for stdin")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--now", type=int)
    args = ap.parse_args()
    db = connect(Path(args.db))
    result = {"observed": None, "events": []}
    if args.observe:
        text = __import__("sys").stdin.read() if args.observe == "-" else Path(args.observe).read_text()
        obs = parse_observation(json.loads(text))
        result["observed"] = {"thread_key": obs.thread_key, "result": record_observation(db, obs, args.now)}
    if args.evaluate:
        result["events"] = evaluate_idle(db, args.now)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
