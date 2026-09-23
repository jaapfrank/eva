import pytest
from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory, ProcedureReferenceActions
from eva.providers.bundled import bundled_provider_factories


def test_opt_in_is_explicit_and_does_not_inspect_gateway_when_disabled(monkeypatch):
    monkeypatch.delenv('EVA_PROCEDURE_REFERENCES_ENABLED',raising=False)
    monkeypatch.setenv('EVA_MCP_GATEWAY_TOKEN','must-not-be-read')
    assert ProcedureReferenceProviderFactory.load_settings() is None
    assert sum(f.provider_id=='procedure_references' for f in bundled_provider_factories())==1


def test_invalid_opt_in_fails_closed(monkeypatch):
    monkeypatch.setenv('EVA_PROCEDURE_REFERENCES_ENABLED','maybe')
    with pytest.raises(RuntimeError):ProcedureReferenceProviderFactory.load_settings()


def test_tools_are_closed_without_owner_or_path_overrides():
    descriptors=ProcedureReferenceActions.conversation_tools()
    assert len(descriptors)==3
    for descriptor in descriptors:
        assert descriptor.input_schema['additionalProperties'] is False
        assert not {'owner','tenant','path','content','proof'} & set(descriptor.input_schema['properties'])


@pytest.mark.parametrize("step_index", [0, 1])
def test_pending_for_another_identity_does_not_block_chat_or_read_journal(monkeypatch, step_index):
    from types import SimpleNamespace
    from eva.application.conversation import ConversationConflict
    import eva.providers.procedure_reference_actions as module

    class OtherOwner:
        def authorize_turn(self, turn):
            raise ConversationConflict("connector mandate belongs to another owner or installation")

    def forbidden(*args, **kwargs):
        pytest.fail("a denied identity must not inspect a procedure journal")

    monkeypatch.setattr(module, "PostgresOperationalTimeline", forbidden)
    actions = ProcedureReferenceActions(None, authority=OtherOwner(), gateway_factory=forbidden)
    turn = SimpleNamespace(principal_id="visitor", conversation_id="chat", message_id="question")
    assert actions.pending(turn, step_index=step_index) is None


@pytest.mark.parametrize("has_pending", [False, True])
def test_only_existing_pending_procedure_requires_current_execution_authority(monkeypatch, has_pending):
    from types import SimpleNamespace
    from eva.application.conversation import ConversationConflict
    import eva.providers.procedure_reference_actions as module

    class Owner:
        scope = "test-scope"
        def authorize_turn(self, turn):
            pass

    class Timeline:
        def __init__(self, engine, scope):
            assert scope == "test-scope"
        def events(self, slot):
            return [SimpleNamespace(thread_id=slot)] if has_pending else []
        def load(self, thread_id):
            pytest.fail("denied pending procedure must not replay")

    monkeypatch.setattr(module, "PostgresOperationalTimeline", Timeline)
    actions = ProcedureReferenceActions(None, authority=Owner(), gateway_factory=None)
    def denied(turn):
        raise ConversationConflict("mandate revoked")
    monkeypatch.setattr(actions, "_host", denied)
    turn = SimpleNamespace(principal_id="owner", conversation_id="chat", message_id="question")
    if has_pending:
        with pytest.raises(ConversationConflict, match="mandate revoked"):
            actions.pending(turn)
    else:
        assert actions.pending(turn) is None



def _private_turn():
    from eva.application.conversation import ConversationTurn
    return ConversationTurn.private("tenant", "install", "owner", "chat", "message", "Use a procedure", "2026-09-23T12:00:00Z")


def test_background_slots_separate_jobs_and_preserve_foreground_hash():
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.contracts.canonical_json import canonical_sha256
    actions = ProcedureReferenceActions(None, authority=None, gateway_factory=None)
    turn = _private_turn()
    foreground = actions._slot(turn, 0)
    assert foreground == "procedure-action:" + canonical_sha256({
        "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
        "message_id": turn.message_id, "step_index": 0})
    with background_execution(BackgroundExecutionScope(turn, "execution:one")):
        first = actions._slot(turn, 0)
        assert actions._slot(turn, 1) != first
    with background_execution(BackgroundExecutionScope(turn, "execution:two")):
        assert actions._slot(turn, 0) not in {first, foreground}
    with background_execution(BackgroundExecutionScope(turn, "execution:one")):
        assert actions._slot(turn, 0) == first != foreground
    assert actions._slot(turn, 0) == foreground


def test_background_designation_rejected_before_host_or_journal(monkeypatch):
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    actions = ProcedureReferenceActions(None, authority=None, gateway_factory=None)
    monkeypatch.setattr(actions, "_host", lambda *_: pytest.fail("No designation host access"))
    turn = _private_turn()
    with background_execution(BackgroundExecutionScope(turn, "execution:one")):
        with pytest.raises(ConversationConflict, match="read-only"):
            actions.execute(turn, ConversationActionCall("call", "designate_reusable_procedure", {}))


@pytest.mark.parametrize("denied", [False, True])
def test_background_readback_checks_job_immediately_before_gateway(monkeypatch, denied):
    from types import SimpleNamespace as NS
    import eva.adapters.postgres.procedure_reference_host as module
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.application.conversation import ConversationConflict
    turn = _private_turn()
    events = []
    class Gateway:
        async def verify_catalog(self): events.append("catalog")
        def planner(self): return None
        async def observe(self, command, dispatch):
            events.append("observe")
            return NS(disposition="verified", evidence={"source": "mcp_gateway_readback"}, observed={"content": "Checked procedure"})
    class Wrapper:
        def __init__(self, *args, **kwargs): pass
        def _request_from_admission(self, *args): return "bound-request"
    monkeypatch.setattr(module, "PostgresExternalConversationTools", Wrapper)
    host = object.__new__(module.PostgresProcedureReferenceHost)
    host.turn = turn
    host.engine = None
    host.authority = NS(authorize_turn=lambda _: None)
    host.gateway_factory = Gateway
    host.require_current = lambda: events.append("authority")
    host._selected = (turn, {"action_arguments": {"path": "procedure.md"}, "action_name": "save_document"})
    def guard(actual):
        assert actual == turn
        events.append("job-guard")
        if denied:
            raise ConversationConflict("work stopped or budget exhausted")
    with background_execution(BackgroundExecutionScope(turn, "execution:one", guard)):
        if denied:
            with pytest.raises(ConversationConflict, match="stopped or budget"):
                host.read_current(None, "procedure.md")
            assert "observe" not in events
        else:
            assert host.read_current(None, "procedure.md") == b"Checked procedure"
            assert events[events.index("observe") - 1] == "job-guard"
    assert events.count("job-guard") == 1
