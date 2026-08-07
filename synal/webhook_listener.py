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
        return {"status": "dispatched_to_aida", "action": comment_body}

    return {"status": "ignored"}
