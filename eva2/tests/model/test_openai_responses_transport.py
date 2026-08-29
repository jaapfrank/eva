from __future__ import annotations

from contextlib import contextmanager
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic as wall_monotonic, sleep
import traceback
from typing import Any, Callable, Iterator

import pytest

pytest.importorskip("openai", reason="DF02a candidate dependency is not in production")

import eva.bootstrap  # noqa: F401  # Load root before its model submodule.
import eva.adapters.model.conversation as conversation_module
from eva.adapters.model.conversation import (
    CodexConversationModel,
    ConversationModelError,
)
from eva.adapters.model.openai_responses_transport import (
    OpenAIResponsesResult,
    OpenAIResponsesTransport,
    OpenAIResponsesTransportError,
    OpenAIResponsesUnavailable,
)
from eva.application.conversation import (
    ConversationModelUsage,
    ConversationTurn,
    IdentityProfile,
    load_current_identity_pack,
)
from eva.application.memory import PresenceMemory
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall


NOW = "2026-08-25T12:00:00Z"


@pytest.fixture(autouse=True)
def local_model_lease_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation_module, "_MODEL_LEASE_OWNER_UID", os.geteuid())
    monkeypatch.setattr(conversation_module, "_MODEL_LEASE_READER_GID", os.getegid())
    monkeypatch.setattr(
        conversation_module,
        "read_shared_runtime_file_bytes",
        lambda path, **_kwargs: Path(path).read_bytes(),
    )


def _completion(
    *,
    input_tokens: int = 11,
    output_tokens: int = 7,
    total_tokens: int | None = None,
    usage: bool = True,
) -> dict[str, Any]:
    usage_value = (
        {
            "input_tokens": input_tokens,
            "input_tokens_details": {
                "cache_write_tokens": 0,
                "cached_tokens": 0,
            },
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": (
                input_tokens + output_tokens
                if total_tokens is None
                else total_tokens
            ),
        }
        if usage
        else None
    )
    return {
        "response": {
            "created_at": 1_777_111_200.0,
            "id": "resp_eva_candidate_1",
            "model": "gpt-5.4-mini",
            "object": "response",
            "output": [],
            "parallel_tool_calls": False,
            "status": "completed",
            "tool_choice": "auto",
            "tools": [],
            "usage": usage_value,
        },
        "sequence_number": 3,
        "type": "response.completed",
    }


def _text_events(text: str = "Dat werkt.") -> tuple[dict[str, Any], ...]:
    return (
        {
            "content_index": 0,
            "delta": text,
            "item_id": "message_eva_1",
            "logprobs": [],
            "output_index": 0,
            "sequence_number": 2,
            "type": "response.output_text.delta",
        },
        _completion(),
    )


def _function_events() -> tuple[dict[str, Any], ...]:
    return (
        {
            "item": {
                "arguments": '{"entity_kind":"light","target":"keuken"}',
                "call_id": "call_eva_1",
                "name": "read_home_assistant_state",
                "type": "function_call",
            },
            "output_index": 0,
            "sequence_number": 2,
            "type": "response.output_item.done",
        },
        _completion(input_tokens=13, output_tokens=4),
    )


def _sse(events: tuple[dict[str, Any], ...]) -> bytes:
    frames = [
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ]
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()


@contextmanager
def _server(
    outcomes: list[tuple[int, bytes, dict[str, str]]],
    *,
    on_request: Callable[[int], None] | None = None,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    captured: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            captured.append(
                {
                    "body": json.loads(raw),
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "path": self.path,
                }
            )
            if on_request is not None:
                on_request(len(captured))
            status, body, headers = outcomes[min(len(captured) - 1, len(outcomes) - 1)]
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/backend-api/codex/responses", captured
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _outcome(events: tuple[dict[str, Any], ...]) -> tuple[int, bytes, dict[str, str]]:
    return 200, _sse(events), {"Content-Type": "text/event-stream"}


@contextmanager
def _slow_server(
    *, header_delay: float, body_delay: float
) -> Iterator[str]:
    body = _sse(_text_events())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(size)
            sleep(header_delay)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.flush()
            sleep(body_delay)
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/backend-api/codex/responses"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _write_lease(path: Path, *, token: str, account_id: str = "account-one") -> None:
    replacement = path.with_suffix(".new")
    replacement.write_text(
        json.dumps(
            {
                "access_token": token,
                "account_id": account_id,
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": canonical_sha256(
                    {"access_token": token, "account_id": account_id}
                ),
            }
        )
    )
    replacement.chmod(0o640)
    replacement.replace(path)


def _memory() -> PresenceMemory:
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="recall:openai-sdk-candidate",
        query_purpose="conversation_reply",
        tenant_id="tenant",
        principal_id="jaap",
        conversation_id="conversation",
        project_id=None,
        requested_valid_at=NOW,
        admitted_fact_ids_sha256=canonical_sha256([]),
        selected=[],
        excluded_reasons={},
        projection_version="hindsight-0.9.1-v1",
        scope_sha256=canonical_sha256({"kind": "person"}),
        created_at=NOW,
    )
    return PresenceMemory(recall, (), None)


def _model(endpoint: str, lease: Path) -> CodexConversationModel:
    return CodexConversationModel(
        auth_file=lease,
        endpoint=endpoint,
        transport_kind="openai_sdk",
    )


def _generate(model: CodexConversationModel, *, actions: tuple[str, ...] = ()):
    return model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-openai-sdk",
            "Hoe gaat het?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=actions,
    )


def _transport_body() -> dict[str, Any]:
    return {
        "include": [],
        "input": [{"content": "Hoi", "role": "user", "type": "message"}],
        "instructions": "Antwoord kort.",
        "model": "gpt-5.4-mini",
        "parallel_tool_calls": False,
        "prompt_cache_key": "eva-test",
        "reasoning": {"effort": "low", "summary": "auto"},
        "store": False,
        "stream": True,
        "tool_choice": "none",
        "tools": [],
    }


def test_sdk_route_preserves_headers_body_text_and_reported_usage(tmp_path: Path) -> None:
    with _server([_outcome(_text_events())]) as (endpoint, captured):
        lease = tmp_path / "lease.json"
        _write_lease(lease, token="access-one")

        result = _generate(_model(endpoint, lease))

    assert result.text == "Dat werkt."
    assert result.action_call is None
    assert result.usage.measurement == "measured"
    assert result.usage.model_attempts == 1
    assert result.usage.tool_calls == 0
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    request = captured[0]
    assert request["path"] == "/backend-api/codex/responses"
    assert request["body"]["store"] is False
    assert request["body"]["stream"] is True
    assert request["body"]["parallel_tool_calls"] is False
    assert request["headers"]["authorization"] == "Bearer access-one"
    assert request["headers"]["accept-encoding"] == "identity"
    assert request["headers"]["openai-account-id"] == "account-one"
    assert request["headers"]["openai-beta"] == "responses=experimental"
    assert request["headers"]["origin"] == "https://chatgpt.com"
    assert request["headers"]["originator"] == "codex_cli_rs"
    assert request["headers"]["session_id"]


def test_sdk_route_returns_one_tool_request_without_executing_it(tmp_path: Path) -> None:
    with _server([_outcome(_function_events())]) as (endpoint, _captured):
        lease = tmp_path / "lease.json"
        _write_lease(lease, token="access-one")

        result = _generate(
            _model(endpoint, lease), actions=("read_home_assistant_state",)
        )

    assert result.text is None
    assert result.action_call is not None
    assert result.action_call.name == "read_home_assistant_state"
    assert result.action_call.arguments == {
        "entity_kind": "light",
        "target": "keuken",
    }
    assert result.usage.tool_calls == 1


def test_sdk_route_reloads_one_rotated_atomic_lease_pair_after_401(
    tmp_path: Path,
) -> None:
    lease = tmp_path / "lease.json"
    _write_lease(lease, token="access-one", account_id="account-one")

    def rotate(call_number: int) -> None:
        if call_number == 1:
            _write_lease(lease, token="access-two", account_id="account-two")

    outcomes = [
        (401, b"", {}),
        _outcome(_text_events("Na rotatie.")),
    ]
    with _server(outcomes, on_request=rotate) as (endpoint, captured):
        result = _generate(_model(endpoint, lease))

    assert result.text == "Na rotatie."
    assert result.usage.model_attempts == 2
    assert [item["headers"]["authorization"] for item in captured] == [
        "Bearer access-one",
        "Bearer access-two",
    ]
    assert [item["headers"]["openai-account-id"] for item in captured] == [
        "account-one",
        "account-two",
    ]


def test_sdk_route_has_no_hidden_retry_for_unchanged_auth_or_server_error(
    tmp_path: Path,
) -> None:
    lease = tmp_path / "lease.json"
    _write_lease(lease, token="access-one")
    with _server([(401, b"", {})]) as (endpoint, rejected):
        with pytest.raises(ConversationModelError, match="credentials were rejected"):
            _generate(_model(endpoint, lease))
    assert len(rejected) == 1

    with _server([(500, b"failure", {})]) as (endpoint, failed):
        with pytest.raises(ConversationModelError, match="HTTP 500"):
            _generate(_model(endpoint, lease))
    assert len(failed) == 1

    with _server([(429, b"slow down", {})]) as (endpoint, limited):
        with pytest.raises(ConversationModelError, match="HTTP 429"):
            _generate(_model(endpoint, lease))
    assert len(limited) == 1


def test_transport_fails_closed_on_redirect_missing_completion_and_size(
    tmp_path: Path,
) -> None:
    body = {
        "include": [],
        "input": [{"content": "Hoi", "role": "user", "type": "message"}],
        "instructions": "Antwoord kort.",
        "model": "gpt-5.4-mini",
        "parallel_tool_calls": False,
        "prompt_cache_key": "eva-test",
        "reasoning": {"effort": "low", "summary": "auto"},
        "store": False,
        "stream": True,
        "tool_choice": "none",
        "tools": [],
    }
    with _server([(307, b"", {"Location": "/elsewhere"})]) as (endpoint, calls):
        transport = OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5)
        with pytest.raises(OpenAIResponsesTransportError):
            transport.request(body, access_token="access", account_id="account")
    assert len(calls) == 1

    incomplete = (_text_events()[0],)
    with _server([_outcome(incomplete)]) as (endpoint, _calls):
        transport = OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5)
        with pytest.raises(OpenAIResponsesTransportError, match="without completion"):
            transport.request(body, access_token="access", account_id="account")

    oversized = _text_events("x" * 700)
    with _server([_outcome(oversized)]) as (endpoint, _calls):
        transport = OpenAIResponsesTransport(
            endpoint=endpoint,
            timeout_seconds=5,
            maximum_response_bytes=256,
        )
        with pytest.raises(OpenAIResponsesTransportError, match="byte limit"):
            transport.request(body, access_token="access", account_id="account")


def test_sdk_endpoint_and_request_contract_are_closed() -> None:
    with pytest.raises(ValueError, match="end in /responses"):
        OpenAIResponsesTransport(
            endpoint="https://example.test/not-responses",
            timeout_seconds=5,
        )
    for endpoint in (
        "http://attacker.example/responses",
        "http://10.0.0.7/responses",
        "http://localhost/responses",
        "https://user:pass@example.test/responses",
        "https://example.test/responses?secret=value",
        "https://example.test/responses#fragment",
    ):
        with pytest.raises(ValueError, match="end in /responses"):
            OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5)

    transport = OpenAIResponsesTransport(
        endpoint="https://example.test/responses", timeout_seconds=5
    )
    with pytest.raises(OpenAIResponsesTransportError, match="unsupported field"):
        transport.request(
            {
                "input": [],
                "model": "gpt-5.4-mini",
                "parallel_tool_calls": False,
                "store": False,
                "stream": True,
                "tools": [],
                "unsafe_extra": "blocked",
            },
            access_token="access",
            account_id="account",
        )

    body = _transport_body()
    body["tools"] = [{"type": "web_search_preview"}]
    with pytest.raises(OpenAIResponsesTransportError, match="contract is invalid"):
        transport.request(body, access_token="access", account_id="account")


@pytest.mark.parametrize(
    "events, message",
    [
        (
            (_function_events()[0], _function_events()[0], _completion()),
            "multiple tool calls",
        ),
        (
            (
                {
                    **_function_events()[0],
                    "item": {**_function_events()[0]["item"], "arguments": "[1]"},
                },
                _completion(),
            ),
            "not an object",
        ),
        (
            (
                {
                    **_function_events()[0],
                    "item": {**_function_events()[0]["item"], "arguments": "{"},
                },
                _completion(),
            ),
            "projection is invalid",
        ),
    ],
)
def test_tool_projection_rejects_ambiguous_or_invalid_calls(
    events: tuple[dict[str, Any], ...], message: str
) -> None:
    with _server([_outcome(events)]) as (endpoint, _calls):
        transport = OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5)
        with pytest.raises(OpenAIResponsesTransportError, match=message):
            transport.request(
                _transport_body(), access_token="access", account_id="account"
            )


@pytest.mark.parametrize(
    "events, message",
    [
        (
            (
                {
                    "code": "provider_failed",
                    "message": "remote detail must not escape",
                    "param": None,
                    "sequence_number": 2,
                    "type": "error",
                },
            ),
            "reported failure",
        ),
        ((_completion(total_tokens=999),), "usage is invalid"),
        ((_completion(), _completion()), "event after completion"),
    ],
)
def test_terminal_event_and_usage_fail_closed(
    events: tuple[dict[str, Any], ...], message: str
) -> None:
    with _server([_outcome(events)]) as (endpoint, _calls):
        transport = OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5)
        with pytest.raises(OpenAIResponsesTransportError, match=message):
            transport.request(
                _transport_body(), access_token="access", account_id="account"
            )


def test_missing_reported_usage_stays_explicitly_unmeasured() -> None:
    events = (_text_events()[0], _completion(usage=False))
    with _server([_outcome(events)]) as (endpoint, _calls):
        result = OpenAIResponsesTransport(
            endpoint=endpoint, timeout_seconds=5
        ).request(_transport_body(), access_token="access", account_id="account")

    assert result.usage == ConversationModelUsage.unmeasured()


def test_language_retry_aggregates_both_successful_sdk_usages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lease = tmp_path / "lease.json"
    _write_lease(lease, token="access-one")
    model = _model("https://example.test/responses", lease)
    results = iter(
        (
            OpenAIResponsesResult(
                "Om 16:29 en nog ერთხელ om 16:35.",
                None,
                ConversationModelUsage.measured(
                    tool_calls=0, input_tokens=10, output_tokens=5
                ),
            ),
            OpenAIResponsesResult(
                "Om 16:29 en nogmaals om 16:35.",
                None,
                ConversationModelUsage.measured(
                    tool_calls=0, input_tokens=12, output_tokens=6
                ),
            ),
        )
    )
    assert model._openai_transport is not None
    monkeypatch.setattr(model._openai_transport, "request", lambda *_a, **_k: next(results))

    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-openai-sdk-language",
            "Hoe laat ging de lamp uit?",
            NOW,
        ),
        IdentityProfile(
            "Eva",
            "Digitale huisgenoot.",
            ("Wees eerlijk.",),
            80,
            load_current_identity_pack(),
        ),
        _memory(),
        (),
    )

    assert result.text == "Om 16:29 en nogmaals om 16:35."
    assert result.usage == ConversationModelUsage.measured(
        model_attempts=2,
        tool_calls=0,
        input_tokens=22,
        output_tokens=11,
    )


def test_transport_does_not_invent_first_text_timing_for_tool_only_stream() -> None:
    with _server([_outcome(_function_events())]) as (endpoint, _calls):
        result = OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5).request(
            _transport_body(), access_token="access", account_id="account"
        )

    assert result.text == ""
    assert result.action_call is not None
    assert result.first_text_delta_seconds is None
    assert result.completed_seconds is not None
    assert result.completed_seconds >= 0


def test_byte_deadline_proxy_and_error_redaction_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with _server([_outcome(_text_events())]) as (endpoint, calls):
        result = OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5).request(
            _transport_body(), access_token="access", account_id="account"
        )
    assert result.text == "Dat werkt."
    assert result.first_text_delta_seconds is not None
    assert result.completed_seconds is not None
    assert 0 <= result.first_text_delta_seconds <= result.completed_seconds
    assert len(calls) == 1

    with _server([(500, b"secret-token secret-prompt", {})]) as (endpoint, calls):
        with pytest.raises(OpenAIResponsesTransportError) as failure:
            OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=5).request(
                _transport_body(),
                access_token="secret-token",
                account_id="secret-account",
            )
    assert len(calls) == 1
    rendered = "".join(
        traceback.format_exception(type(failure.value), failure.value, failure.value.__traceback__)
    )
    assert failure.value.__cause__ is None
    assert failure.value.__context__ is None
    assert not hasattr(failure.value, "request")
    assert not hasattr(failure.value, "response")
    for secret in (
        "secret-token",
        "secret-account",
        "secret-prompt",
        "Bearer",
    ):
        assert secret not in rendered

    oversized = _transport_body()
    oversized["instructions"] = "x" * 400
    with _server([_outcome(_text_events())]) as (endpoint, calls):
        with pytest.raises(OpenAIResponsesTransportError, match="byte limit"):
            OpenAIResponsesTransport(
                endpoint=endpoint,
                timeout_seconds=5,
                maximum_request_bytes=256,
            ).request(oversized, access_token="access", account_id="account")
    assert not calls

    with _slow_server(header_delay=0.7, body_delay=0.7) as endpoint:
        started = wall_monotonic()
        with pytest.raises(OpenAIResponsesUnavailable, match="total deadline"):
            OpenAIResponsesTransport(endpoint=endpoint, timeout_seconds=1).request(
                _transport_body(), access_token="access", account_id="account"
            )
        elapsed = wall_monotonic() - started
    assert 0.8 <= elapsed < 1.3


def test_ambient_sdk_debug_logging_is_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_LOG", "debug")
    with pytest.raises(OpenAIResponsesTransportError, match="logging"):
        OpenAIResponsesTransport(
            endpoint="https://example.test/responses", timeout_seconds=5
        ).request(_transport_body(), access_token="access", account_id="account")


def test_cold_process_request_uses_one_absolute_wall_clock_deadline() -> None:
    script = """
import json, sys
from time import monotonic
from eva.adapters.model.conversation import CodexConversationModel, ConversationModelError
body = {
    'include': [], 'input': [{'content': 'Hoi', 'role': 'user', 'type': 'message'}],
    'instructions': 'Antwoord kort.', 'model': 'gpt-5.4-mini',
    'parallel_tool_calls': False, 'prompt_cache_key': 'eva-cold-process',
    'reasoning': {'effort': 'low', 'summary': 'auto'}, 'store': False,
    'stream': True, 'tool_choice': 'none', 'tools': [],
}
model = CodexConversationModel(
    auth_file=None, endpoint=sys.argv[1], timeout_seconds=1,
    transport_kind='openai_sdk',
)
model._load_auth = lambda: ('access', 'account')
started = monotonic()
try:
    model._request_conversation(body)
except ConversationModelError as error:
    if 'provider is unavailable' not in str(error):
        raise
else:
    raise RuntimeError('slow response unexpectedly completed')
print(json.dumps({'elapsed': monotonic() - started}))
"""
    with _slow_server(header_delay=0.7, body_delay=0.7) as endpoint:
        environment = dict(os.environ)
        environment.pop("OPENAI_LOG", None)
        result = subprocess.run(
            [sys.executable, "-c", script, endpoint],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        elapsed = json.loads(result.stdout)["elapsed"]
    assert 0.8 <= elapsed < 1.3


def test_repeated_timeouts_do_not_accumulate_threads_or_descriptors() -> None:
    descriptor_root = Path("/proc/self/fd")
    if not descriptor_root.is_dir():
        pytest.skip("descriptor accounting requires procfs")
    baseline_descriptors = len(tuple(descriptor_root.iterdir()))

    for header_delay, body_delay in ((1.2, 0.0), (0.0, 1.2)):
        with _slow_server(
            header_delay=header_delay, body_delay=body_delay
        ) as endpoint:
            for _ in range(2):
                with pytest.raises(OpenAIResponsesUnavailable, match="total deadline"):
                    OpenAIResponsesTransport(
                        endpoint=endpoint, timeout_seconds=1
                    ).request(
                        _transport_body(), access_token="access", account_id="account"
                    )

    cleanup_deadline = wall_monotonic() + 1
    while wall_monotonic() < cleanup_deadline:
        candidate_threads = tuple(
            thread for thread in threading.enumerate()
            if thread.name in {
                "eva-openai-close",
                "eva-openai-request",
                "eva-openai-response",
            }
        )
        if not candidate_threads:
            break
        sleep(0.02)
    assert not candidate_threads
    assert len(tuple(descriptor_root.iterdir())) <= baseline_descriptors
