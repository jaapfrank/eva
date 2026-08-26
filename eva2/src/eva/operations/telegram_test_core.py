"""A fixed diagnostic core for a later real Telegram transport test.

It is not Eva's conversation model.  Its presence proves neither Telegram
delivery, pairing, group consent, nor a real Eva conversation; those claims
require a separately recorded live test receipt.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from typing import Any


_REPLY = "Telegram-test ontvangen. Dit controleert de verbinding, niet Eva's gesprek."


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply(self, status: int, payload: object) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._reply(200, {"status": "alive"})
            return
        self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/internal/v1/turns/respond":
            self._reply(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload: Any = json.loads(self.rfile.read(length))
            response = _response(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            self._reply(422, {"error": "invalid_turn"})
            return
        self._reply(200, response)


def _response(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {
        "audience",
        "conversation_id",
        "installation_id",
        "message_id",
        "occurred_at",
        "principal_id",
        "tenant_id",
        "text",
    }:
        raise ValueError("test core turn differs")
    if (
        payload["tenant_id"] != _required("EVA_TEST_RUNTIME_TENANT_ID")
        or payload["installation_id"] != _required("EVA_TEST_RUNTIME_INSTALLATION_ID")
        or not isinstance(payload["conversation_id"], str)
        or not isinstance(payload["message_id"], str)
        or not isinstance(payload["text"], str)
        or not isinstance(payload["audience"], dict)
    ):
        raise ValueError("test core turn scope differs")
    datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00")).astimezone(UTC)
    return {
        "contract": "EvaInternalGroundedReply/v1",
        "conversation_id": payload["conversation_id"],
        "evidence_memory_ids": [],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "message_id": payload["message_id"],
        "provider": "telegram-transport-test-core",
        "text": _REPLY,
        "text_sha256": hashlib.sha256(_REPLY.encode()).hexdigest(),
    }


def main() -> int:
    _required("EVA_TEST_RUNTIME_TENANT_ID")
    _required("EVA_TEST_RUNTIME_INSTALLATION_ID")
    ThreadingHTTPServer(("0.0.0.0", 8080), _Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
