"""Public transport diagnostics; injected offline I/O, no final-case data."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

HERE = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1"
sys.path.insert(0, str(HERE))
import cp001_native as native
import p2_provider_history_native_glue as glue


class IngressDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.path, self.body = native.request_for(
            "eva2", run_id="0123456789ab", task="A", repeat=1, step=0,
            text="Gewone lokale ontwikkelfixture.",
            occurred_at="2026-10-06T12:00:00Z",
        )
        self.reply = {
            "contract": "EvaInternalGroundedReply/v1",
            "conversation_id": self.body["conversation_id"],
            "message_id": self.body["message_id"],
            "text": "Lokaal antwoord.",
            "text_sha256": native.digest("Lokaal antwoord."),
        }
        self.durable = {
            **self.reply, "principal_id": self.body["principal_id"],
        }

    def test_distinguishes_rejected_http_from_invalid_successful_reply(self):
        for response, failure, phase, code, status in (
            (None, native.NativeIngressError("http_rejected", 422),
             "ingress", "http_rejected", 422),
            (native.NativeHTTPResponse({"detail": "sensitive-detail"}, 200), None,
             "response_validation", "invalid_value", 200),
        ):
            with self.subTest(phase=phase):
                post = Mock(return_value=response, side_effect=failure)
                read = Mock()
                result = native.exchange("eva2", self.path, self.body, post=post, readback=read)
                self.assertEqual(result["status"], "execution_unknown")
                self.assertEqual(result["diagnostic"],
                    {"phase": phase, "code": code, "http_status": status})
                self.assertIsNone(result["answer"])
                self.assertIsNone(result["server_quiescent"])
                self.assertIsNone(result["quality"])
                self.assertNotIn("sensitive-detail", json.dumps(result))
                post.assert_called_once()
                read.assert_not_called()

    def test_http_rejection_cannot_be_validated_as_a_successful_reply(self):
        read = Mock()
        result = native.exchange(
            "eva2", self.path, self.body,
            post=Mock(return_value=native.NativeHTTPResponse(self.reply, 503)),
            readback=read,
        )
        self.assertEqual(result["status"], "execution_unknown")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["diagnostic"],
            {"phase": "ingress", "code": "http_rejected", "http_status": 503})
        read.assert_not_called()

    def test_readback_failure_keeps_reply_but_does_not_claim_completion(self):
        result = native.exchange(
            "eva2", self.path, self.body,
            post=Mock(return_value=native.NativeHTTPResponse(self.reply, 200)),
            readback=Mock(side_effect=ValueError("secret-database-detail")),
        )
        self.assertEqual(result["answer"], self.reply["text"])
        self.assertEqual(result["status"], "execution_unknown")
        self.assertEqual(result["diagnostic"],
            {"phase": "readback", "code": "invalid_value", "http_status": 200})
        self.assertIsNone(result["verified_end_ms"])
        self.assertNotIn("secret-database-detail", json.dumps(result))

    def test_successful_envelope_preserves_existing_readback_contract(self):
        result = native.exchange(
            "eva2", self.path, self.body,
            post=Mock(return_value=native.NativeHTTPResponse(self.reply, 200)),
            readback=Mock(return_value=self.durable),
        )
        self.assertEqual(result["status"], "readback_verified")
        self.assertEqual(result["http_status"], 200)
        self.assertIsNone(result["diagnostic"])
        self.assertEqual(result["product_result"], "unknown")
        self.assertIsNone(result["quality"])

    def test_http_error_body_is_closed_without_reading_or_disclosure(self):
        raw = Mock(wraps=io.BytesIO(b"sensitive-server-detail"))
        error = HTTPError("http://offline.invalid/private-url", 422,
                          "sensitive-http-reason", {}, raw)
        with patch.object(glue, "urlopen", side_effect=error), patch.object(
            glue, "read_eva2_committed_reply") as read:
            result = glue.core_exchange("http://offline.invalid", self.body, None,
                                        timeout_seconds=5)
        raw.read.assert_not_called()
        raw.close.assert_called_once()
        read.assert_not_called()
        self.assertEqual(result["diagnostic"],
            {"phase": "ingress", "code": "http_rejected", "http_status": 422})
        wire = json.dumps(result)
        for secret in ("sensitive-server-detail", "private-url", "sensitive-http-reason"):
            self.assertNotIn(secret, wire)

    def test_invalid_json_retains_http_status_and_discards_body(self):
        response = Mock(status=200)
        response.read.return_value = b"not-json-sensitive-content"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(glue, "urlopen", return_value=response), patch.object(
            glue, "read_eva2_committed_reply") as read:
            result = glue.core_exchange("http://offline.invalid", self.body, None,
                                        timeout_seconds=5)
        self.assertEqual(result["diagnostic"],
            {"phase": "ingress", "code": "invalid_json", "http_status": 200})
        self.assertNotIn("not-json-sensitive-content", json.dumps(result))
        read.assert_not_called()

    def test_bottie_rejection_uses_same_content_free_diagnostic(self):
        io_adapter = glue.BottieIO.__new__(glue.BottieIO)
        io_adapter.transact = Mock(return_value={
            "status": 503, "body": {"detail": "secret-native-detail"}})
        path, body = native.request_for(
            "eva1-bottie", run_id="0123456789ab", task="A", repeat=1, step=0,
            text="Gewone lokale ontwikkelfixture.", occurred_at="2026-10-06T12:00:00Z")
        result = native.exchange("eva1-bottie", path, body,
                                 post=io_adapter.post, readback=Mock())
        self.assertEqual(result["diagnostic"],
            {"phase": "ingress", "code": "http_rejected", "http_status": 503})
        self.assertNotIn("secret-native-detail", json.dumps(result))

    def test_invalid_http_status_cannot_disclose_arbitrary_metadata(self):
        result = native.exchange(
            "eva2", self.path, self.body,
            post=Mock(side_effect=native.NativeIngressError(
                "http_rejected", "sensitive-status")),
            readback=Mock(),
        )
        self.assertIsNone(result["http_status"])
        self.assertIsNone(result["diagnostic"]["http_status"])
        self.assertNotIn("sensitive-status", json.dumps(result))
        with self.assertRaises(ValueError):
            native.NativeIngressError("unregistered-code")


if __name__ == "__main__":
    unittest.main()
