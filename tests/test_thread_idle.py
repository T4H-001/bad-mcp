import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "runtime" / "thread_idle.py"
spec = importlib.util.spec_from_file_location("thread_idle", MODULE)
thread_idle = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = thread_idle
spec.loader.exec_module(thread_idle)


class ThreadIdleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = thread_idle.connect(Path(self.tmp.name) / "state.db")
        self.obs = thread_idle.Observation(
            source_system="chatgpt",
            source_native_id="thread-123",
            source_ref="chatgpt://thread-123",
            content_revision="r1",
            content_hash="abc",
            last_message_at=1000,
            last_material_activity_at=1000,
            priority="normal",
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_active_thread_does_not_emit(self):
        thread_idle.record_observation(self.db, self.obs, now=1000)
        self.assertEqual([], thread_idle.evaluate_idle(self.db, now=1000 + 60))

    def test_normal_idle_requires_candidate_then_debounce(self):
        thread_idle.record_observation(self.db, self.obs, now=1000)
        self.assertEqual([], thread_idle.evaluate_idle(self.db, now=19000))
        events = thread_idle.evaluate_idle(self.db, now=19301)
        self.assertEqual(1, len(events))
        self.assertEqual("THREAD_IDLE_CONFIRMED", events[0]["event_type"])
        self.assertEqual("WKR-FINALISE-001", events[0]["target_worker"])

    def test_duplicate_evaluation_emits_once(self):
        thread_idle.record_observation(self.db, self.obs, now=1000)
        thread_idle.evaluate_idle(self.db, now=19000)
        first = thread_idle.evaluate_idle(self.db, now=19301)
        second = thread_idle.evaluate_idle(self.db, now=20000)
        self.assertEqual(1, len(first))
        self.assertEqual([], second)

    def test_new_material_revision_resets_idle(self):
        thread_idle.record_observation(self.db, self.obs, now=1000)
        thread_idle.evaluate_idle(self.db, now=19000)
        newer = thread_idle.Observation(
            source_system="chatgpt",
            source_native_id="thread-123",
            source_ref="chatgpt://thread-123",
            content_revision="r2",
            content_hash="def",
            last_message_at=19200,
            last_material_activity_at=19200,
            priority="normal",
        )
        thread_idle.record_observation(self.db, newer, now=19200)
        self.assertEqual([], thread_idle.evaluate_idle(self.db, now=19301))

    def test_cold_recovery_can_confirm_without_waiting_new_debounce(self):
        thread_idle.record_observation(self.db, self.obs, now=1000)
        thread_idle.evaluate_idle(self.db, now=1000 + 172800)
        events = thread_idle.evaluate_idle(self.db, now=1000 + 172801)
        self.assertEqual(1, len(events))
        self.assertTrue(events[0]["recovery"])


if __name__ == "__main__":
    unittest.main()
