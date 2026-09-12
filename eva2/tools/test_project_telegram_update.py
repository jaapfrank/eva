"""Offline tests: real SQLite claims and process-boundary delivery adapter stub."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location("project_update", Path(__file__).with_name("send_project_telegram_update.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
KEY = "2026-09-12T15:00Z"
RECEIPT = {"provider_receipt_sha256": "a" * 64, "private_chat_and_exact_text_verified": True}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.calls = []

    def sender(self, key, text):
        self.calls.append((key, text))
        return RECEIPT

    def test_success_repeat_and_conflicting_message(self):
        first = helper.deliver_once(self.state, KEY, "Eva-update", self.sender)
        self.assertEqual(first["status"], "delivered")
        self.assertEqual(first, helper.deliver_once(self.state, KEY, "Eva-update", self.sender))
        with self.assertRaises(ValueError):
            helper.deliver_once(self.state, KEY, "ander bericht", self.sender)
        self.assertEqual(len(self.calls), 1)

    def test_timeout_after_provider_acceptance_never_reposts(self):
        def ambiguous(key, text):
            self.calls.append((key, text))
            raise TimeoutError("secret provider URL must never be retained")
        first = helper.deliver_once(self.state, KEY, "Eva-update", ambiguous)
        self.assertEqual(first["status"], "uncertain")
        self.assertEqual(first, helper.deliver_once(self.state, KEY, "Eva-update", self.sender))
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("secret", json.dumps(first))

    def test_interrupted_process_claim_stays_blocked(self):
        def killed(key, text):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            helper.deliver_once(self.state, KEY, "Eva-update", killed)
        result = helper.deliver_once(self.state, KEY, "Eva-update", self.sender)
        self.assertEqual(result["status"], "claimed_uncertain")
        self.assertEqual(self.calls, [])

    def test_concurrent_call_while_send_in_progress_does_not_send(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def paused(key, text):
            self.calls.append((key, text)); entered.set()
            self.assertTrue(release.wait(5))
            return RECEIPT
        thread = threading.Thread(target=lambda: result.append(helper.deliver_once(self.state, KEY, "Eva-update", paused)))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            second = helper.deliver_once(self.state, KEY, "Eva-update", self.sender)
            self.assertEqual(second["status"], "claimed_uncertain")
        finally:
            release.set(); thread.join(5)
        self.assertEqual(result[0]["status"], "delivered")
        self.assertEqual(len(self.calls), 1)

    def test_receipt_write_failure_can_be_repaired_without_send(self):
        first = helper.deliver_once(self.state, KEY, "Eva-update", self.sender)
        with self.assertRaises(FileNotFoundError):
            helper.write_receipt(Path(self.temp.name) / "missing" / "receipt.json", first)
        second = helper.deliver_once(self.state, KEY, "Eva-update", self.sender)
        path = Path(self.temp.name) / "receipt.json"
        helper.write_receipt(path, second)
        self.assertEqual(json.loads(path.read_text()), first)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.calls), 1)

    def test_input_validation_before_claim(self):
        for key, text in [("2026-9-12T15:00Z", "x"), (KEY, " "), (KEY, "😀" * 2049)]:
            with self.assertRaises(ValueError):
                helper.deliver_once(self.state, key, text, self.sender)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
