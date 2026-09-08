"""Trusted Telegram ingress and crash-bounded reply delivery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from dataclasses import dataclass
from datetime import UTC, datetime
import re
import logging
from time import monotonic
from typing import Callable, Protocol
from uuid import UUID

from eva.application.conversation import ConversationTurn, GroundedReply
from eva.application.telegram_group_consent import TelegramGroupConsentResponder
from eva.application.telegram_pairing import (
    TelegramDynamicBindingResolverPort,
    TelegramPairingResponder,
)
from eva.application.memory_audience import MemoryAudience


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_LINK_CONFIRM = re.compile(r"^/link\s+(EVA-[A-Z2-9]{12})$")
_DRAFT_CHUNK_CHARACTERS = 256
_DRAFT_MINIMUM_INTERVAL_SECONDS = 1.0
TELEGRAM_MAXIMUM_TEXT_CHARACTERS = 4096


class TelegramProviderRateLimited(RuntimeError):
    """A Telegram provider asked the caller to pause before another attempt."""

    def __init__(self, retry_after_seconds: int) -> None:
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 86_400:
            raise ValueError("Telegram retry-after value is invalid")
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Telegram provider rate limit reached")


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
        if (
            not self.chat_id
            or len(self.chat_id) > 32
            or not self.chat_id.lstrip("-").isdigit()
        ):
            raise ValueError("Telegram ingress chat is invalid")
        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() != UTC.utcoffset(self.occurred_at)
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
    principal_binding_id: str | None = None

    def __post_init__(self) -> None:
        if self.telegram_user_id < 1 or not _PRINCIPAL.fullmatch(self.principal_id):
            raise ValueError("Telegram principal binding is invalid")
        if self.principal_binding_id is not None:
            try:
                UUID(self.principal_binding_id)
            except ValueError as error:
                raise ValueError("Telegram principal binding ID is invalid") from error
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

    def resolve_sender(self, sender_id: int) -> str | None:
        binding = self._by_user.get(sender_id)
        return None if binding is None else binding.principal_id

    def allowed_chat_ids(self, sender_id: int) -> tuple[str, ...]:
        binding = self._by_user.get(sender_id)
        return () if binding is None else binding.allowed_chat_ids

    def resolve_binding_id(self, sender_id: int, chat_id: str) -> UUID | None:
        binding = self._by_user.get(sender_id)
        if (
            binding is None
            or chat_id not in binding.allowed_chat_ids
            or binding.principal_binding_id is None
        ):
            return None
        return UUID(binding.principal_binding_id)

    def contains_binding(self, *, principal_id: str, principal_binding_id: UUID) -> bool:
        return any(
            binding.principal_id == principal_id
            and binding.principal_binding_id == str(principal_binding_id)
            for binding in self._by_user.values()
        )

    def private_bridge_principal(
        self, *, principal_id: str, principal_binding_id: UUID
    ) -> str | None:
        """Resolve one trusted private bridge alias, never a browser-supplied ID."""
        matches = [
            item for item in self._by_user.values()
            if item.principal_id == principal_id
            and item.principal_binding_id == str(principal_binding_id)
            and str(item.telegram_user_id) in item.allowed_chat_ids
        ]
        if len(matches) > 1:
            raise ValueError("owner private bridge binding is ambiguous")
        return f"telegram:{matches[0].telegram_user_id}" if matches else None


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


class TelegramReplyDraftPort(Protocol):
    def send_reply_draft(
        self, message: TelegramInboundMessage, text: str
    ) -> None: ...


class TelegramCorePort(Protocol):
    def stop_pending(self, message: TelegramInboundMessage, *, stop_message: TelegramInboundMessage, principal_id: str, audience: MemoryAudience) -> None: ...

    def respond(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
    ) -> TelegramCoreReply: ...


class TelegramStreamingCorePort(Protocol):
    def respond_stream(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
        on_delta: Callable[[str], None],
    ) -> TelegramCoreReply: ...


class TelegramDelegatedWorkCommandsPort(Protocol):
    def handle(
        self,
        turn: ConversationTurn,
        *,
        principal_binding_id: UUID | None,
    ) -> GroundedReply | None: ...


class TelegramIngressStorePort(Protocol):
    def next_offset(self) -> int: ...

    def reject(self, message: TelegramInboundMessage, *, reason: str) -> str: ...

    def admit(
        self, message: TelegramInboundMessage, *, principal_id: str
    ) -> TelegramIngressJob | None: ...

    def reject_admitted(
        self, job: TelegramIngressJob, *, reason: str, now: datetime
    ) -> str: ...

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
        pairing: TelegramPairingResponder | None = None,
        group_consent: TelegramGroupConsentResponder | None = None,
        dynamic_bindings: TelegramDynamicBindingResolverPort | None = None,
        delegated_work_commands: TelegramDelegatedWorkCommandsPort | None = None,
        tenant_id: str | None = None,
        installation_id: str | None = None,
        reply_drafts: TelegramReplyDraftPort | None = None,
        streaming_core: TelegramStreamingCorePort | None = None,
        draft_clock: Callable[[], float] = monotonic,
    ) -> None:
        if delegated_work_commands is not None and (
            tenant_id is None or installation_id is None
        ):
            raise ValueError("delegated Telegram commands require a fixed scope")
        self._provider = provider
        self._core = core
        self._store = store
        self._bindings = bindings
        self._pairing = pairing
        self._group_consent = group_consent
        self._dynamic_bindings = dynamic_bindings
        self._delegated_work_commands = delegated_work_commands
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._reply_drafts = reply_drafts
        self._streaming_core = streaming_core
        self._draft_clock = draft_clock
        self._next_draft_at = 0.0
        self._active_lock = Lock()
        self._active_reply: tuple[TelegramInboundMessage, str, MemoryAudience] | None = None
        self._observed_stops: set[int] = set()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC).replace(microsecond=0)

    def run_once_interruptible(self, shutdown: Event) -> str:
        """Keep polling stop requests during core I/O without acknowledging ahead.

        There is still exactly one normal batch and one delivery path. Lookahead
        never admits/delivers Telegram jobs or advances their durable offset.
        """
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="eva-telegram") as executor:
            future = executor.submit(self.run_once)
            while not future.done() and not shutdown.wait(0.2):
                try:
                    self._poll_pending_stop()
                except Exception as error:
                    logging.getLogger(__name__).warning("Telegram stop lookahead failed: %s", type(error).__name__)
                    if shutdown.wait(1.0):
                        break
            return future.result()

    def _poll_pending_stop(self) -> None:
        with self._active_lock:
            active = self._active_reply
        if active is None:
            return
        current, principal_id, audience = active
        updates = self._provider.poll(offset=self._store.next_offset())
        for message in sorted(updates, key=lambda item: item.update_id):
            if (message.update_id <= current.update_id
                    or message.sender_id != current.sender_id or message.chat_id != current.chat_id
                    or message.text is None or message.text.strip().casefold() not in
                    {"/stop", "stop deze opdracht", "stop deze opdracht."}):
                continue
            resolved, conflict = self._resolve_principal(message)
            if conflict or resolved != principal_id:
                continue
            if audience.kind == "telegram_group":
                consent = (None if self._group_consent is None else self._group_consent.active_reference(
                    principal_id=principal_id, group_chat_id=message.chat_id, now=self._now(),
                ))
                if consent != audience.consent_ref:
                    continue
            # Prevent moving on to another ordinary message during this control
            # call. The current provider request itself may still finish.
            with self._active_lock:
                if self._active_reply != active:
                    return
                if message.update_id in self._observed_stops:
                    continue
                self._core.stop_pending(current, stop_message=message, principal_id=principal_id, audience=audience)
                self._observed_stops.add(message.update_id)

    def run_once(self) -> str:
        updates = self._provider.poll(offset=self._store.next_offset())
        if not updates:
            return "idle"
        for message in sorted(updates, key=lambda item: item.update_id):
            if message.text is None:
                self._store.reject(message, reason="unsupported_non_text_message")
                continue
            principal_id, identity_conflict = self._resolve_principal(message)
            if identity_conflict:
                self._store.reject(message, reason="sender_or_chat_not_authorized")
                continue
            if principal_id is None:
                if self._pairing is None or message.text.strip() != "/link":
                    self._store.reject(message, reason="sender_or_chat_not_authorized")
                    continue
                text = self._pairing.begin(
                    update_id=message.update_id,
                    sender_id=message.sender_id,
                    chat_id=message.chat_id,
                    now=self._now(),
                )
                if text is None:
                    self._store.reject(message, reason="sender_or_chat_not_authorized")
                    continue
                principal_id = f"pairing:{message.sender_id}"
                reply = self._local_reply(message, text)
            else:
                audience = MemoryAudience.private_person(principal_id=principal_id)
                match = _LINK_CONFIRM.fullmatch(message.text.strip())
                reply = (
                    None
                    if self._pairing is None or match is None
                    else self._local_reply(
                        message,
                        self._pairing.confirm(
                            code=match.group(1),
                            sender_id=message.sender_id,
                            chat_id=message.chat_id,
                            now=self._now(),
                        ),
                    )
                )
            job = self._store.admit(message, principal_id=principal_id)
            if job is None:
                continue
            if job.reply is None:
                try:
                    if reply is None and message.chat_id.startswith("-"):
                        consent_ref = (
                            None
                            if self._group_consent is None
                            else self._group_consent.active_reference(
                                principal_id=principal_id,
                                group_chat_id=message.chat_id,
                                now=self._now(),
                            )
                        )
                        if consent_ref is None:
                            self._store.reject_admitted(
                                job,
                                reason="group_consent_required",
                                now=self._now(),
                            )
                            continue
                        audience = MemoryAudience.telegram_group(
                            principal_id=principal_id,
                            group_chat_id=message.chat_id,
                            consent_ref=consent_ref,
                        )
                    reply = (
                        reply
                        if reply is not None
                        else self._private_group_consent_reply(
                            message, principal_id=principal_id
                        )
                    )
                    reply = (
                        reply
                        if reply is not None
                        else self._delegated_work_reply(
                            message,
                            principal_id=principal_id,
                            audience=audience,
                        )
                    )
                    reply = (
                        reply
                        if reply is not None
                        else self._core_reply_with_optional_draft(
                            message, principal_id=principal_id, audience=audience
                        )
                    )
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
            with self._active_lock:
                self._observed_stops.discard(message.update_id)
        return "processed"

    def _core_reply_with_optional_draft(
        self, message: TelegramInboundMessage, *, principal_id: str, audience: MemoryAudience,
    ) -> TelegramCoreReply:
        with self._active_lock:
            self._active_reply = (message, principal_id, audience)
        try:
            return self._respond_core(message, principal_id=principal_id, audience=audience)
        finally:
            with self._active_lock:
                self._active_reply = None

    def _respond_core(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
    ) -> TelegramCoreReply:
        if (
            audience.kind != "private_person"
            or self._reply_drafts is None
            or self._streaming_core is None
        ):
            return self._core.respond(
                message, principal_id=principal_id, audience=audience
            )
        draft_now = self._draft_clock()
        if draft_now < self._next_draft_at:
            return self._core.respond(
                message, principal_id=principal_id, audience=audience
            )
        try:
            self._reply_drafts.send_reply_draft(message, "")
        except TelegramProviderRateLimited as error:
            self._next_draft_at = max(
                draft_now + _DRAFT_MINIMUM_INTERVAL_SECONDS,
                self._draft_clock() + error.retry_after_seconds,
            )
            return self._core.respond(
                message, principal_id=principal_id, audience=audience
            )
        except Exception:
            self._next_draft_at = draft_now + _DRAFT_MINIMUM_INTERVAL_SECONDS
            return self._core.respond(
                message, principal_id=principal_id, audience=audience
            )
        self._next_draft_at = draft_now + _DRAFT_MINIMUM_INTERVAL_SECONDS

        accumulated = ""
        last_draft_length = 0
        drafts_available = True

        def publish(delta: str) -> None:
            nonlocal accumulated, drafts_available, last_draft_length
            accumulated += delta
            if (
                not drafts_available
                or len(accumulated) > TELEGRAM_MAXIMUM_TEXT_CHARACTERS
                or not accumulated.strip()
                or (
                    last_draft_length
                    and len(accumulated) - last_draft_length
                    < _DRAFT_CHUNK_CHARACTERS
                )
            ):
                return
            draft_now = self._draft_clock()
            if draft_now < self._next_draft_at:
                return
            try:
                self._reply_drafts.send_reply_draft(message, accumulated)
            except TelegramProviderRateLimited as error:
                drafts_available = False
                self._next_draft_at = max(
                    self._next_draft_at,
                    self._draft_clock() + error.retry_after_seconds,
                )
            except Exception:
                drafts_available = False
            finally:
                self._next_draft_at = max(
                    self._next_draft_at,
                    draft_now + _DRAFT_MINIMUM_INTERVAL_SECONDS,
                )
            if drafts_available:
                last_draft_length = len(accumulated)

        return self._streaming_core.respond_stream(
            message,
            principal_id=principal_id,
            audience=audience,
            on_delta=publish,
        )

    def _delegated_work_reply(
        self,
        message: TelegramInboundMessage,
        *,
        principal_id: str,
        audience: MemoryAudience,
    ) -> TelegramCoreReply | None:
        if (
            self._delegated_work_commands is None
            or self._tenant_id is None
            or self._installation_id is None
            or message.text is None
            or audience.kind != "private_person"
        ):
            return None
        reply = self._delegated_work_commands.handle(
            ConversationTurn.private(
                tenant_id=self._tenant_id,
                installation_id=self._installation_id,
                principal_id=principal_id,
                conversation_id=f"telegram:{message.chat_id}",
                message_id=f"telegram:{message.chat_id}:{message.message_id}",
                text=message.text,
                occurred_at=message.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            principal_binding_id=self._bindings.resolve_binding_id(
                message.sender_id, message.chat_id
            ),
        )
        if reply is None:
            return None
        return TelegramCoreReply(
            reply.conversation_id,
            reply.message_id,
            reply.text,
            reply.text_sha256,
        )

    def _resolve_principal(
        self, message: TelegramInboundMessage
    ) -> tuple[str | None, bool]:
        static_sender_principal = self._bindings.resolve_sender(message.sender_id)
        static_authorized_principal = self._bindings.resolve(
            message.sender_id, message.chat_id
        )
        dynamic_principal = (
            None
            if self._dynamic_bindings is None
            else self._dynamic_bindings.resolve(
                telegram_user_id=message.sender_id,
                chat_id=str(message.sender_id),
            )
        )
        if (
            static_sender_principal is not None
            and dynamic_principal is not None
            and static_sender_principal != dynamic_principal
        ):
            return None, True
        if static_authorized_principal is not None:
            return static_authorized_principal, False
        if message.chat_id == str(message.sender_id):
            return dynamic_principal, False
        return None, False

    @staticmethod
    def _local_reply(message: TelegramInboundMessage, text: str) -> TelegramCoreReply:
        import hashlib

        return TelegramCoreReply(
            f"telegram:{message.chat_id}",
            f"telegram:{message.chat_id}:{message.message_id}",
            text,
            hashlib.sha256(text.encode()).hexdigest(),
        )

    def _private_group_consent_reply(
        self, message: TelegramInboundMessage, *, principal_id: str
    ) -> TelegramCoreReply | None:
        if self._group_consent is None or message.chat_id != str(message.sender_id):
            return None
        text = self._group_consent.handle(
            text=message.text or "",
            principal_id=principal_id,
            allowed_group_chat_ids=self._bindings.allowed_chat_ids(message.sender_id),
            operation_id=f"telegram-group-consent:{message.update_id}",
            now=self._now(),
        )
        return None if text is None else self._local_reply(message, text)


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
