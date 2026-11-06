"""Raw stream boundaries survive SDK phase normalization and cleanup changes."""
import asyncio
from itertools import permutations
import json

import httpx2
from pydantic_ai import Agent
from pydantic_ai.messages import PartStartEvent, PartDeltaEvent, TextPart, TextPartDelta
from pydantic_ai.usage import UsageLimits
import pytest

from eva.adapters.model.openai_codex_provider import (
    CodexProviderError, OpenAICodexProvider, _RawSseUsageAttestor,
)
from tests.model.test_openai_codex_provider_shared import _Chunks, _response, _settings

_MISSING = object()


def added(identity="msg_guard", phase=_MISSING):
    item = {"id": identity, "type": "message", "status": "in_progress",
            "role": "assistant", "content": []}
    if phase is not _MISSING:
        item["phase"] = phase
    return {"type": "response.output_item.added", "output_index": 0, "item": item}


def wire(event):
    return ("data: " + json.dumps(event) + "\n\n").encode()


@pytest.mark.parametrize("before,after", list(permutations([None, "commentary", "final_answer"], 2)))
@pytest.mark.parametrize("fragment_bytes", [1, 7, 65536])
def test_one_item_cannot_change_its_raw_phase_even_when_fragmented(before, after, fragment_bytes):
    attestor = _RawSseUsageAttestor()
    first, second = wire(added(phase=before)), wire(added(phase=after))
    for index in range(0, len(first), fragment_bytes):
        attestor.feed(first[index:index + fragment_bytes])
    with pytest.raises(CodexProviderError, match="provider protocol"):
        for index in range(0, len(second), fragment_bytes):
            attestor.feed(second[index:index + fragment_bytes])
    assert attestor.terminal is None


@pytest.mark.parametrize("phase", [None, "commentary", "final_answer"])
def test_repeated_item_with_same_phase_and_distinct_items_are_supported(phase):
    attestor = _RawSseUsageAttestor()
    attestor.feed(wire(added(phase=phase)))
    attestor.feed(wire(added(phase=phase)))
    for index, other in enumerate([None, "commentary", "final_answer"]):
        attestor.feed(wire(added("other_" + str(index), phase=other)))
    attestor.feed(wire({"type": "response.completed", "response": _response("done")}))
    assert attestor.finish().total_tokens == 24


def test_missing_phase_is_a_stable_identity_and_cannot_later_become_final():
    attestor = _RawSseUsageAttestor()
    attestor.feed(wire(added()))
    attestor.feed(wire(added(phase=None)))
    with pytest.raises(CodexProviderError, match="provider protocol"):
        attestor.feed(wire(added(phase="final_answer")))


@pytest.mark.parametrize("identity", [None, "", "x" * 257, "😀" * 65, "\ud800"])
def test_message_identity_has_a_bounded_valid_utf8_shape(identity):
    with pytest.raises(CodexProviderError, match="provider protocol"):
        _RawSseUsageAttestor().feed(wire(added(identity, "final_answer")))


def test_message_identity_tracking_is_bounded_for_one_provider_response():
    attestor = _RawSseUsageAttestor()
    for index in range(1024):
        attestor.feed(wire(added("message_" + str(index), "commentary")))
    with pytest.raises(CodexProviderError, match="provider protocol"):
        attestor.feed(wire(added("over_the_response_limit", "commentary")))


def test_tool_items_do_not_share_message_phase_identity():
    attestor = _RawSseUsageAttestor()
    tool = added(phase="not_a_message_phase")
    tool["item"]["type"] = "function_call"
    attestor.feed(wire(tool))
    attestor.feed(wire(added(phase="final_answer")))
    with pytest.raises(CodexProviderError, match="provider protocol"):
        attestor.feed(wire(added("different_id", "unsupported_phase")))


def test_sdk_stream_never_exposes_reclassified_commentary_or_retries():
    requests, previews = [], []
    secret_id = "PRIVATE_ITEM_ID"
    created = _response("", status="in_progress");created["output"] = []
    delta = {"type": "response.output_text.delta", "item_id": secret_id,
             "output_index": 0, "content_index": 0, "delta": "Hoi", "logprobs": []}
    events = [{"type": "response.created", "response": created},
        added(secret_id, "final_answer"), delta, added(secret_id, "commentary"),
        {**delta, "delta": "PRIVATE_COMMENTARY"},
        {"type": "response.completed", "response": _response("HoiPRIVATE_COMMENTARY")}]

    async def respond(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
            stream=_Chunks([wire(event) for event in events]), request=request)

    async def capture(_context, stream):
        async for event in stream:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                previews.append(event.part.content)
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                previews.append(event.delta.content_delta)

    async def run():
        provider = OpenAICodexProvider(_settings(), credential_loader=lambda: ("PRIVATE_TOKEN", "PRIVATE_ACCOUNT"),
            network_transport=httpx2.MockTransport(respond))
        async with provider as session:
            with pytest.raises(CodexProviderError) as raised:
                await Agent(session.model, retries=0).run("PRIVATE_PROMPT", instructions="Say hello.",
                    event_stream_handler=capture, usage_limits=UsageLimits(request_limit=1))
            assert raised.value.category == "protocol"
            assert "".join(previews) == "Hoi"
            assert session.model_requests == len(requests) == 1
            observed = json.dumps(session.observation) + str(raised.value)
            for private in (secret_id, "PRIVATE_COMMENTARY", "PRIVATE_PROMPT", "PRIVATE_TOKEN", "PRIVATE_ACCOUNT"):
                assert private not in observed
        assert session.local_stream_closed is True

    asyncio.run(run())


def test_fake_stream_has_one_network_like_pass_even_if_sdk_drains_again():
    async def run():
        stream = _Chunks([b"first", b"last"])
        iterator = stream.__aiter__()
        assert await anext(iterator) == b"first"
        assert [chunk async for chunk in iterator] == [b"last"]
        assert [chunk async for chunk in stream] == []
    asyncio.run(run())
