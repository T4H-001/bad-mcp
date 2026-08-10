import importlib.util
import sys
import unittest
from pathlib import Path
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "synal" / "thread_activity_http.py"
spec = importlib.util.spec_from_file_location("thread_activity_http_test", MODULE)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class ThreadActivityHttpTests(unittest.TestCase):
    def test_accepts_metadata_only_observation(self):
        mod._validate_payload({
            "source_system": "chatgpt",
            "source_native_id": "abc",
            "content_revision": "2:def",
            "content_hash": "def",
            "last_message_at": 1,
            "last_material_activity_at": 1,
        })

    def test_rejects_session_fields(self):
        with self.assertRaises(HTTPException) as ctx:
            mod._validate_payload({"source_system": "chatgpt", "access_token": "nope"})
        self.assertEqual(400, ctx.exception.status_code)

    def test_rejects_unknown_source(self):
        with self.assertRaises(HTTPException) as ctx:
            mod._validate_payload({"source_system": "unexpected"})
        self.assertEqual(400, ctx.exception.status_code)

    def test_rejects_oversize_observation(self):
        with self.assertRaises(HTTPException) as ctx:
            mod._validate_payload({"source_system": "chatgpt", "padding": "x" * 40000})
        self.assertEqual(413, ctx.exception.status_code)


if __name__ == "__main__":
    unittest.main()
