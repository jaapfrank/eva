"""Explicit per-person consent boundary for Telegram group memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Protocol


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MAXIMUM_DURATION_SECONDS = 31 * 24 * 60 * 60
_GROUP = re.compile(r"^-[0-9]{1,31}$")
_CONSENT_COMMAND = re.compile(
    r"^/share\s+(-[0-9]{1,31})(?:\s+([1-9]|[12][0-9]|3[01])d)?$"
)
_REVOKE_COMMAND = re.compile(r"^/stop-sharing\s+(-[0-9]{1,31})$")


@dataclass(frozen=True, slots=True)
class TelegramGroupConsent:
    """A person may expose only their own group contributions, for a bounded time."""

    group_chat_id: str
    principal_id: str
    granted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not _GROUP.fullmatch(self.group_chat_id):
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


class TelegramGroupConsentStorePort(Protocol):
    """Append-only consent state; group messages must fail closed without it."""

    def grant(
        self, consent: TelegramGroupConsent, *, operation_id: str
    ) -> TelegramGroupConsent: ...

    def revoke(
        self,
        *,
        principal_id: str,
        group_chat_id: str,
        operation_id: str,
        now: datetime,
    ) -> bool: ...

    def permits(
        self, *, principal_id: str, group_chat_id: str, now: datetime
    ) -> bool: ...


class TelegramGroupConsentResponder:
    """Private-chat command parser. The worker supplies the trusted identity check."""

    def __init__(self, store: TelegramGroupConsentStorePort) -> None:
        self._store = store

    def handle(
        self,
        *,
        text: str,
        principal_id: str,
        allowed_group_chat_ids: tuple[str, ...],
        operation_id: str,
        now: datetime,
    ) -> str | None:
        match = _CONSENT_COMMAND.fullmatch(text.strip())
        if match is not None:
            group_chat_id, days_raw = match.groups()
            if group_chat_id not in allowed_group_chat_ids:
                return "Die groep hoort niet bij jouw gekoppelde Telegram-account."
            days = 7 if days_raw is None else int(days_raw)
            consent = self._store.grant(
                TelegramGroupConsent(
                    group_chat_id,
                    principal_id,
                    now,
                    now + timedelta(days=days),
                ),
                operation_id=operation_id,
            )
            return (
                f"Ik gebruik jouw bijdragen in die groep tot "
                f"{consent.expires_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                "Stoppen kan met /stop-sharing gevolgd door het groepsnummer."
            )
        match = _REVOKE_COMMAND.fullmatch(text.strip())
        if match is not None:
            group_chat_id = match.group(1)
            if group_chat_id not in allowed_group_chat_ids:
                return "Die groep hoort niet bij jouw gekoppelde Telegram-account."
            if self._store.revoke(
                principal_id=principal_id,
                group_chat_id=group_chat_id,
                operation_id=operation_id,
                now=now,
            ):
                return "Delen met die groep is gestopt."
            return "Voor die groep staat geen actieve deeltoestemming aan."
        return None

    def permits(self, *, principal_id: str, group_chat_id: str, now: datetime) -> bool:
        return self._store.permits(
            principal_id=principal_id, group_chat_id=group_chat_id, now=now
        )


__all__ = [
    "TelegramGroupConsent",
    "TelegramGroupConsentResponder",
    "TelegramGroupConsentStorePort",
]
