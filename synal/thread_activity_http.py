"""Authenticated provider-neutral THREAD_ACTIVITY HTTP ingress.

The client supplies metadata only (thread identity, revision/hash, timestamps). Raw chat content,
browser cookies and provider session credentials are neither required nor accepted.
"""
from __future__ import annotations

import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


thread_idle = _load("thread_idle_http", ROOT / "runtime" / "thread_idle.py")
thread_store = _load("thread_store_http", ROOT / "runtime" / "thread_store.py")

router = APIRouter()
FORBIDDEN_FIELDS = {
    "cookie", "cookies", "session_token", "access_token", "refresh_token", "password",
    "authorization", "openai_api_key", "supabase_service_role_key", "supabase_secret_key",
}
ALLOWED_SOURCES = {"chatgpt", "claude", "gemini", "grok", "perplexity", "other"}


def _require_observer_key(request: Request) -> None:
    # Prefer a dedicated observer key when configured, but reuse the existing governed
    # Synal Snaps ingress credential to avoid creating and repeatedly provisioning a
    # second browser-ingress secret for the same trusted extension/runtime boundary.
    expected = (
        os.environ.get("T4H_THREAD_OBSERVER_API_KEY", "").strip()
        or os.environ.get("SNAPS_INGEST_API_KEY", "").strip()
    )
    if not expected:
        raise HTTPException(503, "thread observer API key not configured")
    supplied = request.headers.get("x-api-key", "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid thread observer credential")


def _validate_payload(raw: dict) -> None:
    leaked = sorted(k for k in raw if k.lower() in FORBIDDEN_FIELDS)
    if leaked:
        raise HTTPException(400, "credential/session fields are forbidden")
    if str(raw.get("source_system", "")).lower() not in ALLOWED_SOURCES:
        raise HTTPException(400, "unsupported source_system")
    encoded = json.dumps(raw, separators=(",", ":"))
    if len(encoded.encode()) > 32768:
        raise HTTPException(413, "observation too large")


@router.post("/thread/activity")
async def thread_activity(request: Request):
    _require_observer_key(request)
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(400, "invalid JSON") from exc
    if not isinstance(raw, dict):
        raise HTTPException(400, "observation must be an object")
    _validate_payload(raw)

    try:
        obs = thread_idle.parse_observation(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    db = thread_idle.connect(thread_idle.DEFAULT_STATE)
    try:
        local_state = thread_idle.record_observation(db, obs)
    finally:
        db.close()

    try:
        remote = thread_store.persist_activity(
            thread_store.client_from_env(),
            obs,
            state="ACTIVE",
            observer_id=str(raw.get("observer_id", "http-source-adapter"))[:120],
        )
    except Exception as exc:
        # Local durability survives a transient remote outage; return failure so the source retries.
        raise HTTPException(503, f"governed persistence unavailable: {type(exc).__name__}") from exc

    return {
        "status": "RECORDED",
        "thread_key": obs.thread_key,
        "content_revision": obs.content_revision,
        "local": local_state,
        "supabase": remote,
    }
