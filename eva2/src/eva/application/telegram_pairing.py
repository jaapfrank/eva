"""Durable-contract primitives for a two-sided Telegram account pairing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
from typing import Protocol


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


@dataclass(frozen=True, slots=True)
class TelegramDynamicBinding:
    """A binding created by a completed two-sided pairing, never by free text."""

    telegram_user_id: int
    principal_id: str

    def __post_init__(self) -> None:
        if self.telegram_user_id < 1 or not self.principal_id:
            raise ValueError("Telegram dynamic binding is invalid")


class TelegramPairingStorePort(Protocol):
    """Durable pairing state. Implementations must survive an ingress restart."""

    def start(
        self, challenge: TelegramPairingChallenge, *, update_id: int, now: datetime
    ) -> TelegramPairingChallenge: ...

    def confirm(
        self,
        code: str,
        *,
        confirmer_user_id: int,
        confirmer_chat_id: str,
        now: datetime,
    ) -> TelegramDynamicBinding | None: ...

    def resolve(self, *, telegram_user_id: int, chat_id: str) -> str | None: ...


def pairing_expiry(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now) or now.microsecond:
        raise ValueError("Telegram pairing time must be whole-second UTC")
    return now + timedelta(minutes=10)


def derive_pairing_code(
    secret: bytes, *, telegram_user_id: int, update_id: int
) -> str:
    """Recreate a short-lived code after restart without storing it in the ledger."""
    if len(secret) < 32 or telegram_user_id < 1 or update_id < 0:
        raise ValueError("Telegram pairing derivation input is invalid")
    digest = hmac.new(
        secret,
        f"eva2-telegram-pairing-v1:{telegram_user_id}:{update_id}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    value = int.from_bytes(digest[:9], "big")
    code = "".join(alphabet[(value >> (5 * offset)) & 31] for offset in range(12))
    return f"EVA-{code}"


__all__ = [
    "TelegramDynamicBinding",
    "TelegramPairingChallenge",
    "TelegramPairingStorePort",
    "derive_pairing_code",
    "pairing_expiry",
]
