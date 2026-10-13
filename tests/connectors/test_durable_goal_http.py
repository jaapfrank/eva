from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from threading import Thread
from urllib.parse import unquote, urlparse

import pytest

from eva.adapters.connectors.durable_goal_http import HttpDurableGoalConnector
from eva.application.durable_goals import (
    DurableGoalSpec,
    GoalWakeCondition,
)


NOW = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


def test_http_goal_connector_reads_executes_and_reads_back_without_duplicate() -> None:
    effects: dict[str, dict[str, object]] = {}
    deliveries: dict[str, dict[str, object]] = {}
    counts = {"effect": 0, "delivery": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/v1/evidence":
                self._json(
                    {
                        "contract": "EvaGoalEvidence/v1",
                        "observed_at": "2026-08-24T07:01:00Z",
                        "provider": "goal-test",
                        "provider_revision": "7",
                        "resource": "release-1",
                        "values": {"ready": "yes"},
                    }
                )
                return
            if path.startswith("/v1/effects/"):
                value = effects.get(unquote(path.removeprefix("/v1/effects/")))
            else:
                value = deliveries.get(
                    unquote(path.removeprefix("/v1/deliveries/"))
                )
            if value is None:
                self.send_response(404)
                self.end_headers()
            else:
                self._json(value)

        def do_POST(self) -> None:  # noqa: N802
            request = json.loads(self.rfile.read(int(self.headers["content-length"])))
            key = request["idempotency_key"]
            if self.path == "/v1/effects":
                counts["effect"] += 1
                content = request["instruction"]
                value = {
                    "artifact_id": "artifact-http",
                    "content": content,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "contract": "EvaGoalArtifact/v1",
                    "created_at": "2026-08-24T07:02:00Z",
                    "media_type": "text/plain",
                }
                effects.setdefault(key, value)
            else:
                counts["delivery"] += 1
                value = {
                    "contract": "EvaGoalDeliveryReceipt/v1",
                    "reason": None,
                    "recipient": request["recipient"],
                    "status": "accepted",
                }
                deliveries.setdefault(key, value)
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
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        connector = HttpDurableGoalConnector(
            provider="goal-test",
            base_url=f"http://127.0.0.1:{server.server_port}",
            allow_insecure_http=True,
        )
        wake = GoalWakeCondition(
            "goal-test",
            "release-1",
            "ready",
            "yes",
            NOW + timedelta(hours=1),
        )
        goal = DurableGoalSpec(
            "goal-http",
            "jaap",
            "Maak één resultaat",
            "Maak de checklist",
            None,
            None,
            None,
            "sink-a",
            wake,
            NOW,
        )
        evidence = asyncio.run(connector.read(wake))
        assert evidence.values == {"ready": "yes"}
        assert asyncio.run(
            connector.readback(goal=goal, idempotency_key="goal-effect:key")
        ) is None
        artifact = asyncio.run(
            connector.execute(
                goal=goal,
                instruction=goal.primary_instruction,
                evidence=evidence,
                idempotency_key="goal-effect:key",
            )
        )
        assert asyncio.run(
            connector.readback(goal=goal, idempotency_key="goal-effect:key")
        ) == artifact
        assert asyncio.run(
            connector.readback(
                recipient="sink-a", idempotency_key="goal-delivery:key"
            )
        ) is None
        receipt = asyncio.run(
            connector.deliver(
                recipient="sink-a",
                content=artifact.content,
                content_sha256=artifact.content_sha256,
                idempotency_key="goal-delivery:key",
            )
        )
        assert receipt == ("accepted", None)
        assert asyncio.run(
            connector.readback(
                recipient="sink-a", idempotency_key="goal-delivery:key"
            )
        ) == receipt
        assert counts == {"effect": 1, "delivery": 1}
    finally:
        server.shutdown()
        server.server_close()


def test_http_goal_connector_rejects_plain_http_without_explicit_test_flag() -> None:
    with pytest.raises(ValueError, match="not admitted"):
        HttpDurableGoalConnector(
            provider="goal-test", base_url="http://127.0.0.1:8091"
        )
