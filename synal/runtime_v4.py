"""Runtime v4 compatibility shim.

Keeps the proven v3 ingress/proof machinery, but makes WKR-SWEEP-001 a
bounded deterministic auditor/router. An LLM is never required for its core
classification or routing path.
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
ROUTE_INBOX = {
    "WKR-EVENT-SENTRY-001": 37,
    "WKR-RECOVER-001": 29,
    "WKR-FINALISE-001": 43,
    "WKR-EXEC-001": 28,
    "WKR-FACTORY-001": 13,
}


def _parse_max_items(body: str) -> int:
    m = re.search(r"(?i)max_items\s*=\s*(\d+)", body or "")
    n = int(m.group(1)) if m else 25
    return max(1, min(n, 25))


def _gh_json(args: list[str], method: str = "GET") -> Any:
    p = subprocess.run(
        ["gh", "api", "-X", method, *args],
        env=v3.gh_env(), capture_output=True, text=True, timeout=45,
    )
    if p.returncode:
        raise RuntimeError("GitHub deterministic sweep provider call failed: " + p.stderr.strip())
    return json.loads(p.stdout) if p.stdout.strip() else {}


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
    title_l = title.lower()
    assignees = [str(a.get("login") or "") for a in item.get("assignees") or []]
    is_worker_inbox = "[worker-inbox]" in title_l

    result = {
        "number": item.get("number"),
        "url": item.get("html_url"),
        "title": title[:180],
        "state": "COVERED",
        "route_to": None,
        "reason": "no high-confidence structural routing signal",
    }

    if is_worker_inbox:
        result.update(state="EXEMPT", reason="worker inbox is a control surface, not domain completion work")
        return result

    if "TML-4PM" in assignees:
        result.update(state="ROUTE_REQUIRED", route_to="WKR-EXEC-001", reason="human executive assignment requires delegation stewardship")
        return result

    continuity_title_terms = (
        "dead-wiring", "dead wiring", "missing wake", "missing receipt",
        "event continuity", "webhook failure", "hmac", "control-path reachability",
    )
    if any(t in title_l for t in continuity_title_terms):
        result.update(state="ROUTE_REQUIRED", route_to="WKR-EVENT-SENTRY-001", reason="high-confidence event-continuity/control-path title signal")
        return result

    recovery_title_terms = (
        "stalled build", "actions recovery", "runner unavailable", "billing blocked",
        "spending limit", "no workflow run",
    )
    if any(t in title_l for t in recovery_title_terms):
        result.update(state="ROUTE_REQUIRED", route_to="WKR-RECOVER-001", reason="high-confidence provider/runtime recovery title signal")
        return result

    return result


def _deduped_handoff(repo: str, work_key: str, decision: dict) -> dict | None:
    target_worker = decision.get("route_to")
    inbox = ROUTE_INBOX.get(str(target_worker))
    source_number = decision.get("number")
    if not inbox or not source_number or int(source_number) == int(inbox):
        return None

    marker = f"<!-- wkr-sweep-route source={repo}#{source_number} target={target_worker} -->"
    comments = _gh_json([f"repos/{repo}/issues/{inbox}/comments", "-f", "per_page=100"])
    for c in comments if isinstance(comments, list) else []:
        if marker in str(c.get("body") or ""):
            return {
                "target_worker": target_worker,
                "target_inbox": inbox,
                "source_issue": source_number,
                "deduped": True,
                "comment_id": c.get("id"),
                "provider_readback": c.get("html_url"),
            }

    body = (
        marker + "\n"
        "message_id: MSG-SWEEP-ROUTE-" + str(source_number) + "\n"
        "from_worker: WKR-SWEEP-001\n"
        f"to_worker: {target_worker}\n"
        f"work_key: {work_key}:route:{source_number}:{target_worker}\n"
        f"task_url: {decision.get('url')}\n"
        "type: REQUEST\n"
        f"summary: Bounded operability audit found {decision.get('reason')}.\n"
        "requested_action: Refresh source truth and own only the specialist action within your existing capability; ACK/CLAIM or REROUTE.\n"
        "authority_scope: BOUNDED_WRITE\n"
    )
    created = _gh_json([f"repos/{repo}/issues/{inbox}/comments", "-f", f"body={body}"], method="POST")
    cid = created.get("id")
    if not cid:
        raise RuntimeError("SWEEP_ROUTE_PROVIDER_READBACK_MISSING")
    readback = _gh_json([f"repos/{repo}/issues/comments/{cid}"])
    if marker not in str(readback.get("body") or ""):
        raise RuntimeError("SWEEP_ROUTE_PROVIDER_READBACK_MISMATCH")
    return {
        "target_worker": target_worker,
        "target_inbox": inbox,
        "source_issue": source_number,
        "deduped": False,
        "comment_id": cid,
        "provider_readback": readback.get("html_url"),
    }


def bounded_sweep(work_key: str, source_url: str, body: str) -> dict:
    repo, source_issue = v3.parse_repo_issue(source_url)
    max_items = _parse_max_items(body)

    # The triggering source item is audited first. Remaining capacity is filled
    # from the repository's latest open issues. This keeps cycles event-centric.
    items: list[dict] = []
    if source_issue:
        focus = _gh_json([f"repos/{repo}/issues/{source_issue}"])
        if isinstance(focus, dict) and focus.get("number"):
            items.append(focus)
    remaining = max_items - len(items)
    if remaining > 0:
        recent = _gh_json([f"repos/{repo}/issues", "-f", "state=open", "-f", f"per_page={max_items}", "-f", "sort=updated", "-f", "direction=desc"])
        seen = {x.get("number") for x in items}
        for item in recent if isinstance(recent, list) else []:
            if item.get("number") in seen:
                continue
            items.append(item)
            seen.add(item.get("number"))
            if len(items) >= max_items:
                break

    classified = [_classify(item) for item in items[:max_items]]
    route_candidates = [x for x in classified if x["route_to"]]
    exempted = [x for x in classified if x["state"] == "EXEMPT"]

    # Pressure control: make at most ONE specialist handoff per audit cycle.
    handoffs = []
    if route_candidates:
        h = _deduped_handoff(repo, work_key, route_candidates[0])
        if h:
            handoffs.append(h)

    v3.event(work_key, "SWEEP_BOUNDED_AUDIT", json.dumps({
        "repo": repo,
        "max_items": max_items,
        "items_examined": len(classified),
        "route_candidates": len(route_candidates),
        "handoffs_written": len([h for h in handoffs if not h.get("deduped")]),
        "handoffs_deduped": len([h for h in handoffs if h.get("deduped")]),
        "items_exempted": len(exempted),
    }, sort_keys=True))
    v3.metric(work_key, "sweep_items_examined", len(classified), "items")
    v3.metric(work_key, "sweep_route_candidates", len(route_candidates), "items")
    v3.metric(work_key, "sweep_handoffs", len(handoffs), "items")
    v3.metric(work_key, "sweep_items_exempted", len(exempted), "items")
    v3.metric(work_key, "llm_required", 0, "boolean", "deterministic sweep core")

    return {
        "mode": "DETERMINISTIC_BOUNDED_AUDIT",
        "llm_required": False,
        "scope_repo": repo,
        "focus_issue": source_issue,
        "max_items": max_items,
        "items_examined": len(classified),
        "route_candidates": len(route_candidates),
        "handoffs": handoffs,
        "handoff_limit": 1,
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
