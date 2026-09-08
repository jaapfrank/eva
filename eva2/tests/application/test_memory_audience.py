import pytest

from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
    admit_telegram_memory,
    memory_audience_from_wire,
    memory_audience_to_wire,
)


CONSENT = TelegramGroupConsentReference(
    principal_id="person:jaap",
    group_chat_id="-100123",
    operation_id="telegram-group-consent:42",
)


def test_private_telegram_memory_has_only_a_person_audience() -> None:
    admission = admit_telegram_memory(
        principal_id="person:jaap",
        chat_id="101",
        group_consent_permitted=False,
    )

    assert admission.audience == MemoryAudience(
        kind="private_person",
        audience_id="person:person:jaap",
        principal_id="person:jaap",
    )


def test_group_telegram_memory_fails_closed_without_active_consent() -> None:
    with pytest.raises(ValueError, match="requires active consent"):
        admit_telegram_memory(
            principal_id="person:jaap",
            chat_id="-100123",
            group_consent_permitted=False,
        )


def test_group_telegram_memory_binds_author_group_and_consent_reference() -> None:
    admission = admit_telegram_memory(
        principal_id="person:jaap",
        chat_id="-100123",
        group_consent_permitted=True,
        consent_ref=CONSENT,
    )

    assert admission.audience == MemoryAudience(
        kind="telegram_group",
        audience_id="telegram-group:-100123",
        principal_id="person:jaap",
        group_chat_id="-100123",
        consent_ref=CONSENT,
    )


@pytest.mark.parametrize(
    ("chat_id", "permitted", "consent_ref"),
    (
        ("-100123", True, None),
        ("101", True, CONSENT),
        ("-100123", False, CONSENT),
        ("not-a-chat", False, None),
    ),
)
def test_telegram_memory_never_defaults_a_group_or_invalid_chat_to_private(
    chat_id: str, permitted: bool, consent_ref: TelegramGroupConsentReference | None
) -> None:
    with pytest.raises(ValueError):
        admit_telegram_memory(
            principal_id="person:jaap",
            chat_id=chat_id,
            group_consent_permitted=permitted,
            consent_ref=consent_ref,
        )


@pytest.mark.parametrize("permitted", (1, 0, "yes", None))
def test_telegram_memory_requires_a_real_boolean_consent_flag(
    permitted: object,
) -> None:
    with pytest.raises(ValueError, match="consent flag"):
        admit_telegram_memory(  # type: ignore[arg-type]
            principal_id="person:jaap",
            chat_id="-100123",
            group_consent_permitted=permitted,
            consent_ref=CONSENT,
        )


@pytest.mark.parametrize(
    "consent_ref",
    (
        TelegramGroupConsentReference(
            principal_id="person:frank",
            group_chat_id="-100123",
            operation_id="telegram-group-consent:42",
        ),
        TelegramGroupConsentReference(
            principal_id="person:jaap",
            group_chat_id="-100124",
            operation_id="telegram-group-consent:42",
        ),
    ),
)
def test_telegram_group_memory_rejects_cross_person_or_cross_group_consent(
    consent_ref: TelegramGroupConsentReference,
) -> None:
    with pytest.raises(ValueError, match="audience is invalid"):
        admit_telegram_memory(
            principal_id="person:jaap",
            chat_id="-100123",
            group_consent_permitted=True,
            consent_ref=consent_ref,
        )


def test_memory_audience_wire_round_trip_preserves_the_complete_group_grant() -> None:
    audience = MemoryAudience.telegram_group(
        principal_id="person:jaap",
        group_chat_id="-100123",
        consent_ref=CONSENT,
    )

    assert memory_audience_from_wire(memory_audience_to_wire(audience)) == audience


def test_memory_audience_wire_rejects_a_partial_or_cross_bound_group_grant() -> None:
    payload = memory_audience_to_wire(
        MemoryAudience.telegram_group(
            principal_id="person:jaap",
            group_chat_id="-100123",
            consent_ref=CONSENT,
        )
    )
    del payload["consent_operation_id"]
    with pytest.raises(ValueError, match="incomplete"):
        memory_audience_from_wire(payload)

    payload = memory_audience_to_wire(
        MemoryAudience.telegram_group(
            principal_id="person:jaap",
            group_chat_id="-100123",
            consent_ref=CONSENT,
        )
    )
    payload["consent_principal_id"] = "person:frank"
    with pytest.raises(ValueError, match="audience is invalid"):
        memory_audience_from_wire(payload)



def household():
    from eva.application.memory_audience import HouseholdConsentReference
    return MemoryAudience.household(principal_id="household-device:abc", household_id="home",
        consent_ref=HouseholdConsentReference("household-device:abc", "home", "household-consent:1"))


def test_household_audience_roundtrips_without_person_or_telegram_fallback():
    from eva.application.memory_audience import TelegramMemoryAdmission
    audience = household()
    wire = memory_audience_to_wire(audience)
    assert memory_audience_from_wire(wire) == audience
    assert wire["audience_id"] == "household:home"
    for chat in ("101", "-100123", None):
        with pytest.raises(ValueError):
            TelegramMemoryAdmission(audience, chat)


@pytest.mark.parametrize("field,value", [
    ("kind", "private_person"), ("kind", "telegram_group"),
    ("audience_id", "person:jaap"), ("principal_id", "person:jaap"),
    ("household_id", "neighbours"), ("consent_household_id", "neighbours"),
    ("consent_operation_id", "telegram-group-consent:1"),
    ("group_chat_id", "-100123"),
])
def test_household_wire_rejects_mixed_or_cross_bound_authority(field, value):
    wire = memory_audience_to_wire(household())
    wire[field] = value
    with pytest.raises(ValueError):
        memory_audience_from_wire(wire)


def test_household_turn_and_runtime_require_exact_scope_and_current_mandate():
    from dataclasses import replace
    from eva.application.conversation import ConversationTurn
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    turn = ConversationTurn("tenant", "installation", "household-device:abc",
        "household:home:conversation:one", "household:home:conversation:one:request:1",
        "Hallo", "2026-09-08T21:00:00Z", household())
    for changes in ({"conversation_id": "telegram:101"}, {"message_id": "private:1"},
                    {"project_id": "owner-project"}):
        with pytest.raises(ValueError):
            replace(turn, **changes)
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._runtime_tenant_id = "tenant"
    runtime._runtime_installation_id = "installation"
    with pytest.raises(ValueError, match="no mandate verifier"):
        runtime.admit_turn_audience(turn)
    class Resolver:
        active = turn.audience.household_consent_ref
        def active_reference(self, **kwargs):
            assert kwargs["principal_id"] == turn.principal_id
            assert kwargs["household_id"] == "home"
            return self.active
    runtime._household_consent = Resolver()
    runtime.admit_turn_audience(turn)
    runtime._household_consent.active = None
    with pytest.raises(ValueError, match="not active and exact"):
        runtime.admit_turn_audience(turn)
