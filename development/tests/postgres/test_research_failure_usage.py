from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.application.conversation import ConversationConflict, ConversationModelUsage, ConversationTurn
from eva.application.orchestration import ToolProofObservation, ToolRequest, VerifiedToolOrchestrator


USAGE = ConversationModelUsage.measured(
    model_attempts=1, tool_calls=0, input_tokens=39122, output_tokens=1267,
)
NOW = datetime(2026, 9, 12, 19, 0, tzinfo=UTC)
TURN = ConversationTurn.private("tenant", "installation", "owner", "chat", "message",
    "Onderzoek de primaire bron.", "2026-09-12T19:00:00Z")


class Timeline(PostgresOperationalTimeline):
    """Exercise real failure serialization with in-memory load/transition ports."""

    def __init__(self, thread_id):
        self.state = SimpleNamespace(thread_id=thread_id, revision=1, retry_count=0,
            last_error_fingerprint=None, phase="research_admitted", status="active")
        self.recorded = []

    def load(self, thread_id):
        assert thread_id == self.state.thread_id
        return self.state

    def transition(self, **kwargs):
        assert kwargs["expected_revision"] == self.state.revision
        self.recorded.append(kwargs)
        self.state = SimpleNamespace(**{
            **vars(self.state), **{k: kwargs[k] for k in ["status", "checkpoint_id", "retry_count",
                "last_error_fingerprint"]}, "revision": self.state.revision + 1,
        })
        return self.state


@pytest.mark.parametrize("measured", [True, False])
def test_owner_research_failure_records_usage_without_response_or_secret(measured):
    calls = []
    error = ConversationModelError("PRIVATE_RESPONSE_AND_SECRET", model_usage=USAGE if measured else None)
    def research(**kwargs):
        calls.append(kwargs)
        raise error
    actions = PostgresConversationActions.__new__(PostgresConversationActions)
    actions._research = SimpleNamespace(research_current_options=research)
    timeline = Timeline(actions._research_thread_id(TURN))
    actions._timeline = lambda turn: timeline
    admission = {"action_name": "research_current_options", "principal_id": TURN.principal_id,
        "conversation_id": TURN.conversation_id, "source_message_id": TURN.message_id,
        "arguments": {"title": "Bron", "question": "Lees primaire documentatie.", "options": ["Bron"]}}
    events = (SimpleNamespace(payload={"data": admission}),)
    with pytest.raises(ConversationModelError):
        actions._resume_research(TURN, events)
    assert len(calls) == len(timeline.recorded) == 1
    event = timeline.recorded[0]
    assert event["event_type"] == "RECOVERY_FAILED" and event["status"] == "failed"
    assert event["data"].get("model_usage") == (USAGE.to_wire() if measured else None)
    assert set(event["data"]) == ({"error_fingerprint", "retry_count", "model_usage"}
                                 if measured else {"error_fingerprint", "retry_count"})
    assert "PRIVATE_RESPONSE_AND_SECRET" not in json.dumps(event, default=str)
    assert event["thread_id"] == actions._research_thread_id(TURN)
    admission["principal_id"] = "another-owner"
    with pytest.raises(ConversationConflict, match="scope differs"):
        actions._resume_research(TURN, events)
    assert len(calls) == 1


def test_native_tool_failure_uses_same_numeric_usage_event_without_retry():
    timeline = Timeline("research:test")
    orchestrator = VerifiedToolOrchestrator(timeline, object(), clock=lambda: NOW)
    request = ToolRequest(thread_id="research:test", operation_id="op:test", checkpoint_prefix="check:test",
        tool_name="research.public_sources", arguments={}, expected_proof={})
    result = asyncio.run(orchestrator._record_recovery_failure(request, timeline.state,
        ConversationModelError("PRIVATE_RESPONSE_AND_SECRET", model_usage=USAGE)))
    assert result.status == "failed" and len(timeline.recorded) == 1
    assert timeline.recorded[0]["data"]["model_usage"] == USAGE.to_wire()
    assert "PRIVATE_RESPONSE_AND_SECRET" not in json.dumps(timeline.recorded, default=str)


def test_failure_contract_does_not_accept_response_dict_as_usage():
    with pytest.raises(ValueError, match="usage is invalid"):
        ConversationModelError("failed", model_usage={"text": "private"})
    timeline = Timeline("research:test")
    with pytest.raises(ValueError, match="usage is invalid"):
        timeline.record_recovery_failure(thread_id="research:test", expected_revision=1,
            checkpoint_id="failed", operation_id=None, error_text="failed", occurred_at=NOW,
            model_usage={"text": "private"})
    assert not timeline.recorded


@pytest.mark.parametrize("args", [(), ("original",), ("original", 42)])
def test_model_error_preserves_runtime_error_arguments(args):
    error = ConversationModelError(*args, model_usage=USAGE)
    assert error.args == args and str(error) == str(RuntimeError(*args))
    assert error.model_usage is USAGE
    assert ConversationModelError(*args).model_usage is None


def test_native_failure_readback_does_not_duplicate_usage_or_repeat_model_dispatch():
    from tests.application.test_orchestration import Timeline as MemoryTimeline

    class Journal(MemoryTimeline):
        record_recovery_failure = PostgresOperationalTimeline.record_recovery_failure

        def transition(self, **kwargs):
            kwargs.setdefault("deadline", None)
            return super().transition(**kwargs)

    class Tool:
        calls = 0

        async def execute(self, request):
            self.calls += 1
            raise ConversationModelError("output budget", model_usage=USAGE)

        async def observe(self, request, dispatch):
            assert dispatch is None
            return ToolProofObservation("conflict", {}, {"outcome": "unknown"})

    journal, tool = Journal(), Tool()
    orchestrator = VerifiedToolOrchestrator(journal, tool, clock=lambda: NOW)
    request = ToolRequest(thread_id="thread:1", operation_id="op:test", checkpoint_prefix="check:test",
        tool_name="research.public_sources", arguments={}, expected_proof={})
    assert asyncio.run(orchestrator.run(request)).status == "failed"
    first = [event.payload["data"]["model_usage"] for event in journal.recorded
             if "model_usage" in event.payload.get("data", {})]
    assert first == [USAGE.to_wire()]
    asyncio.run(orchestrator.run(request))
    later = [event.payload["data"]["model_usage"] for event in journal.recorded
             if "model_usage" in event.payload.get("data", {})]
    assert later == first and tool.calls == 1
