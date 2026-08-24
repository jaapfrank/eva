"""Trusted Telegram ingress and crash-bounded reply delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Protocol


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


@dataclass(frozen=True, slots=True)
class TelegramInboundMessage:
    update_id: int
    chat_id: str
    message_id: int
    sender_id: int
    occurred_at: datetime
    text: str | None

    def __post_init__(self) -> None:
        if self.update_id < 0 or self.message_id < 1 or self.sender_id < 1:
            raise ValueError("Telegram ingress identity is invalid")
        if not self.chat_id or len(self.chat_id) > 32 or not self.chat_id.lstrip("-").isdigit():
            raise ValueError("Telegram ingress chat is invalid")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != UTC.utcoffset(
            self.occurred_at
        ):
            raise ValueError("Telegram ingress timestamp must be UTC")
        if self.occurred_at.microsecond:
            raise ValueError("Telegram ingress timestamp must use whole seconds")
        if self.text is not None and (not self.text.strip() or len(self.text) > 4096):
            raise ValueError("Telegram ingress text is invalid")


@dataclass(frozen=True, slots=True)
class TelegramPrincipalBinding:
    telegram_user_id: int
    principal_id: str
    allowed_chat_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.telegram_user_id < 1 or not _PRINCIPAL.fullmatch(self.principal_id):
            raise ValueError("Telegram principal binding is invalid")
        if not self.allowed_chat_ids:
            raise ValueError("Telegram principal binding has no allowed chat")
        normalized = tuple(sorted(set(self.allowed_chat_ids)))
        if normalized != self.allowed_chat_ids or any(
            not value or len(value) > 32 or not value.lstrip("-").isdigit()
            for value in normalized
        ):
            raise ValueError("Telegram principal chat binding is invalid")


class TelegramPrincipalBindings:
    def __init__(self, bindings: tuple[TelegramPrincipalBinding, ...]) -> None:
        if not bindings:
            raise ValueError("Telegram principal bindings are empty")
        users = [item.telegram_user_id for item in bindings]
        if len(set(users)) != len(users):
            raise ValueError("Telegram principal bindings are ambiguous")
        self._by_user = {item.telegram_user_id: item for item in bindings}

    def resolve(self, sender_id: int, chat_id: str) -> str | None:
        binding = self._by_user.get(sender_id)
        if binding is None or chat_id not in binding.allowed_chat_ids:
            return None
        return binding.principal_id


@dataclass(frozen=True, slots=True)
class TelegramCoreReply:
    conversation_id: str
    message_id: str
    text: str
    text_sha256: str


@dataclass(frozen=True, slots=True)
class TelegramIngressJob:
    message: TelegramInboundMessage
    principal_id: str
    phase: str
    revision: int
    reply: TelegramCoreReply | None = None


class TelegramProviderPort(Protocol):
    def poll(self, *, offset: int) -> tuple[TelegramInboundMessage, ...]: ...

    def send_reply(self, message: TelegramInboundMessage, text: str) -> str: ...


class TelegramCorePort(Protocol):
    def respond(
        self, message: TelegramInboundMessage, *, principal_id: str
    ) -> TelegramCoreReply: ...


class TelegramIngressStorePort(Protocol):
    def next_offset(self) -> int: ...

    def reject(self, message: TelegramInboundMessage, *, reason: str) -> str: ...

    def admit(
        self, message: TelegramInboundMessage, *, principal_id: str
    ) -> TelegramIngressJob | None: ...

    def prepare_reply(
        self, job: TelegramIngressJob, reply: TelegramCoreReply, *, now: datetime
    ) -> TelegramIngressJob: ...

    def fail_processing(
        self, job: TelegramIngressJob, error: Exception, *, now: datetime
    ) -> None: ...

    def claim_delivery(
        self, job: TelegramIngressJob, *, now: datetime
    ) -> TelegramIngressJob | None: ...

    def acknowledge_delivery(
        self, job: TelegramIngressJob, provider_message_id: str, *, now: datetime
    ) -> None: ...

    def mark_delivery_ambiguous(
        self, job: TelegramIngressJob, error: Exception, *, now: datetime
    ) -> None: ...


class TelegramIngressWorker:
    """Process provider updates in order and never blindly resend after uncertainty."""

    def __init__(
        self,
        provider: TelegramProviderPort,
        core: TelegramCorePort,
        store: TelegramIngressStorePort,
        bindings: TelegramPrincipalBindings,
    ) -> None:
        self._provider = provider
        self._core = core
        self._store = store
        self._bindings = bindings

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC).replace(microsecond=0)

    def run_once(self) -> str:
        updates = self._provider.poll(offset=self._store.next_offset())
        if not updates:
            return "idle"
        for message in sorted(updates, key=lambda item: item.update_id):
            if message.text is None:
                self._store.reject(message, reason="unsupported_non_text_message")
                continue
            principal_id = self._bindings.resolve(message.sender_id, message.chat_id)
            if principal_id is None:
                self._store.reject(message, reason="sender_or_chat_not_authorized")
                continue
            job = self._store.admit(message, principal_id=principal_id)
            if job is None:
                continue
            if job.reply is None:
                try:
                    reply = self._core.respond(message, principal_id=principal_id)
                except Exception as error:
                    self._store.fail_processing(job, error, now=self._now())
                    return "processing_failed"
                job = self._store.prepare_reply(job, reply, now=self._now())
            claimed = self._store.claim_delivery(job, now=self._now())
            if claimed is None:
                continue
            try:
                provider_message_id = self._provider.send_reply(
                    message, claimed.reply.text if claimed.reply is not None else ""
                )
            except Exception as error:
                self._store.mark_delivery_ambiguous(claimed, error, now=self._now())
                return "delivery_ambiguous"
            self._store.acknowledge_delivery(
                claimed, provider_message_id, now=self._now()
            )
        return "processed"


__all__ = [
    "TelegramCorePort",
    "TelegramCoreReply",
    "TelegramInboundMessage",
    "TelegramIngressJob",
    "TelegramIngressStorePort",
    "TelegramIngressWorker",
    "TelegramPrincipalBinding",
    "TelegramPrincipalBindings",
    "TelegramProviderPort",
]
