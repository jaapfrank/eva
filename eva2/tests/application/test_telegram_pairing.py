from datetime import UTC, datetime, timedelta

import pytest

from eva.application.telegram_pairing import TelegramPairingChallenge, pairing_expiry


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


def test_pairing_rejects_non_private_or_invalid_challenges() -> None:
    with pytest.raises(ValueError, match="private"):
        TelegramPairingChallenge("EVA-ABCDEFGHJKLM", 101, "-42", pairing_expiry(NOW))
    with pytest.raises(ValueError, match="code"):
        TelegramPairingChallenge("eva-no", 101, "101", pairing_expiry(NOW))
