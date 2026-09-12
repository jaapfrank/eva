"""Offline sender checks with the real Telegram adapter, stubbed provider and wiring.
Run using the existing Eva test environment with PYTHONPATH=src. No network/DB.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eva.adapters.connectors.telegram import TelegramProactiveDelivery

spec = importlib.util.spec_from_file_location("project_update", Path(__file__).with_name("send_project_telegram_update.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RuntimeSenderTests(unittest.TestCase):
    def run_sender(self, text, provider):
        delivery = TelegramProactiveDelivery(bot_token="123:test-only", chat_id="12345")
        delivery._api._call = provider
        output = io.StringIO()
        engine = SimpleNamespace(dispose=lambda: None)
        payload = json.dumps({"key": "2026-09-12T23:00Z", "text": text})
        with patch("eva.bootstrap.runtime.RuntimeSettings.from_environment", return_value=SimpleNamespace(database_dsn="postgresql://test")), \
             patch("eva.bootstrap.runtime._build_proactive_delivery", return_value=delivery), \
             patch("sqlalchemy.create_engine", return_value=engine), \
             patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(output):
            try:
                exec(helper.CONTAINER_SENDER, {})
                code = 0
            except SystemExit as error:
                code = error.code
        self.assertNotIn("test-only", output.getvalue())
        self.assertNotIn("secret-provider-url", output.getvalue())
        return code, json.loads(output.getvalue())

    @staticmethod
    def trims_edges(method, payload, **kwargs):
        return {"message_id": 321, "chat": {"id": 12345, "type": "private"},
                "text": payload["text"].strip(" \t\r\n")}

    def test_normalized_wire_keeps_existing_strict_receipt_success(self):
        code, data = self.run_sender("Eva-update", self.trims_edges)
        self.assertEqual(code, 0)
        self.assertTrue(data["private_chat_and_exact_text_verified"])
        self.assertEqual(len(data["provider_receipt_sha256"]), 64)

    def test_receipt_text_mismatch_is_uncertain_with_safe_observation(self):
        code, data = self.run_sender("Eva-update\n", self.trims_edges)
        self.assertEqual(code, 1)
        self.assertEqual(data["stage"], "provider_reply_received")
        observation = data["provider_observation"]
        self.assertTrue(observation["intended_private_chat"])
        self.assertTrue(observation["positive_message_id"])
        self.assertFalse(observation["exact_text"])
        self.assertNotIn("Eva-update", json.dumps(data))
        self.assertEqual(len(observation["provider_receipt_sha256"]), 64)

    def test_transport_failure_has_no_provider_acceptance_or_leaked_error(self):
        def failed(*args, **kwargs):
            raise RuntimeError("secret-provider-url")
        code, data = self.run_sender("Eva-update", failed)
        self.assertEqual(code, 1)
        self.assertEqual(data["stage"], "provider_request_started")
        self.assertEqual(data["provider_observation"], {})


if __name__ == "__main__":
    unittest.main()
