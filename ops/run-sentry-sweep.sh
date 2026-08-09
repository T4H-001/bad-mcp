#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/my-project
git fetch origin main
git checkout main
git reset --hard origin/main

# Thread-idle is a recovery-net participant only. Primary observations should arrive from an
authorised provider/browser/client adapter. If the activity register exists, evaluate any missed
idle transitions and route them through the same GitHub->Synal event ingress.
if [[ -f "$HOME/.local/state/synal/thread-activity.db" ]]; then
  /usr/bin/python3 /home/ubuntu/my-project/ops/emit-thread-idle-events.py
fi

exec /usr/bin/python3 /home/ubuntu/my-project/ops/sweep-event-continuity.py
