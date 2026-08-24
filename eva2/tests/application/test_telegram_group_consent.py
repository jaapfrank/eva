from datetime import UTC, datetime, timedelta

import pytest

from eva.application.telegram_group_consent import TelegramGroupConsent


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_group_consent_is_person_and_group_scoped_and_expires() -> None:
    consent = TelegramGroupConsent("-100123", "person:jaap", NOW, NOW + timedelta(days=7))
    assert consent.permits(principal_id="person:jaap", group_chat_id="-100123", now=NOW)
    assert not consent.permits(principal_id="person:frank", group_chat_id="-100123", now=NOW)
    assert not consent.permits(principal_id="person:jaap", group_chat_id="-100124", now=NOW)
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
