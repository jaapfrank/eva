"""First providerless acceptance slice for PydanticConversationModel.

These tests intentionally name the future product entrypoint.  Until that
single in-place adapter exists they fail with one explicit missing-module
message; they never fall back to ``CodexConversationModel``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any, AsyncIterator

import pytest
import httpx2
from pydantic_ai.models import Model, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.usage import RequestUsage

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.openai_codex_provider import (
    CodexProviderError,
    OpenAICodexProvider,
    RawUsageAttestation,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationModelUsage,
    ConversationToolDescriptor,
    ConversationTurn,
    IdentityProfile,
    stream_conversation_text,
)
from eva.application.memory import PresenceMemory
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall
from eva.contracts.runtime_budget import RuntimeResourceBudget
from tests.support.scripted_pydantic_conversation import (
    ScriptedCall,
    ScriptedPhase,
    ScriptedPydanticConversation,
    tool_calls,
    tool_returns,
)


NOW = "2026-08-31T12:00:00Z"


def _phase_transport_adapter(
    tmp_path, monkeypatch, parts, *, fail=False, reuse_message_id=False,
):
    """Exercise the installed SDK and Pydantic parser; no external network."""
    requests = []
    delivered = []
    completed_seen = []
    output = []
    events = []
    response = {
        "id": "resp_phase", "object": "response", "created_at": 1777111200,
        "status": "in_progress", "model": "gpt-6-astra", "output": [],
        "parallel_tool_calls": False,
    }
    events.append({"type": "response.created", "response": dict(response)})
    if any(phase == "tool" for phase, _ in parts):
        output.extend([
            {"id": "search", "type": "tool_search_call", "execution": "server",
             "call_id": None, "status": "completed", "arguments": {"paths": [TOOL.name]}},
            {"id": "discovery", "type": "tool_search_output", "execution": "server",
             "call_id": None, "status": "completed", "tools": [{
                 "type": "function", "name": TOOL.name, "description": TOOL.description,
                 "parameters": TOOL.input_schema, "strict": True, "defer_loading": True,
             }]},
        ])
        for index, item in enumerate(output):
            for stage in ("added", "done"):
                events.append({"type": f"response.output_item.{stage}",
                               "output_index": index, "item": item})
    for index, (phase, chunks) in enumerate(parts, start=len(output)):
        if phase == "tool":
            item = {
                "id": f"call_{index}", "type": "function_call", "status": "completed",
                "call_id": "comfort-call-1", "name": TOOL.name,
                "arguments": json.dumps(CALL.arguments),
            }
            events.append({"type": "response.output_item.added", "output_index": index,
                           "item": item})
            output.append(item)
            continue
        item = {
            "id": "msg_0" if reuse_message_id else f"msg_{index}",
            "type": "message", "role": "assistant",
            "status": "in_progress", "content": [],
        }
        if phase is not None:
            item["phase"] = phase
        events.append({"type": "response.output_item.added", "output_index": index,
                       "item": dict(item)})
        for chunk in chunks:
            events.append({"type": "response.output_text.delta", "item_id": item["id"],
                           "output_index": index, "content_index": 0,
                           "delta": chunk, "logprobs": []})
        item.update(status="completed", content=[{
            "type": "output_text", "text": "".join(chunks), "annotations": [],
            "logprobs": [],
        }])
        output.append(item)
    response.update(status="completed", output=output, usage={
        "input_tokens": 17, "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 24,
    })

    class WireStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for event in events:
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
            if fail:
                raise httpx2.ReadError("synthetic interrupted stream")
            # Snapshot before response.completed: proves incremental delivery,
            # not splitting an already-complete answer into pretend chunks.
            completed_seen.append(tuple(delivered))
            yield ("event: response.completed\ndata: " + json.dumps({
                "type": "response.completed", "response": response,
            }) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    async def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                              stream=WireStream(), request=request)

    class LocalProvider(OpenAICodexProvider):
        def __init__(self, settings, *, credential_loader):
            super().__init__(settings, credential_loader=lambda: ("test", "test"),
                             network_transport=httpx2.MockTransport(respond))

    monkeypatch.setattr("eva.adapters.model.pydantic_conversation.OpenAICodexProvider",
                        LocalProvider)
    model = _adapter_type()(auth_file=tmp_path / "unused.json", model="gpt-6-astra",
                            conversation_tools=(TOOL,))
    return model, delivered, requests, completed_seen


@pytest.mark.parametrize("repeat", range(2))
@pytest.mark.parametrize("parts", [
    [("final_answer", ("Hoi ", "Jaap!"))],
    [("commentary", ("Interne ", "tussenstap.")),
     ("final_answer", ("Hoi ", "Jaap!"))],
])
def test_wire_final_phase_streams_with_tools_before_completion(
    tmp_path, monkeypatch, parts, repeat,
):
    model, deltas, requests, snapshots = _phase_transport_adapter(
        tmp_path, monkeypatch, parts,
    )
    with stream_conversation_text(deltas.append):
        result = _generate(model)
    assert deltas == ["Hoi ", "Jaap!"]
    assert snapshots == [("Hoi ", "Jaap!")]
    assert result.text == "Hoi Jaap!"
    assert result.action_call is None
    assert result.usage.input_tokens == 17
    assert result.usage.output_tokens == 7
    assert len(requests) == 1
    assert requests[0]["tools"]


@pytest.mark.parametrize("phase", [None, "commentary"])
def test_wire_unmarked_or_commentary_text_never_becomes_live_answer(
    tmp_path, monkeypatch, phase,
):
    model, deltas, requests, _ = _phase_transport_adapter(
        tmp_path, monkeypatch, [(phase, ('final_answer is just text',))],
    )
    with stream_conversation_text(deltas.append):
        if phase == "commentary":
            with pytest.raises(ConversationModelError):
                _generate(model)
        else:
            assert _generate(model).text == 'final_answer is just text'
    assert deltas == []
    assert len(requests) == 1


def test_wire_failure_after_final_preview_does_not_retry(tmp_path, monkeypatch):
    model, deltas, requests, _ = _phase_transport_adapter(
        tmp_path, monkeypatch, [("final_answer", ("Hoi ",))], fail=True,
    )
    with stream_conversation_text(deltas.append):
        with pytest.raises(ConversationModelError):
            _generate(model)
    assert deltas == ["Hoi "]
    assert len(requests) == 1


@pytest.mark.parametrize("phase", [None, "commentary"])
def test_wire_later_part_does_not_inherit_final_phase(tmp_path, monkeypatch, phase):
    model, deltas, requests, _ = _phase_transport_adapter(
        tmp_path, monkeypatch, [
            ("final_answer", ("Hoi!",)), (phase, ("Verborgen ", "tekst.")),
        ],
    )
    with stream_conversation_text(deltas.append):
        result = _generate(model)
    assert deltas == ["Hoi!"]
    assert result.text == "Hoi!"
    assert len(requests) == 1


def test_wire_commentary_with_tool_remains_deferred_and_hidden(tmp_path, monkeypatch):
    model, deltas, requests, _ = _phase_transport_adapter(
        tmp_path, monkeypatch, [("commentary", ("Ik ga controleren.",)), ("tool", ())],
    )
    with stream_conversation_text(deltas.append):
        result = _generate(model, available_actions=(TOOL.name,))
    assert result.action_call == CALL
    assert deltas == []
    assert len(requests) == 1


@pytest.mark.parametrize("tool_first", [False, True])
def test_wire_mixed_final_and_external_tool_never_returns_action(
    tmp_path, monkeypatch, tool_first,
):
    parts = [("final_answer", ("Klaar!",)), ("tool", ())]
    if tool_first:
        parts.reverse()
    model, deltas, requests, _ = _phase_transport_adapter(tmp_path, monkeypatch, parts)
    with stream_conversation_text(deltas.append):
        with pytest.raises(ConversationModelError):
            _generate(model, available_actions=(TOOL.name,))
    assert deltas == ([] if tool_first else ["Klaar!"])
    assert len(requests) == 1


def test_wire_phase_change_on_same_part_fails_without_retry(tmp_path, monkeypatch):
    model, deltas, requests, _ = _phase_transport_adapter(
        tmp_path, monkeypatch,
        [("final_answer", ("Hoi!",)), ("commentary", ("Verborgen.",))],
        reuse_message_id=True,
    )
    with stream_conversation_text(deltas.append):
        with pytest.raises(ConversationModelError):
            _generate(model)
    assert deltas == ["Hoi!"]
    assert len(requests) == 1


@pytest.mark.parametrize("replacement_phase", [None, "final_answer", "commentary"])
def test_framework_replacement_start_is_not_appended(
    tmp_path, monkeypatch, replacement_phase,
):
    """Pydantic's public event contract allows replacing a started part.

    Inject that event at the framework boundary, not pretend OpenAI always
    emits it. The separate wire test exercises actual SDK metadata changes.
    """
    from pydantic_ai import Agent, PartStartEvent, TextPart

    original_run = Agent.run

    async def with_replacement(self, *args, **kwargs):
        capture = kwargs["event_stream_handler"]

        async def replacing(context, events):
            async def changed():
                async for event in events:
                    yield event
                    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                        yield PartStartEvent(index=event.index, part=TextPart(
                            "Replacement must not be appended",
                            provider_details={"phase": replacement_phase},
                        ))
            await capture(context, changed())

        kwargs["event_stream_handler"] = replacing
        return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "run", with_replacement)
    model, deltas, requests, _ = _phase_transport_adapter(
        tmp_path, monkeypatch, [("final_answer", ("Hoi!",))],
    )
    with stream_conversation_text(deltas.append):
        with pytest.raises(ConversationModelError):
            _generate(model)
    assert deltas == ["Hoi!"]
    assert len(requests) == 1
TOOL = ConversationToolDescriptor(
    name="adjust_space_comfort",
    description=(
        "Make one named space warmer or cooler and return acceptance evidence."
    ),
    input_schema={
        "additionalProperties": False,
        "properties": {
            "direction": {"enum": ["warmer", "cooler"], "type": "string"},
            "space": {"type": "string"},
        },
        "required": ["direction", "space"],
        "type": "object",
    },
    completion_guidance="State only what the durable receipt proves.",
)
CALL = ConversationActionCall(
    "comfort-call-1",
    TOOL.name,
    {"direction": "warmer", "space": "overkapping"},
)
RECEIPT = ConversationActionReceipt(
    CALL,
    {"accepted": True, "observed_temperature_c": 20},
)


def _adapter_type() -> type[Any]:
    try:
        module = import_module("eva.adapters.model.pydantic_conversation")
    except ModuleNotFoundError as error:
        if error.name != "eva.adapters.model.pydantic_conversation":
            raise
        pytest.fail(
            "missing product module eva.adapters.model.pydantic_conversation",
            pytrace=False,
        )
    adapter = getattr(module, "PydanticConversationModel", None)
    if not isinstance(adapter, type):
        pytest.fail("missing product class PydanticConversationModel", pytrace=False)
    return adapter


def _adapter(
    tmp_path: Path,
    scripted: ScriptedPydanticConversation,
    *,
    timeout_seconds: float = 30.0,
    resource_budget: RuntimeResourceBudget | None = None,
) -> Any:
    return _adapter_type()(
        auth_file=tmp_path / "unused-providerless-lease.json",
        model_backend=scripted.model,
        timeout_seconds=timeout_seconds,
        conversation_tools=(TOOL,),
        resource_budget=resource_budget,
    )


class _ExactStreamUsageModel(WrapperModel):
    """Keep the scripted stream while supplying its provider-side input usage."""

    def __init__(self, wrapped: Model, *, input_tokens: int) -> None:
        super().__init__(wrapped)
        self._input_tokens = input_tokens

    @asynccontextmanager
    async def request_stream(  # type: ignore[override]
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[StreamedResponse]:
        async with self.wrapped.request_stream(*args, **kwargs) as response:
            # Pydantic's public StreamedResponse provider contract explicitly
            # requires provider implementations to maintain this accumulator.
            response._usage = RequestUsage(input_tokens=self._input_tokens)
            yield response


def _fake_provider_type(
    scripted: ScriptedPydanticConversation,
    *,
    raw_usage: RawUsageAttestation,
) -> type[Any]:
    class FakeOpenAICodexProvider:
        constructed = 0
        entered = 0
        exited = 0
        usage_reads = 0

        def __init__(
            self,
            settings: Any,
            *,
            credential_loader: Any,
        ) -> None:
            type(self).constructed += 1
            self.settings = settings
            self.credential_loader = credential_loader
            self.model = _ExactStreamUsageModel(
                scripted.model,
                input_tokens=17,
            )

        async def __aenter__(self) -> "FakeOpenAICodexProvider":
            type(self).entered += 1
            return self

        async def __aexit__(self, *_error: object) -> None:
            type(self).exited += 1

        def consume_raw_usage(self) -> RawUsageAttestation:
            type(self).usage_reads += 1
            return raw_usage

    return FakeOpenAICodexProvider


def _turn(message_id: str = "message:providerless") -> ConversationTurn:
    return ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        message_id,
        "Het is koud in de overkapping.",
        NOW,
    )


def _identity() -> IdentityProfile:
    return IdentityProfile(
        "Eva",
        "Een warme en zelfstandige digitale huisgenoot.",
        ("Wees eerlijk.",),
        80,
    )


def _memory() -> PresenceMemory:
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="recall:providerless",
        query_purpose="conversation_reply",
        tenant_id="tenant",
        principal_id="jaap",
        conversation_id="conversation",
        project_id=None,
        requested_valid_at=NOW,
        admitted_fact_ids_sha256=canonical_sha256([]),
        selected=[],
        excluded_reasons={},
        projection_version="providerless-test-v1",
        scope_sha256=canonical_sha256({"kind": "person"}),
        created_at=NOW,
    )
    return PresenceMemory(recall, ())


def _runtime_budget(*, max_input_tokens: int = 4_000) -> RuntimeResourceBudget:
    values = {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": 1_200,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_person": 2_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id="tenant:test",
        installation_id="installation:test",
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(values)).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=max_input_tokens,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        memory_records_per_group=2_000,
        memory_bytes_per_group=20_971_520,
        memory_records_per_project=2_000,
        memory_bytes_per_project=20_971_520,
        memory_records_per_installation=20_000,
        memory_bytes_per_installation=209_715_200,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=40,
        external_monthly_spend_cap_eur_cents=0,
    )


def _generate(
    model: Any,
    *,
    completed_action: ConversationActionReceipt | None = None,
    available_actions: tuple[str, ...] = (),
    prior_actions: tuple[ConversationActionReceipt, ...] = (),
):  # type: ignore[no-untyped-def]
    return model.generate(
        _turn(),
        _identity(),
        _memory(),
        (),
        completed_action=completed_action,
        available_actions=available_actions,
        prior_actions=prior_actions,
    )


def test_scripted_text_stream_preserves_exact_deltas_final_usage_and_timing(
    tmp_path: Path,
) -> None:
    scripted = ScriptedPydanticConversation(
        (ScriptedPhase.text("De ", "ruimte ", "is warmer."),)
    )
    deltas: list[str] = []
    with stream_conversation_text(deltas.append):
        result = _generate(_adapter(tmp_path, scripted), completed_action=RECEIPT)

    assert result.text == "De ruimte is warmer."
    assert "".join(deltas) == result.text
    assert len(deltas) == 3
    assert result.usage.measurement == "measured"
    assert result.usage.model_attempts == 1
    assert result.usage.tool_calls == 0
    assert (result.usage.input_tokens or 0) > 0
    assert (result.usage.output_tokens or 0) > 0
    assert result.timing.first_content_ms is not None
    assert result.timing.completed_ms is not None
    assert result.timing.first_content_ms <= result.timing.completed_ms
    assert scripted.remaining_phases == 0


def test_scripted_external_tool_returns_deferred_call_without_execution_or_delta(
    tmp_path: Path,
) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.deferred(
                ScriptedCall(CALL.call_id, CALL.name, dict(CALL.arguments)),
                usage=RequestUsage(input_tokens=40, output_tokens=3),
            ),
        )
    )
    deltas: list[str] = []
    with stream_conversation_text(deltas.append):
        result = _generate(_adapter(tmp_path, scripted), available_actions=(TOOL.name,))

    assert result.text is None
    assert result.action_call == CALL
    assert deltas == []
    assert len(scripted.captures) == 1
    first_request = scripted.captures[0]
    assert first_request.offered_tool_names == ()
    assert first_request.native_tool_kinds == ("tool_search",)
    assert result.usage.tool_calls == 1

    multiple = ScriptedPydanticConversation(
        (
            ScriptedPhase.deferred(
                ScriptedCall(CALL.call_id, CALL.name, dict(CALL.arguments)),
                ScriptedCall("comfort-call-2", CALL.name, dict(CALL.arguments)),
            ),
        )
    )
    with pytest.raises(ConversationModelError, match="exactly one|precies één"):
        _generate(_adapter(tmp_path, multiple), available_actions=(TOOL.name,))
    assert len(multiple.captures) == 1


def test_scripted_durable_receipt_resumes_in_fresh_adapter_with_no_tools(
    tmp_path: Path,
) -> None:
    execute_calls: list[ConversationActionCall] = []

    def execute(call: ConversationActionCall) -> ConversationActionReceipt:
        execute_calls.append(call)
        return ConversationActionReceipt(
            call, {"accepted": True, "observed_temperature_c": 20}
        )

    durable_receipt = execute(CALL)
    scripted = ScriptedPydanticConversation(
        (ScriptedPhase.text("De overkapping wordt nu warmer."),)
    )
    result = _generate(_adapter(tmp_path, scripted), completed_action=durable_receipt)

    assert result.text == "De overkapping wordt nu warmer."
    assert execute_calls == [CALL]
    assert len(scripted.captures) == 1
    capture = scripted.captures[0]
    assert capture.offered_tool_names == ()
    assert [item.tool_call_id for item in tool_calls(capture.messages)] == [
        CALL.call_id
    ]
    returns = tool_returns(capture.messages)
    assert [item.tool_call_id for item in returns] == [CALL.call_id]
    assert returns[0].content == durable_receipt.output


def test_scripted_request_usage_projects_and_two_phase_usage_aggregates(
    tmp_path: Path,
) -> None:
    selected_script = ScriptedPydanticConversation(
        (
            ScriptedPhase.deferred(
                ScriptedCall(CALL.call_id, CALL.name, dict(CALL.arguments)),
                usage=RequestUsage(input_tokens=40, output_tokens=3),
            ),
        )
    )
    selected = _generate(
        _adapter(tmp_path, selected_script), available_actions=(TOOL.name,)
    )
    final_script = ScriptedPydanticConversation(
        (ScriptedPhase.text("De overkapping is warmer."),)
    )
    final = _generate(
        _adapter(tmp_path, final_script),
        completed_action=ConversationActionReceipt(
            selected.action_call or CALL, RECEIPT.output
        ),
    )
    aggregate = ConversationModelUsage.aggregate((selected.usage, final.usage))

    assert selected.usage.measurement == "measured"
    assert selected.usage.tool_calls == 1
    assert final.usage.measurement == "measured"
    assert final.usage.tool_calls == 0
    assert aggregate.measurement == "measured"
    assert aggregate.model_attempts == 2
    assert aggregate.tool_calls == 1
    assert aggregate.input_tokens == (
        selected.usage.input_tokens + final.usage.input_tokens  # type: ignore[operator]
    )
    assert aggregate.output_tokens == (
        selected.usage.output_tokens + final.usage.output_tokens  # type: ignore[operator]
    )


def test_scripted_absolute_deadline_cancels_one_stalled_phase(
    tmp_path: Path,
) -> None:
    scripted = ScriptedPydanticConversation((ScriptedPhase.stall(),))
    started = monotonic()
    with pytest.raises(ConversationModelError, match="deadline"):
        _generate(
            _adapter(tmp_path, scripted, timeout_seconds=1.0),
            completed_action=RECEIPT,
        )
    elapsed = monotonic() - started

    assert 0.8 <= elapsed < 1.5
    assert len(scripted.captures) == 1
    assert scripted.cancelled_requests == 1


def test_scripted_post_delta_stall_is_not_retried(tmp_path: Path) -> None:
    scripted = ScriptedPydanticConversation(
        (ScriptedPhase.text("gedeeltelijk", stall_after_output=True),)
    )
    deltas: list[str] = []
    with stream_conversation_text(deltas.append):
        with pytest.raises(ConversationModelError, match="deadline"):
            _generate(
                _adapter(tmp_path, scripted, timeout_seconds=1.0),
                completed_action=RECEIPT,
            )

    assert deltas == ["gedeeltelijk"]
    assert len(scripted.captures) == 1
    assert scripted.cancelled_requests == 1


def test_unavailable_before_output_retries_once_and_reports_partial_usage(
    tmp_path: Path,
) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.failure(CodexProviderError("unavailable")),
            ScriptedPhase.text("hersteld"),
        )
    )
    model = _adapter(
        tmp_path,
        scripted,
        resource_budget=_runtime_budget(max_input_tokens=16_000),
    )

    result = _generate(
        model,
        available_actions=(TOOL.name,),
    )

    assert result.text == "hersteld"
    assert result.usage.measurement == "partial"
    assert result.usage.model_attempts == 2
    assert result.usage.tool_calls == 0
    assert (result.usage.input_tokens or 0) > 0
    assert (result.usage.output_tokens or 0) > 0
    assert len(scripted.captures) == 2
    assert scripted.remaining_phases == 0
    assert scripted.captures[0].native_tool_kinds == ("tool_search",)
    with pytest.raises(ConversationModelError, match="unmeasured"):
        model._enforce_reported_usage(
            ConversationModelUsage.partial(
                model_attempts=3,
                tool_calls=0,
                input_tokens=1,
                output_tokens=1,
            )
        )


def test_unavailable_after_visible_text_is_not_retried(tmp_path: Path) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.text(
                "zichtbaar",
                exception_after_output=CodexProviderError("unavailable"),
            ),
            ScriptedPhase.text("mag niet worden gebruikt"),
        )
    )
    deltas: list[str] = []

    with stream_conversation_text(deltas.append):
        with pytest.raises(ConversationModelError, match="unavailable"):
            _generate(_adapter(tmp_path, scripted), completed_action=RECEIPT)

    assert deltas == ["zichtbaar"]
    assert len(scripted.captures) == 1
    assert scripted.remaining_phases == 1


def test_unavailable_after_tool_call_start_is_not_retried(tmp_path: Path) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.deferred(
                ScriptedCall(CALL.call_id, CALL.name, dict(CALL.arguments)),
                exception_after_output=CodexProviderError("unavailable"),
            ),
            ScriptedPhase.text("mag niet worden gebruikt"),
        )
    )

    with pytest.raises(ConversationModelError, match="unavailable"):
        _generate(_adapter(tmp_path, scripted), available_actions=(TOOL.name,))

    assert len(scripted.captures) == 1
    assert scripted.remaining_phases == 1


def test_second_pre_output_unavailable_is_not_retried_again(tmp_path: Path) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.failure(CodexProviderError("unavailable")),
            ScriptedPhase.failure(CodexProviderError("unavailable")),
            ScriptedPhase.text("derde poging mag niet bestaan"),
        )
    )

    with pytest.raises(ConversationModelError, match="unavailable"):
        _generate(_adapter(tmp_path, scripted), completed_action=RECEIPT)

    assert len(scripted.captures) == 2
    assert scripted.remaining_phases == 1


@pytest.mark.parametrize(
    "failure",
    [
        CodexProviderError("credentials_rejected"),
        CodexProviderError("deadline"),
        CodexProviderError("protocol"),
        CodexProviderError("configuration"),
        CodexProviderError("request_too_large"),
        CodexProviderError("response_too_large"),
        RuntimeError("unknown provider failure"),
    ],
)
def test_non_transient_or_unknown_failure_is_never_retried(
    tmp_path: Path, failure: Exception
) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.failure(failure),
            ScriptedPhase.text("mag niet worden gebruikt"),
        )
    )

    with pytest.raises(ConversationModelError):
        _generate(_adapter(tmp_path, scripted), completed_action=RECEIPT)

    assert len(scripted.captures) == 1
    assert scripted.remaining_phases == 1


def test_retry_attempts_share_one_absolute_phase_deadline(tmp_path: Path) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.failure(CodexProviderError("unavailable"), delay_seconds=0.6),
            ScriptedPhase.stall(),
        )
    )
    started = monotonic()

    with pytest.raises(ConversationModelError, match="deadline"):
        _generate(
            _adapter(tmp_path, scripted, timeout_seconds=1.0),
            completed_action=RECEIPT,
        )
    elapsed = monotonic() - started

    assert 0.8 <= elapsed < 1.4
    assert len(scripted.captures) == 2
    assert scripted.cancelled_requests == 1


def test_real_provider_seam_streams_and_matches_raw_usage_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.text(
                "Echte ",
                "provider ",
                "geeft ",
                "nu ",
                "veilig ",
                "streamend ",
                "antwoord",
            ),
        )
    )
    raw_usage = RawUsageAttestation(17, 7, 24)
    fake_provider = _fake_provider_type(scripted, raw_usage=raw_usage)
    module = import_module("eva.adapters.model.pydantic_conversation")
    monkeypatch.setattr(module, "OpenAICodexProvider", fake_provider)
    model = _adapter_type()(
        auth_file=tmp_path / "must-not-be-read.json",
        conversation_tools=(TOOL,),
    )

    deltas: list[str] = []
    with stream_conversation_text(deltas.append):
        result = _generate(model, completed_action=RECEIPT)

    assert result.text == "".join(deltas)
    assert deltas == [
        "Echte ",
        "provider ",
        "geeft ",
        "nu ",
        "veilig ",
        "streamend ",
        "antwoord",
    ]
    assert result.usage.measurement == "measured"
    assert result.usage.input_tokens == 17
    assert result.usage.output_tokens == 7
    assert result.usage.input_tokens + result.usage.output_tokens == 24
    assert fake_provider.constructed == 1
    assert fake_provider.entered == 1
    assert fake_provider.exited == 1
    assert fake_provider.usage_reads == 1
    assert len(scripted.captures) == 1


def test_real_provider_retry_rotates_credentials_and_keeps_only_known_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.failure(CodexProviderError("unavailable")),
            ScriptedPhase.text(
                "Tweede ",
                "provider ",
                "geeft ",
                "nu ",
                "veilig ",
                "antwoord ",
                "terug",
            ),
        )
    )

    class RetryingProvider:
        constructed = 0
        entered = 0
        exited = 0
        usage_reads = 0
        access_tokens: list[str] = []

        def __init__(self, settings: Any, *, credential_loader: Any) -> None:
            type(self).constructed += 1
            self.settings = settings
            self.credential_loader = credential_loader
            self.access_token_sha256: str | None = None
            self.model = _ExactStreamUsageModel(scripted.model, input_tokens=17)

        async def __aenter__(self) -> "RetryingProvider":
            type(self).entered += 1
            access_token, _account_id = self.credential_loader()
            type(self).access_tokens.append(access_token)
            self.access_token_sha256 = hashlib.sha256(access_token.encode()).hexdigest()
            return self

        async def __aexit__(self, *_error: object) -> None:
            type(self).exited += 1

        def consume_raw_usage(self) -> RawUsageAttestation:
            type(self).usage_reads += 1
            return RawUsageAttestation(17, 7, 24)

    leases = iter(("access-first", "access-second"))
    module = import_module("eva.adapters.model.pydantic_conversation")
    monkeypatch.setattr(module, "OpenAICodexProvider", RetryingProvider)
    model = _adapter_type()(
        auth_file=tmp_path / "must-not-be-read.json",
        conversation_tools=(TOOL,),
        resource_budget=_runtime_budget(),
    )
    monkeypatch.setattr(model, "_load_auth", lambda: (next(leases), "account-a"))

    result = _generate(model, completed_action=RECEIPT)

    assert result.text == "Tweede provider geeft nu veilig antwoord terug"
    assert result.usage == ConversationModelUsage.partial(
        model_attempts=2,
        tool_calls=0,
        input_tokens=17,
        output_tokens=7,
    )
    assert RetryingProvider.access_tokens == ["access-first", "access-second"]
    assert RetryingProvider.constructed == 2
    assert RetryingProvider.entered == 2
    assert RetryingProvider.exited == 2
    assert RetryingProvider.usage_reads == 1
    assert len(scripted.captures) == 2


def test_real_provider_seam_rejects_raw_usage_mismatch_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.text(
                "Echte ",
                "provider ",
                "geeft ",
                "nu ",
                "veilig ",
                "streamend ",
                "antwoord",
            ),
        )
    )
    mismatched_usage = RawUsageAttestation(18, 7, 25)
    fake_provider = _fake_provider_type(scripted, raw_usage=mismatched_usage)
    module = import_module("eva.adapters.model.pydantic_conversation")
    monkeypatch.setattr(module, "OpenAICodexProvider", fake_provider)
    model = _adapter_type()(
        auth_file=tmp_path / "must-not-be-read.json",
        conversation_tools=(TOOL,),
    )

    with pytest.raises(
        ConversationModelError,
        match="usage attestation differs",
    ):
        _generate(model, completed_action=RECEIPT)

    assert fake_provider.constructed == 1
    assert fake_provider.entered == 1
    assert fake_provider.exited == 1
    assert fake_provider.usage_reads == 1
    assert len(scripted.captures) == 1


def test_oversized_prompt_is_rejected_before_model_credentials_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = ScriptedPydanticConversation(
        (ScriptedPhase.text("Dit antwoord mag nooit worden gemaakt."),)
    )
    fake_provider = _fake_provider_type(
        scripted,
        raw_usage=RawUsageAttestation(17, 7, 24),
    )
    module = import_module("eva.adapters.model.pydantic_conversation")
    monkeypatch.setattr(module, "OpenAICodexProvider", fake_provider)
    model = _adapter_type()(
        auth_file=tmp_path / "must-not-be-read.json",
        conversation_tools=(TOOL,),
        resource_budget=_runtime_budget(max_input_tokens=4_000),
    )
    oversized = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message:oversized",
        "x" * 32_000,
        NOW,
    )

    with pytest.raises(ConversationModelError, match="input budget"):
        model.generate(
            oversized,
            _identity(),
            _memory(),
            (),
            available_actions=(TOOL.name,),
        )

    assert scripted.captures == []
    assert fake_provider.constructed == 0
    assert fake_provider.entered == 0
    assert fake_provider.usage_reads == 0


def test_rejected_status_tracks_only_the_access_token_used_by_the_401_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_lease = {"token": "access-token-a", "account": "account-a"}

    class RejectingProvider:
        constructed = 0
        entered = 0
        model_requests = 0

        def __init__(self, _settings: Any, *, credential_loader: Any) -> None:
            type(self).constructed += 1
            self._credential_loader = credential_loader
            self.access_token_sha256: str | None = None
            self.model = FunctionModel(
                self._reject,
                stream_function=self._reject_stream,
                model_name="providerless-401",
            )

        async def __aenter__(self) -> "RejectingProvider":
            type(self).entered += 1
            access_token, _account_id = self._credential_loader()
            self.access_token_sha256 = hashlib.sha256(access_token.encode()).hexdigest()
            return self

        async def __aexit__(self, *_error: object) -> None:
            return None

        async def _reject(self, _messages: Any, _info: AgentInfo) -> Any:
            type(self).model_requests += 1
            raise CodexProviderError("credentials_rejected")

        async def _reject_stream(
            self, _messages: Any, _info: AgentInfo
        ) -> AsyncIterator[Any]:
            type(self).model_requests += 1
            raise CodexProviderError("credentials_rejected")
            yield  # pragma: no cover

    module = import_module("eva.adapters.model.pydantic_conversation")
    monkeypatch.setattr(module, "OpenAICodexProvider", RejectingProvider)
    model = _adapter_type()(
        auth_file=tmp_path / "providerless-lease.json",
        conversation_tools=(TOOL,),
    )
    monkeypatch.setattr(
        model,
        "_load_auth",
        lambda: (active_lease["token"], active_lease["account"]),
    )

    with pytest.raises(ConversationModelError, match="credentials were rejected"):
        _generate(model, completed_action=RECEIPT)

    rejected = model.credential_status()
    assert rejected.ready is False
    assert rejected.state == "rejected"
    assert RejectingProvider.constructed == 1
    assert RejectingProvider.entered == 1
    assert RejectingProvider.model_requests == 1

    active_lease["token"] = "access-token-b"
    rotated = model.credential_status()
    assert rotated.ready is True
    assert rotated.state == "ready"



def test_completed_tool_can_choose_next_tool_and_preserve_all_receipts(tmp_path):
    second = ConversationActionCall(
        "comfort-call-2", TOOL.name, {"direction": "cooler", "space": "zolder"},
    )
    scripted = ScriptedPydanticConversation((
        ScriptedPhase.deferred(ScriptedCall(second.call_id, second.name, second.arguments)),
        ScriptedPhase.text("Beide ruimtes zijn gecontroleerd."),
    ))
    adapter = _adapter(tmp_path, scripted)
    result = _generate(adapter, completed_action=RECEIPT, available_actions=(TOOL.name,))
    assert result.action_call == second
    second_receipt = ConversationActionReceipt(second, {"observed_temperature_c": 19})
    final = _generate(adapter, completed_action=second_receipt, prior_actions=(RECEIPT,))
    assert final.text == "Beide ruimtes zijn gecontroleerd."
    assert [part.tool_call_id for part in tool_calls(scripted.captures[-1].messages)] == [CALL.call_id, second.call_id]
    assert [part.content for part in tool_returns(scripted.captures[-1].messages)] == [RECEIPT.output, second_receipt.output]



def test_prior_tool_results_are_included_in_input_budget_before_provider_call(tmp_path):
    scripted = ScriptedPydanticConversation((ScriptedPhase.text("Mag niet starten."),))
    previous = ConversationActionReceipt(CALL, {"large_result": "word " * 20_000})
    latest = ConversationActionReceipt(
        ConversationActionCall("next-call", TOOL.name, dict(CALL.arguments)), {"done": True},
    )
    adapter = _adapter(tmp_path, scripted, resource_budget=_runtime_budget())
    with pytest.raises(ConversationModelError, match="input|invoer|budget"):
        _generate(adapter, completed_action=latest, prior_actions=(previous,))
    assert scripted.captures == []


def test_duplicate_history_call_ids_are_rejected_before_provider_call(tmp_path):
    scripted = ScriptedPydanticConversation((ScriptedPhase.text("Mag niet starten."),))
    with pytest.raises(ValueError, match="duplicate call IDs"):
        _generate(_adapter(tmp_path, scripted), completed_action=RECEIPT, prior_actions=(RECEIPT,))
    assert scripted.captures == []


@pytest.mark.parametrize("kind", ["retry", "language_repair"])
@pytest.mark.parametrize("stop", [False, True])
def test_each_provider_attempt_is_journaled_and_stop_blocks_internal_repeat(tmp_path, kind, stop):
    from dataclasses import replace
    from eva.adapters.objects.identity_verifier import load_current_identity_pack
    from eva.application.conversation import ConversationStopped, observe_model_requests

    first = (ScriptedPhase.failure(CodexProviderError("unavailable")) if kind == "retry"
             else ScriptedPhase.text("Привет"))
    scripted = ScriptedPydanticConversation((first, ScriptedPhase.text("Hallo Jaap.")))
    starts, results = [], {}

    class Journal:
        def begin_model_request(self, turn):
            if stop and starts:
                raise ConversationStopped("owner stopped")
            request_id = str(len(starts))
            starts.append(request_id)
            return request_id

        def complete_model_request(self, turn, request_id, usage):
            results[request_id] = usage

    identity = replace(_identity(), identity_pack=load_current_identity_pack())
    with observe_model_requests(_turn(), Journal()):
        if stop:
            with pytest.raises(ConversationStopped):
                _adapter(tmp_path, scripted).generate(_turn(), identity, _memory(), ())
        else:
            result = _adapter(tmp_path, scripted).generate(_turn(), identity, _memory(), ())
            assert result.text == "Hallo Jaap."
    assert len(scripted.captures) == (1 if stop else 2)
    assert len(starts) == len(scripted.captures)
    # First successful usage survives a stop before language repair. Failed
    # provider attempts keep their admission without invented completion usage.
    assert len(results) == ((0 if stop else 1) if kind == "retry" else len(starts))
    assert all(usage.model_attempts == 1 for usage in results.values())
    # Context teardown cannot leak one owner's journal into another request.
    from eva.application.conversation import begin_current_model_request
    assert begin_current_model_request() is None
