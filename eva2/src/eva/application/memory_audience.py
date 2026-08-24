"""Fail-closed audience admission for private and Telegram-group memory.

This module deliberately has no database, Hindsight or connector dependency.
The connector must establish that a group contribution is currently consented
before it creates an admission. Later retention and recall checkpoints carry
this exact audience rather than inferring privacy from a chat identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_PRIVATE_CHAT = re.compile(r"^[1-9][0-9]{0,31}$")
_GROUP_CHAT = re.compile(r"^-[0-9]{1,31}$")
_CONSENT_OPERATION = re.compile(r"^telegram-group-consent:[A-Za-z0-9._:-]{1,200}$")


@dataclass(frozen=True, slots=True)
class TelegramGroupConsentReference:
    """The durable consent operation that authorized one group contribution."""

    principal_id: str
    group_chat_id: str
    operation_id: str

    def __post_init__(self) -> None:
        if not _PRINCIPAL.fullmatch(self.principal_id):
            raise ValueError("Telegram group consent reference principal is invalid")
        if not _GROUP_CHAT.fullmatch(self.group_chat_id):
            raise ValueError("Telegram group consent reference group is invalid")
        if not _CONSENT_OPERATION.fullmatch(self.operation_id):
            raise ValueError("Telegram group consent reference operation is invalid")


@dataclass(frozen=True, slots=True)
class MemoryAudience:
    """The only audience eligible to receive a retained or recalled memory.

    ``principal_id`` remains the author/audit actor.  It is deliberately not
    used as a substitute for the audience: a group contribution has a group
    audience and a durable consent reference, never an implicit personal one.
    """

    kind: Literal["private_person", "telegram_group"]
    audience_id: str
    principal_id: str
    group_chat_id: str | None = None
    consent_ref: TelegramGroupConsentReference | None = None

    def __post_init__(self) -> None:
        if not _PRINCIPAL.fullmatch(self.principal_id):
            raise ValueError("memory audience principal is invalid")
        if self.kind == "private_person":
            if (
                self.audience_id != f"person:{self.principal_id}"
                or self.group_chat_id is not None
                or self.consent_ref is not None
            ):
                raise ValueError("private memory audience is invalid")
            return
        if self.kind != "telegram_group":
            raise ValueError("memory audience kind is invalid")
        if (
            self.group_chat_id is None
            or not _GROUP_CHAT.fullmatch(self.group_chat_id)
            or self.audience_id != f"telegram-group:{self.group_chat_id}"
            or self.consent_ref is None
            or self.consent_ref.principal_id != self.principal_id
            or self.consent_ref.group_chat_id != self.group_chat_id
        ):
            raise ValueError("Telegram group memory audience is invalid")

    @classmethod
    def private_person(cls, *, principal_id: str) -> "MemoryAudience":
        return cls(
            kind="private_person",
            audience_id=f"person:{principal_id}",
            principal_id=principal_id,
        )

    @classmethod
    def telegram_group(
        cls,
        *,
        principal_id: str,
        group_chat_id: str,
        consent_ref: TelegramGroupConsentReference,
    ) -> "MemoryAudience":
        return cls(
            kind="telegram_group",
            audience_id=f"telegram-group:{group_chat_id}",
            principal_id=principal_id,
            group_chat_id=group_chat_id,
            consent_ref=consent_ref,
        )


@dataclass(frozen=True, slots=True)
class TelegramMemoryAdmission:
    """A connector-bound decision before a Telegram message reaches memory."""

    audience: MemoryAudience
    chat_id: str

    def __post_init__(self) -> None:
        if self.audience.kind == "private_person":
            if not _PRIVATE_CHAT.fullmatch(self.chat_id):
                raise ValueError("private Telegram memory admission is invalid")
            return
        if self.chat_id != self.audience.group_chat_id:
            raise ValueError("Telegram group memory audience differs from chat")


def memory_audience_to_wire(audience: MemoryAudience) -> dict[str, str]:
    """Return the complete, canonical audience payload for a durable boundary."""

    data = {
        "audience_id": audience.audience_id,
        "kind": audience.kind,
        "principal_id": audience.principal_id,
    }
    if audience.kind == "telegram_group":
        assert audience.group_chat_id is not None
        assert audience.consent_ref is not None
        data.update(
            {
                "consent_group_chat_id": audience.consent_ref.group_chat_id,
                "consent_operation_id": audience.consent_ref.operation_id,
                "consent_principal_id": audience.consent_ref.principal_id,
                "group_chat_id": audience.group_chat_id,
            }
        )
    return data


def memory_audience_from_wire(value: object) -> MemoryAudience:
    """Reject partial or cross-bound audience data at a durable boundary."""

    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("memory audience payload is invalid")
    private_keys = {"audience_id", "kind", "principal_id"}
    group_keys = {
        *private_keys,
        "consent_group_chat_id",
        "consent_operation_id",
        "consent_principal_id",
        "group_chat_id",
    }
    if set(value) == private_keys:
        return MemoryAudience(
            kind=value["kind"],  # type: ignore[arg-type]
            audience_id=value["audience_id"],
            principal_id=value["principal_id"],
        )
    if set(value) != group_keys:
        raise ValueError("memory audience payload is incomplete")
    return MemoryAudience(
        kind=value["kind"],  # type: ignore[arg-type]
        audience_id=value["audience_id"],
        principal_id=value["principal_id"],
        group_chat_id=value["group_chat_id"],
        consent_ref=TelegramGroupConsentReference(
            principal_id=value["consent_principal_id"],
            group_chat_id=value["consent_group_chat_id"],
            operation_id=value["consent_operation_id"],
        ),
    )


def admit_telegram_memory(
    *,
    principal_id: str,
    chat_id: str,
    group_consent_permitted: bool,
    consent_ref: TelegramGroupConsentReference | None = None,
) -> TelegramMemoryAdmission:
    """Return an explicit audience or reject before any memory side effect.

    A group has no private fallback.  ``group_consent_permitted`` is an
    attestation from the durable consent boundary, and its matching timeline
    reference is mandatory for a group admission.
    """

    if type(group_consent_permitted) is not bool:
        raise ValueError("Telegram group memory consent flag is invalid")
    if _PRIVATE_CHAT.fullmatch(chat_id):
        if group_consent_permitted is not False or consent_ref is not None:
            raise ValueError("private Telegram memory must not carry group consent")
        return TelegramMemoryAdmission(
            audience=MemoryAudience.private_person(principal_id=principal_id),
            chat_id=chat_id,
        )
    if not _GROUP_CHAT.fullmatch(chat_id):
        raise ValueError("Telegram memory chat is invalid")
    if group_consent_permitted is not True or consent_ref is None:
        raise ValueError("Telegram group memory requires active consent")
    return TelegramMemoryAdmission(
        audience=MemoryAudience.telegram_group(
            principal_id=principal_id,
            group_chat_id=chat_id,
            consent_ref=consent_ref,
        ),
        chat_id=chat_id,
    )


__all__ = [
    "MemoryAudience",
    "TelegramGroupConsentReference",
    "TelegramMemoryAdmission",
    "admit_telegram_memory",
    "memory_audience_from_wire",
    "memory_audience_to_wire",
]
