#!/bin/bash
set -e

echo "=== 1. Getting EC2 Public IP ==="
PUBLIC_IP=$(curl -s ifconfig.me)
echo "Public IP: $PUBLIC_IP"
echo "Webhook URL will be: http://$PUBLIC_IP:8000/github-webhook"
echo ""

echo "=== 2. Installing Dependencies ==="
pip install fastapi uvicorn pydantic python-telegram-bot requests

echo "=== 3. Creating synal directory structure ==="
mkdir -p synal

echo "=== 4. Writing Webhook Listener ==="
cat << 'PYEOF' > synal/webhook_listener.py
import os
import subprocess
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/github-webhook")
async def handle_github_webhook(request: Request):
    payload = await request.json()
    comment_body = payload.get("comment", {}).get("body", "")
    
    if "/action" in comment_body or "/approve" in comment_body:
        print(f"[AIDA DAEMON] Triggered by comment: {comment_body}")
        # Add your worker invocation here
        return {"status": "dispatched_to_aida", "action": comment_body}

    return {"status": "ignored"}
PYEOF

echo "=== 5. Starting Uvicorn Listener in Background ==="
nohup uvicorn synal.webhook_listener:app --host 0.0.0.0 --port 8000 > webhook.log 2>&1 &

echo "=== SUCCESS ==="
echo "Listener running on port 8000!"
echo "Check logs anytime with: tail -f webhook.log"
