#!/usr/bin/env python3
"""Authorised thread observation ingest: local durable checkpoint + governed Supabase readback.

Input is one JSON observation on stdin. This is the provider-neutral boundary for browser/client
adapters. It does not scrape providers and accepts no session credentials.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


thread_idle = load("thread_idle", ROOT / "runtime" / "thread_idle.py")
thread_store = load("thread_store", ROOT / "runtime" / "thread_store.py")


def main() -> None:
    raw = json.load(sys.stdin)
    forbidden = {"cookie", "cookies", "session_token", "access_token", "refresh_token", "password", "authorization"}
    leaked = sorted(k for k in raw if k.lower() in forbidden)
    if leaked:
        raise SystemExit("refusing credential/session fields: " + ",".join(leaked))

    obs = thread_idle.parse_observation(raw)
    db = thread_idle.connect(thread_idle.DEFAULT_STATE)
    local = thread_idle.record_observation(db, obs)
    remote = thread_store.persist_activity(
        thread_store.client_from_env(),
        obs,
        state="ACTIVE",
        observer_id=str(raw.get("observer_id", "authorised-source-adapter")),
    )
    print(json.dumps({"status": "RECORDED", "thread_key": obs.thread_key, "local": local, "supabase": remote}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
