#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/my-project
git fetch origin main
git checkout main
git reset --hard origin/main
exec /usr/bin/python3 /home/ubuntu/my-project/ops/sweep-event-continuity.py
