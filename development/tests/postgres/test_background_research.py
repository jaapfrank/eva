"""Task research reuses durable tool receipts and one shared model budget."""
from types import SimpleNamespace
import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationModelUsage, ConversationTurn
from eva.bootstrap.background_research import task_research_tools
from tests.postgres.test_operational_timeline import timeline_engine
from tests.postgres.test_owner_research_continuation import Research, ARGS, RESEARCH


def setup(engine, label, research):
    turn = ConversationTurn.private("digital-research-" + label, "home", "owner", "chat",
        "message", "Read the specification.", "2026-09-28T19:00:00Z")
    ordinary = PostgresConversationJournal(engine, external_timeline_scope(
        turn.tenant_id, turn.installation_id, "test scope"), maximum_model_requests=8)
    ordinary.admit(turn)
    journal = BackgroundModelJournal(ordinary, turn, work_id="work:test")
    rid = journal.begin_model_request(turn)
    journal.complete_model_request(turn, rid, ConversationModelUsage.measured(
        tool_calls=1, input_tokens=10, output_tokens=2))
    grant = SimpleNamespace(maximum_model_runs=3, maximum_input_tokens=1000, maximum_output_tokens=1000)
    profile = SimpleNamespace(maximum_response_input_tokens=100, maximum_response_output_tokens=100)
    stopped = []
    def authorize(source):
        assert source == turn
        if stopped:
            raise ConversationConflict("stopped")
    def tools():
        return task_research_tools(engine, research, turn=turn, journal=journal,
            authorize=authorize, grant=grant, profile=profile)
    return turn, journal, grant, stopped, tools


def test_research_is_counted_once_and_durable_receipt_replays_without_provider(timeline_engine):
    research = Research()
    turn, journal, grant, stopped, tools = setup(timeline_engine, "success", research)
    with background_execution(BackgroundExecutionScope(turn, "work:test", lambda source: None)):
        call = ConversationActionCall("research", RESEARCH, ARGS)
        receipt = tools().execute(turn, call)
        assert receipt.output["status"] == "verified"
        assert receipt.output["result"]["sources"]
        replayed = tools().execute(turn, call)
        assert replayed.call == receipt.call and replayed.output["status"] == receipt.output["status"]
        assert research.calls == 1
        used = journal.model_usage(turn)
        assert used.measurement == "measured"
        assert (used.model_attempts, used.input_tokens, used.output_tokens) == (2, 510, 102)


@pytest.mark.parametrize("failure", ["known_usage", "unknown_usage", "hosted_input_over_grant"])
def test_failed_research_keeps_usage_and_does_not_replay(timeline_engine, failure):
    class FailedResearch(Research):
        def research_current_options(self, **kwargs):
            if failure == "hosted_input_over_grant":
                return super().research_current_options(**kwargs)
            self.calls += 1
            usage = (ConversationModelUsage.measured(tool_calls=1, input_tokens=500, output_tokens=1100)
                     if failure == "known_usage" else None)
            raise ConversationModelError("failed upstream", model_usage=usage)
    research = FailedResearch()
    turn, journal, grant, stopped, tools = setup(timeline_engine, failure, research)
    if failure == "hosted_input_over_grant":
        grant.maximum_input_tokens = 200  # Envelope fits; hosted retrieval adds more.
    with background_execution(BackgroundExecutionScope(turn, "work:test", lambda source: None)):
        call = ConversationActionCall("research", RESEARCH, ARGS)
        try:
            receipt = tools().execute(turn, call)
        except Exception:
            receipt = tools().pending(turn, step_index=0)
        assert receipt.output["status"] == "failed"
        replayed = tools().execute(turn, call)
        assert replayed.call == receipt.call and replayed.output["status"] == receipt.output["status"]
        assert research.calls == 1
        used = journal.model_usage(turn)
        assert used.model_attempts == 2
        assert used.measurement == ("partial" if failure == "unknown_usage" else "measured")
        if failure != "unknown_usage":
            assert used.input_tokens == 510
        # A subsequent operation cannot spend a fresh or uncertain budget.
        try:
            tools().execute(turn, ConversationActionCall("next", RESEARCH, ARGS), step_index=1)
        except Exception:
            pass
        assert research.calls == 1


@pytest.mark.parametrize("blocker", ["stopped", "output", "requests"])
def test_admission_denial_prevents_provider_dispatch(timeline_engine, blocker):
    research = Research()
    turn, journal, grant, stopped, tools = setup(timeline_engine, blocker, research)
    if blocker == "stopped":
        stopped.append(True)
    elif blocker == "output":
        grant.maximum_output_tokens = 50
    else:
        grant.maximum_model_runs = 1
    with background_execution(BackgroundExecutionScope(turn, "work:test", lambda source: None)):
        try:
            tools().execute(turn, ConversationActionCall("research", RESEARCH, ARGS))
        except Exception:
            pass
    assert research.calls == 0
    assert journal.model_usage(turn).model_attempts == 1


def test_real_conversation_loop_research_then_save_uses_shared_journal(timeline_engine, tmp_path):
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.application.conversation import ConversationToolDescriptor, IdentityProfile, LivingConversation
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
    from tests.postgres.test_owner_research_continuation import Connector, Memory
    from tests.support.scripted_pydantic_conversation import ScriptedCall, ScriptedPhase, ScriptedPydanticConversation
    research = Research()
    turn, journal, grant, stopped, tools = setup(timeline_engine, "loop", research)
    grant.maximum_model_runs = 8
    grant.maximum_input_tokens = 100000
    connector = Connector()
    route = PostgresExternalConversationTools(timeline_engine, connector, connector)
    actions = BackgroundConnectorActions((tools(), route), (RESEARCH, "save_finding"), lambda turn: None, 4)
    script = ScriptedPydanticConversation((
        ScriptedPhase.deferred(ScriptedCall("research", RESEARCH, ARGS)),
        ScriptedPhase.deferred(ScriptedCall("save", "save_finding", {"content": "PRIMARY_SOURCE_FINDING"})),
        ScriptedPhase.text("The finding was saved."),
    ))
    descriptor = ConversationToolDescriptor("save_finding", "Save the source finding", {
        "type": "object", "additionalProperties": False,
        "properties": {"content": {"type": "string"}}, "required": ["content"]})
    model = PydanticConversationModel(auth_file=tmp_path/"unused", model_backend=script.model,
        conversation_tools=(descriptor,))
    with background_execution(BackgroundExecutionScope(turn, "work:test", lambda source: None)):
        reply = LivingConversation(Memory(), model, IdentityProfile("Eva", "Helpful", ("Honest",)),
            actions).respond(turn, model_journal=journal)
    assert reply.text == "The finding was saved."
    assert connector.calls == ["save_finding"] and research.calls == 1
    assert journal.model_usage(turn).model_attempts == 5  # Seed, three chat calls, one research call.
    assert not script.remaining_phases
