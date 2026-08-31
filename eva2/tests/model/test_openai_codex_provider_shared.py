"""Focused contract tests for the shared access-only Codex provider seam."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.usage import UsageLimits

from eva.adapters.model.openai_codex_provider import (
    _CodexStreamingModel,
    CodexProviderError,
    OpenAICodexProvider,
    OpenAICodexProviderSettings,
    RawUsageAttestation,
)


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


def _response(text: str, *, status: str = "completed") -> dict[str, Any]:
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
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 24,
        },
    }


def _sse(text: str, *, duplicate_completion: bool = False) -> list[bytes]:
    created = _response(text, status="in_progress")
    created["output"] = []
    completed = {"type": "response.completed", "response": _response(text)}
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


def test_true_stream_has_one_raw_usage_attestation_and_rotates_access_token() -> None:
    requests: list[httpx2.Request] = []
    lease_reads = 0

    def credentials() -> tuple[str, str]:
        nonlocal lease_reads
        lease_reads += 1
        return (f"access-{lease_reads}", "account-a")

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
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
