"""Trusted Eva1-to-Eva2 Telegram bridge with per-person group consent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Protocol

from eva.application.memory_audience import MemoryAudience
from eva.application.telegram_group_consent import (
    TelegramGroupConsentResponder,
    TelegramGroupConsentStorePort,
)


_GROUP = re.compile(r"^-[0-9]{1,31}$")
_SHARE_GROUP = re.compile(
    r"^/share\s+(-[0-9]{1,31})(?:\s+(?:[1-9]|[12][0-9]|3[01])d)?$"
)
_STOP_GROUP = re.compile(r"^/stop-sharing\s+(-[0-9]{1,31})$")
_OBSERVATION_TTL = timedelta(hours=24)


class TelegramGroupObservationStorePort(Protocol):
    """Durable proof that the trusted connector saw one sender in one group."""

    def observe(
        self,
        *,
        principal_id: str,
        group_chat_id: str,
        operation_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> None: ...

    def recently_observed(
        self, *, principal_id: str, group_chat_id: str, now: datetime
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class TelegramCanaryAdmission:
    """Either a conversation audience or a safe local reply, never both."""

    principal_id: str
    audience: MemoryAudience | None = None
    local_reply: str | None = None

    def __post_init__(self) -> None:
        if (self.audience is None) == (self.local_reply is None):
            raise ValueError("Telegram canary admission must choose one outcome")
        if self.audience is not None and self.audience.principal_id != self.principal_id:
            raise ValueError("Telegram canary audience principal differs")


class TelegramCanaryBridge:
    """Derive identity server-side and keep group text out until consent exists."""

    def __init__(
        self,
        *,
        observations: TelegramGroupObservationStorePort,
        group_consent: TelegramGroupConsentStorePort,
    ) -> None:
        self._observations = observations
        self._group_consent = group_consent
        self._responder = TelegramGroupConsentResponder(group_consent)

    @staticmethod
    def principal_id(telegram_user_id: int) -> str:
        if not isinstance(telegram_user_id, int) or isinstance(telegram_user_id, bool):
            raise ValueError("Telegram canary user ID is invalid")
        if telegram_user_id < 1:
            raise ValueError("Telegram canary user ID is invalid")
        return f"telegram:{telegram_user_id}"

    def admit(
        self,
        *,
        telegram_user_id: int,
        chat_id: str,
        text: str,
        operation_id: str,
        now: datetime,
    ) -> TelegramCanaryAdmission:
        principal_id = self.principal_id(telegram_user_id)
        self._validate_time(now)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Telegram canary text is empty")
        if not isinstance(operation_id, str) or not operation_id.startswith(
            "telegram-canary:"
        ):
            raise ValueError("Telegram canary operation ID is invalid")

        if chat_id == str(telegram_user_id):
            local_reply = self._private_consent_command(
                principal_id=principal_id,
                text=text,
                operation_id=operation_id,
                now=now,
            )
            if local_reply is not None:
                return TelegramCanaryAdmission(principal_id, local_reply=local_reply)
            return TelegramCanaryAdmission(
                principal_id,
                audience=MemoryAudience.private_person(principal_id=principal_id),
            )

        if not _GROUP.fullmatch(chat_id):
            raise ValueError("Telegram canary chat is neither private nor a group")
        consent_ref = self._group_consent.active_reference(
            principal_id=principal_id,
            group_chat_id=chat_id,
            now=now,
        )
        if consent_ref is not None:
            return TelegramCanaryAdmission(
                principal_id,
                audience=MemoryAudience.telegram_group(
                    principal_id=principal_id,
                    group_chat_id=chat_id,
                    consent_ref=consent_ref,
                ),
            )
        self._observations.observe(
            principal_id=principal_id,
            group_chat_id=chat_id,
            operation_id=f"telegram-group-observation:{operation_id}",
            now=now,
            expires_at=now + _OBSERVATION_TTL,
        )
        return TelegramCanaryAdmission(
            principal_id,
            local_reply=(
                "Ik gebruik dit groepsbericht nog niet. Stuur mij privé "
                f"/eva2 /share {chat_id} om jouw eigen bijdragen zeven dagen te delen."
            ),
        )

    def _private_consent_command(
        self,
        *,
        principal_id: str,
        text: str,
        operation_id: str,
        now: datetime,
    ) -> str | None:
        match = _SHARE_GROUP.fullmatch(text.strip()) or _STOP_GROUP.fullmatch(
            text.strip()
        )
        if match is None:
            return None
        group_chat_id = match.group(1)
        active = self._group_consent.active_reference(
            principal_id=principal_id,
            group_chat_id=group_chat_id,
            now=now,
        )
        observed = self._observations.recently_observed(
            principal_id=principal_id,
            group_chat_id=group_chat_id,
            now=now,
        )
        allowed = (group_chat_id,) if observed or active is not None else ()
        return self._responder.handle(
            text=text,
            principal_id=principal_id,
            allowed_group_chat_ids=allowed,
            operation_id=f"telegram-group-consent:{operation_id}",
            now=now,
        )

    @staticmethod
    def _validate_time(now: datetime) -> None:
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != UTC.utcoffset(now)
            or now.microsecond
        ):
            raise ValueError("Telegram canary time must be whole-second UTC")


__all__ = [
    "TelegramCanaryAdmission",
    "TelegramCanaryBridge",
    "TelegramGroupObservationStorePort",
]
