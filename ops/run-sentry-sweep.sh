#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/my-project

before_sha="$(git rev-parse HEAD)"
git fetch origin main
git checkout main
git reset --hard origin/main
after_sha="$(git rev-parse HEAD)"

# The recovery sweep is also the bounded source-refresh path for the persistent
# event runtime. Previously it could update files on disk while uvicorn kept the
# old Python modules in memory indefinitely. If main changed, terminate only the
# ubuntu-owned canonical uvicorn process; systemd Restart=always immediately
# starts a fresh process from the updated checkout.
if [[ "$before_sha" != "$after_sha" ]]; then
  mapfile -t runtime_pids < <(
    pgrep -u "$(id -u)" -f '/home/ubuntu/my-project/venv/bin/uvicorn synal.webhook_listener:app --host 0.0.0.0 --port 8000' || true
  )
  if (( ${#runtime_pids[@]} > 0 )); then
    kill -TERM "${runtime_pids[@]}"
    healthy=0
    for _ in $(seq 1 20); do
      if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        healthy=1
        break
      fi
      sleep 1
    done
    if [[ "$healthy" != "1" ]]; then
      echo "EVENT_RUNTIME_RELOAD_FAILED before=${before_sha} after=${after_sha}" >&2
      exit 42
    fi
    echo "EVENT_RUNTIME_RELOADED before=${before_sha} after=${after_sha}"
  else
    echo "EVENT_RUNTIME_PROCESS_NOT_FOUND after source refresh" >&2
    exit 43
  fi
fi

# Thread-idle is a recovery-net participant only. Primary observations should arrive from an
# authorised provider/browser/client adapter. If the activity register exists, evaluate any missed
# idle transitions and route them through the same GitHub->Synal event ingress.
if [[ -f "$HOME/.local/state/synal/thread-activity.db" ]]; then
  /usr/bin/python3 /home/ubuntu/my-project/ops/emit-thread-idle-events.py
fi

exec /usr/bin/python3 /home/ubuntu/my-project/ops/sweep-event-continuity.py
