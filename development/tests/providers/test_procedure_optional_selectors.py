"""Model-filled optional selectors cannot abort an ordinary verified readback."""
from copy import deepcopy
import pytest
from eva.application.conversation import ConversationActionCall, ConversationTurn, ConversationConflict
from eva.application.procedure_references import ProcedureReferenceRejected
from eva.providers.procedure_reference_actions import ProcedureReferenceActions
from tests.application.test_procedure_references import setup, designate, SCOPE, NOW


def route(monkeypatch):
    import eva.providers.procedure_reference_actions as module
    host, store, service = setup()
    reference = designate(service)
    host.require_current = lambda: None
    monkeypatch.setattr(module, 'PostgresMemoryOutbox', lambda *a, **kw: store)
    monkeypatch.setattr(module, 'ProcedureReferences', lambda *a: service)
    actions = ProcedureReferenceActions(None, authority=None, gateway_factory=None, clock=lambda: NOW)
    monkeypatch.setattr(actions, '_host', lambda turn: host)
    monkeypatch.setattr(actions, '_scopes', lambda turn: (SCOPE,))
    turn = ConversationTurn.private(SCOPE.tenant_id, SCOPE.installation_id, SCOPE.principal_id,
                                    'chat', 'read', 'Read saved procedure', NOW)
    return actions, host, reference, turn


@pytest.mark.parametrize('selector', [{}, {'resource_path':''}, {'resource_path':None},
    {'resource_path':'','resource_offset':0}, {'resource_path':None,'resource_offset':None},
    {'resource_offset':0}, {'resource_offset':None}])
def test_real_reference_readback_preserves_original_call(monkeypatch, selector):
    actions, host, reference, turn = route(monkeypatch)
    call = ConversationActionCall('resolve', 'resolve_reusable_procedure',
                                 {'document_id':reference.document_id, **selector})
    original = deepcopy(call.arguments)
    receipt = actions._execute(turn, call)
    assert receipt.output['status'] == 'resolved'
    assert receipt.output['references'][0]['untrusted_content'] == host.bytes.decode()
    assert receipt.call == call and call.arguments == original


@pytest.mark.parametrize('offset', [1, -1, False, '0'])
def test_nondefault_offset_never_silently_changes_resource_selection(monkeypatch, offset):
    actions, host, reference, turn = route(monkeypatch)
    with pytest.raises((ConversationConflict, ProcedureReferenceRejected)):
        actions._execute(turn, ConversationActionCall('read', 'resolve_reusable_procedure',
            {'document_id':reference.document_id, 'resource_path':'', 'resource_offset':offset}))


def test_current_authority_still_required(monkeypatch):
    actions, host, reference, turn = route(monkeypatch)
    def revoked(turn): raise ConversationConflict('revoked')
    monkeypatch.setattr(actions, '_host', revoked)
    with pytest.raises(ConversationConflict, match='revoked'):
        actions._execute(turn, ConversationActionCall('read', 'resolve_reusable_procedure',
            {'document_id':reference.document_id, 'resource_path':'', 'resource_offset':0}))
