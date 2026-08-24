from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from eva.adapters.connectors.agentmail import AgentMailConfig, AgentMailReadAdapter


MESSAGE = {
    "attachments": [{"attachment_id": "attachment-1"}],
    "extracted_text": "Hoi Eva",
    "from": "Jaap <jaap@example.nl>",
    "labels": ["inbox"],
    "message_id": "message-001",
    "preview": "Hoi Eva",
    "subject": "Even testen",
    "text": "Hoi Eva, dit is een echte mailtekst.",
    "thread_id": "thread-001",
    "timestamp": "2026-08-23T16:00:00Z",
    "to": ["eva@agentmail.to"],
}


class _Server:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.auth: list[str | None] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                outer.paths.append(self.path)
                outer.auth.append(self.headers.get("Authorization"))
                value: object
                if self.path.endswith("/messages/message-001"):
                    value = MESSAGE
                else:
                    value = {
                        "count": 1,
                        "messages": [
                            {
                                key: value
                                for key, value in MESSAGE.items()
                                if key not in {"attachments", "extracted_text", "text"}
                            }
                        ],
                        "next_page_token": "page-2",
                    }
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v0"


def test_agentmail_lists_and_gets_bounded_messages_without_exposing_key() -> None:
    with _Server() as server:
        config = AgentMailConfig(
            inbox_id="eva@agentmail.to",
            api_key="agentmail-test-secret",
            base_url=server.url,
        )
        adapter = AgentMailReadAdapter(config)
        page = asyncio.run(adapter.list_messages(limit=1))
        message = asyncio.run(adapter.get_message("message-001"))

    assert page.messages[0].subject == "Even testen"
    assert page.next_page_token == "page-2"
    assert message.text == "Hoi Eva, dit is een echte mailtekst."
    assert message.attachment_count == 1
    assert server.auth == ["Bearer agentmail-test-secret"] * 2
    assert "agentmail-test-secret" not in repr(config)
    assert len(page.sha256) == 64
    assert len(message.sha256) == 64


def test_agentmail_rejects_unbounded_reads_and_public_plain_http() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        AgentMailConfig(
            inbox_id="eva@agentmail.to",
            api_key="secret",
            base_url="http://api.agentmail.to/v0",
        )
    adapter = AgentMailReadAdapter(AgentMailConfig("eva@agentmail.to", "secret"))
    with pytest.raises(ValueError, match="outside 1..20"):
        asyncio.run(adapter.list_messages(limit=21))
