#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/my-project

gh auth status >/dev/null
git fetch origin main
git checkout main
git reset --hard origin/main

SHELL_ENV=/home/ubuntu/.config/synal/runtime.env
SYSTEMD_ENV=/home/ubuntu/.config/synal/systemd.env
[ -f "$SHELL_ENV" ] || { echo 'runtime.env missing'; exit 31; }
mkdir -p /home/ubuntu/.config/synal
sed -E 's/^export[[:space:]]+//' "$SHELL_ENV" > "$SYSTEMD_ENV"
chmod 600 "$SHELL_ENV" "$SYSTEMD_ENV"

cat > /tmp/t4h-synal-event.service <<'EOF'
[Unit]
Description=T4H Synal Canonical Event Runtime
After=network-online.target ollama.service
Wants=network-online.target
[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/my-project
Environment=HOME=/home/ubuntu
Environment=PATH=/home/ubuntu/my-project/venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=/home/ubuntu/.config/synal/systemd.env
ExecStart=/home/ubuntu/my-project/venv/bin/uvicorn synal.webhook_listener:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
TimeoutStopSec=10
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
EOF

cat > /tmp/t4h-event-sentry-sweep.service <<'EOF'
[Unit]
Description=T4H Estate Event Continuity Recovery Sweep
After=network-online.target t4h-synal-event.service
Wants=network-online.target
[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/my-project
Environment=HOME=/home/ubuntu
Environment=PATH=/home/ubuntu/my-project/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 /home/ubuntu/my-project/ops/sweep-event-continuity.py
NoNewPrivileges=true
EOF

cat > /tmp/t4h-event-sentry-sweep.timer <<'EOF'
[Unit]
Description=Periodic recovery net for missed T4H event-continuity work
[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=10min
Persistent=true
Unit=t4h-event-sentry-sweep.service
[Install]
WantedBy=timers.target
EOF

sudo install -o root -g root -m 0644 /tmp/t4h-synal-event.service /etc/systemd/system/t4h-synal-event.service
sudo install -o root -g root -m 0644 /tmp/t4h-event-sentry-sweep.service /etc/systemd/system/t4h-event-sentry-sweep.service
sudo install -o root -g root -m 0644 /tmp/t4h-event-sentry-sweep.timer /etc/systemd/system/t4h-event-sentry-sweep.timer
rm -f /tmp/t4h-synal-event.service /tmp/t4h-event-sentry-sweep.service /tmp/t4h-event-sentry-sweep.timer
sudo systemctl daemon-reload
sudo systemctl enable t4h-synal-event.service >/dev/null
sudo systemctl enable --now t4h-event-sentry-sweep.timer >/dev/null
sudo systemctl restart t4h-synal-event.service
sleep 4
sudo systemctl is-active --quiet t4h-synal-event.service
sudo systemctl is-active --quiet t4h-event-sentry-sweep.timer
curl -fsS http://127.0.0.1:8000/health | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["status"]=="ok" and x["hmac_required"] and x["hmac_configured"] and x.get("event_sentry_persistent"); print("SYSTEMD_RUNTIME_AND_SENTRY_SWEEP_OK")'
# Initial recovery pass proves the safety net now; timer owns later passes.
sudo systemctl start t4h-event-sentry-sweep.service
sudo systemctl is-failed --quiet t4h-event-sentry-sweep.service && exit 41 || true
