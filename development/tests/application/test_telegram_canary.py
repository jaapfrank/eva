from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eva.application.memory_audience import TelegramGroupConsentReference
from eva.application.telegram_canary import TelegramCanaryBridge
from eva.application.telegram_group_consent import TelegramGroupConsent


NOW = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


class _Observations:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], datetime] = {}

    def observe(self, **values) -> None:  # type: ignore[no-untyped-def]
        self.items[(values["principal_id"], values["group_chat_id"])] = values[
            "expires_at"
        ]

    def recently_observed(self, **values) -> bool:  # type: ignore[no-untyped-def]
        expires = self.items.get((values["principal_id"], values["group_chat_id"]))
        return expires is not None and values["now"] <= expires


class _Consent:
    def __init__(self) -> None:
        self.active: dict[tuple[str, str], TelegramGroupConsent] = {}

    def grant(self, consent, *, operation_id):  # type: ignore[no-untyped-def]
        self.active[(consent.principal_id, consent.group_chat_id)] = consent
        return consent

    def revoke(self, *, principal_id, group_chat_id, operation_id, now):  # type: ignore[no-untyped-def]
        return self.active.pop((principal_id, group_chat_id), None) is not None

    def permits(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
        return self.active_reference(
            principal_id=principal_id, group_chat_id=group_chat_id, now=now
        ) is not None

    def active_reference(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
        consent = self.active.get((principal_id, group_chat_id))
        if consent is None or not consent.permits(
            principal_id=principal_id, group_chat_id=group_chat_id, now=now
        ):
            return None
        return TelegramGroupConsentReference(
            principal_id, group_chat_id, "telegram-group-consent:test"
        )


def _bridge() -> tuple[TelegramCanaryBridge, _Observations, _Consent]:
    observations = _Observations()
    consent = _Consent()
    return (
        TelegramCanaryBridge(observations=observations, group_consent=consent),
        observations,
        consent,
    )


def test_private_accounts_are_distinct() -> None:
    bridge, _, _ = _bridge()
    first = bridge.admit(
        telegram_user_id=7,
        chat_id="7",
        text="Hoi",
        operation_id="telegram-canary:1",
        now=NOW,
    )
    second = bridge.admit(
        telegram_user_id=8,
        chat_id="8",
        text="Hoi",
        operation_id="telegram-canary:2",
        now=NOW,
    )
    assert first.audience is not None
    assert second.audience is not None
    assert first.principal_id == "telegram:7"
    assert second.principal_id == "telegram:8"
    assert first.audience != second.audience


def test_first_group_turn_is_not_admitted_and_creates_bounded_observation() -> None:
    bridge, observations, _ = _bridge()
    result = bridge.admit(
        telegram_user_id=7,
        chat_id="-42",
        text="Dit is privé binnen de groep",
        operation_id="telegram-canary:3",
        now=NOW,
    )
    assert result.audience is None
    assert "/share -42" in (result.local_reply or "")
    assert observations.items[("telegram:7", "-42")] == NOW + timedelta(hours=24)


def test_private_share_then_admits_only_that_person_in_that_group() -> None:
    bridge, _, _ = _bridge()
    bridge.admit(
        telegram_user_id=7,
        chat_id="-42",
        text="eerste groepsbericht",
        operation_id="telegram-canary:4",
        now=NOW,
    )
    shared = bridge.admit(
        telegram_user_id=7,
        chat_id="7",
        text="/share -42",
        operation_id="telegram-canary:5",
        now=NOW,
    )
    admitted = bridge.admit(
        telegram_user_id=7,
        chat_id="-42",
        text="nu wel",
        operation_id="telegram-canary:6",
        now=NOW,
    )
    other = bridge.admit(
        telegram_user_id=8,
        chat_id="-42",
        text="niet van persoon acht",
        operation_id="telegram-canary:7",
        now=NOW,
    )
    assert "tot" in (shared.local_reply or "")
    assert admitted.audience is not None
    assert admitted.audience.kind == "telegram_group"
    assert other.audience is None


def test_share_requires_recent_observation_and_stop_revokes() -> None:
    bridge, _, _ = _bridge()
    denied = bridge.admit(
        telegram_user_id=7,
        chat_id="7",
        text="/share -42",
        operation_id="telegram-canary:8",
        now=NOW,
    )
    assert denied.local_reply == "Die groep hoort niet bij jouw gekoppelde Telegram-account."
    bridge.admit(
        telegram_user_id=7,
        chat_id="-42",
        text="observeer",
        operation_id="telegram-canary:9",
        now=NOW,
    )
    bridge.admit(
        telegram_user_id=7,
        chat_id="7",
        text="/share -42 1d",
        operation_id="telegram-canary:10",
        now=NOW,
    )
    stopped = bridge.admit(
        telegram_user_id=7,
        chat_id="7",
        text="/stop-sharing -42",
        operation_id="telegram-canary:11",
        now=NOW,
    )
    assert stopped.local_reply == "Delen met die groep is gestopt."
    again = bridge.admit(
        telegram_user_id=7,
        chat_id="-42",
        text="weer dicht",
        operation_id="telegram-canary:12",
        now=NOW,
    )
    assert again.audience is None
