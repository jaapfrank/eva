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
        def _request_from_admission(self, *args, **kwargs): return "bound-request"
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


@pytest.mark.parametrize("deadline, accepted", [
    ("2026-09-24T14:00:00+02:00", True),
    ("2026-09-23T14:00:00+02:00", False),
    ("invalid", False),
])
def test_designation_normalizes_valid_offset_and_rejects_expired_deadlines(monkeypatch, deadline, accepted):
    from dataclasses import dataclass
    from types import SimpleNamespace as NS
    from eva.application.conversation import ConversationActionCall
    from eva.application.procedure_references import ProcedureReferenceRejected
    import eva.providers.procedure_reference_actions as module
    turn = _private_turn()
    host = NS(current_save_call_id=lambda *args: "save-call",
              load_save=lambda *args: NS(admission={"conversation_id": turn.conversation_id,
                                                  "source_message_id": turn.message_id}),
              require_current=lambda: None)
    calls = []
    @dataclass
    class Reference:
        document_id: str = "saved-reference"
    class Service:
        def __init__(self, *args): pass
        def designate(self, scope, **kwargs):
            calls.append(kwargs)
            return Reference()
    monkeypatch.setattr(module, "ProcedureReferences", Service)
    monkeypatch.setattr(module, "PostgresMemoryOutbox", lambda *args, **kwargs: object())
    actions = ProcedureReferenceActions(None, authority=None, gateway_factory=None,
        clock=lambda: turn.occurred_at)
    monkeypatch.setattr(actions, "_host", lambda actual: host)
    args = {"document_path": "procedure.md", "use_when": "Compare costs", "retention_until": deadline}
    call = ConversationActionCall("designation", "designate_reusable_procedure", args)
    if accepted:
        receipt = actions._execute(turn, call)
        assert receipt.output["status"] == "designated"
        assert calls[0]["retention_until"] == "2026-09-24T12:00:00Z"
    else:
        with pytest.raises((ValueError, ProcedureReferenceRejected)):
            actions._execute(turn, call)
        assert not calls
    assert call.arguments["retention_until"] == deadline



def test_historical_read_request_keeps_old_identity_inside_different_background_turn():
    from dataclasses import replace
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools as Tools
    from eva.application.background_execution import BackgroundExecutionScope, background_execution, authorize_background_dispatch
    from eva.application.conversation import ConversationConflict
    original = _private_turn()
    current = replace(original, message_id="later-message", conversation_id="later-chat", text="Apply the saved procedure")
    admission = {"action_name": "save_document", "step_index": 1,
        "principal_id": original.principal_id, "conversation_id": original.conversation_id,
        "source_message_id": original.message_id,
        "request_tool_name": "save", "request_arguments": {"path": "procedure.md"},
        "request_expected_proof": {}, "request_observation_tool_name": "read",
        "request_observation_arguments": {"path": "procedure.md"}}
    tools = object.__new__(Tools)
    historical = tools._request_from_admission(original, "save_document", admission, 1)
    guarded = []
    with background_execution(BackgroundExecutionScope(current, "execution:later", guarded.append)):
        rebuilt = tools._request_from_admission(original, "save_document", admission, 1, historical=True)
        assert rebuilt == historical
        assert "tool:" + Tools._historical_operation_id(original, "save_document", admission, 1) == historical.thread_id
        with pytest.raises(ConversationConflict, match="source turn differs"):
            tools._request_from_admission(original, "save_document", admission, 1)
        with pytest.raises(ConversationConflict, match="source turn differs"):
            authorize_background_dispatch(original)
        authorize_background_dispatch(current)
    assert guarded == [current]
    with pytest.raises(ConversationConflict, match="namespace"):
        tools._request_from_admission(original, "save_document", dict(admission, execution_namespace="untrusted"), 1, historical=True)
