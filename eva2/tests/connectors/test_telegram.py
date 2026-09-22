from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from eva.adapters.connectors.telegram import (
    LivingCoreTurnHttpAdapter,
    TelegramBotApiAdapter,
    TelegramProactiveDelivery,
)
from eva.application.free_will import ProactiveExpressionClaim
from eva.application.memory_audience import MemoryAudience
from eva.application.telegram_ingress import TelegramProviderRateLimited


def test_outbound_telegram_delivery_returns_provider_receipt() -> None:
    captured: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            captured.append(json.loads(self.rfile.read(length)))
            response = json.dumps({"ok": True, "result": {
                "message_id": 5001, "chat": {"id": 42, "type": "private"},
                "text": captured[-1]["text"],
            }}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        delivery = TelegramProactiveDelivery(
            bot_token="123456:test-token",
            chat_id="42",
            api_base_url=f"http://127.0.0.1:{server.server_port}",
        )
        claim = ProactiveExpressionClaim(
            "sensory:water",
            "home-assistant:water",
            4,
            0,
            "home-assistant:water",
            "De watermelder in de kelder is actief.",
            3,
        )

        assert delivery.deliver(claim) == "telegram:42:5001"
        assert captured == [
            {
                "chat_id": "42",
                "disable_notification": False,
                "text": "De watermelder in de kelder is actief.",
            }
        ]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("changes", [
    {"message_id": True}, {"message_id": 0}, {"message_id": -1},
    {"chat": None}, {"chat": {"id": 43, "type": "private"}},
    {"chat": {"id": "42", "type": "private"}}, {"chat": {"id": 42, "type": "group"}},
    {"text": "other text"},
])
def test_proactive_receipt_must_prove_exact_private_delivery(monkeypatch, changes):
    delivery = TelegramProactiveDelivery(bot_token="123456:test", chat_id="42")
    result = {"message_id": 7, "chat": {"id": 42, "type": "private"}, "text": "Fixture"} | changes
    calls = []
    def send(method, body, **kwargs):
        calls.append(method)
        assert kwargs["maximum_response_bytes"] == 65_536
        return result
    monkeypatch.setattr(delivery._api, "_call", send)
    claim = ProactiveExpressionClaim("thread", "op", 1, 0, "event", "Fixture", 1)
    with pytest.raises(RuntimeError, match="does not prove"):
        delivery.deliver(claim)
    assert calls == ["sendMessage"]  # Uncertain acceptance never causes an automatic resend.


@pytest.mark.parametrize("recipient", ["-42", "0", "042", "another-person", "４２", None])
def test_proactive_recipient_is_checked_before_http(monkeypatch, recipient):
    delivery = TelegramProactiveDelivery(bot_token="123456:test", recipient_resolver=lambda: recipient)
    monkeypatch.setattr(delivery._api, "_call", lambda *a, **k: pytest.fail("no HTTP to an unverified private chat"))
    claim = ProactiveExpressionClaim("thread", "op", 1, 0, "event", "Fixture", 1)
    with pytest.raises(ValueError):
        delivery.deliver(claim)


def test_proactive_recipient_changes_cannot_override_a_configured_owner(monkeypatch):
    delivery = TelegramProactiveDelivery(bot_token="123456:test", chat_id="42", recipient_resolver=lambda: "43")
    monkeypatch.setattr(delivery._api, "_call", lambda *a, **k: pytest.fail("mismatched owner must not send"))
    with pytest.raises(RuntimeError, match="differs from verified owner"):
        delivery.deliver(ProactiveExpressionClaim("thread", "op", 1, 0, "event", "Fixture", 1))


def test_proactive_http_error_cannot_claim_success(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from eva.adapters.connectors import telegram
    calls = []
    def send(req, **kwargs):
        calls.append(req.get_method())
        raise HTTPError(req.full_url, 500, "fixture", {}, BytesIO(json.dumps({
            "ok": True, "result": {"message_id": 7, "chat": {"id": 42, "type": "private"}, "text": "Fixture"},
        }).encode()))
    monkeypatch.setattr(telegram.urllib_request, "urlopen", send)
    delivery = TelegramProactiveDelivery(bot_token="123456:test", chat_id="42")
    with pytest.raises(RuntimeError, match="HTTP error contradicts"):
        delivery.deliver(ProactiveExpressionClaim("thread", "op", 1, 0, "event", "Fixture", 1))
    assert calls == ["POST"]


def test_saved_result_rechecks_destination_on_each_delivery(monkeypatch):
    recipient = "42"
    delivery = TelegramProactiveDelivery(
        bot_token="123456:test", recipient_resolver=lambda: recipient,
    )
    calls = []
    def send(method, body, **kwargs):
        calls.append(body)
        return {"message_id": 7, "chat": {"id": 42, "type": "private"},
                "text": body["text"]}
    monkeypatch.setattr(delivery._api, "_call", send)
    assert delivery.deliver_text("Onderzoek afgerond", expected_chat_id="42") == "telegram:42:7"
    recipient = "43"
    with pytest.raises(RuntimeError, match="saved Telegram destination"):
        delivery.deliver_text("Privéresultaat", expected_chat_id="42")
    assert calls == [{"chat_id": "42", "disable_notification": True,
                      "text": "Onderzoek afgerond"}]


@pytest.mark.parametrize("destination", ["-42", "0", "042", "４２", None])
def test_saved_result_rejects_invalid_destination_before_network(monkeypatch, destination):
    delivery = TelegramProactiveDelivery(bot_token="123456:test", chat_id="42")
    monkeypatch.setattr(delivery._api, "_call", lambda *a, **k: pytest.fail("no send"))
    with pytest.raises(ValueError):
        delivery.deliver_text("Resultaat", expected_chat_id=destination)


def test_saved_result_does_not_retry_ambiguous_send(monkeypatch):
    delivery = TelegramProactiveDelivery(bot_token="123456:test", chat_id="42")
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("acceptance unknown")
    monkeypatch.setattr(delivery._api, "_call", send)
    with pytest.raises(TimeoutError):
        delivery.deliver_text("Resultaat", expected_chat_id="42")
    assert calls == [1]


def test_bot_api_poll_send_and_living_core_contract_are_bounded() -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            captured.append((self.path, payload))
            status = 200
            if self.path.endswith("/getUpdates"):
                result: object = [
                    {
                        "message": {
                            "chat": {"id": 101},
                            "date": 1787558400,
                            "from": {"id": 101},
                            "message_id": 501,
                            "text": "Hoi Eva",
                        },
                        "update_id": 7001,
                    }
                ]
                response = {"ok": True, "result": result}
            elif self.path.endswith("/sendMessageDraft"):
                if payload["text"] == "flood":
                    status = 429
                    response = {
                        "description": "Too Many Requests",
                        "error_code": 429,
                        "ok": False,
                        "parameters": {"retry_after": 7},
                    }
                else:
                    response = {"ok": True, "result": True}
            elif self.path.endswith("/sendMessage"):
                response = {"ok": True, "result": {"message_id": 9001}}
            else:
                bounded_stream = payload["text"] == "Begrens dit antwoord."
                padded_final = payload["text"] == "Weiger een niet-canonieke final."
                text = (
                    "x" * 4096 + " wordt niet zichtbaar"
                    if bounded_stream
                    else " antwoord "
                    if padded_final
                    else "Hoi terug."
                )
                stream_deltas = (
                    ("  " + "x" * 4096, " wordt niet zichtbaar ")
                    if bounded_stream
                    else ("Hoi ", "terug.")
                )
                response = {
                    "contract": "EvaInternalGroundedReply/v1",
                    "conversation_id": payload["conversation_id"],
                    "message_id": payload["message_id"],
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            encoded = json.dumps(response).encode()
            if self.headers.get("Accept") == "application/x-ndjson":
                encoded = b"\n".join(
                    json.dumps(row, separators=(",", ":")).encode()
                    for row in (
                        *(
                            {"contract": "EvaTextDelta/v1", "text": delta}
                            for delta in stream_deltas
                        ),
                        response,
                    )
                ) + b"\n"
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "application/x-ndjson"
                if self.headers.get("Accept") == "application/x-ndjson"
                else "application/json",
            )
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        telegram = TelegramBotApiAdapter(
            bot_token="123456:test-token",
            api_base_url=base_url,
            timeout_seconds=2,
        )
        updates = telegram.poll(offset=7001)
        assert len(updates) == 1
        assert updates[0].sender_id == 101
        assert updates[0].text == "Hoi Eva"
        telegram.send_reply_draft(updates[0], "")
        telegram.send_reply_draft(updates[0], "Hoi")
        assert telegram.send_reply(updates[0], "Hoi terug.") == "telegram:101:9001"

        core = LivingCoreTurnHttpAdapter(
            base_url=base_url,
            tenant_id="tenant",
            installation_id="home",
            timeout_seconds=2,
        )
        reply = core.respond(
            updates[0],
            principal_id="person:jaap",
            audience=MemoryAudience.private_person(principal_id="person:jaap"),
        )
        assert reply.text == "Hoi terug."
        deltas: list[str] = []
        streamed = core.respond_stream(
            updates[0],
            principal_id="person:jaap",
            audience=MemoryAudience.private_person(principal_id="person:jaap"),
            on_delta=deltas.append,
        )
        assert streamed == reply
        assert "".join(deltas) == reply.text
        assert len(deltas) == 2
        assert captured[0][1] == {
            "allowed_updates": ["message"],
            "limit": 32,
            "offset": 7001,
            "timeout": 2,
        }
        first_draft = captured[1][1]
        second_draft = captured[2][1]
        assert first_draft == {
            "chat_id": 101,
            "draft_id": first_draft["draft_id"],
            "text": "",
        }
        assert isinstance(first_draft["draft_id"], int)
        assert 1 <= first_draft["draft_id"] <= 2_147_483_647
        assert second_draft == {**first_draft, "text": "Hoi"}
        assert captured[3][1]["reply_parameters"] == {"message_id": 501}
        assert captured[4][1]["principal_id"] == "person:jaap"
        assert captured[5][1] == captured[4][1]
        assert captured[4][1]["audience"] == {
            "audience_id": "person:person:jaap",
            "kind": "private_person",
            "principal_id": "person:jaap",
        }
        bounded_message = type(updates[0])(
            7002,
            "101",
            502,
            101,
            updates[0].occurred_at,
            "Begrens dit antwoord.",
        )
        bounded_deltas: list[str] = []
        bounded = core.respond_stream(
            bounded_message,
            principal_id="person:jaap",
            audience=MemoryAudience.private_person(principal_id="person:jaap"),
            on_delta=bounded_deltas.append,
        )
        assert bounded.text == "x" * 4095 + "…"
        assert "".join(bounded_deltas) == bounded.text
        padded_message = type(updates[0])(
            7003,
            "101",
            503,
            101,
            updates[0].occurred_at,
            "Weiger een niet-canonieke final.",
        )
        with pytest.raises(RuntimeError, match="reply content differs"):
            core.respond(
                padded_message,
                principal_id="person:jaap",
                audience=MemoryAudience.private_person(principal_id="person:jaap"),
            )
        with pytest.raises(TelegramProviderRateLimited) as rate_limited:
            telegram.send_reply_draft(updates[0], "flood")
        assert rate_limited.value.retry_after_seconds == 7
        with pytest.raises(ValueError, match="private user chat"):
            telegram.send_reply_draft(
                type(updates[0])(
                    7002,
                    "-42",
                    502,
                    101,
                    updates[0].occurred_at,
                    "groep",
                ),
                "",
            )
        with pytest.raises(ValueError, match="draft text"):
            telegram.send_reply_draft(updates[0], "x" * 4097)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
