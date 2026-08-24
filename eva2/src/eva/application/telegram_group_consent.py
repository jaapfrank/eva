"""Explicit per-person consent boundary for Telegram group memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MAXIMUM_DURATION_SECONDS = 31 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class TelegramGroupConsent:
    """A person may expose only their own group contributions, for a bounded time."""

    group_chat_id: str
    principal_id: str
    granted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.group_chat_id.startswith("-") or not self.group_chat_id[1:].isdigit():
            raise ValueError("Telegram group consent requires a group chat")
        if not _PRINCIPAL.fullmatch(self.principal_id):
            raise ValueError("Telegram group consent principal is invalid")
        for value in (self.granted_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                raise ValueError("Telegram group consent time must be UTC")
            if value.microsecond:
                raise ValueError("Telegram group consent time must use whole seconds")
        duration = (self.expires_at - self.granted_at).total_seconds()
        if not 0 < duration <= _MAXIMUM_DURATION_SECONDS:
            raise ValueError("Telegram group consent duration is outside 0..31 days")

    def permits(self, *, principal_id: str, group_chat_id: str, now: datetime) -> bool:
        return (
            principal_id == self.principal_id
            and group_chat_id == self.group_chat_id
            and now.tzinfo is not None
            and now.utcoffset() == UTC.utcoffset(now)
            and now.microsecond == 0
            and self.granted_at <= now <= self.expires_at
        )


__all__ = ["TelegramGroupConsent"]
