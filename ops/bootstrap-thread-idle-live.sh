#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
export AWS_PAGER=""

REGION="${AWS_REGION:-ap-southeast-2}"
LIVE_REPO="${SYNAL_LIVE_REPO:-/home/ubuntu/my-project}"
ENV_DIR="/etc/t4h"
ENV_FILE="$ENV_DIR/synal-runtime.env"
DROPIN_DIR="/etc/systemd/system/t4h-synal-event.service.d"
DROPIN_FILE="$DROPIN_DIR/10-runtime-secrets.conf"
SERVICE="t4h-synal-event.service"

SUPABASE_URL_PARAM="/t4h/runtime/prod/supabase/url"
SUPABASE_KEY_PARAM="/t4h/runtime/prod/supabase/service_role_key"
SNAPS_KEY_PARAM="/t4h/canonical/SNAPS_INGEST_API_KEY"

echo "THREAD_IDLE_LIVE_BOOTSTRAP=START"

[[ $EUID -eq 0 ]] || { echo "BLOCKED reason=root_required"; exit 20; }
command -v aws >/dev/null || { echo "BLOCKED reason=aws_missing"; exit 21; }
command -v git >/dev/null || { echo "BLOCKED reason=git_missing"; exit 22; }
command -v curl >/dev/null || { echo "BLOCKED reason=curl_missing"; exit 23; }
[[ -d "$LIVE_REPO/.git" ]] || { echo "BLOCKED reason=live_repo_missing path=$LIVE_REPO"; exit 24; }

origin="$(git -C "$LIVE_REPO" remote get-url origin 2>/dev/null || true)"
case "$origin" in
  *TML-4PM/synal-core*|*TML-4PM/synal-core.git*) ;;
  *) echo "BLOCKED reason=unexpected_live_origin origin=$origin"; exit 25 ;;
esac

echo "LIVE_ORIGIN=VERIFIED"

git -C "$LIVE_REPO" fetch origin main --quiet
git -C "$LIVE_REPO" reset --hard origin/main >/dev/null
head="$(git -C "$LIVE_REPO" rev-parse HEAD)"
echo "LIVE_HEAD=$head"

SUPABASE_URL="$(aws ssm get-parameter --region "$REGION" --name "$SUPABASE_URL_PARAM" --with-decryption --query 'Parameter.Value' --output text)"
SUPABASE_SERVICE_ROLE_KEY="$(aws ssm get-parameter --region "$REGION" --name "$SUPABASE_KEY_PARAM" --with-decryption --query 'Parameter.Value' --output text)"
SNAPS_INGEST_API_KEY="$(aws ssm get-parameter --region "$REGION" --name "$SNAPS_KEY_PARAM" --with-decryption --query 'Parameter.Value' --output text)"

[[ -n "$SUPABASE_URL" && "$SUPABASE_URL" != "None" ]] || { echo "BLOCKED reason=supabase_url_missing"; exit 26; }
[[ -n "$SUPABASE_SERVICE_ROLE_KEY" && "$SUPABASE_SERVICE_ROLE_KEY" != "None" ]] || { echo "BLOCKED reason=supabase_key_missing"; exit 27; }
[[ -n "$SNAPS_INGEST_API_KEY" && "$SNAPS_INGEST_API_KEY" != "None" ]] || { echo "BLOCKED reason=snaps_key_missing"; exit 28; }

echo "SECRET_READBACK supabase_url=present supabase_service_role=present snaps_ingest=present values_exposed=false"

install -d -m 700 "$ENV_DIR"
umask 077
tmp="$(mktemp "$ENV_DIR/.synal-runtime.env.XXXXXX")"
trap 'rm -f "$tmp"; unset SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY SNAPS_INGEST_API_KEY' EXIT
printf 'SUPABASE_URL=%q\nSUPABASE_SERVICE_ROLE_KEY=%q\nSNAPS_INGEST_API_KEY=%q\n' \
  "$SUPABASE_URL" "$SUPABASE_SERVICE_ROLE_KEY" "$SNAPS_INGEST_API_KEY" > "$tmp"
chmod 600 "$tmp"
chown root:root "$tmp"
mv -f "$tmp" "$ENV_FILE"

install -d -m 755 "$DROPIN_DIR"
printf '[Service]\nEnvironmentFile=%s\n' "$ENV_FILE" > "$DROPIN_FILE"
chmod 644 "$DROPIN_FILE"

unset SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY SNAPS_INGEST_API_KEY
trap - EXIT

systemctl daemon-reload
systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { echo "BLOCKED reason=service_not_active"; exit 29; }

envfiles="$(systemctl show "$SERVICE" -p EnvironmentFiles --value)"
echo "SERVICE_STATE=active"
echo "ENV_FILE_BOUND=$([[ "$envfiles" == *"$ENV_FILE"* ]] && echo true || echo false)"

http="$(curl -sS -o /tmp/thread-idle-bootstrap-probe.$$ -w '%{http_code}' -X POST http://127.0.0.1:8000/thread/activity -H 'Content-Type: application/json' --data '{}' || true)"
rm -f /tmp/thread-idle-bootstrap-probe.$$
if [[ "$http" != "401" ]]; then
  echo "BLOCKED reason=thread_activity_probe expected=401 actual=$http"
  exit 30
fi

echo "THREAD_ACTIVITY_UNAUTHENTICATED_PROBE=401"
echo "THREAD_IDLE_LIVE_BOOTSTRAP=REAL live_head=$head env_file=$ENV_FILE values_exposed=false"
