#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/my-project

gh auth status >/dev/null
git fetch origin main
git checkout main
git reset --hard origin/main

ENVFILE=/home/ubuntu/.config/synal/runtime.env
[ -f "$ENVFILE" ] || { echo 'runtime.env missing'; exit 31; }
chmod 600 "$ENVFILE"

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
EnvironmentFile=/home/ubuntu/.config/synal/runtime.env
ExecStart=/home/ubuntu/my-project/venv/bin/uvicorn synal.webhook_listener:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
TimeoutStopSec=10
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo install -o root -g root -m 0644 /tmp/t4h-synal-event.service /etc/systemd/system/t4h-synal-event.service
rm -f /tmp/t4h-synal-event.service
sudo systemctl daemon-reload
sudo systemctl enable t4h-synal-event.service >/dev/null
sudo systemctl restart t4h-synal-event.service
sleep 4
sudo systemctl is-active --quiet t4h-synal-event.service
curl -fsS http://127.0.0.1:8000/health
