import pytest

from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
    admit_telegram_memory,
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
