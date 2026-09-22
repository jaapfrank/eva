from uuid import UUID

import pytest

from eva.application.conversation import ConversationTurn
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.delegated_work_delivery import private_destination_loader

OWNER = UUID('33333333-3333-3333-3333-333333333333')
BINDING = UUID('44444444-4444-4444-4444-444444444444')


def bindings(user=42):
    return TelegramPrincipalBindings((TelegramPrincipalBinding(
        user, str(OWNER), (str(user),), str(BINDING)),))


def turn(conversation='telegram:42'):
    return ConversationTurn.private(tenant_id='tenant', installation_id='installation',
        principal_id=str(OWNER), conversation_id=conversation, message_id='message:1',
        text='Onderzoek dit', occurred_at='2026-09-22T10:00:00Z')


def test_destination_loader_refreshes_binding_instead_of_redirecting_old_turn():
    current = bindings()
    resolve = private_destination_loader(owner_principal_id=OWNER,
        principal_binding_id=BINDING, bindings_loader=lambda:current)
    destination = resolve(turn())
    assert destination.chat_id == '42'
    assert destination.origin_conversation_id == 'telegram:42'
    current = bindings(43)
    with pytest.raises(ValueError, match='not current'):
        resolve(turn())


def test_destination_loader_does_not_infer_telegram_route_for_website():
    resolve = private_destination_loader(owner_principal_id=OWNER,
        principal_binding_id=BINDING,
        bindings_loader=lambda:pytest.fail('website must not guess a Telegram destination'))
    assert resolve(turn('web:owner')) is None


@pytest.mark.parametrize('conversation', ['telegram:-42', 'telegram:042', 'telegram:４２', 'telegram:0'])
def test_destination_loader_rejects_non_private_or_noncanonical_envelope(conversation):
    resolve = private_destination_loader(owner_principal_id=OWNER,
        principal_binding_id=BINDING, bindings_loader=bindings)
    with pytest.raises(ValueError):
        resolve(turn(conversation))


def test_status_loader_only_reads_with_the_fixed_owner_authority(monkeypatch):
    from eva.bootstrap.delegated_work_delivery import delivery_status_loader
    from eva.adapters.postgres import delegated_work_delivery as module
    calls=[]
    work_store=object()
    fresh=bindings
    class Store:
        def __init__(self, actual, *, bindings_loader):
            assert actual is work_store and bindings_loader is fresh
        def status(self, work_id, **authority):
            calls.append((work_id,authority))
            return "delivery_ambiguous"
    monkeypatch.setattr(module,"PostgresDelegatedWorkDeliveryStore",Store)
    load=delivery_status_loader(work_store,owner_principal_id=OWNER,
                               principal_binding_id=BINDING,bindings_loader=fresh)
    assert calls==[]
    assert load("work:original")=="delivery_ambiguous"
    assert calls==[("work:original",dict(owner_principal_id=OWNER,principal_binding_id=BINDING))]
