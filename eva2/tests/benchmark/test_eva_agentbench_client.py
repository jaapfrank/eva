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


@contextmanager
def _tool_free_core(
    *, answer: str | list[str], measured_usage: bool = True
) -> Iterator[tuple[str, list[dict[str, object]]]]:
    received: list[dict[str, object]] = []
    answers = [answer] if isinstance(answer, str) else answer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(size)))
            selected_answer = answers[min(len(received) - 1, len(answers) - 1)]
            body = json.dumps(
                {
                    "contract": "EvaToolFreeModelReply/v1",
                    "provider": "chatgpt-codex:test-model",
                    "text": selected_answer,
                    "text_sha256": hashlib.sha256(selected_answer.encode()).hexdigest(),
                    "timing": {"completed_ms": 20, "first_content_ms": 10},
                    "tools_exposed": 0,
                    "usage": (
                        {
                            "input_tokens": 12,
                            "measurement": "measured",
                            "model_attempts": 1,
                            "output_tokens": 8,
                            "tool_calls": 0,
                        }
                        if measured_usage
                        else {
                            "input_tokens": None,
                            "measurement": "unmeasured",
                            "model_attempts": None,
                            "output_tokens": None,
                            "tool_calls": None,
                        }
                    ),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/generate", received
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


def test_client_spaces_reserved_provider_starts_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
    ticks = iter([100.0, 100.0, 102.0, 105.0])
    sleeps: list[float] = []
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(adapter.time, "sleep", sleeps.append)
    client = adapter.EvaAgentBenchClient(
        minimum_interval_seconds=5.0, timeout_seconds=10
    )

    assert client._reserve_turn() == 1
    assert client._reserve_turn() == 2
    assert sleeps == [3.0]


@pytest.mark.parametrize("interval", [-0.1, 60.1])
def test_rejects_an_invalid_provider_call_interval(
    monkeypatch: pytest.MonkeyPatch, interval: float
) -> None:
    _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
    with pytest.raises(adapter.AgentBenchEvaError, match="call interval is invalid"):
        adapter.EvaAgentBenchClient(
            minimum_interval_seconds=interval, timeout_seconds=10
        )


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
    assert "database operation 1; provider call 1 of at most 2" in wrapped_history[0]["content"]


def test_tool_free_client_uses_private_sidecar_and_records_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = adapter._DBBENCH_TOOLS
    messages = [
        {"role": "system", "content": "Use the supplied database tools."},
        {"role": "user", "content": "Return the number of rows."},
    ]
    answer = '{"name":"execute_sql","arguments":{"query":"SELECT COUNT(*) FROM t"}}'
    with _tool_free_core(answer=answer) as (endpoint, received):
        _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
        monkeypatch.setenv("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT", endpoint)
        client = adapter.EvaToolFreeAgentBenchClient(timeout_seconds=10)
        result = client.inference_message(messages, tools)

    assert result["tool_calls"][0]["function"]["name"] == "execute_sql"
    assert received == [
        {
            "contract": "EvaToolFreeModelRequest/v1",
            "prompt": received[0]["prompt"],
        }
    ]
    assert "database_transcript" in received[0]["prompt"]
    assert client.model_receipts == [
        {
            "provider": "chatgpt-codex:test-model",
            "textSha256": hashlib.sha256(answer.encode()).hexdigest(),
            "timing": {"completed_ms": 20, "first_content_ms": 10},
            "toolsExposed": 0,
            "turn": 1,
            "usage": {
                "input_tokens": 12,
                "measurement": "measured",
                "model_attempts": 1,
                "output_tokens": 8,
                "tool_calls": 0,
            },
        }
    ]


def test_tool_free_client_reviews_a_commit_inside_the_same_call_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {"role": "user", "content": "Which value is largest?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_one",
                    "type": "function",
                    "function": {
                        "name": "execute_sql",
                        "arguments": '{"query":"SELECT name, value FROM t"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_one",
            "content": "[('row name', '42')]",
        },
    ]
    proposed = '{"name":"commit_final_answer","arguments":{"answers":["row name"]}}'
    corrected = (
        '{"name":"execute_sql","arguments":'
        '{"query":"SELECT value FROM t ORDER BY value DESC LIMIT 1"}}'
    )
    with _tool_free_core(answer=[proposed, corrected]) as (endpoint, received):
        _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
        monkeypatch.setenv("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT", endpoint)
        monkeypatch.setenv("EVA_AGENTBENCH_MAX_TURNS", "3")
        client = adapter.EvaToolFreeAgentBenchClient(
            minimum_interval_seconds=0, timeout_seconds=10
        )
        result = client.inference_message(messages, adapter._DBBENCH_TOOLS)

    assert result["tool_calls"][0]["function"] == {
        "name": "execute_sql",
        "arguments": '{"query":"SELECT value FROM t ORDER BY value DESC LIMIT 1"}',
    }
    assert len(received) == 2
    assert "proposed_operation" in received[1]["prompt"]
    assert "hidden label" in received[1]["prompt"]
    assert client.turns_used == 2
    assert [item["turn"] for item in client.model_receipts] == [1, 2]
    assert client.commit_reviews_used == 1
    assert client.review_failures == []


def test_invalid_commit_review_falls_back_to_the_valid_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = '{"name":"commit_final_answer","arguments":{"answers":["42"]}}'
    with _tool_free_core(answer=[candidate, "not json"]) as (endpoint, received):
        _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
        monkeypatch.setenv("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT", endpoint)
        monkeypatch.setenv("EVA_AGENTBENCH_MAX_TURNS", "3")
        client = adapter.EvaToolFreeAgentBenchClient(
            minimum_interval_seconds=0, timeout_seconds=10
        )
        result = client.inference_message(
            [{"role": "user", "content": "Return the observed value."}],
            adapter._DBBENCH_TOOLS,
        )

    assert result["tool_calls"][0]["function"] == {
        "name": "commit_final_answer",
        "arguments": '{"answers":["42"]}',
    }
    assert len(received) == 2
    assert client.turns_used == 2
    assert client.commit_reviews_used == 1
    assert client.review_failures == [
        {"failure": "Eva DBBench tool answer is not exact JSON", "turn": 2}
    ]


def test_commit_review_is_limited_to_two_calls_per_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = '{"name":"commit_final_answer","arguments":{"answers":["42"]}}'
    with _tool_free_core(answer=candidate) as (endpoint, received):
        _environment(monkeypatch, "http://127.0.0.1:1/internal/v1/turns/respond")
        monkeypatch.setenv("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT", endpoint)
        monkeypatch.setenv("EVA_AGENTBENCH_MAX_TURNS", "6")
        client = adapter.EvaToolFreeAgentBenchClient(
            minimum_interval_seconds=0, timeout_seconds=10
        )
        for _ in range(3):
            result = client.inference_message(
                [{"role": "user", "content": "Return the observed value."}],
                adapter._DBBENCH_TOOLS,
            )
            assert result["tool_calls"][0]["function"]["name"] == (
                "commit_final_answer"
            )

    assert len(received) == 5
    assert client.turns_used == 5
    assert client.commit_reviews_used == 2


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:8090/v1/generate",
        "http://example.com:8090/v1/generate",
        "http://127.0.0.1:8090/wrong",
    ],
)
def test_tool_free_client_rejects_non_private_or_wrong_route(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    monkeypatch.setenv("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT", endpoint)
    with pytest.raises(adapter.AgentBenchEvaError, match="TOOL_FREE_ENDPOINT"):
        adapter.tool_free_answer("valid prompt", timeout_seconds=10)


def test_tool_free_client_accepts_honestly_unmeasured_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = '{"name":"commit_final_answer","arguments":{"answers":["7"]}}'
    with _tool_free_core(answer=answer, measured_usage=False) as (endpoint, _received):
        monkeypatch.setenv("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT", endpoint)
        text, receipt = adapter.tool_free_answer("valid prompt", timeout_seconds=10)

    assert text == answer
    assert receipt["toolsExposed"] == 0
    assert receipt["usage"]["measurement"] == "unmeasured"
    assert receipt["usage"]["tool_calls"] is None


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


@pytest.mark.parametrize(
    "answer,error",
    [
        ('{"name":"commit_final_answer","arguments":[]}', "shape is invalid"),
        (
            '{"name":"commit_final_answer","arguments":{"answers":"7"}}',
            "list is invalid",
        ),
        (
            '{"name":"commit_final_answer","arguments":{"answers":[]}}',
            "count is invalid",
        ),
        (
            '{"name":"commit_final_answer","arguments":{"answers":[7]}}',
            "numeric item is invalid",
        ),
        (
            '{"name":"commit_final_answer","arguments":{"answers":[true]}}',
            "item type is invalid",
        ),
        (
            '{"name":"commit_final_answer","arguments":{"answers":[""]}}',
            "empty item is invalid",
        ),
    ],
)
def test_reports_private_final_answer_failure_subtypes(
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
    second_history = json.loads(received[1]["text"].split("\n", 1)[1])
    assert "database operation 1; provider call 2 of at most 2" in second_history[0]["content"]


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
    assert "schema and row count" in model_prompt
    assert "across every relevant text column" in model_prompt
    assert "exact answer column or columns" in model_prompt
    assert "Words such as name, tell, or give are verbs" in model_prompt
    assert "Select only the requested answer columns" in model_prompt
    assert "Use SUM, COUNT, or AVG only when" in model_prompt
    assert "each selected answer cell as its own list item" in model_prompt
    assert "add no labels, explanation, units, or prose" in model_prompt


@pytest.mark.parametrize("turn_limit", ["0", "16", "nope"])
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
