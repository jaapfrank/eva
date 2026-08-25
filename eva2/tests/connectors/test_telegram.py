from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from eva.adapters.connectors.telegram import (
    LivingCoreTurnHttpAdapter,
    TelegramBotApiAdapter,
    TelegramProactiveDelivery,
)
from eva.application.free_will import ProactiveExpressionClaim
from eva.application.memory_audience import MemoryAudience


def test_outbound_telegram_delivery_returns_provider_receipt() -> None:
    captured: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            captured.append(json.loads(self.rfile.read(length)))
            response = json.dumps({"ok": True, "result": {"message_id": 5001}}).encode()
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


def test_bot_api_poll_send_and_living_core_contract_are_bounded() -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            captured.append((self.path, payload))
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
            elif self.path.endswith("/sendMessage"):
                response = {"ok": True, "result": {"message_id": 9001}}
            else:
                text = "Hoi terug."
                import hashlib

                response = {
                    "contract": "EvaInternalGroundedReply/v1",
                    "conversation_id": "telegram:101",
                    "message_id": "telegram:101:501",
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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
        assert captured[0][1] == {
            "allowed_updates": ["message"],
            "limit": 32,
            "offset": 7001,
            "timeout": 2,
        }
        assert captured[1][1]["reply_parameters"] == {"message_id": 501}
        assert captured[2][1]["principal_id"] == "person:jaap"
        assert captured[2][1]["audience"] == {
            "audience_id": "person:person:jaap",
            "kind": "private_person",
            "principal_id": "person:jaap",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
