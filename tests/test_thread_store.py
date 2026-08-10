import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


thread_idle = load("thread_idle_store_test", ROOT / "runtime" / "thread_idle.py")
thread_store = load("thread_store_test", ROOT / "runtime" / "thread_store.py")


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return Result(self.data)


class FakeClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, args):
        self.calls.append((name, args))
        payload = args["p"]
        if name == thread_store.ACTIVITY_RPC:
            return Query({"thread_key": payload["thread_key"], "content_revision": payload["content_revision"], "state": payload["state"]})
        return Query({"work_key": payload["work_key"], "classification": payload["classification"], "outcomes": len(payload.get("outcomes", []))})


class ThreadStoreTests(unittest.TestCase):
    def test_activity_payload_and_readback(self):
        obs = thread_idle.Observation(
            source_system="chatgpt", source_native_id="abc", source_ref="chatgpt://abc",
            content_revision="r2", content_hash="hash", last_message_at=1000,
            last_material_activity_at=1000, priority="normal"
        )
        client = FakeClient()
        result = thread_store.persist_activity(client, obs)
        self.assertEqual("chatgpt:abc", result["thread_key"])
        name, args = client.calls[0]
        self.assertEqual(thread_store.ACTIVITY_RPC, name)
        self.assertTrue(args["p"]["last_message_at"].endswith("+00:00"))

    def test_closeout_requires_contract_and_readback(self):
        client = FakeClient()
        payload = {
            "work_key": "thread-idle:1", "thread_key": "chatgpt:abc", "source_revision": "r2",
            "source_hash": "hash", "classification": "PARTIAL", "outcomes": []
        }
        result = thread_store.persist_closeout(client, payload)
        self.assertEqual("thread-idle:1", result["work_key"])
        with self.assertRaises(ValueError):
            thread_store.persist_closeout(client, {"work_key": "missing"})


if __name__ == "__main__":
    unittest.main()
