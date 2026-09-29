"""Focused contract tests for the shared access-only Codex provider seam."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent, DeferredToolRequests, UnexpectedModelBehavior
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.profiles.openai import openai_model_profile
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset
from pydantic_ai.usage import UsageLimits

from eva.adapters.model.openai_codex_provider import (
    _CodexStreamingModel,
    _sanitized,
    CodexProviderError,
    OpenAICodexProvider,
    OpenAICodexProviderSettings,
    RawUsageAttestation,
)


class _StatusFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("unsafe upstream detail")


class _Chunks(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield chunk


class _CancelledModel(Model):
    @property
    def model_name(self) -> str:
        return "cancelled-test"

    @property
    def system(self) -> str:
        return "test"

    async def request(self, *_args: Any, **_kwargs: Any):  # type: ignore[no-untyped-def]
        raise asyncio.CancelledError

    @asynccontextmanager
    async def request_stream(  # type: ignore[override]
        self, *_args: Any, **_kwargs: Any
    ):
        raise asyncio.CancelledError
        yield  # pragma: no cover


def _response(
    text: str, *, status: str = "completed", reasoning_tokens: int = 0
) -> dict[str, Any]:
    return {
        "id": "resp_shared_1",
        "object": "response",
        "created_at": 1_777_111_200.0,
        "status": status,
        "model": "gpt-5.4-mini",
        "output": [
            {
                "id": "msg_shared_1",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "tools": [],
        "usage": {
            "input_tokens": 17,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 7,
            "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            "total_tokens": 24,
        },
    }


def _sse(
    text: str,
    *,
    duplicate_completion: bool = False,
    reasoning_tokens: int = 0,
) -> list[bytes]:
    created = _response(text, status="in_progress")
    created["output"] = []
    completed = {
        "type": "response.completed",
        "response": _response(text, reasoning_tokens=reasoning_tokens),
    }
    events = [
        {"type": "response.created", "response": created},
        {
            "type": "response.output_text.delta",
            "item_id": "msg_shared_1",
            "output_index": 0,
            "content_index": 0,
            "delta": text,
            "logprobs": [],
        },
        completed,
    ]
    if duplicate_completion:
        events.append(completed)
    return [
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode()
        for event in events
    ] + [b"data: [DONE]\n\n"]


def _settings(**overrides: Any) -> OpenAICodexProviderSettings:
    return OpenAICodexProviderSettings(
        endpoint="https://codex.test/responses",
        model_name="gpt-5.4-mini",
        **overrides,
    )


@pytest.mark.parametrize("content_type", ["text/event-stream", None])
def test_true_stream_has_one_raw_usage_attestation_and_rotates_access_token(
    content_type: str | None,
) -> None:
    requests: list[httpx2.Request] = []
    lease_reads = 0

    def credentials() -> tuple[str, str]:
        nonlocal lease_reads
        lease_reads += 1
        return (f"access-{lease_reads}", "account-a")

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        headers = {"content-type": content_type} if content_type is not None else {}
        return httpx2.Response(
            200,
            headers=headers,
            stream=_Chunks(_sse("STROOM")),
            request=request,
        )

    async def run() -> None:
        deltas: list[str] = []

        async def events(_ctx: Any, stream: Any) -> None:
            async for event in stream:
                if isinstance(event, PartStartEvent) and isinstance(
                    event.part, TextPart
                ):
                    deltas.append(event.part.content)
                elif isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, TextPartDelta
                ):
                    deltas.append(event.delta.content_delta)

        provider = OpenAICodexProvider(
            _settings(),
            credential_loader=credentials,
            network_transport=httpx2.MockTransport(respond),
        )
        async with provider as session:
            result = await Agent(session.model, retries=0).run(
                "Antwoord exact STROOM.",
                instructions="Volg exact de gebruikersopdracht.",
                output_type=str,
                event_stream_handler=events,
                usage_limits=UsageLimits(request_limit=1),
            )
            assert result.output == "STROOM"
            assert "".join(deltas) == "STROOM"
            assert session.model_requests == 1
            assert (
                session.access_token_sha256
                == hashlib.sha256(f"access-{lease_reads}".encode()).hexdigest()
            )
            assert session.consume_raw_usage() == RawUsageAttestation(17, 7, 24)
            with pytest.raises(CodexProviderError, match="protocol"):
                session.consume_raw_usage()

    asyncio.run(run())
    assert lease_reads >= 2
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == f"Bearer access-{lease_reads}"
    body = json.loads(requests[0].content)
    assert body["stream"] is True
    assert body["store"] is False
    assert body["instructions"] == "Volg exact de gebruikersopdracht."
    assert body["input"] == [
        {
            "content": "Antwoord exact STROOM.",
            "role": "user",
            "type": "message",
        }
    ]


def test_nonzero_reasoning_tokens_do_not_change_attested_top_level_usage() -> None:
    async def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Chunks(_sse("REDENERING", reasoning_tokens=5)),
            request=request,
        )

    async def run() -> None:
        provider = OpenAICodexProvider(
            _settings(),
            credential_loader=lambda: ("access-a", "account-a"),
            network_transport=httpx2.MockTransport(respond),
        )
        async with provider as session:
            result = await Agent(session.model, retries=0).run(
                "Antwoord exact REDENERING.",
                instructions="Volg exact de gebruikersopdracht.",
                output_type=str,
                event_stream_handler=lambda _ctx, events: _drain(events),
                usage_limits=UsageLimits(request_limit=1),
            )
            assert result.output == "REDENERING"
            assert result.usage.input_tokens == 17
            assert result.usage.output_tokens == 7
            assert result.usage.total_tokens == 24
            assert session.consume_raw_usage() == RawUsageAttestation(17, 7, 24)

    asyncio.run(run())


def test_duplicate_raw_completion_is_a_sanitized_protocol_failure() -> None:
    async def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Chunks(_sse("ONGELDIG", duplicate_completion=True)),
            request=request,
        )

    async def run() -> None:
        provider = OpenAICodexProvider(
            _settings(),
            credential_loader=lambda: ("access-a", "account-a"),
            network_transport=httpx2.MockTransport(respond),
        )
        with pytest.raises(CodexProviderError) as failure:
            async with provider as session:
                await Agent(session.model, retries=0).run(
                    "Antwoord.",
                    instructions="Antwoord kort.",
                    output_type=str,
                    event_stream_handler=lambda _ctx, events: _drain(events),
                    usage_limits=UsageLimits(request_limit=1),
                )
        assert failure.value.category == "protocol"

    asyncio.run(run())


async def _drain(events: Any) -> None:
    async for _event in events:
        pass


@pytest.mark.parametrize("model_name", ["gpt-6-astra", "gpt-5.4-mini", "gpt-6-sol"])
def test_hosted_search_defers_external_action_in_one_request(model_name: str) -> None:
    """Exercise the SDK wire, not a FunctionModel's permissive tool profile."""
    requests: list[dict[str, Any]] = []
    definition = ToolDefinition(
        name="list_calendar_events",
        description="Read calendar entries for a date.",
        parameters_json_schema={
            "type": "object",
            "properties": {"date": {"type": "string"}},
            "required": ["date"],
            "additionalProperties": False,
        },
        strict=True,
        defer_loading=True,
    )

    async def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == model_name
        assert body["reasoning"]["effort"] == ("none" if model_name == "gpt-6-sol" else "medium")
        assert {"type": "tool_search"} in body["tools"]
        assert not any(tool.get("name") == "search_tools" for tool in body["tools"])
        tool = next(
            tool for tool in body["tools"] if tool.get("name") == definition.name
        )
        assert tool["defer_loading"] is True
        search = {
            "id": "ts_1",
            "type": "tool_search_call",
            "execution": "server",
            "call_id": None,
            "status": "completed",
            "arguments": {"paths": [definition.name]},
        }
        discovery = {
            "id": "tso_1",
            "type": "tool_search_output",
            "execution": "server",
            "call_id": None,
            "status": "completed",
            "tools": [tool],
        }
        action = {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "calendar_1",
            "status": "completed",
            "name": definition.name,
            "arguments": '{"date":"2026-09-06"}',
        }
        created = {
            **_response("", status="in_progress"),
            "model": model_name,
            "output": [],
        }
        completed = {
            **_response(""),
            "model": model_name,
            "output": [search, discovery, action],
        }
        events = [{"type": "response.created", "response": created}]
        for index, item in enumerate(completed["output"]):
            for phase in ("added", "done"):
                events.append(
                    {
                        "type": f"response.output_item.{phase}",
                        "output_index": index,
                        "item": item,
                    }
                )
        events.append({"type": "response.completed", "response": completed})
        chunks = [
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
            for event in events
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Chunks(chunks),
            request=request,
        )

    async def run() -> None:
        provider = OpenAICodexProvider(
            OpenAICodexProviderSettings(
                endpoint="https://codex.test/responses", model_name=model_name,
                reasoning_effort="none" if model_name == "gpt-6-sol" else "medium",
            ),
            credential_loader=lambda: ("access-a", "account-a"),
            network_transport=httpx2.MockTransport(respond),
        )
        async with provider as session:
            result = await Agent(
                session.model,
                retries=0,
                instructions="Choose the supplied read tool; external execution belongs to the caller.",
                toolsets=[ExternalToolset([definition])],
                output_type=[str, DeferredToolRequests],
            ).run(
                "Wat staat er op 6 september 2026 in mijn agenda?",
                event_stream_handler=lambda _ctx, events: _drain(events),
                usage_limits=UsageLimits(request_limit=1, tool_calls_limit=1),
            )
            assert isinstance(result.output, DeferredToolRequests)
            assert not result.output.approvals
            assert len(result.output.calls) == 1
            assert result.output.calls[0].tool_name == definition.name
            assert result.output.calls[0].args_as_dict() == {"date": "2026-09-06"}
            assert session.model_requests == len(requests) == 1
            assert session.consume_raw_usage() == RawUsageAttestation(17, 7, 24)

    asyncio.run(run())


@pytest.mark.parametrize("model_name", ["gpt-4.1", "gpt-6-unknown"])
def test_astra_compatibility_does_not_expand_other_model_capabilities(
    model_name: str,
) -> None:
    async def run() -> None:
        async with OpenAICodexProvider(
            OpenAICodexProviderSettings(
                endpoint="https://codex.test/responses", model_name=model_name,
                reasoning_effort="none" if model_name == "gpt-6-sol" else "medium",
            ),
            credential_loader=lambda: ("access-a", "account-a"),
        ) as session:
            assert (
                session.model.profile["supported_native_tools"]
                == openai_model_profile(model_name)["supported_native_tools"]
            )
            assert (
                session.model.profile["openai_responses_supports_reasoning_context"]
                is False
            )
            assert session.model_requests == 0

    asyncio.run(run())


def test_account_authority_cannot_change_inside_one_phase() -> None:
    lease_reads = 0
    network_calls = 0

    def credentials() -> tuple[str, str]:
        nonlocal lease_reads
        lease_reads += 1
        return "access-a", "account-a" if lease_reads == 1 else "account-b"

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx2.Response(500, request=request)

    async def run() -> None:
        provider = OpenAICodexProvider(
            _settings(),
            credential_loader=credentials,
            network_transport=httpx2.MockTransport(respond),
        )
        with pytest.raises(CodexProviderError) as failure:
            async with provider as session:
                await Agent(session.model, retries=0).run(
                    "Antwoord.",
                    instructions="Antwoord kort.",
                    output_type=str,
                    event_stream_handler=lambda _ctx, events: _drain(events),
                    usage_limits=UsageLimits(request_limit=1),
                )
        assert failure.value.category == "configuration"

    asyncio.run(run())
    assert network_calls == 0


def test_oversized_request_stops_before_network() -> None:
    network_calls = 0

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx2.Response(500, request=request)

    async def run() -> None:
        provider = OpenAICodexProvider(
            _settings(maximum_request_bytes=4_096),
            credential_loader=lambda: ("access-a", "account-a"),
            network_transport=httpx2.MockTransport(respond),
        )
        with pytest.raises(CodexProviderError) as failure:
            async with provider as session:
                await Agent(session.model, retries=0).run(
                    "x" * 8_000,
                    instructions="Antwoord kort.",
                    output_type=str,
                    event_stream_handler=lambda _ctx, events: _drain(events),
                    usage_limits=UsageLimits(request_limit=1),
                )
        assert failure.value.category == "request_too_large"

    asyncio.run(run())
    assert network_calls == 0


def test_cancellation_is_not_relabelled_as_provider_unavailable() -> None:
    async def run() -> None:
        model = _CodexStreamingModel(_CancelledModel())
        with pytest.raises(asyncio.CancelledError):
            async with model.request_stream(
                [], None, ModelRequestParameters()
            ) as response:
                async for _event in response:
                    pass

    asyncio.run(run())


@pytest.mark.parametrize("status_code", [401, 403])
def test_sanitization_marks_only_authentication_statuses_as_rejected(
    status_code: int,
) -> None:
    assert _sanitized(_StatusFailure(status_code)).category == "credentials_rejected"


@pytest.mark.parametrize("status_code", [408, 429, 500, 503, 599])
def test_sanitization_marks_retryable_http_statuses_as_unavailable(
    status_code: int,
) -> None:
    assert _sanitized(_StatusFailure(status_code)).category == "unavailable"


@pytest.mark.parametrize("status_code", [400, 404, 409, 422, 600])
def test_sanitization_marks_other_http_statuses_as_protocol_failures(
    status_code: int,
) -> None:
    assert _sanitized(_StatusFailure(status_code)).category == "protocol"


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("deadline"),
        ConnectionError("connection lost"),
        httpx2.ConnectError("connection lost"),
        httpx2.ReadTimeout("read stalled"),
    ],
)
def test_sanitization_marks_explicit_transport_failures_as_unavailable(
    error: BaseException,
) -> None:
    assert _sanitized(error).category == "unavailable"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("unknown internal failure"),
        OSError("non-network operating-system failure"),
        UnexpectedModelBehavior("malformed provider response"),
        httpx2.RemoteProtocolError("malformed wire response"),
    ],
)
def test_sanitization_marks_unknown_and_protocol_failures_as_protocol(
    error: BaseException,
) -> None:
    assert _sanitized(error).category == "protocol"


def test_sanitization_inspects_wrapped_transport_failure() -> None:
    transport = httpx2.ConnectError("connection lost")
    wrapper = RuntimeError("unsafe wrapper")
    wrapper.__cause__ = transport

    assert _sanitized(wrapper).category == "unavailable"


def test_sanitization_preserves_existing_provider_category() -> None:
    error = CodexProviderError("deadline")

    assert _sanitized(error) is error


@pytest.mark.parametrize("stage", ["before_headers", "after_headers", "after_chunk"])
def test_cancelled_request_retains_content_free_transport_observation(stage: str) -> None:
    """Distinguish upstream wait stages even when the outer deadline cancels."""
    reached = asyncio.Event()

    class WaitingBody(httpx2.AsyncByteStream):
        async def __aiter__(self):
            if stage == "after_chunk":
                yield b": private upstream keepalive\n\n"
            reached.set()
            await asyncio.Event().wait()
            yield b""  # pragma: no cover

    async def respond(request: httpx2.Request) -> httpx2.Response:
        if stage == "before_headers":
            reached.set()
            await asyncio.Event().wait()
        return httpx2.Response(200, stream=WaitingBody(), request=request)

    async def run() -> None:
        provider = OpenAICodexProvider(
            _settings(), credential_loader=lambda: ("secret-access", "private-account"),
            network_transport=httpx2.MockTransport(respond),
        )
        async with provider as session:
            task = asyncio.create_task(Agent(session.model, retries=0).run(
                "private request", instructions="private instructions",
                usage_limits=UsageLimits(request_limit=1),
            ))
            await asyncio.wait_for(reached.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            observed = session.observation
            assert observed["network_started_ms"] is not None
            assert (observed["headers_received_ms"] is None) == (stage == "before_headers")
            assert observed["response_status"] == (None if stage == "before_headers" else 200)
            assert observed["received_chunks"] == (1 if stage == "after_chunk" else 0)
            assert observed["received_bytes"] == (len(b": private upstream keepalive\n\n") if stage == "after_chunk" else 0)
            assert "private" not in json.dumps(observed)
            assert "secret" not in json.dumps(observed)
            assert session.model_requests == 1

    asyncio.run(run())


def test_partial_sse_progress_counts_do_not_retain_content_or_accept_completion() -> None:
    from eva.adapters.model.openai_codex_provider import _RawSseUsageAttestor

    attestor = _RawSseUsageAttestor()
    kinds = ["response.output_text.delta", "response.function_call_arguments.delta",
             "response.reasoning_summary_text.delta", "private-unknown-event"]
    wire = b"".join(b"data: " + json.dumps({"type": kind, "delta": "secret-content"}).encode()
                    + b"\n\n" for kind in kinds)
    for offset in range(0, len(wire), 7):
        attestor.feed(wire[offset:offset + 7])
    assert attestor.event_counts == {"sse_events": 4, "text_deltas": 1,
        "tool_argument_deltas": 1, "reasoning_deltas": 1, "completed_events": 0}
    assert "secret" not in json.dumps(attestor.event_counts)
    assert "private" not in json.dumps(attestor.event_counts)
    assert attestor.terminal is None
    with pytest.raises(CodexProviderError, match="protocol"):
        attestor.finish()
