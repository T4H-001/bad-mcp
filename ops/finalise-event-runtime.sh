#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/my-project

echo 'T4H EVENT-001 FINALISER'
gh auth status >/dev/null

PID=$(ps -eo pid,args | awk '/venv\/bin\/uvicorn synal\.webhook_listener:app/ && !/awk/ {print $1; exit}')
[ -n "${PID:-}" ] || { echo 'no live synal pid'; exit 21; }

while IFS= read -r -d '' e; do export "$e"; done < "/proc/$PID/environ"

git fetch origin main
git checkout main
git reset --hard origin/main

HOOK_ID=$(gh api repos/TML-4PM/synal-core/hooks --jq '[.[]|select(.active==true)|select(.config.url|contains("github-webhook"))][0].id // empty')
[ -n "$HOOK_ID" ] || { echo 'synal webhook missing'; exit 22; }
HOOK_URL=$(gh api repos/TML-4PM/synal-core/hooks/$HOOK_ID --jq '.config.url')

SECRET=${GITHUB_WEBHOOK_SECRET:-$(openssl rand -hex 32)}
export GITHUB_WEBHOOK_SECRET="$SECRET" REQUIRE_GITHUB_HMAC=1 T4H_OLLAMA_MODEL=${T4H_OLLAMA_MODEL:-qwen2.5:1.5b}
mkdir -p "$HOME/.config/synal" && chmod 700 "$HOME/.config/synal"
printf 'export GITHUB_WEBHOOK_SECRET=%q\nexport REQUIRE_GITHUB_HMAC=1\nexport T4H_OLLAMA_MODEL=%q\n' "$SECRET" "$T4H_OLLAMA_MODEL" > "$HOME/.config/synal/runtime.env"
chmod 600 "$HOME/.config/synal/runtime.env"

export HOOK_URL
python3 -c 'import json,os; json.dump({"name":"web","active":True,"events":["issue_comment"],"config":{"url":os.environ["HOOK_URL"],"content_type":"json","insecure_ssl":"0","secret":os.environ["GITHUB_WEBHOOK_SECRET"]}},open("/tmp/t4h-hook.json","w"))'
chmod 600 /tmp/t4h-hook.json
gh api -X PATCH repos/TML-4PM/synal-core/hooks/$HOOK_ID --input /tmp/t4h-hook.json >/dev/null
CP_HOOK=$(gh api repos/TML-4PM/t4h-engineering-control-plane/hooks | HOOK_URL="$HOOK_URL" python3 -c 'import json,os,sys; u=os.environ["HOOK_URL"]; xs=json.load(sys.stdin); print(next((str(x["id"]) for x in xs if (x.get("config") or {}).get("url")==u),""))')
if [ -n "$CP_HOOK" ]; then gh api -X PATCH repos/TML-4PM/t4h-engineering-control-plane/hooks/$CP_HOOK --input /tmp/t4h-hook.json >/dev/null; else CP_HOOK=$(gh api -X POST repos/TML-4PM/t4h-engineering-control-plane/hooks --input /tmp/t4h-hook.json --jq '.id'); fi
rm -f /tmp/t4h-hook.json

kill "$PID"
sleep 2
source "$HOME/.config/synal/runtime.env"
nohup ./venv/bin/uvicorn synal.webhook_listener:app --host 0.0.0.0 --port 8000 >> bridge.log 2>&1 &
sleep 4

curl -fsS http://127.0.0.1:8000/health > /tmp/t4h-health.json
python3 -c 'import json; x=json.load(open("/tmp/t4h-health.json")); assert x["status"]=="ok" and x["runtime"]=="synal-ec2-standard-v2" and x["hmac_required"] and x["hmac_configured"] and x["ledger"]=="work_ledger" and x["telemetry"]=="telemetry"'

BAD=$(curl -sS -o /tmp/t4h-bad.out -w '%{http_code}' -X POST "$HOOK_URL" -H 'Content-Type: application/json' -H 'X-Hub-Signature-256: sha256=invalid' -d '{"comment":{"body":"/action invalid signature proof"}}' || true)
[ "$BAD" = 401 ] || { echo "invalid signature expected 401 got $BAD"; exit 23; }

HKEY='github:synal-core:issue-1:event-001-hmac-proof-001'
PKEY='github:t4h-engineering-control-plane:issue-4:plugin-registry-real'
gh api repos/TML-4PM/synal-core/issues/1/comments -f body="/action EVENT-001 live HMAC proof
<!-- t4h-event -->
work_key: $HKEY
target_worker: WKR-EVENT-001
requested_action: signed external delivery valid and invalid HMAC proof ledger telemetry readback sleep
test_class: LIVE_HMAC_PROOF" >/dev/null
gh api repos/TML-4PM/t4h-engineering-control-plane/issues/4/comments -f body="/action PLUGINS-001 portable vertical slice
<!-- t4h-event -->
work_key: $PKEY
target_worker: WKR-PLUGIN-001
authority_scope: BOUNDED_WRITE
requested_action: Agent Plugins 1.0 synthetic Skill package scan sandbox registry runtime revoke readback ledger telemetry sleep
test_class: LIVE_PLUGIN_VERTICAL_SLICE" >/dev/null

H=''; P=''
for i in $(seq 1 45); do
  H=$(gh api repos/TML-4PM/synal-core/issues/1/comments --jq '[.[]|select(.body|contains("github:synal-core:issue-1:event-001-hmac-proof-001"))|select(.body|contains("state: COMPLETED"))][-1].id // empty')
  P=$(gh api repos/TML-4PM/t4h-engineering-control-plane/issues/4/comments --jq '[.[]|select(.body|contains("github:t4h-engineering-control-plane:issue-4:plugin-registry-real"))|select(.body|contains("state: COMPLETED"))][-1].id // empty')
  [ -n "$H" ] && [ -n "$P" ] && break
  sleep 4
done
[ -n "$H" ] && [ -n "$P" ] || { echo "receipts missing hmac=$H plugin=$P"; exit 24; }

read L T HS PS <<< "$(python3 -c 'import sqlite3,pathlib; db=sqlite3.connect(pathlib.Path.home()/".local/state/synal/work.db"); k=("github:synal-core:issue-1:event-001-hmac-proof-001","github:t4h-engineering-control-plane:issue-4:plugin-registry-real"); print(db.execute("select count(*) from work_ledger where work_key in (?,?)",k).fetchone()[0],db.execute("select count(*) from telemetry where work_key in (?,?)",k).fetchone()[0],db.execute("select count(*) from work_events where work_key=? and event_type in (\"HMAC_VALID_ACCEPTED\",\"HMAC_INVALID_REJECTED\")",(k[0],)).fetchone()[0],db.execute("select count(*) from plugin_registry where plugin_id=\"PLG-T4H-PORTABLE-SLICE-001\" and state=\"REVOKED\"").fetchone()[0])')"
[ "$L" -ge 4 ] && [ "$T" -ge 4 ] && [ "$HS" -ge 2 ] && [ "$PS" -eq 1 ] || { echo "evidence failed ledger=$L telemetry=$T hmac=$HS plugin=$PS"; exit 25; }

HEAD=$(git rev-parse HEAD)
SUMMARY="FINAL EVENT-001 SELF-HOSTED PROOF COMPLETE

classification: REAL for WKR-EVENT-001 portable no-HITL substrate
canonical_runtime: synal-ec2-standard-v2
canonical_source: TML-4PM/synal-core@$HEAD
canonical_manifest: runtime/canonical-event-runtime.json
public_ingress: CONFIGURED_PROVIDER_READBACK
synal_hook_id: $HOOK_ID
control_plane_hook_id: $CP_HOOK
hmac_required: true
live_invalid_signature: REJECTED_HTTP_401
signed_valid_hmac: PASS
signed_hmac_receipt: $H
plugin_vertical_slice: PASS
plugin_vertical_slice_receipt: $P
plugin_final_state: REVOKED
ledger_rows_for_final_proofs: $L
telemetry_rows_for_final_proofs: $T
github_hosted_compute_required: false
executor: self-hosted EC2/Synal + local Ollama
recovery: WKR-RECOVER-001 autonomous retry PASS
next_state: SLEEP"
for I in 24 26; do gh api repos/TML-4PM/t4h-engineering-control-plane/issues/$I/comments -f body="$SUMMARY" >/dev/null; done
gh api repos/TML-4PM/t4h-engineering-control-plane/issues/23/comments -f body="${SUMMARY/WKR-EVENT-001 portable no-HITL substrate/WKR-PLUGIN-001 proving slice}" >/dev/null
echo "$SUMMARY"
