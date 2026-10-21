"""Owner controls resume research without losing its original private return route."""
from datetime import timedelta

import pytest

from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.application.conversation import ConversationTurn
from eva.application.delegated_work_delivery import PrivateWorkDestination
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.delegated_research import DelegatedResearchProfile
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.delegated_work_provider import MODEL_PROFILE_ID
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_delegated_research import PUBLIC, NOW


@pytest.mark.parametrize('original_return', [True, False])
def test_dashboard_resume_preserves_only_existing_research_destination(database, original_return):
    label = 'research-return-' + str(original_return)
    scope, _, binding = _seed_authority(database, label)
    owner = _id(label, 'principal')
    store = PostgresDelegatedWorkStore(database, scope)
    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(42, str(owner), ('42',), str(binding)),))
    source = ConversationTurn.private('tenant:'+label, 'installation:'+label, str(owner),
        'telegram:42', 'telegram:42:9', PUBLIC['question'], NOW.strftime('%Y-%m-%dT%H:%M:%SZ'))
    destination = PrivateWorkDestination.from_telegram_turn(source, owner_principal_id=owner,
        principal_binding_id=binding, telegram_user_id=42, chat_id='42', bindings=bindings)
    clock = [NOW]
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner, principal_binding_id=binding,
        research_profile=DelegatedResearchProfile(MODEL_PROFILE_ID, 'a'*64), clock=lambda: clock[0],
        delivery_destination_loader=lambda turn: destination if original_return and turn == source else None)
    first = service.execute('research', source, research=PUBLIC, request_key='start')['work']['work_id']
    original_spec = store.load(first, **service._owner).spec
    dashboard = ConversationTurn.private(source.tenant_id, source.installation_id, str(owner),
        'dashboard:'+str(binding), 'dashboard:resume', 'Werkbediening', source.occurred_at)
    service.execute('stop', dashboard, work_id=first, request_key='stop-first')
    clock[0] += timedelta(seconds=1)
    resumed = service.execute('resume', dashboard, work_id=first, request_key='resume-first')
    successor = resumed['work']['work_id']
    delivery = PostgresDelegatedWorkDeliveryStore(store, bindings_loader=lambda: bindings, clock=lambda: clock[0])
    assert delivery.load(successor, **service._owner) == (destination if original_return else None)
    assert service.execute('resume', dashboard, work_id=first, request_key='resume-first')['created'] is False
    assert store.load(first, **service._owner).spec == original_spec
    assert store.load(first, **service._owner).call_id is None
    assert store.load(successor, **service._owner).spec.research == original_spec.research
    with pytest.raises((DelegatedWorkConflict, ValueError)):
        delivery.load(successor, owner_principal_id=_id('other', 'principal'), principal_binding_id=binding)
    service.execute('stop', dashboard, work_id=successor, request_key='stop-second')
    clock[0] += timedelta(seconds=1)
    next_id = service.execute('resume', dashboard, work_id=successor, request_key='resume-second')['work']['work_id']
    assert delivery.load(next_id, **service._owner) == (destination if original_return else None)
