from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import unquote

from eva.adapters.connectors.fanout_http import HttpFanoutDeliverySink
from eva.application.delivery import FanoutDeliveryRequest


NOW = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


def test_http_sink_posts_once_and_reads_back_the_same_provider_effect() -> None:
    receipts: dict[str, dict[str, object]] = {}
    counts: dict[str, int] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            key = unquote(self.path.rsplit("/", 1)[-1])
            value = receipts.get(key)
            if value is None:
                self.send_response(404)
                self.end_headers()
                return
            self._json(value)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["content-length"])
            request = json.loads(self.rfile.read(length))
            recipient = request["recipient"]
            key = request["idempotency_key"]
            counts[recipient] = counts.get(recipient, 0) + 1
            value = {
                "contract": "EvaFanoutRecipientReceipt/v1",
                "idempotency_key": key,
                "message_sha256": request["message_sha256"],
                "observed_at": "2026-08-24T07:00:00Z",
                "provider_receipt_id": f"sink:{recipient}:1",
                "reason_code": None,
                "recipient": recipient,
                "status": "accepted",
            }
            receipts[key] = value
            self._json(value)

        def _json(self, value: object) -> None:
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_values: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        sink = HttpFanoutDeliverySink(
            recipient_endpoints={"sink-a": endpoint},
            allow_insecure_http=True,
        )
        request = FanoutDeliveryRequest(
            "fanout:http", "jaap", "Test", ("sink-a",), NOW
        )
        key = request.idempotency_key("sink-a")
        assert asyncio.run(
            sink.readback(recipient="sink-a", idempotency_key=key)
        ) is None
        delivered = asyncio.run(
            sink.deliver(
                recipient="sink-a",
                message=request.message,
                message_sha256=request.message_sha256,
                idempotency_key=key,
            )
        )
        observed = asyncio.run(
            sink.readback(recipient="sink-a", idempotency_key=key)
        )

        assert observed == delivered
        assert counts == {"sink-a": 1}
    finally:
        server.shutdown()
        server.server_close()
