#!/usr/bin/env python3
"""Serve one isolated, tool-free Eva model boundary for benchmark development.

The container running this service receives only an access-only Codex lease.
It deliberately has no Eva database, memory, connector, Telegram, or Home
Assistant mount.  The fixed instruction and closed response schema keep the
benchmark transport separate from Eva's normal conversation surface.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from eva.adapters.model.conversation import (
    CodexConversationModel,
    ConversationModelError,
)


AUTH_FILE = Path("/run/eva/model-lease/auth.json")
MAX_REQUEST_BYTES = 160 * 1024
MAX_PROMPT_BYTES = 128 * 1024
INSTRUCTIONS = (
    "You are Eva in an isolated DBBench development run. Work carefully and "
    "honestly from only the supplied prompt. You have no tools, personal memory, "
    "house access, connectors, or answer key. Return exactly the text format "
    "requested by the prompt, without markdown or commentary."
)


class ToolFreeModelServerError(ValueError):
    """The isolated request cannot be handled safely."""


def _decode_request(raw: bytes) -> str:
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise ToolFreeModelServerError("request size is invalid")
    try:
        payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ToolFreeModelServerError("request is not JSON") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "prompt"}
        or payload.get("contract") != "EvaToolFreeModelRequest/v1"
        or not isinstance(payload.get("prompt"), str)
        or not payload["prompt"].strip()
        or len(payload["prompt"].encode("utf-8")) > MAX_PROMPT_BYTES
    ):
        raise ToolFreeModelServerError("request shape is invalid")
    return payload["prompt"]


def _reply(model: CodexConversationModel, prompt: str) -> dict[str, object]:
    result = model.generate_tool_free_text(
        prompt=prompt,
        instructions=INSTRUCTIONS,
    )
    return {
        "contract": "EvaToolFreeModelReply/v1",
        "provider": result.provider,
        "text": result.text,
        "text_sha256": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
        "timing": result.timing.to_wire(),
        "tools_exposed": 0,
        "usage": result.usage.to_wire(),
    }


def _handler(model: CodexConversationModel) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "EvaToolFreeModel/1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path != "/v1/generate":
                    raise ToolFreeModelServerError("route is invalid")
                content_type = self.headers.get_content_type()
                raw_length = self.headers.get("Content-Length", "")
                if content_type != "application/json" or not raw_length.isdecimal():
                    raise ToolFreeModelServerError("request headers are invalid")
                length = int(raw_length)
                if not 1 <= length <= MAX_REQUEST_BYTES:
                    raise ToolFreeModelServerError("request size is invalid")
                payload = _reply(model, _decode_request(self.rfile.read(length)))
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(200)
            except ToolFreeModelServerError:
                body = b'{"error":"invalid_request"}'
                self.send_response(400)
            except ConversationModelError:
                body = b'{"error":"model_unavailable"}'
                self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> int:
    if os.environ.get("EVA_TOOL_FREE_AUTH_FILE", str(AUTH_FILE)) != str(AUTH_FILE):
        raise SystemExit("tool-free model auth path is fixed")
    model = CodexConversationModel(
        auth_file=AUTH_FILE,
        model=os.environ.get("EVA_TOOL_FREE_MODEL", "gpt-5.4-mini"),
        reasoning_effort="medium",
        timeout_seconds=120.0,
    )
    if not model.credential_status().ready:
        raise SystemExit("tool-free model access lease is unavailable")
    server = HTTPServer(("0.0.0.0", 8090), _handler(model))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
