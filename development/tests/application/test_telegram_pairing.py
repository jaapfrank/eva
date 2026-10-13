from datetime import UTC, datetime, timedelta

import pytest

from eva.application.telegram_pairing import (
    TelegramPairingChallenge,
    TelegramPairingResponder,
    derive_pairing_code,
    pairing_expiry,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_pairing_requires_two_distinct_private_telegram_accounts() -> None:
    challenge = TelegramPairingChallenge("EVA-ABCDEFGHJKLM", 101, "101", pairing_expiry(NOW))

    assert challenge.may_confirm(user_id=202, chat_id="202", now=NOW)
    assert not challenge.may_confirm(user_id=101, chat_id="101", now=NOW)
    assert not challenge.may_confirm(user_id=202, chat_id="-42", now=NOW)
    assert not challenge.may_confirm(user_id=202, chat_id="202", now=NOW + timedelta(minutes=11))


def test_pairing_never_persists_plain_code_as_its_identifier() -> None:
    challenge = TelegramPairingChallenge("EVA-ABCDEFGHJKLM", 101, "101", pairing_expiry(NOW))

    assert challenge.code_sha256 != challenge.code
    assert len(challenge.code_sha256) == 64


def test_pairing_code_is_reproducible_after_restart_without_ledger_storage() -> None:
    secret = b"p" * 32
    first = derive_pairing_code(secret, telegram_user_id=404, update_id=81001)
    assert first == derive_pairing_code(secret, telegram_user_id=404, update_id=81001)
    assert first != derive_pairing_code(secret, telegram_user_id=404, update_id=81002)


def test_pairing_responder_never_starts_from_a_group_and_confirms_through_store() -> None:
    calls: list[tuple[str, object]] = []

    class Store:
        def start(self, challenge, *, update_id, now):  # type: ignore[no-untyped-def]
            calls.append(("start", (challenge, update_id, now)))
            return challenge

        def confirm(self, code, *, confirmer_user_id, confirmer_chat_id, now):  # type: ignore[no-untyped-def]
            calls.append(("confirm", (code, confirmer_user_id, confirmer_chat_id, now)))
            return None

        def resolve(self, *, telegram_user_id, chat_id):  # type: ignore[no-untyped-def]
            return None

    responder = TelegramPairingResponder(Store(), derivation_secret=b"p" * 32)  # type: ignore[arg-type]
    assert responder.begin(update_id=1, sender_id=101, chat_id="-42", now=NOW) is None
    assert responder.begin(update_id=1, sender_id=101, chat_id="101", now=NOW).startswith("Stuur deze")
    assert responder.confirm(code="EVA-ABCDEFGHJKLM", sender_id=101, chat_id="101", now=NOW).startswith("Deze")
    assert [name for name, _value in calls] == ["start", "confirm"]


def test_pairing_rejects_non_private_or_invalid_challenges() -> None:
    with pytest.raises(ValueError, match="private"):
        TelegramPairingChallenge("EVA-ABCDEFGHJKLM", 101, "-42", pairing_expiry(NOW))
    with pytest.raises(ValueError, match="code"):
        TelegramPairingChallenge("eva-no", 101, "101", pairing_expiry(NOW))
