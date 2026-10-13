from datetime import UTC, datetime, timedelta

import pytest

from eva.application.telegram_group_consent import (
    TelegramGroupConsent,
    TelegramGroupConsentResponder,
)
from eva.application.memory_audience import TelegramGroupConsentReference


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_group_consent_is_person_and_group_scoped_and_expires() -> None:
    consent = TelegramGroupConsent(
        "-100123", "person:jaap", NOW, NOW + timedelta(days=7)
    )
    assert consent.permits(principal_id="person:jaap", group_chat_id="-100123", now=NOW)
    assert not consent.permits(
        principal_id="person:frank", group_chat_id="-100123", now=NOW
    )
    assert not consent.permits(
        principal_id="person:jaap", group_chat_id="-100124", now=NOW
    )
    assert not consent.permits(
        principal_id="person:jaap", group_chat_id="-100123", now=NOW + timedelta(days=8)
    )


def test_group_consent_rejects_private_chat_and_unbounded_expiry() -> None:
    with pytest.raises(ValueError, match="group chat"):
        TelegramGroupConsent("101", "person:jaap", NOW, NOW + timedelta(days=1))
    with pytest.raises(ValueError, match="duration"):
        TelegramGroupConsent("-100123", "person:jaap", NOW, NOW)
    with pytest.raises(ValueError, match="duration"):
        TelegramGroupConsent("-100123", "person:jaap", NOW, NOW + timedelta(days=32))


def test_private_commands_grant_and_revoke_only_an_allowed_group() -> None:
    class Store:
        def __init__(self) -> None:
            self.current: TelegramGroupConsent | None = None

        def grant(
            self, consent: TelegramGroupConsent, *, operation_id: str
        ) -> TelegramGroupConsent:
            self.current = consent
            return consent

        def revoke(
            self,
            *,
            principal_id: str,
            group_chat_id: str,
            operation_id: str,
            now: datetime,
        ) -> bool:
            if not self.permits(
                principal_id=principal_id, group_chat_id=group_chat_id, now=now
            ):
                return False
            self.current = None
            return True

        def permits(
            self, *, principal_id: str, group_chat_id: str, now: datetime
        ) -> bool:
            return self.current is not None and self.current.permits(
                principal_id=principal_id, group_chat_id=group_chat_id, now=now
            )

        def active_reference(
            self, *, principal_id: str, group_chat_id: str, now: datetime
        ) -> TelegramGroupConsentReference | None:
            if not self.permits(
                principal_id=principal_id, group_chat_id=group_chat_id, now=now
            ):
                return None
            return TelegramGroupConsentReference(
                principal_id=principal_id,
                group_chat_id=group_chat_id,
                operation_id="telegram-group-consent:1",
            )

    responder = TelegramGroupConsentResponder(Store())
    assert responder.handle(
        text="/share -100123 3d",
        principal_id="person:jaap",
        allowed_group_chat_ids=("101", "-100123"),
        operation_id="telegram-group-consent:1",
        now=NOW,
    ).startswith("Ik gebruik jouw bijdragen")
    assert responder.permits(
        principal_id="person:jaap", group_chat_id="-100123", now=NOW
    )
    assert responder.active_reference(
        principal_id="person:jaap", group_chat_id="-100123", now=NOW
    ) == TelegramGroupConsentReference(
        principal_id="person:jaap",
        group_chat_id="-100123",
        operation_id="telegram-group-consent:1",
    )
    assert (
        responder.handle(
            text="/share -999 7d",
            principal_id="person:jaap",
            allowed_group_chat_ids=("101", "-100123"),
            operation_id="telegram-group-consent:2",
            now=NOW,
        )
        == "Die groep hoort niet bij jouw gekoppelde Telegram-account."
    )
    assert (
        responder.handle(
            text="/stop-sharing -100123",
            principal_id="person:jaap",
            allowed_group_chat_ids=("101", "-100123"),
            operation_id="telegram-group-consent:3",
            now=NOW,
        )
        == "Delen met die groep is gestopt."
    )
    assert (
        responder.active_reference(
            principal_id="person:jaap", group_chat_id="-100123", now=NOW
        )
        is None
    )
