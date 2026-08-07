import os
import hashlib
import json
from typing import Optional, Dict, Any, List
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

def get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def enqueue_chunk(workflow_id: str, payload: Dict[str, Any], memory_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    client = get_client()
    payload_str = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
    
    data = {
        "workflow_id": workflow_id,
        "status": "PENDING_HUMAN_APPROVAL",
        "payload_hash": payload_hash,
        "memory_context": memory_context or {},
        "artifact_url": None
    }
    res = client.table("chunks").insert(data).execute()
    return res.data[0] if res.data else {}

def get_pending_hitl() -> List[Dict[str, Any]]:
    client = get_client()
    res = client.table("chunks").select("*").eq("status", "PENDING_HUMAN_APPROVAL").execute()
    return res.data or []

def approve_chunk(chunk_id: str) -> Dict[str, Any]:
    client = get_client()
    res = client.table("chunks").update({"status": "APPROVED"}).eq("id", chunk_id).execute()
    return res.data[0] if res.data else {}

def reject_chunk(chunk_id: str, reason: str) -> Dict[str, Any]:
    client = get_client()
    res = client.table("chunks").update({
        "status": "REJECTED",
        "memory_context": {"rejection_reason": reason}
    }).eq("id", chunk_id).execute()
    return res.data[0] if res.data else {}
