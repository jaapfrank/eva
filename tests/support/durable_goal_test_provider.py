"""Test-only HTTP provider for historical durable-goal fault and wake gates."""

from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from threading import Lock
import time
from urllib import parse as urllib_parse


_lock = Lock()
_evidence: dict[str, dict[str, str]] = {}
_evidence_revisions: dict[str, int] = {}
_effects: dict[str, dict[str, object]] = {}
_deliveries: dict[tuple[str, str], dict[str, object]] = {}
_effect_posts: dict[str, int] = {}
_delivery_posts: dict[str, int] = {}


def _now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _encoded(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "EvaDurableGoalTestProvider/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib_parse.urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"status": "healthy"})
            return
        if parsed.path == "/v1/evidence":
            resource = urllib_parse.parse_qs(parsed.query).get("resource", [""])[0]
            with _lock:
                values = _evidence.get(resource)
                revision = _evidence_revisions.get(resource, 0)
            if values is None:
                self._send(404, {"detail": "not found"})
                return
            self._send(
                200,
                {
                    "contract": "EvaGoalEvidence/v1",
                    "observed_at": _now(),
                    "provider": os.environ.get("EVA_GOAL_TEST_PROVIDER", "goal-test"),
                    "provider_revision": str(revision),
                    "resource": resource,
                    "values": values,
                },
            )
            return
        if parsed.path.startswith("/v1/effects/"):
            key = urllib_parse.unquote(parsed.path.removeprefix("/v1/effects/"))
            with _lock:
                artifact = _effects.get(key)
            self._send(200, artifact) if artifact is not None else self._send(
                404, {"detail": "not found"}
            )
            return
        if parsed.path.startswith("/v1/deliveries/"):
            key = urllib_parse.unquote(parsed.path.removeprefix("/v1/deliveries/"))
            recipient = urllib_parse.parse_qs(parsed.query).get("recipient", [""])[0]
            with _lock:
                receipt = _deliveries.get((recipient, key))
            self._send(200, receipt) if receipt is not None else self._send(
                404, {"detail": "not found"}
            )
            return
        if parsed.path == "/v1/metrics":
            with _lock:
                payload = {
                    "contract": "EvaDurableGoalTestMetrics/v1",
                    "delivery_count": len(_deliveries),
                    "delivery_posts": dict(sorted(_delivery_posts.items())),
                    "effect_count": len(_effects),
                    "effect_posts": dict(sorted(_effect_posts.items())),
                    "evidence_revisions": dict(sorted(_evidence_revisions.items())),
                }
            self._send(200, payload)
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        raw = self._body()
        if raw is None:
            return
        if self.path == "/v1/test/evidence":
            resource = raw.get("resource")
            values = raw.get("values")
            if not isinstance(resource, str) or not isinstance(values, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in values.items()
            ):
                self._send(422, {"detail": "invalid evidence"})
                return
            with _lock:
                _evidence[resource] = dict(values)
                _evidence_revisions[resource] = _evidence_revisions.get(resource, 0) + 1
                revision = _evidence_revisions[resource]
            self._send(200, {"resource": resource, "revision": revision})
            return
        if self.path == "/v1/effects":
            key = raw.get("idempotency_key")
            instruction = raw.get("instruction")
            goal_id = raw.get("goal_id")
            evidence = raw.get("evidence")
            if not all(isinstance(item, str) and item for item in (key, instruction, goal_id)):
                self._send(422, {"detail": "invalid effect"})
                return
            if not isinstance(evidence, dict):
                self._send(422, {"detail": "invalid effect evidence"})
                return
            content = f"# Resultaat\n\n{instruction}\n\nBewijs: {evidence.get('provider_revision')}"
            artifact = {
                "artifact_id": "goal-artifact:"
                + hashlib.sha256(str(key).encode()).hexdigest()[:24],
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "contract": "EvaGoalArtifact/v1",
                "created_at": _now(),
                "media_type": "text/markdown",
            }
            with _lock:
                _effect_posts[str(key)] = _effect_posts.get(str(key), 0) + 1
                existing = _effects.setdefault(str(key), artifact)
            delay = float(os.environ.get("EVA_GOAL_TEST_EFFECT_DELAY_SECONDS", "0"))
            if delay:
                time.sleep(delay)
            self._send(200, existing)
            return
        if self.path == "/v1/deliveries":
            key = raw.get("idempotency_key")
            recipient = raw.get("recipient")
            if not isinstance(key, str) or not isinstance(recipient, str):
                self._send(422, {"detail": "invalid delivery"})
                return
            rejected = {
                item.strip()
                for item in os.environ.get("EVA_GOAL_TEST_REJECT_RECIPIENTS", "").split(",")
                if item.strip()
            }
            receipt = {
                "contract": "EvaGoalDeliveryReceipt/v1",
                "reason": "recipient_rejected" if recipient in rejected else None,
                "recipient": recipient,
                "status": "rejected" if recipient in rejected else "accepted",
            }
            with _lock:
                _delivery_posts[recipient] = _delivery_posts.get(recipient, 0) + 1
                existing = _deliveries.setdefault((recipient, key), receipt)
            delay = float(os.environ.get("EVA_GOAL_TEST_DELIVERY_DELAY_SECONDS", "0"))
            if delay:
                time.sleep(delay)
            self._send(200, existing)
            return
        self._send(404, {"detail": "not found"})

    def _body(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self._send(400, {"detail": "invalid length"})
            return None
        if not 1 <= length <= 131_072:
            self._send(413, {"detail": "invalid body size"})
            return None
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"detail": "invalid json"})
            return None
        if not isinstance(value, dict):
            self._send(422, {"detail": "invalid object"})
            return None
        return value

    def _send(self, status: int, value: object) -> None:
        body = _encoded(value)
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = os.environ.get("EVA_GOAL_TEST_HOST", "0.0.0.0")  # noqa: S104
    port = int(os.environ.get("EVA_GOAL_TEST_PORT", "8091"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
