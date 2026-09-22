from dataclasses import replace
from uuid import UUID
import pytest
from eva.application.conversation import ConversationTurn
from eva.application.delegated_work_delivery import PrivateWorkDestination
from eva.application.memory_audience import MemoryAudience, HouseholdConsentReference
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings

OWNER = UUID(int=1)
BINDING = UUID(int=2)


def bindings(chats=("42",)):
    return TelegramPrincipalBindings((TelegramPrincipalBinding(42, str(OWNER), chats, str(BINDING)),))


def turn(principal="telegram:42", conversation="telegram:42"):
    return ConversationTurn.private("tenant", "installation", principal, conversation,
                                    "message:1", "A public task", "2026-08-26T12:00:00Z")


def destination(source=None, **changes):
    return PrivateWorkDestination.from_telegram_turn(source or turn(), **{
        "owner_principal_id": OWNER, "principal_binding_id": BINDING,
        "telegram_user_id": 42, "chat_id": "42", "bindings": bindings(), **changes})


@pytest.mark.parametrize("principal", ["telegram:42", str(OWNER)])
def test_private_destination_roundtrip_keeps_exact_origin(principal):
    value = destination(turn(principal))
    assert PrivateWorkDestination.from_payload(value.to_payload()) == value
    assert value.origin_message_id == "message:1" and value.chat_id == "42"
    with pytest.raises(ValueError):
        value.validate_current(bindings(("-100",)))


@pytest.mark.parametrize("changes", [
    {"chat_id":"12345678901234567", "telegram_user_id":12345678901234567},
    {"chat_id":"-100"}, {"chat_id":"43"}, {"telegram_user_id":True},
    {"owner_principal_id":UUID(int=9)}, {"principal_binding_id":UUID(int=9)},
    {"bindings":bindings(("-100",))},
])
def test_destination_rejects_nonprivate_or_unverified_routes(changes):
    with pytest.raises(ValueError):
        destination(**changes)


def test_shared_device_and_website_cannot_select_private_telegram_destination():
    actor = "household-device:" + "a" * 32
    shared = replace(turn(), principal_id=actor, conversation_id="household:home:conversation:test",
        message_id="household:home:conversation:test:1", audience=MemoryAudience.household(
        principal_id=actor, household_id="home", consent_ref=HouseholdConsentReference(
            actor, "home", "household-consent:test")))
    for source in (shared, turn(conversation="dashboard:owner"), turn(principal="Jaap")):
        with pytest.raises(ValueError):
            destination(source)
    raw = destination().to_payload()
    with pytest.raises(ValueError):
        PrivateWorkDestination.from_payload({**raw, "extra":"ignored"})
