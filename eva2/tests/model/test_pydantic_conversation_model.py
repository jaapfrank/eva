"""First providerless acceptance slice for PydanticConversationModel.

These tests intentionally name the future product entrypoint.  Until that
single in-place adapter exists they fail with one explicit missing-module
message; they never fall back to ``CodexConversationModel``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any, AsyncIterator

import pytest
from pydantic_ai.models import Model, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.usage import RequestUsage

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.openai_codex_provider import (
    CodexProviderError,
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
) -> Any:
    return _adapter_type()(
        auth_file=tmp_path / "unused-providerless-lease.json",
        model_backend=scripted.model,
        timeout_seconds=timeout_seconds,
        conversation_tools=(TOOL,),
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
):  # type: ignore[no-untyped-def]
    return model.generate(
        _turn(),
        _identity(),
        _memory(),
        (),
        completed_action=completed_action,
        available_actions=available_actions,
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
    assert TOOL.name in scripted.captures[0].offered_tool_names
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
