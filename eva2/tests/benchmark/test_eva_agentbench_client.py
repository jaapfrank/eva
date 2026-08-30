from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
from typing import Iterator

import pytest


BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
sys.path.insert(0, str(BENCHMARK))
import eva_agentbench_client as adapter  # noqa: E402


HISTORY = [
    {"content": "Open het gereedschap dat de omgeving aanbiedt.", "role": "user"},
    {"content": "Ik gebruik het beschikbare gereedschap.", "role": "agent"},
    {"content": "Geef nu de volgende stap.", "role": "user"},
]


def _environment(monkeypatch: pytest.MonkeyPatch, endpoint: str) -> None:
    monkeypatch.setenv("EVA_AGENTBENCH_TURN_ENDPOINT", endpoint)
    monkeypatch.setenv("EVA_AGENTBENCH_RUN_ID", "agentbench-dev-001")
    monkeypatch.setenv("EVA_AGENTBENCH_MAX_TURNS", "2")
    monkeypatch.setenv("EVA_AGENTBENCH_TENANT_ID", "benchmark-tenant")
    monkeypatch.setenv("EVA_AGENTBENCH_INSTALLATION_ID", "benchmark-installation")
    monkeypatch.setenv("EVA_AGENTBENCH_PRINCIPAL_PREFIX", "benchmark-principal")


@contextmanager
def _core(
    *,
    answer: str = 'browser_navigate({"url":"https://example.test"})',
    evidence_memory_ids: list[str] | None = None,
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
                    "generated_at": "2026-08-27T11:00:00Z",
                    "message_id": received[-1]["message_id"],
                    "provider": "test",
                    "text": answer,
                    "text_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
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


def test_submits_complete_history_in_a_fresh_isolated_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _core() as (endpoint, received):
        _environment(monkeypatch, endpoint)
        result = adapter.answer_for_history(HISTORY)

    assert result == 'browser_navigate({"url":"https://example.test"})'
    assert received[0]["text"].endswith(
        json.dumps(HISTORY, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    assert received[0]["conversation_id"] == received[0]["message_id"].removesuffix(
        ":message:1"
    )
    assert received[0]["principal_id"] in received[0]["audience"]["audience_id"]


def test_each_changed_transcript_gets_a_different_eva_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    next_history = [*HISTORY, {"content": "Volgende omgevingsantwoord.", "role": "user"}]
    with _core() as (endpoint, received):
        _environment(monkeypatch, endpoint)
        adapter.answer_for_history(HISTORY)
        adapter.answer_for_history(next_history)

    assert received[0]["conversation_id"] != received[1]["conversation_id"]
    assert received[0]["principal_id"] != received[1]["principal_id"]


def test_client_reserves_a_hard_turn_budget_before_calling_eva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _core() as (endpoint, received):
        _environment(monkeypatch, endpoint)
        client = adapter.EvaAgentBenchClient(timeout_seconds=10)
        client.inference(HISTORY)
        client.inference(HISTORY)
        with pytest.raises(adapter.AgentBenchEvaError, match="turn budget is exhausted"):
            client.inference(HISTORY)

    assert len(received) == 2


def test_returns_one_closed_agentrl_dbbench_tool_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = adapter._DBBENCH_TOOLS
    messages = [
        {"role": "system", "content": "Use the supplied database tools."},
        {"role": "user", "content": "Return the number of rows."},
    ]
    answer = '{"name":"execute_sql","arguments":{"query":"SELECT COUNT(*) FROM t"}}'
    with _core(answer=answer) as (endpoint, received):
        _environment(monkeypatch, endpoint)
        result = adapter.EvaAgentBenchClient(timeout_seconds=10).inference_message(
            messages, tools
        )

    assert result["role"] == "assistant"
    assert result["content"] is None
    tool_call = result["tool_calls"][0]
    assert tool_call["type"] == "function"
    assert tool_call["function"] == {
        "name": "execute_sql",
        "arguments": '{"query":"SELECT COUNT(*) FROM t"}',
    }
    wrapped_history = json.loads(received[0]["text"].split("\n", 1)[1])
    assert '"database_transcript"' in wrapped_history[0]["content"]
    assert '"execute_sql"' in wrapped_history[0]["content"]


@pytest.mark.parametrize(
    "answer,error",
    [
        ('```json\n{"name":"execute_sql","arguments":{"query":"SELECT 1"}}\n```', "not exact JSON"),
        ('{"name":"drop_database","arguments":{}}', "unsealed DBBench tool"),
        ('{"name":"execute_sql","arguments":{"query":"SELECT 1","extra":true}}', "SQL arguments are invalid"),
    ],
)
def test_rejects_nonexact_or_unsealed_dbbench_tool_answers(
    monkeypatch: pytest.MonkeyPatch, answer: str, error: str
) -> None:
    with _core(answer=answer) as (endpoint, _received):
        _environment(monkeypatch, endpoint)
        with pytest.raises(adapter.AgentBenchEvaError, match=error):
            adapter.EvaAgentBenchClient(timeout_seconds=10).inference_message(
                [{"role": "user", "content": "Do the task."}],
                adapter._DBBENCH_TOOLS,
            )


def test_structured_and_legacy_calls_share_the_same_reserved_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = '{"name":"commit_final_answer","arguments":{"answers":["one"]}}'
    with _core(answer=answer) as (endpoint, received):
        _environment(monkeypatch, endpoint)
        client = adapter.EvaAgentBenchClient(timeout_seconds=10)
        client.inference_message(
            [{"role": "user", "content": "Finish."}], adapter._DBBENCH_TOOLS
        )
        client.inference_message(
            [{"role": "user", "content": "Finish."}], adapter._DBBENCH_TOOLS
        )
        with pytest.raises(adapter.AgentBenchEvaError, match="turn budget is exhausted"):
            client.inference(HISTORY)

    assert len(received) == 2


def test_neutralizes_prior_tool_messages_before_calling_eva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = '{"name":"commit_final_answer","arguments":{"answers":["done"]}}'
    messages = [
        {"role": "system", "content": "Use the database."},
        {"role": "user", "content": "Find the value."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_one",
                    "type": "function",
                    "function": {
                        "name": "execute_sql",
                        "arguments": '{"query":"SELECT 1"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_one", "content": "[[1]]"},
    ]
    with _core(answer=answer) as (endpoint, received):
        _environment(monkeypatch, endpoint)
        adapter.EvaAgentBenchClient(timeout_seconds=10).inference_message(
            messages, adapter._DBBENCH_TOOLS
        )

    wrapped_history = json.loads(received[0]["text"].split("\n", 1)[1])
    model_prompt = wrapped_history[0]["content"]
    assert '"kind":"database_operation"' in model_prompt
    assert '"kind":"database_result"' in model_prompt
    assert '"tool_calls"' not in model_prompt


@pytest.mark.parametrize("turn_limit", ["0", "9", "nope"])
def test_rejects_an_invalid_turn_budget(
    monkeypatch: pytest.MonkeyPatch, turn_limit: str
) -> None:
    _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
    monkeypatch.setenv("EVA_AGENTBENCH_MAX_TURNS", turn_limit)

    with pytest.raises(adapter.AgentBenchEvaError, match="MAX_TURNS is invalid"):
        adapter.EvaAgentBenchClient(timeout_seconds=10)


def test_rejects_non_isolated_turn_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _environment(monkeypatch, "https://example.com/internal/v1/turns/respond")

    with pytest.raises(adapter.AgentBenchEvaError, match="isolated Eva turn route"):
        adapter.answer_for_history(HISTORY)


@pytest.mark.parametrize(
    "history",
    [
        [],
        [{"content": "x", "role": "system"}],
        [{"content": "x", "role": "user", "tool": "forbidden"}],
        [{"content": "", "role": "user"}],
    ],
)
def test_rejects_an_ambiguous_or_unsupported_history(
    monkeypatch: pytest.MonkeyPatch, history: object
) -> None:
    _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")

    with pytest.raises(adapter.AgentBenchEvaError, match="AgentBench history"):
        adapter.answer_for_history(history)


def test_rejects_a_reply_that_uses_existing_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _core(evidence_memory_ids=["memory:unrelated"]) as (endpoint, _received):
        _environment(monkeypatch, endpoint)
        with pytest.raises(adapter.AgentBenchEvaError, match="answer is invalid"):
            adapter.EvaAgentBenchClient(timeout_seconds=10).inference(HISTORY)
