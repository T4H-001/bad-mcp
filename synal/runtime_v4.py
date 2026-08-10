"""Runtime v4 compatibility shim.

Keeps the proven v3 ingress/proof machinery, but removes mandatory LLM use from
WKR-SWEEP-001 and gives bounded sweep tests an explicit route.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from synal import runtime_v3 as v3

RUNTIME_ID = "synal-ec2-standard-v4"
SWEEP_WORKER = "WKR-SWEEP-001"
V3_RESOLVE_WORKER = v3.resolve_worker


def _parse_max_items(body: str) -> int:
    m = re.search(r"(?i)max_items\s*=\s*(\d+)", body or "")
    n = int(m.group(1)) if m else 25
    return max(1, min(n, 25))


def _gh_json(args: list[str]) -> Any:
    p = subprocess.run(
        ["gh", "api", "-X", "GET", *args],
        env=v3.gh_env(), capture_output=True, text=True, timeout=45,
    )
    if p.returncode:
        raise RuntimeError("GitHub deterministic sweep read failed: " + p.stderr.strip())
    return json.loads(p.stdout)


def resolve_worker(body: str) -> str:
    explicit = v3.parse_field(body, "target_worker")
    if explicit:
        return explicit
    test_class = (v3.parse_field(body, "test_class") or "").upper()
    if test_class in {"LIVE_BOUNDED_SWEEP_PROOF", "LIVE_ESTATE_CONVERGENCE", "LIVE_SWEEP_PROOF"}:
        return SWEEP_WORKER
    return V3_RESOLVE_WORKER(body)


def _classify(item: dict) -> dict:
    title = str(item.get("title") or "")
    body = str(item.get("body") or "")
    text = (title + "\n" + body).lower()
    assignees = [str(a.get("login") or "") for a in item.get("assignees") or []]
    is_worker_inbox = "[worker-inbox]" in title.lower()

    result = {
        "number": item.get("number"),
        "url": item.get("html_url"),
        "title": title[:180],
        "state": "COVERED",
        "route_to": None,
        "reason": "deterministic rules found no routing change",
    }

    if is_worker_inbox:
        result.update(state="EXEMPT", reason="worker inbox is audited as a control surface, not adopted as domain completion work")
        return result

    if "TML-4PM" in assignees:
        result.update(state="ROUTE_REQUIRED", route_to="WKR-EXEC-001", reason="human executive assignment requires delegation stewardship")
        return result

    continuity_terms = ("missing receipt", "missing readback", "webhook", "hmac", "worker did not wake", "event continuity")
    if any(t in text for t in continuity_terms):
        result.update(state="ROUTE_REQUIRED", route_to="WKR-EVENT-SENTRY-001", reason="event-continuity symptom")
        return result

    recovery_terms = ("runner unavailable", "billing", "spending limit", "no workflow run", "stalled build", "actions recovery")
    if any(t in text for t in recovery_terms):
        result.update(state="ROUTE_REQUIRED", route_to="WKR-RECOVER-001", reason="provider/runtime recovery symptom")
        return result

    if "partial" in text and "reference" not in text:
        result.update(state="ROUTE_REQUIRED", route_to="WKR-FINALISE-001", reason="recent PARTIAL completion candidate; finaliser must apply its own admission gate")
        return result

    return result


def bounded_sweep(work_key: str, source_url: str, body: str) -> dict:
    repo, _ = v3.parse_repo_issue(source_url)
    max_items = _parse_max_items(body)
    items = _gh_json([f"repos/{repo}/issues", "-f", "state=open", "-f", f"per_page={max_items}", "-f", "sort=updated", "-f", "direction=desc"])
    if not isinstance(items, list):
        raise RuntimeError("SWEEP_PROVIDER_SHAPE_INVALID")

    classified = [_classify(item) for item in items[:max_items]]
    routed = [x for x in classified if x["route_to"]]
    exempted = [x for x in classified if x["state"] == "EXEMPT"]

    v3.event(work_key, "SWEEP_BOUNDED_AUDIT", json.dumps({
        "repo": repo,
        "max_items": max_items,
        "items_examined": len(classified),
        "items_routed": len(routed),
        "items_exempted": len(exempted),
    }, sort_keys=True))
    v3.metric(work_key, "sweep_items_examined", len(classified), "items")
    v3.metric(work_key, "sweep_items_routed", len(routed), "items")
    v3.metric(work_key, "sweep_items_exempted", len(exempted), "items")
    v3.metric(work_key, "llm_required", 0, "boolean", "deterministic sweep core")

    return {
        "mode": "DETERMINISTIC_BOUNDED_AUDIT",
        "llm_required": False,
        "scope_repo": repo,
        "max_items": max_items,
        "items_examined": len(classified),
        "items_routed": len(routed),
        "items_exempted": len(exempted),
        "worker_creation_requests": 0,
        "continuous_scan": False,
        "completion_ownership_retained": False,
        "checkpoint": {"cursor_end": len(classified), "batch_complete": True},
        "decisions": classified,
    }


def execute(work_key: str, url: str, body: str, worker: str) -> None:
    while True:
        attempt = v3.attempt_for(work_key)
        test_class = (v3.parse_field(body, "test_class") or "").upper()
        try:
            v3.event(work_key, "EXECUTE_START", f"attempt={attempt};worker={worker};runtime={RUNTIME_ID}")
            v3.ledger(work_key, worker, "EXECUTE_START", attempt, f"runtime={RUNTIME_ID}")

            if test_class == "LIVE_FORCED_FAILURE" and attempt == 1:
                raise RuntimeError("CONTROLLED_TEST_FAILURE:first_attempt")
            if worker == SWEEP_WORKER or test_class in {"LIVE_BOUNDED_SWEEP_PROOF", "LIVE_ESTATE_CONVERGENCE", "LIVE_SWEEP_PROOF"}:
                result = bounded_sweep(work_key, url, body)
            elif worker == v3.SENTRY_WORKER or test_class in {"LIVE_EVENT_SENTINEL", "LIVE_USER_ACCEPTANCE"}:
                result = v3.sentinel_acceptance(work_key)
            elif test_class == "LIVE_HMAC_PROOF":
                result = v3.hmac_proof(work_key)
            elif worker == "WKR-PLUGIN-001" or test_class == "LIVE_PLUGIN_VERTICAL_SLICE":
                result = v3.plugin_slice(work_key)
            else:
                result = {"qwen": v3.qwen("You are a bounded engineering worker. Do not request human approval except credentials/legal/safety/destructive/missing authority.\n\n" + body)}

            v3.complete(work_key, url, worker, result, attempt)
            print(f"[T4H WORKER] COMPLETE {work_key} attempt={attempt} -> SLEEP", flush=True)
            return
        except Exception as exc:
            terminal = v3.failure(work_key, url, worker, exc)
            print(f"[T4H WORKER] FAILED {work_key} attempt={attempt}: {exc}", flush=True)
            if terminal:
                return
            v3.event(work_key, "ROUTED_RECOVERY", v3.RECOVERY_WORKER)
            import time
            time.sleep(v3.RETRY_DELAY_SECONDS)
            n = v3.reclaim(work_key)
            worker = SWEEP_WORKER if test_class in {"LIVE_BOUNDED_SWEEP_PROOF", "LIVE_ESTATE_CONVERGENCE", "LIVE_SWEEP_PROOF"} else v3.RECOVERY_WORKER
            print(f"[T4H WORKER] recovery retry {work_key} attempt={n};effective_worker={worker}", flush=True)


v3.RUNTIME_ID = RUNTIME_ID
v3.resolve_worker = resolve_worker
v3.execute = execute
app = v3.app
