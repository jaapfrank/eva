from __future__ import annotations

from contextlib import contextmanager
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
from typing import Iterator

import pytest


BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
sys.path.insert(0, str(BENCHMARK))
import eva_agentbench_client as adapter  # noqa: E402
import run_agentbench_dbbench_development as runner  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def inference_message(
        self, _messages: object, _tools: object
    ) -> dict[str, object]:
        self.calls += 1
        name = "execute_sql" if self.calls == 1 else "commit_final_answer"
        arguments = (
            '{"query":"SELECT 1"}'
            if self.calls == 1
            else '{"answers":["one"]}'
        )
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        }


class FailingClient:
    model_receipts = [{"turn": 1, "toolsExposed": 0}]
    turns_used = runner.MAX_MODEL_TURNS

    def inference_message(self, _messages: object, _tools: object) -> dict[str, object]:
        raise adapter.AgentBenchEvaError("answer is not exact JSON")


class TransientFailingClient(FakeClient):
    model_receipts: list[dict[str, object]] = []

    def __init__(self) -> None:
        super().__init__()
        self.reservations = 0

    @property
    def turns_used(self) -> int:
        return self.reservations

    def inference_message(
        self, messages: object, tools: object
    ) -> dict[str, object]:
        self.reservations += 1
        if self.reservations == 1:
            raise adapter.AgentBenchEvaError("tool-free AgentBench request failed")
        return super().inference_message(messages, tools)


@contextmanager
def _controller() -> Iterator[tuple[str, list[dict[str, object]]]]:
    received: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(size))
            received.append(
                {
                    "path": self.path,
                    "payload": payload,
                    "session_id": self.headers.get("Session_id"),
                }
            )
            if self.path == "/api/start_sample":
                body = {
                    "messages": [
                        {"role": "system", "content": "Use the database."},
                        {"role": "user", "content": "Return one."},
                    ],
                    "tools": adapter._DBBENCH_TOOLS,
                }
                session_id = "7"
            elif len(received) == 2:
                body = {
                    "finish": False,
                    "messages": [
                        {"role": "tool", "tool_call_id": "call_1", "content": "[[1]]"}
                    ],
                    "reward": 0,
                    "status": "running",
                }
                session_id = None
            else:
                body = {
                    "finish": True,
                    "messages": [],
                    "metric": {"score": 1},
                    "reward": 1,
                    "status": "completed",
                }
                session_id = None
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if session_id is not None:
                self.send_header("Session_id", session_id)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api", received
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_runs_two_structured_turns_and_writes_a_private_complete_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    client = FakeClient()
    open_modes: list[int] = []
    real_open = os.open

    def recording_open(path: object, flags: int, mode: int = 0o777) -> int:
        open_modes.append(mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(runner.os, "open", recording_open)
    with _controller() as (controller, received):
        artifact = runner.run_one_case(
            controller=controller,
            case_index=0,
            output=output,
            client=client,  # type: ignore[arg-type]
        )

    assert client.calls == 2
    assert artifact["result"] == {
        "finish": True,
        "metric": {"score": 1},
        "reward": 1,
        "status": "completed",
        "taskResult": None,
    }
    assert len(artifact["initialMessages"]) == 2
    assert artifact["sealedTools"] == adapter._DBBENCH_TOOLS
    assert received[0]["session_id"] is None
    assert received[1]["session_id"] == "7"
    assert received[2]["session_id"] == "7"
    assert set(received[1]["payload"]) == {"messages"}
    assert open_modes == [0o600]
    assert json.loads(output.read_text(encoding="utf-8"))["modelTurnsUsed"] == 2


def test_refuses_to_overwrite_an_existing_result(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    output.write_text("existing", encoding="utf-8")
    with _controller() as (controller, _received):
        with pytest.raises(runner.DbBenchDevelopmentError, match="already exists"):
            runner.run_one_case(
                controller=controller,
                case_index=0,
                output=output,
                client=FakeClient(),  # type: ignore[arg-type]
            )

    assert output.read_text(encoding="utf-8") == "existing"


def test_writes_a_private_sanitized_failure_artifact(tmp_path: Path) -> None:
    output = tmp_path / "failure.json"
    with _controller() as (controller, _received):
        with pytest.raises(
            runner.DbBenchDevelopmentError, match="answer is not exact JSON"
        ):
            runner.run_one_case(
                controller=controller,
                case_index=0,
                output=output,
                client=FailingClient(),  # type: ignore[arg-type]
            )

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["contract"] == "EvaAgentBenchDbBenchDevelopmentFailure/v1"
    assert artifact["failedTurn"] == 1
    assert artifact["modelTurnsCompleted"] == 0
    assert artifact["modelReceipts"] == [{"toolsExposed": 0, "turn": 1}]
    assert artifact["modelFailures"] == [
        {"failure": "answer is not exact JSON", "turn": 1}
    ]
    assert "label" not in output.read_text(encoding="utf-8").lower()


def test_retries_before_interact_within_the_same_hard_model_budget(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    client = TransientFailingClient()
    with _controller() as (controller, received):
        artifact = runner.run_one_case(
            controller=controller,
            case_index=0,
            output=output,
            client=client,  # type: ignore[arg-type]
        )

    assert len(received) == 3
    assert artifact["modelTurnsUsed"] == 3
    assert artifact["modelFailures"] == [
        {"failure": "tool-free AgentBench request failed", "turn": 1}
    ]
    assert [item["turn"] for item in artifact["trajectory"]] == [2, 3]


@pytest.mark.parametrize(
    "controller",
    ["https://127.0.0.1/api", "http://example.com/api", "http://127.0.0.1/wrong"],
)
def test_rejects_nonprivate_or_wrong_controller_routes(
    controller: str, tmp_path: Path
) -> None:
    with pytest.raises(runner.DbBenchDevelopmentError, match="controller"):
        runner.run_one_case(
            controller=controller,
            case_index=0,
            output=tmp_path / "unused.json",
            client=FakeClient(),  # type: ignore[arg-type]
        )
