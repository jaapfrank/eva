"""Native observation awaits asynchronous retention within one sample deadline."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[3] / "development/benchmark/inspect_ai_v1"
sys.path.insert(0, str(HERE))
import p2_provider_history_native_glue as glue  # noqa: E402


class MemorySettlementTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.sleeps = []
        self.body = {"message_id": "public-development-message"}
        self.core, self.memory = object(), object()

    def sleep(self, seconds):
        self.assertGreater(seconds, 0)
        self.sleeps.append(seconds)
        self.now += seconds

    def observe(self, reader, timeout=1.0):
        with patch.object(glue, "read_eva2_memory_settlement", reader),\
                patch.object(glue.time, "monotonic", lambda: self.now),\
                patch.object(glue.time, "sleep", self.sleep):
            return glue.memory_settlement(self.core, self.memory, self.body, timeout)

    def test_http_reply_can_precede_queue_and_full_retention(self):
        complete = {"status": "completed", "settled": True,
                    "document_id": "exact-document", "source_facts": 2}
        reader = Mock(side_effect=[
            {"status": "pending", "settled": False,
             "reason": "no exact native retain document"},
            {"status": "pending", "settled": False,
             "reason": "native memory operations or consolidation pending"},
            complete,
        ])
        self.assertIs(self.observe(reader), complete)
        self.assertEqual(reader.call_count, 3)
        remaining = [c.kwargs["timeout_seconds"] for c in reader.call_args_list]
        self.assertTrue(all(a > b for a, b in zip(remaining, remaining[1:])))
        for call in reader.call_args_list:
            self.assertEqual(call.args, (self.core, self.memory, self.body))

    def test_failed_retention_is_terminal_and_unmodified(self):
        failed = {"status": "failed", "settled": False,
                  "reason": "native consolidation failed"}
        reader = Mock(return_value=failed)
        self.assertIs(self.observe(reader), failed)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_identity_or_ambiguous_document_error_is_not_retried(self):
        for reason in ("native retain document scope mismatch",
                       "ambiguous native memory document or receipt"):
            with self.subTest(reason=reason):
                reader = Mock(side_effect=ValueError(reason))
                with self.assertRaisesRegex(ValueError, reason):
                    self.observe(reader)
                self.assertEqual(reader.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_continuous_pending_cannot_reset_sample_deadline(self):
        reader = Mock(return_value={"status": "pending", "settled": False})
        with self.assertRaisesRegex(TimeoutError, "settlement deadline"):
            self.observe(reader, timeout=0.25)
        self.assertAlmostEqual(self.now, 100.25)
        self.assertAlmostEqual(sum(self.sleeps), 0.25)
        remaining = [c.kwargs["timeout_seconds"] for c in reader.call_args_list]
        self.assertTrue(all(a > b > 0 for a, b in zip(remaining, remaining[1:])))

    def test_expired_reader_result_cannot_become_completion(self):
        def read(*args, **kwargs):
            self.now += kwargs["timeout_seconds"]
            return {"status": "completed", "settled": True}
        with self.assertRaisesRegex(TimeoutError, "settlement deadline"):
            self.observe(Mock(side_effect=read))
        self.assertEqual(self.sleeps, [])

    def test_invalid_deadline_is_rejected_before_any_read(self):
        for timeout in (0, -1, True, float("inf"), float("nan"), "1"):
            with self.subTest(timeout=timeout):
                reader = Mock()
                with self.assertRaisesRegex(ValueError, "positive remaining"):
                    self.observe(reader, timeout=timeout)
                reader.assert_not_called()
        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
