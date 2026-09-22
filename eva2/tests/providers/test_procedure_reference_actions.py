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
