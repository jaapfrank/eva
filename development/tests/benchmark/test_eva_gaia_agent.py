from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
from typing import Iterator

import pytest


BENCHMARK = Path(__file__).resolve().parents[3] / "development/benchmark"
sys.path.insert(0, str(BENCHMARK))
import run_eva_gaia_agent as adapter  # noqa: E402

GAIA_SCRIPT = BENCHMARK / "run_gaia_development.py"
GAIA_SPEC = importlib.util.spec_from_file_location("gaia_development_for_eva", GAIA_SCRIPT)
assert GAIA_SPEC is not None and GAIA_SPEC.loader is not None
gaia = importlib.util.module_from_spec(GAIA_SPEC)
sys.modules[GAIA_SPEC.name] = gaia
GAIA_SPEC.loader.exec_module(gaia)


def _environment(monkeypatch: pytest.MonkeyPatch, endpoint: str) -> None:
    monkeypatch.setenv("EVA_GAIA_TURN_ENDPOINT", endpoint)
    monkeypatch.setenv("EVA_GAIA_RUN_ID", "gaia-dev-001")
    monkeypatch.setenv("EVA_GAIA_TENANT_ID", "benchmark-tenant")
    monkeypatch.setenv("EVA_GAIA_INSTALLATION_ID", "benchmark-installation")
    monkeypatch.setenv("EVA_GAIA_PRINCIPAL_PREFIX", "benchmark-principal")


@contextmanager
def _core(
    *, correct_reply_hash: bool = True, evidence_memory_ids: list[str] | None = None
) -> Iterator[tuple[str, list[dict[str, object]]]]:
    received: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(size)))
            body = json.dumps(
                {
                    "contract": "EvaInternalGroundedReply/v1",
                    "conversation_id": received[-1]["conversation_id"],
                    "evidence_memory_ids": (
                        [] if evidence_memory_ids is None else evidence_memory_ids
                    ),
                    "generated_at": "2026-08-26T12:00:00Z",
                    "message_id": received[-1]["message_id"],
                    "provider": "test",
                    "text": "42",
                    "text_sha256": (
                        hashlib.sha256(b"42").hexdigest()
                        if correct_reply_hash
                        else "0" * 64
                    ),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/internal/v1/turns/respond", received
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_submits_one_isolated_private_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    with _core() as (endpoint, received):
        _environment(monkeypatch, endpoint)
        answer = adapter.answer_for_task(
            {"attachment_path": None, "question": "Wat is zes maal zeven?", "task_id": "task-1"}
        )

    assert answer == "42"
    assert received == [
        {
            "audience": {
                "audience_id": "person:benchmark-principal:7afaa346b4bf92bf9dc21e9a",
                "kind": "private_person",
                "principal_id": "benchmark-principal:7afaa346b4bf92bf9dc21e9a",
            },
            "conversation_id": "gaia:gaia-dev-001:task:7afaa346b4bf92bf9dc21e9a",
            "installation_id": "benchmark-installation",
            "message_id": "gaia:gaia-dev-001:task:7afaa346b4bf92bf9dc21e9a:turn:1",
            "occurred_at": received[0]["occurred_at"],
            "principal_id": "benchmark-principal:7afaa346b4bf92bf9dc21e9a",
            "tenant_id": "benchmark-tenant",
            "text": "Wat is zes maal zeven?",
        }
    ]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/internal/v1/turns/respond",
        "http://core.eva.internal/internal/v1/turns/respond",
        "http://127.0.0.1/not-eva",
        "http://user:password@127.0.0.1/internal/v1/turns/respond",
    ],
)
def test_rejects_non_isolated_turn_endpoints(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    _environment(monkeypatch, endpoint)

    with pytest.raises(adapter.GaiaEvaAgentError, match="isolated Eva turn route"):
        adapter.answer_for_task(
            {"attachment_path": None, "question": "Een vraag", "task_id": "task-2"}
        )


def test_rejects_attachment_value_that_is_not_gaia_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")

    with pytest.raises(adapter.GaiaEvaAgentError, match="GAIA task values are invalid"):
        adapter.answer_for_task(
            {"attachment_path": 3, "question": "Een vraag", "task_id": "task-3"}
        )


def test_rejects_a_reply_that_is_not_bound_to_its_text_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _core(correct_reply_hash=False) as (endpoint, _received):
        _environment(monkeypatch, endpoint)
        with pytest.raises(adapter.GaiaEvaAgentError, match="answer is invalid"):
            adapter.answer_for_task(
                {"attachment_path": None, "question": "Een vraag", "task_id": "task-hash"}
            )


def test_rejects_a_reply_that_uses_existing_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _core(evidence_memory_ids=["memory:unrelated"]) as (endpoint, _received):
        _environment(monkeypatch, endpoint)
        with pytest.raises(adapter.GaiaEvaAgentError, match="answer is invalid"):
            adapter.answer_for_task(
                {"attachment_path": None, "question": "Een vraag", "task_id": "task-memory"}
            )


def test_runs_through_the_existing_gaia_development_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text(
        json.dumps(
            {
                "Final answer": "this must remain unseen",
                "Question": "Wat is zes maal zeven?",
                "file_path": None,
                "task_id": "task-4",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    output = tmp_path / "run"
    with _core() as (endpoint, received):
        _environment(monkeypatch, endpoint)
        receipt = gaia.run(
            metadata_path=metadata,
            attachment_root=attachments,
            output_directory=output,
            maximum_tasks=1,
            timeout_seconds=10,
            command=[sys.executable, str(BENCHMARK / "run_eva_gaia_agent.py")],
        )

    assert receipt["officialScore"] is None
    assert json.loads((output / "answers.jsonl").read_text()) == {
        "model_answer": "42",
        "task_id": "task-4",
    }
    assert received[0]["text"] == "Wat is zes maal zeven?"
    assert "Final answer" not in received[0]["text"]
