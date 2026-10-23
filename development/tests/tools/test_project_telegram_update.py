"""Offline tests: real SQLite claims and process-boundary delivery adapter stub."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tools import send_project_telegram_update as helper
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

    def test_edge_whitespace_normalized_without_changing_raw_idempotency(self):
        import hashlib
        raw = "  Eva-update\n\n"
        result = helper.deliver_once(self.state, KEY, raw, self.sender)
        self.assertEqual(self.calls, [(KEY, "Eva-update")])
        self.assertTrue(result["edge_whitespace_normalized"])
        self.assertEqual(result["message_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(result["sent_message_sha256"], hashlib.sha256(b"Eva-update").hexdigest())
        self.assertEqual(helper.deliver_once(self.state, KEY, raw, self.sender), result)
        with self.assertRaises(ValueError):
            helper.deliver_once(self.state, KEY, raw.strip(), self.sender)
        self.assertEqual(len(self.calls), 1)

    def test_legacy_uncertain_record_remains_unchanged_after_normalization_fix(self):
        import sqlite3, hashlib
        self.state.mkdir()
        raw = "Eva-update\n"
        old = {"status": "uncertain", "hour_key": KEY, "message_sha256": hashlib.sha256(raw.encode()).hexdigest()}
        with sqlite3.connect(self.state / "delivery.sqlite3") as con:
            con.execute("CREATE TABLE deliveries(hour_key TEXT PRIMARY KEY, message_sha256 TEXT NOT NULL, record TEXT NOT NULL)")
            con.execute("INSERT INTO deliveries VALUES(?,?,?)", (KEY, old["message_sha256"], json.dumps(old)))
        self.assertEqual(helper.deliver_once(self.state, KEY, raw, self.sender), old)
        self.assertEqual(self.calls, [])

    def test_diagnostics_keep_only_fixed_stages_booleans_and_hashes(self):
        def uncertain(key, text):
            raise helper.DeliveryUncertain({"stage": "provider_reply_received", "token": "secret",
                "provider_observation": {"intended_private_chat": True, "exact_text": False,
                  "text_sha256": "a" * 64, "text": "secret", "chat_id": "secret"}})
        result = helper.deliver_once(self.state, KEY, "Eva-update", uncertain)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["diagnostics"]["stage"], "provider_reply_received")
        self.assertFalse(result["diagnostics"]["provider_observation"]["exact_text"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(helper.safe_diagnostics({"stage": "secret"}), {})

    def test_input_validation_before_claim(self):
        for key, text in [("2026-9-12T15:00Z", "x"), (KEY, " "), (KEY, "😀" * 2049)]:
            with self.assertRaises(ValueError):
                helper.deliver_once(self.state, key, text, self.sender)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
