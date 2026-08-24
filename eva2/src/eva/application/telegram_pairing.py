"""Durable-contract primitives for a two-sided Telegram account pairing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import re


_CODE = re.compile(r"^EVA-[A-Z2-9]{12}$")


@dataclass(frozen=True, slots=True)
class TelegramPairingChallenge:
    code: str
    requested_user_id: int
    requested_chat_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not _CODE.fullmatch(self.code):
            raise ValueError("Telegram pairing code is invalid")
        if self.requested_user_id < 1 or self.requested_chat_id != str(self.requested_user_id):
            raise ValueError("Telegram pairing must start in the requester's private chat")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() != UTC.utcoffset(self.expires_at):
            raise ValueError("Telegram pairing expiry must be UTC")

    @property
    def code_sha256(self) -> str:
        return hashlib.sha256(self.code.encode("ascii")).hexdigest()

    def may_confirm(self, *, user_id: int, chat_id: str, now: datetime) -> bool:
        return user_id != self.requested_user_id and user_id > 0 and chat_id == str(user_id) and now <= self.expires_at


def pairing_expiry(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now) or now.microsecond:
        raise ValueError("Telegram pairing time must be whole-second UTC")
    return now + timedelta(minutes=10)
