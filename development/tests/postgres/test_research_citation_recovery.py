"""The existing durable tool lane keeps the true failure and never repeats it."""
import pytest
from eva.adapters.model.contracts import ResearchCitationError
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationActionCall, ConversationModelUsage
from tests.postgres.test_background_research import setup
from tests.postgres.test_owner_research_continuation import Research, ARGS, RESEARCH
from tests.postgres.test_operational_timeline import timeline_engine


def test_citation_failure_keeps_durable_receipt_and_original_measured_failure(timeline_engine):
    error = ResearchCitationError("research citation URL is invalid", model_usage=
        ConversationModelUsage.measured(input_tokens=100, output_tokens=15, tool_calls=1))
    class InvalidCitation(Research):
        def research_current_options(self, **kwargs):
            self.calls += 1
            raise error
    research = InvalidCitation()
    turn, journal, grant, stopped, factory = setup(timeline_engine, "citation", research)
    tools = factory()
    call = ConversationActionCall("citation-1", RESEARCH, ARGS)
    with background_execution(BackgroundExecutionScope(turn, "work:source", lambda turn: None)):
        with pytest.raises(ResearchCitationError) as caught:
            tools.execute(turn, call)
        assert caught.value is error
        events = tools._timeline(turn).events(tools._thread_id(turn, call.name, 0))
        assert events[-1].event_type == "RECOVERY_FAILED"
        with pytest.raises(ResearchCitationError):
            tools.pending(turn)
        # A fresh object reads the already failed receipt; it does not replay.
        replay = factory().execute(turn, call)
        assert replay.output["status"] == "failed"
    assert research.calls == 1
    used = journal.model_usage(turn)
    assert used.measurement == "measured"
    assert (used.model_attempts, used.input_tokens, used.output_tokens) == (2, 110, 17)
