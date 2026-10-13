"""Contract tests use injected local I/O, never a product server or model."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[3] / "development/benchmark/inspect_ai_v1"
sys.path.insert(0, str(HERE))
import cp001_native as native  # noqa: E402


class NativeAdapterTests(unittest.TestCase):
    def request(self, participant="eva2", task="A", step=0):
        return native.request_for(
            participant,
            run_id="0123456789ab",
            task=task,
            repeat=1,
            step=step,
            text="Een ongeziene vraag.",
            occurred_at="2026-09-12T00:00:00Z",
        )

    def response(self, participant, body, text="Vrij geformuleerd antwoord."):
        if participant == "eva2":
            return {
                "contract": "EvaInternalGroundedReply/v1",
                "conversation_id": body["conversation_id"],
                "message_id": body["message_id"],
                "text": text,
                "text_sha256": native.digest(text),
            }
        return {
            "conversation_id": body["conversation_id"],
            "request_id": body["request_id"],
            "request_replay_scope": "durable_http_ingress",
            "reply": text,
        }

    def durable(self, participant, body, text):
        if participant == "eva1-bottie":
            return self.response(participant, body, text)
        return {
            "text": text,
            "text_sha256": native.digest(text),
            "message_id": body["message_id"],
            "conversation_id": body["conversation_id"],
            "principal_id": body["principal_id"],
            "provider": "chatgpt-codex:model-revision",
            "model_usage": {
                "input_tokens": 12,
                "output_tokens": 7,
                "model_attempts": 1,
                "tool_calls": 0,
                "measurement": "measured",
            },
        }

    def test_identical_text_real_wire_fields_and_stable_replay(self):
        eva_path, eva = self.request()
        bot_path, bot = self.request("eva1-bottie")
        self.assertEqual(
            (eva_path, bot_path), ("/internal/v1/turns/respond", "/api/chat")
        )
        self.assertEqual(eva["text"], bot["text"])
        self.assertEqual(eva["message_id"], bot["request_id"])
        self.assertEqual(self.request(), (eva_path, eva))
        self.assertNotIn("recent_messages", eva)
        self.assertNotIn("synthetic", bot)
        self.assertNotIn("model", eva)
        self.assertNotIn("budget", bot)

    def test_new_conversation_preserves_person_and_resume_preserves_session(self):
        for participant in native.PARTICIPANTS:
            _, first = self.request(participant)
            _, correction = self.request(participant, step=1)
            _, recall = self.request(participant, step=2)
            self.assertEqual(first["conversation_id"], correction["conversation_id"])
            self.assertNotEqual(
                correction["conversation_id"], recall["conversation_id"]
            )
            _, save = self.request(participant, task="B")
            _, resume = self.request(participant, task="B", step=1)
            self.assertEqual(save["conversation_id"], resume["conversation_id"])
        self.assertEqual(first.get("principal_id"), recall.get("principal_id"))
        self.assertEqual(
            self.request()[1]["principal_id"], self.request(step=2)[1]["principal_id"]
        )

    def test_bottie_session_matches_existing_native_normalizer(self):
        _, body = self.request("eva1-bottie")
        module = (native.ROOT / "src/web/access.js").as_uri()
        script = f"import {{normalizeWebConversationId}} from {json.dumps(module)}; console.log(normalizeWebConversationId(process.argv[1]));"
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, body["conversation_id"]],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), body["conversation_id"])

    def test_native_arbitrary_answer_independent_readback_not_fixture_oracle(self):
        for participant in native.PARTICIPANTS:
            path, body = self.request(participant)
            text = "Mijn antwoord is geheel anders dan de vaste proeftekst."
            post = Mock(return_value=self.response(participant, body, text))
            read = Mock(return_value=self.durable(participant, body, text))
            result = native.exchange(participant, path, body, post=post, readback=read)
            self.assertEqual(result["status"], "readback_verified")
            self.assertEqual(result["product_result"], "unknown")
            self.assertIsNone(result["quality"])
            self.assertIsNone(result["readback"]["total_usage"])
            self.assertIsNone(result["readback"]["money"])
            post.assert_called_once_with(
                path, body, timeout_seconds=5, max_response_bytes=65536
            )
            self.assertEqual(read.call_args.args[0], body)
            self.assertLessEqual(read.call_args.kwargs["timeout_seconds"], 5)

    def test_bottie_public_usage_is_not_accepted_as_complete_usage(self):
        path, body = self.request("eva1-bottie")
        response = self.response("eva1-bottie", body)
        response["usage"] = {"input_tokens": 0, "output_tokens": 0}
        result = native.exchange(
            "eva1-bottie",
            path,
            body,
            post=Mock(return_value=response),
            readback=Mock(return_value=response),
        )
        self.assertIsNone(result["usage"])

    def test_partial_and_missing_usage_remain_unknown_in_total(self):
        path, body = self.request()
        response = self.response("eva2", body)
        durable = self.durable("eva2", body, response["text"])
        durable["model_usage"]["measurement"] = "partial"
        result = native.exchange(
            "eva2",
            path,
            body,
            post=Mock(return_value=response),
            readback=Mock(return_value=durable),
        )
        self.assertEqual(result["usage"]["measurement"], "partial")
        self.assertIsNone(result["readback"]["total_usage"])
        durable["model_usage"]["input_tokens"] = True
        self.assertIsNone(
            native.independent_readback("eva2", body, response["text"], durable)[
                "conversation_usage"
            ]
        )

    def test_identity_hash_and_pending_response_rejection(self):
        for participant in native.PARTICIPANTS:
            _, body = self.request(participant)
            response = self.response(participant, body)
            variants = [{"conversation_id": "wrong"}]
            variants += (
                [{"message_id": "wrong"}, {"text_sha256": "wrong"}]
                if participant == "eva2"
                else [
                    {"request_id": "wrong"},
                    {"execution_pending": True},
                    {"durable_handoff": True},
                    {"error": "failure"},
                    {"request_replay_scope": "host_only"},
                ]
            )
            for change in variants:
                with self.assertRaises(ValueError):
                    native.response_text(participant, body, {**response, **change})

    def test_eva_durable_reply_requires_every_binding_and_content_hash(self):
        path, body = self.request()
        response = self.response("eva2", body)
        intact = self.durable("eva2", body, response["text"])
        for field in (
            "message_id",
            "conversation_id",
            "principal_id",
            "text",
            "text_sha256",
        ):
            for missing in (False, True):
                with self.subTest(field=field, missing=missing):
                    durable = dict(intact)
                    if missing:
                        durable.pop(field)
                    else:
                        durable[field] = "wrong"
                    result = native.exchange(
                        "eva2",
                        path,
                        body,
                        post=Mock(return_value=response),
                        readback=Mock(return_value=durable),
                    )
                    self.assertEqual(result["status"], "readback_failed")
                    self.assertFalse(result["readback"]["integrity"])
                    self.assertIsNone(result["verified_end_ms"])
                    self.assertIsNone(result["usage"])
        result = native.exchange(
            "eva2",
            path,
            body,
            post=Mock(return_value=response),
            readback=Mock(return_value=intact),
        )
        self.assertEqual(result["status"], "readback_verified")
        self.assertTrue(result["readback"]["integrity"])
        self.assertIsNotNone(result["verified_end_ms"])
        for malformed in ([], "not a journal payload"):
            self.assertFalse(
                native.independent_readback("eva2", body, response["text"], malformed)[
                    "integrity"
                ]
            )

    def test_changed_or_missing_readback_not_success(self):
        path, body = self.request()
        response = self.response("eva2", body)
        for durable, status in [
            (None, "readback_unknown"),
            ({"text": "wrong"}, "readback_failed"),
        ]:
            result = native.exchange(
                "eva2",
                path,
                body,
                post=Mock(return_value=response),
                readback=Mock(return_value=durable),
            )
            self.assertEqual(result["status"], status)
            self.assertIsNone(result["verified_end_ms"])

    def test_timeout_failure_no_retry_no_false_server_cancellation(self):
        path, body = self.request()
        for error, status in [
            (TimeoutError(), "timeout_execution_unknown"),
            (ConnectionError(), "execution_unknown"),
        ]:
            post = Mock(side_effect=error)
            read = Mock()
            result = native.exchange("eva2", path, body, post=post, readback=read)
            self.assertEqual(result["status"], status)
            self.assertIsNone(result["server_quiescent"])
            self.assertIsNone(result["usage"])
            post.assert_called_once()
            read.assert_not_called()

    def test_equal_transport_budgets_and_deadline_include_readback(self):
        for participant in native.PARTICIPANTS:
            path, body = self.request(participant)
            post = Mock(return_value=self.response(participant, body))
            result = native.exchange(
                participant, path, body, post=post, readback=Mock(), input_bytes=1
            )
            self.assertEqual(result["status"], "input_budget_exceeded")
            post.assert_not_called()
            result = native.exchange(
                participant, path, body, post=post, readback=Mock(), output_bytes=1
            )
            self.assertEqual(result["status"], "output_budget_exceeded")
        path, body = self.request()
        with patch.object(native.time, "monotonic", side_effect=[0, 1, 6, 6]):
            result = native.exchange(
                "eva2",
                path,
                body,
                post=Mock(return_value=self.response("eva2", body)),
                readback=Mock(),
            )
        self.assertEqual(result["status"], "timeout_execution_unknown")

    def test_readonly_preflight_cannot_admit_live_or_missing_sources(self):
        report = native.preflight()
        self.assertFalse(report["network_used"])
        for participant in report["participants"].values():
            self.assertFalse(participant["live_enabled"])
            self.assertEqual(participant["admission"], "unknown")
            self.assertTrue(all(participant["source_files"].values()))
        with tempfile.TemporaryDirectory() as directory:
            missing = native.preflight(Path(directory))
        self.assertIn(
            "required native source missing",
            missing["participants"]["eva2"]["blockers"],
        )




class NativeProviderControlTests(unittest.TestCase):
    def control(self):
        from cp001_accounting import ProviderSampleLedger
        ledger = ProviderSampleLedger(
            "sample-1",
            {"providerAttempts": 2, "providerInputTokens": 20, "providerOutputTokens": 10},
            {"contact": ("fixture-model", "none")},
        )
        return native.NativeProviderControl(ledger, "request-1"), ledger

    def begin(self, event_id="event-1", request_id="request-1"):
        return {"provider_event": "begin", "event_id": event_id,
                "request_id": request_id, "role": "contact",
                "model": "fixture-model", "reasoning_effort": "none"}

    def settle(self, attempt=0, usage=None, status="completed"):
        return {"provider_event": "settle", "event_id": "event-1",
                "request_id": "request-1", "attempt_id": attempt,
                "status": status, "usage": usage}

    def test_raw_usage_and_attempt_identity_flow_into_existing_ledger(self):
        control, ledger = self.control()
        permit = control.handle(self.begin())
        self.assertTrue(permit["allow"])
        self.assertEqual(permit["attempt_id"], 0)
        usage = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
        self.assertTrue(control.handle(self.settle(usage=usage))["allow"])
        self.assertEqual(ledger.attempts[0]["usage"], usage)
        self.assertTrue(ledger.report()["complete"])
        self.assertFalse(control.handle(self.settle(usage=usage))["allow"])
        self.assertEqual(len(ledger.attempts), 1)

    def test_other_request_cannot_consume_or_settle_the_sample(self):
        control, ledger = self.control()
        self.assertFalse(control.handle(self.begin(request_id="other"))["allow"])
        self.assertEqual(ledger.attempts, [])
        self.assertFalse(control.handle(self.begin())["allow"])
        self.assertEqual(ledger.attempts, [])

    def test_wrong_attempt_identity_never_creates_success(self):
        control, ledger = self.control()
        control.handle(self.begin())
        result = control.handle(self.settle(attempt=True,
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))
        self.assertFalse(result["allow"])
        self.assertFalse(ledger.report()["complete"])
        self.assertEqual(ledger.attempts[0]["status"], "pending")

    def test_missing_usage_stays_unknown_and_prevents_another_attempt(self):
        control, ledger = self.control()
        control.handle(self.begin())
        self.assertFalse(control.handle(self.settle())["allow"])
        self.assertIsNone(ledger.attempts[0]["usage"])
        self.assertFalse(ledger.report()["complete"])
        self.assertFalse(control.handle(self.begin("event-2"))["allow"])
        self.assertEqual(len(ledger.attempts), 1)

    def test_stop_can_arrive_before_an_admitted_permit_reaches_the_worker(self):
        control, ledger = self.control()
        withheld_permit = control.handle(self.begin())
        self.assertTrue(withheld_permit["allow"])
        control.stop()
        terminal = control.handle(self.settle(attempt=None, status="aborted"))
        self.assertFalse(terminal["allow"])
        self.assertNotIn("stop", terminal)
        self.assertEqual(ledger.attempts[0]["status"], "aborted")
        self.assertIsNone(ledger.attempts[0]["usage"])
        self.assertFalse(ledger.report()["complete"])

    def test_stop_requires_terminal_observation_and_does_not_guess_usage(self):
        control, ledger = self.control()
        control.handle(self.begin())
        self.assertEqual(control.stop(), {"stop": True})
        self.assertEqual(ledger.attempts[0]["status"], "pending")
        self.assertFalse(control.handle(self.settle(status="aborted"))["allow"])
        self.assertEqual(ledger.attempts[0]["status"], "aborted")
        self.assertIsNone(ledger.attempts[0]["usage"])
        self.assertFalse(control.handle(self.begin("event-2"))["allow"])
        self.assertEqual(len(ledger.attempts), 1)


if __name__ == "__main__":
    unittest.main()
