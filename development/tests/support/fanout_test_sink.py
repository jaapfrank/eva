"""Test-only idempotent HTTP sink for historical native delivery gates."""

from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Lock
import time
from urllib.parse import unquote


def main() -> int:
    host = os.environ.get("EVA_FANOUT_TEST_SINK_HOST", "0.0.0.0").strip()
    port = int(os.environ.get("EVA_FANOUT_TEST_SINK_PORT", "8090"))
    rejected = frozenset(
        item.strip()
        for item in os.environ.get(
            "EVA_FANOUT_TEST_REJECT_RECIPIENTS", "sink-c"
        ).split(",")
        if item.strip()
    )
    delayed_recipient = os.environ.get(
        "EVA_FANOUT_TEST_DELAY_RECIPIENT", ""
    ).strip()
    try:
        response_delay_seconds = float(
            os.environ.get("EVA_FANOUT_TEST_RESPONSE_DELAY_SECONDS", "0")
        )
    except ValueError as error:
        raise RuntimeError("fan-out test delay is invalid") from error
    if not 0 <= response_delay_seconds <= 30:
        raise RuntimeError("fan-out test delay is outside 0..30 seconds")
    receipts: dict[str, dict[str, object]] = {}
    request_payloads: dict[str, dict[str, object]] = {}
    post_counts: dict[str, int] = {}
    accepted_effect_counts: dict[str, int] = {}
    rejected_counts: dict[str, int] = {}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._respond(200, {"status": "alive"})
                return
            if self.path == "/v1/metrics":
                with lock:
                    value = {
                        "accepted_effect_counts": dict(accepted_effect_counts),
                        "contract": "EvaFanoutTestSinkMetrics/v1",
                        "post_counts": dict(post_counts),
                        "receipt_count": len(receipts),
                        "rejected_counts": dict(rejected_counts),
                    }
                self._respond(200, value)
                return
            prefix = "/v1/deliveries/"
            if not self.path.startswith(prefix):
                self._respond(404, {"detail": "not found"})
                return
            key = unquote(self.path[len(prefix) :])
            with lock:
                value = receipts.get(key)
            if value is None:
                self._respond(404, {"detail": "delivery not found"})
                return
            self._respond(200, value)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/deliveries":
                self._respond(404, {"detail": "not found"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
                if not 1 <= length <= 8192:
                    raise ValueError
                raw = self.rfile.read(length)
                payload = json.loads(raw)
                required = {
                    "contract",
                    "idempotency_key",
                    "message",
                    "message_sha256",
                    "recipient",
                }
                if not isinstance(payload, dict) or set(payload) != required:
                    raise ValueError
                if payload.get("contract") != "EvaFanoutSinkRequest/v1":
                    raise ValueError
                recipient = payload["recipient"]
                key = payload["idempotency_key"]
                message_hash = payload["message_sha256"]
                if not all(
                    isinstance(value, str) and value
                    for value in (recipient, key, message_hash, payload["message"])
                ):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self._respond(422, {"detail": "delivery request is invalid"})
                return
            with lock:
                post_counts[recipient] = post_counts.get(recipient, 0) + 1
                previous_payload = request_payloads.get(key)
                if previous_payload is not None and previous_payload != payload:
                    self._respond(409, {"detail": "idempotency conflict"})
                    return
                value = receipts.get(key)
                if value is None:
                    is_rejected = recipient in rejected
                    if is_rejected:
                        rejected_counts[recipient] = rejected_counts.get(recipient, 0) + 1
                    else:
                        accepted_effect_counts[recipient] = (
                            accepted_effect_counts.get(recipient, 0) + 1
                        )
                    value = {
                        "contract": "EvaFanoutRecipientReceipt/v1",
                        "idempotency_key": key,
                        "message_sha256": message_hash,
                        "observed_at": datetime.now(tz=UTC)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "provider_receipt_id": (
                            None if is_rejected else f"test-sink:{recipient}:{key[-16:]}"
                        ),
                        "reason_code": (
                            "recipient_rejected" if is_rejected else None
                        ),
                        "recipient": recipient,
                        "status": "rejected" if is_rejected else "accepted",
                    }
                    request_payloads[key] = payload
                    receipts[key] = value
            if recipient == delayed_recipient and response_delay_seconds:
                time.sleep(response_delay_seconds)
            self._respond(200, value)

        def _respond(self, status: int, value: object) -> None:
            raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_values: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
