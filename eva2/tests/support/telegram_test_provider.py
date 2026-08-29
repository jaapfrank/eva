"""Test-only Bot API contract provider for historical native connector gates."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Lock
from typing import Any
from urllib.parse import urlparse


_lock = Lock()
_sent: list[dict[str, Any]] = []


def _updates() -> list[dict[str, Any]]:
    raw = os.environ.get("EVA_TELEGRAM_TEST_UPDATES_JSON", "")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Telegram test updates are invalid JSON") from error
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise RuntimeError("Telegram test update set is invalid")
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply(self, status: int, payload: object) -> None:
        raw = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/metrics":
            self._reply(404, {"error": "not_found"})
            return
        with _lock:
            sent = list(_sent)
        self._reply(200, {"contract": "EvaTelegramTestMetrics/v1", "sent": sent})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"ok": False})
            return
        if not 0 < length <= 65_536:
            self._reply(413, {"ok": False})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"ok": False})
            return
        path = urlparse(self.path).path
        if path.endswith("/getUpdates"):
            offset = payload.get("offset") if isinstance(payload, dict) else None
            if not isinstance(offset, int):
                self._reply(400, {"ok": False})
                return
            result = [item for item in _updates() if item.get("update_id", -1) >= offset]
            self._reply(200, {"ok": True, "result": result})
            return
        if path.endswith("/sendMessage"):
            if not isinstance(payload, dict):
                self._reply(400, {"ok": False})
                return
            with _lock:
                message_id = 990_000 + len(_sent) + 1
                _sent.append(
                    {
                        "chat_id": str(payload.get("chat_id")),
                        "message_id": message_id,
                        "reply_to_message_id": (
                            payload.get("reply_parameters", {}).get("message_id")
                            if isinstance(payload.get("reply_parameters"), dict)
                            else None
                        ),
                        "text": payload.get("text"),
                    }
                )
            self._reply(200, {"ok": True, "result": {"message_id": message_id}})
            return
        self._reply(404, {"ok": False})


def main() -> int:
    host = os.environ.get("EVA_TELEGRAM_TEST_HOST", "127.0.0.1")
    port = int(os.environ.get("EVA_TELEGRAM_TEST_PORT", "8080"))
    _updates()
    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
