"""A native research timeout remains a timeout after its durable failure receipt."""
from types import SimpleNamespace

import pytest

from eva.adapters.model.contracts import ConversationModelDeadlineExceeded
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, ConversationTurn
from eva.bootstrap.background_research import task_research_tools
from tests.postgres.test_operational_timeline import timeline_engine


def test_native_timeout_persists_failure_and_never_repeats_provider(timeline_engine):
    turn = ConversationTurn.private("research-deadline", "home", "owner", "chat", "message",
        "Research", "2026-09-29T06:00:00Z")
    journal = PostgresConversationJournal(timeline_engine,
        external_timeline_scope(turn.tenant_id, turn.installation_id, "test"))
    journal.admit(turn)
    request = journal.begin_model_request(turn)
    journal.complete_model_request(turn, request, ConversationModelUsage.measured(
        model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=10))

    class Research:
        calls = 0
        def native_research_timeout_seconds(self): return 120
        def research_configuration_sha256(self): return "b" * 64
        def research_request_payload(self, **kwargs): return {**kwargs, "options": list(kwargs["options"])}
        def research_current_options(self, **kwargs):
            self.calls += 1
            raise ConversationModelDeadlineExceeded("research provider deadline expired")

    model = Research()
    tools = task_research_tools(timeline_engine, model, turn=turn, journal=journal,
        authorize=lambda turn: None,
        grant=SimpleNamespace(maximum_model_runs=32, maximum_input_tokens=100000,
            maximum_output_tokens=10000),
        profile=SimpleNamespace(maximum_response_input_tokens=1000, maximum_response_output_tokens=1000))
    call = ConversationActionCall("research-1", "research_current_options", {
        "question": "Read the public specification", "title": "Research",
        "options": ["https://docs.example/specification"], "research_mode": "topic",
        "public_review": "not_applicable"})
    with background_execution(BackgroundExecutionScope(turn, "work:deadline", lambda turn: None)):
        with pytest.raises(ConversationModelDeadlineExceeded):
            tools.execute(turn, call)
        events = tools._timeline(turn).events(tools._thread_id(turn, call.name, 0))
        assert events[-1].event_type == "RECOVERY_FAILED"
        assert events[0].payload["data"]["request_execution_timeout_seconds"] == 125
        with pytest.raises(ConversationModelDeadlineExceeded):
            tools.pending(turn)
    assert model.calls == 1
    assert journal.model_usage(turn).measurement != "measured"
