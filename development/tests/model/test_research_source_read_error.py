"""A measured missing source permits another reader, never fake completion/replay."""
import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eva.adapters.model.contracts import ConversationModelError, ResearchSourceCoverageError
from eva.adapters.model.research_tools import OwnerResearchContinuationTool
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.adapters.connectors.delegated_completion import completed_tool_evidence
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationConflict,
    ConversationModelResult, ConversationModelUsage, IdentityProfile, LivingConversation,
)
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
from eva.contracts.canonical_json import canonical_sha256
from tests.application.test_conversation import _Memory
from tests.connectors.test_mcp_gateway import _turn

URL = "https://example.org/manual"
USAGE = ConversationModelUsage.measured(tool_calls=1, input_tokens=20, output_tokens=5)
ARGS = {"question": "Read the supplied public manual", "title": "Manual",
        "options": [URL], "research_mode": "topic"}


class Research:
    calls = 0
    error = None

    def native_research_timeout_seconds(self):
        return 55

    def research_configuration_sha256(self):
        return "a" * 64

    def research_request_payload(self, **kwargs):
        return {"input": dict(kwargs, options=list(kwargs["options"])), "tools": [{"type": "web_search"}]}

    def research_current_options(self, **kwargs):
        self.calls += 1
        raise self.error or ResearchSourceCoverageError(URL, model_usage=USAGE)


def setup():
    research = Research()
    tool = OwnerResearchContinuationTool(research, lambda x: x)
    call = ConversationActionCall("read", "research_current_options", deepcopy(ARGS))
    prepared = asyncio.run(tool.prepare(_turn(), call))
    request = SimpleNamespace(operation_id="operation", tool_name=prepared.tool_name,
        arguments=prepared.arguments, expected_proof=prepared.expected_proof,
        observation_tool_name=prepared.observation_tool_name,
        observation_arguments=prepared.observation_arguments,
        tool_configuration_sha256=prepared.tool_configuration_sha256)
    return research, tool, call, prepared, request


def receipt(tool, call, prepared, observed, turn):
    admission = {"action_name": call.name, "action_call_id": call.call_id,
        "action_arguments": call.arguments, "conversation_id": turn.conversation_id,
        "principal_id": turn.principal_id, "source_message_id": turn.message_id,
        "receipt_output": prepared.receipt_output,
        "observation_output_key": prepared.observation_output_key}
    proof = {"observed": observed.observed, "evidence": observed.evidence}
    events = tuple(TimelineEvent(uuid4(), "research-thread", i, f"cp-{i}", "op", kind,
        {"data": data}, canonical_sha256({"data": data}), datetime.now(UTC))
        for i, (kind, data) in enumerate((("TOOL_REQUEST_ADMITTED", admission),
            ("STATE_VERIFICATION_CONFLICT", {"proof": proof}))))
    # No DB or model-side retention is needed for this durable receipt projection.
    return PostgresExternalConversationTools(None, tool, tool)._receipt(turn, events, project=False)


def test_known_source_failure_is_bound_read_context_not_a_completed_artifact():
    research, tool, call, prepared, request = setup()
    dispatch = asyncio.run(tool.execute(request))
    observed = asyncio.run(tool.observe(request, dispatch))
    result = receipt(tool, call, prepared, observed, _turn())
    assert research.calls == 1 and observed.disposition == "conflict"
    assert observed.observed == {} and result.output["status"] == "failed"
    assert result.output["recoverable_read_error"] is True
    assert result.output["tool_error"]["required_source"] == URL
    assert result.output["tool_error"]["model_usage"] == USAGE.to_wire()
    assert "result" not in result.output and not completed_tool_evidence([result])
    assert "Do not repeat" in result.output["completion_guidance"]
    dispatch.details["research_read_error"]["required_source"] = "https://other.example/"
    with pytest.raises(ConversationConflict, match="binding differs"):
        asyncio.run(tool.observe(request, dispatch))
    assert research.calls == 1  # Observation never repeats provider work.


@pytest.mark.parametrize("kind", ["unknown", "partial", "wrong_source"])
def test_unknown_usage_or_unbound_failure_cannot_become_recoverable(kind):
    research, tool, _, _, request = setup()
    research.error = (ConversationModelError("unknown provider failure") if kind == "unknown" else
        ResearchSourceCoverageError(URL, model_usage=ConversationModelUsage.partial(
            model_attempts=1, tool_calls=1, input_tokens=20, output_tokens=5)) if kind == "partial" else
        ResearchSourceCoverageError("https://other.example/", model_usage=USAGE))
    with pytest.raises(ConversationModelError):
        asyncio.run(tool.execute(request))
    assert research.calls == 1
    unknown = asyncio.run(tool.observe(request, None))
    assert unknown.disposition == "conflict" and "source" not in unknown.evidence


def test_same_core_loop_chooses_a_different_reader_after_measured_source_failure():
    research, tool, _, _, request = setup()
    turn = _turn()
    calls = []

    class Route:
        action_names = ("research_current_options", "use_connector_tool")

        def available_for(self, turn): return True
        def active_context(self, turn): return ()
        def pending(self, turn, *, step_index): return None

        def execute(self, turn, call, *, step_index):
            calls.append(call.name)
            if call.name == "use_connector_tool":
                return ConversationActionReceipt(call, {"status": "verified",
                    "result": {"url": URL, "content": "The public manual was read."}})
            prepared = asyncio.run(tool.prepare(turn, call))
            observed = asyncio.run(tool.observe(request, asyncio.run(tool.execute(request))))
            return receipt(tool, call, prepared, observed, turn)

    class Model:
        calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                action = ConversationActionCall("research", "research_current_options", deepcopy(ARGS))
            elif self.calls == 2:
                result = kwargs["completed_action"]
                assert not completed_tool_evidence([result])
                assert result.output["recoverable_read_error"] is True
                action = ConversationActionCall("browser", "use_connector_tool", {"url": URL})
            else:
                assert kwargs["completed_action"].output["result"]["content"]
                return ConversationModelResult("De handleiding is gelezen.", "controlled")
            return ConversationModelResult(None, "controlled", action)

    actions = BackgroundConnectorActions((Route(),), Route.action_names, lambda _: None, 4)
    model = Model()
    result = LivingConversation(_Memory(), model, IdentityProfile("Eva", "Helpful", ("Stay truthful.",)), actions).respond(turn)
    assert result.text == "De handleiding is gelezen."
    assert model.calls == 3 and research.calls == 1
    assert calls == ["research_current_options", "use_connector_tool"]
    assert completed_tool_evidence(actions.receipts)


def test_completed_failed_research_keeps_its_accounted_usage_once():
    from eva.bootstrap.background_research import AccountedResearch

    records = []
    journal = SimpleNamespace(
        model_usage=lambda _: ConversationModelUsage.measured(tool_calls=0, input_tokens=100, output_tokens=10),
        begin_model_request=lambda _: "research-request",
        complete_model_request=lambda turn, request, usage, **kwargs: records.append((request, usage, kwargs)),
    )
    research = Research()
    accounted = AccountedResearch(research, turn=_turn(), journal=journal, authorize=lambda _: None,
        grant=SimpleNamespace(maximum_model_runs=10, maximum_input_tokens=10000,
                              maximum_output_tokens=1000), input_bound=100, output_bound=10)
    tool = OwnerResearchContinuationTool(accounted, lambda x: x)
    # Request configuration/preparation uses the wrapped real provider metadata.
    prepared = asyncio.run(tool.prepare(_turn(), ConversationActionCall("read", "research_current_options", deepcopy(ARGS))))
    request = SimpleNamespace(operation_id="op", tool_name=prepared.tool_name,
        arguments=prepared.arguments, expected_proof=prepared.expected_proof,
        observation_tool_name=prepared.observation_tool_name,
        observation_arguments=prepared.observation_arguments,
        tool_configuration_sha256=prepared.tool_configuration_sha256)
    result = asyncio.run(tool.observe(request, asyncio.run(tool.execute(request))))
    assert result.disposition == "conflict" and research.calls == 1
    assert records == [("research-request", USAGE, {"outcome": "failed"})]
    assert accounted.deadline_failure is None and accounted.citation_failure is None
