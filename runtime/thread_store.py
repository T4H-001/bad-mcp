#!/usr/bin/env python3
"""Server-side persistence adapter for THREAD-IDLE-001.

Only trusted T4H runtime processes should load the Supabase service credential. Browser/client
observers submit bounded observations to the runtime; they never receive the service credential.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

ACTIVITY_RPC = "t4h_thread_activity_upsert"
CLOSEOUT_RPC = "t4h_thread_closeout_persist"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()


def client_from_env() -> Client:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_SECRET_KEY", "")).strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and server-side Supabase service/secret key are required")
    return create_client(url, key)


def activity_payload(obs: Any, state: str = "ACTIVE", observer_id: str = "synal-thread-observer") -> dict[str, Any]:
    return {
        "thread_key": obs.thread_key,
        "source_system": obs.source_system,
        "source_native_id": obs.source_native_id,
        "source_ref": obs.source_ref,
        "content_revision": obs.content_revision,
        "content_hash": obs.content_hash,
        "last_message_at": _iso(obs.last_message_at),
        "last_material_activity_at": _iso(obs.last_material_activity_at),
        "priority": obs.priority,
        "state": state,
        "observer_id": observer_id,
        "observer_checkpoint": {"source_revision": obs.content_revision},
    }


def persist_activity(client: Client, obs: Any, state: str = "ACTIVE", observer_id: str = "synal-thread-observer") -> dict[str, Any]:
    result = client.rpc(ACTIVITY_RPC, {"p": activity_payload(obs, state, observer_id)}).execute()
    data = result.data
    if not isinstance(data, dict) or data.get("thread_key") != obs.thread_key:
        raise RuntimeError("Supabase activity readback mismatch")
    return data


def persist_closeout(client: Client, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"work_key", "thread_key", "source_revision", "source_hash", "classification"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError("missing closeout fields: " + ",".join(missing))
    result = client.rpc(CLOSEOUT_RPC, {"p": payload}).execute()
    data = result.data
    if not isinstance(data, dict) or data.get("work_key") != payload["work_key"]:
        raise RuntimeError("Supabase closeout readback mismatch")
    return data
