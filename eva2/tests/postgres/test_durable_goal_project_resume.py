from __future__ import annotations

from contextlib import contextmanager
import asyncio
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Thread
from urllib.parse import unquote, urlparse

import pytest

from eva.adapters.api.app import _durable_goal_payload
from eva.adapters.connectors.durable_goal_http import HttpDurableGoalConnector
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.durable_goals import DurableGoalSpec, DurableGoalWorker, GoalArtifact, GoalEvidence, GoalWakeCondition
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


NOW = datetime.now(UTC).replace(microsecond=0)


@contextmanager
def provider(path: Path):
    """Real loopback HTTP; receipts and request counts survive server replacement."""
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, payload TEXT, request TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS posts (kind TEXT, key TEXT)")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/v1/evidence":
                self.send(200, GoalEvidence("project-test", "release", {"ready": "yes"}, NOW, "1").to_payload())
                return
            key = unquote(route.rsplit("/", 1)[-1])
            with sqlite3.connect(path) as db:
                row = db.execute("SELECT payload FROM receipts WHERE key=?", (key,)).fetchone()
            self.send(404 if row is None else 200, {} if row is None else json.loads(row[0]))

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = request["idempotency_key"]
            if self.path == "/v1/effects":
                previous = request.get("previous_artifact")
                content = "First result: 21" if previous is None else (
                    "Final result using: " + GoalArtifact.from_payload(previous).content
                )
                payload = GoalArtifact(key, content, "text/plain", NOW).to_payload()
                kind = "first" if previous is None else "second"
            else:
                assert self.path == "/v1/deliveries"
                kind = "delivery"
                payload = {"contract": "EvaGoalDeliveryReceipt/v1", "recipient": request["recipient"],
                           "status": "accepted", "reason": None}
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO posts VALUES (?,?)", (kind, key))
                row = db.execute("SELECT payload, request FROM receipts WHERE key=?", (key,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO receipts VALUES (?,?,?)", (key, json.dumps(payload), json.dumps(request, sort_keys=True)))
                else:
                    assert row[1] == json.dumps(request, sort_keys=True)
                    payload = json.loads(row[0])
            self.send(200, payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def store(engine):
    return PostgresDurableGoalStore(engine, tenant_id="project-test", installation_id="isolated")


def goal(name):
    return DurableGoalSpec(name, "owner", "Two dependent steps", "Compute source", None, None, None,
                           "sink", GoalWakeCondition("project-test", "release", "ready", "yes", NOW + timedelta(hours=1)),
                           NOW, followup_instruction="Build final result from previous artifact")


def run(engine, url, name, mode="normal"):
    result = subprocess.run([sys.executable, "-m", "tests.support.durable_goal_project_probe",
                             engine.url.render_as_string(hide_password=False), url, name, mode],
                            capture_output=True, text=True, timeout=30)
    expected = {"after-first-checkpoint": 71, "after-first-effect": 72,
                "after-second-effect": 73, "after-delivery": 74}.get(mode, 0)
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("crash", ["after-first-checkpoint", "after-first-effect", "after-second-effect", "after-delivery"])
def test_real_process_restart_recovers_only_missing_work(timeline_engine, tmp_path, crash):
    spec = goal("resume-" + crash)
    repository = store(timeline_engine)
    repository.admit(spec)
    receipts = tmp_path / "receipts.sqlite"
    with provider(receipts) as url:
        run(timeline_engine, url, spec.goal_id, crash)
    checkpoint = store(timeline_engine).state(spec.goal_id, "owner")
    assert checkpoint.status == "waiting"
    if crash != "after-first-effect":
        assert checkpoint.first_artifact.content == "First result: 21"
    if crash == "after-first-checkpoint":
        assert checkpoint.completed_steps == 1
        assert checkpoint.artifact is None
    # Both worker process and HTTP server are replaced; provider receipts remain durable.
    with provider(receipts) as url:
        run(timeline_engine, url, spec.goal_id)
        run(timeline_engine, url, spec.goal_id)
    state = store(timeline_engine).state(spec.goal_id, "owner")
    assert state.status == "completed"
    assert state.completed_steps == 2
    assert state.effect_dispatch_reservations == 2
    assert state.first_artifact.content == "First result: 21"
    assert state.artifact.content == "Final result using: First result: 21"
    assert state.delivery_status == "accepted"
    assert store(timeline_engine).state(spec.goal_id, "other-owner") is None
    with sqlite3.connect(receipts) as db:
        assert dict(db.execute("SELECT kind, count(*) FROM posts GROUP BY kind")) == {"first": 1, "second": 1, "delivery": 1}
        requests = [json.loads(row[0]) for row in db.execute("SELECT request FROM receipts")]
    second = next(row for row in requests if "previous_artifact" in row)
    assert second["previous_artifact"] == state.first_artifact.to_payload()
    delivery = next(row for row in requests if "recipient" in row)
    assert delivery["content"] == state.artifact.content
    payload = _durable_goal_payload(state)
    assert payload["completed_steps"] == payload["total_steps"] == 2
    assert payload["usage"] == {"effect_dispatch_reservations": 2, "model_tokens": None, "cost": None}


def test_stop_after_checkpoint_preserves_first_step_and_prevents_second(timeline_engine, tmp_path):
    spec = goal("cancel-project")
    repository = store(timeline_engine)
    repository.admit(spec)
    receipts = tmp_path / "receipts.sqlite"
    with provider(receipts) as url:
        run(timeline_engine, url, spec.goal_id, "after-first-checkpoint")
        repository.cancel(spec.goal_id, "owner", "Stop project", NOW)
        run(timeline_engine, url, spec.goal_id)
    state = store(timeline_engine).state(spec.goal_id, "owner")
    assert state.status == "cancelled"
    assert state.completed_steps == 1
    assert state.first_artifact.content == "First result: 21"
    assert state.artifact is None
    assert state.effect_dispatch_reservations == 1
    assert spec not in repository.waiting(observed_before=NOW + timedelta(hours=1))
    with sqlite3.connect(receipts) as db:
        assert dict(db.execute("SELECT kind, count(*) FROM posts GROUP BY kind")) == {"first": 1}


@pytest.mark.parametrize("boundary", ["second-effect", "delivery"])
def test_stop_during_readback_prevents_next_dispatch(timeline_engine, tmp_path, boundary):
    spec = goal("stop-readback-" + boundary)
    repository = store(timeline_engine)
    repository.admit(spec)
    receipts = tmp_path / "receipts.sqlite"
    with provider(receipts) as url:
        run(timeline_engine, url, spec.goal_id, "after-first-checkpoint")
        connector = HttpDurableGoalConnector(
            provider="project-test", base_url=url, allow_insecure_http=True
        )
        original = connector.readback

        async def readback(**kwargs):
            result = await original(**kwargs)
            is_delivery = kwargs.get("recipient") is not None
            if is_delivery == (boundary == "delivery"):
                repository.cancel(spec.goal_id, "owner", "Stop while waiting", NOW)
            return result

        connector.readback = readback
        state = asyncio.run(
            DurableGoalWorker(repository, connector, connector, connector).on_wake(spec)
        )
    assert state.status == "cancelled"
    assert state.first_artifact.content == "First result: 21"
    with sqlite3.connect(receipts) as db:
        counts = dict(db.execute("SELECT kind, count(*) FROM posts GROUP BY kind"))
    assert counts == ({"first": 1} if boundary == "second-effect" else {"first": 1, "second": 1})
