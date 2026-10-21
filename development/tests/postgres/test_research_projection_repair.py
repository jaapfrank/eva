"""Real private research admission uses the existing single bounded input repair."""
import pytest

from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import (ConversationActionCall, ConversationActionInputRejected,
    ConversationConflict, ConversationTurn, LivingConversation)
from eva.bootstrap.delegated_research import DelegatedResearchProfile
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.delegated_work_provider import MODEL_PROFILE_ID
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_delegated_research import PUBLIC, NOW
from tests.application.test_contact_model import Model, IDENTITY
from tests.application.test_conversation import _Memory


def setup(engine, label):
    scope, _, binding = _seed_authority(engine, label)
    store = PostgresDelegatedWorkStore(engine, scope)
    service = DelegatedWorkOwnerService(store, owner_principal_id=_id(label, 'principal'),
        principal_binding_id=binding, research_profile=DelegatedResearchProfile(MODEL_PROFILE_ID, 'a'*64),
        clock=lambda: NOW)
    turn = ConversationTurn.private('tenant:'+label, 'installation:'+label, str(service.owner_principal_id),
        'dashboard:'+str(binding), 'message', 'Research the public disclosure timeline',
        NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    return store, service, turn, PostgresDelegatedWorkActions(engine, service)


@pytest.mark.parametrize('repeat_invalid', [False, True])
def test_invalid_projection_gets_one_repair_and_no_premature_admission(database, repeat_invalid):
    store, service, turn, actions = setup(database, 'projection-repair-'+str(repeat_invalid))
    calls = []
    def choose(turn, context):
        receipt = context['completed_action']
        calls.append(receipt)
        if receipt is not None and receipt.output['status'] != 'invalid_input':
            return 'Ik zoek de openbare meldingsgeschiedenis uit.'
        if receipt is not None:
            assert receipt.output['action_admitted'] is False
            assert not store.list_owned(**service._owner)
            assert 'private@example.com' not in str(receipt.output)
        question = ('Onderzoek private@example.com' if receipt is None or repeat_invalid else
                    'When were CVE-2026-109618 and CVE-2026-109619 first reported?')
        return ConversationActionCall('research-'+str(len(calls)), 'start_background_research',
            {'research': PUBLIC | {'question': question}})
    model = Model(choose)
    reply = LivingConversation(_Memory(), model, IDENTITY, actions, contact_model=model).respond(turn)
    jobs = store.list_owned(**service._owner)
    if repeat_invalid:
        assert len(calls) == 2 and not jobs
        assert 'nog niet gestart' in reply.text
    else:
        assert len(calls) == 3 and len(jobs) == 1
        assert jobs[0].state == 'admitted' and jobs[0].call_id is None
        assert 'CVE-2026-109618' in jobs[0].spec.research.question


def test_failure_after_admission_is_never_reclassified_as_input_repair(database, monkeypatch):
    store, service, turn, actions = setup(database, 'projection-postadmission-failure')
    execute = service.execute
    attempts = []
    def uncertain(*args, **kwargs):
        result = execute(*args, **kwargs)
        attempts.append(result)
        raise ConversationConflict('admission response was lost')
    monkeypatch.setattr(service, 'execute', uncertain)
    call = ConversationActionCall('research', 'start_background_research',
        {'research': PUBLIC | {'question': 'Research CVE-2026-109618'}})
    with pytest.raises(ConversationConflict, match='response was lost') as error:
        actions.execute(turn, call)
    assert not isinstance(error.value, ConversationActionInputRejected)
    assert len(attempts) == 1 and len(store.list_owned(**service._owner)) == 1
