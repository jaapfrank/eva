"""Durable admission and ambiguity-safe delivery for Telegram ingress."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.telegram_ingress import (
    TelegramCoreReply,
    TelegramInboundMessage,
    TelegramIngressJob,
)
from eva.contracts.base import JsonValue


class PostgresTelegramIngressStore:
    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        installation_id: str,
        delivery_lease_seconds: int = 30,
    ) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("Telegram ingress store scope is required")
        if not 10 <= delivery_lease_seconds <= 120:
            raise ValueError("Telegram delivery lease is outside 10..120 seconds")
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )
        self._delivery_lease_seconds = delivery_lease_seconds

    @staticmethod
    def _thread_id(update_id: int) -> str:
        return f"telegram-inbound:{update_id}"

    @staticmethod
    def _message_data(
        message: TelegramInboundMessage, *, principal_id: str | None
    ) -> dict[str, JsonValue]:
        return {
            "chat_id": message.chat_id,
            "kind": "telegram_inbound_message",
            "message_id": message.message_id,
            "occurred_at": message.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "principal_id": principal_id,
            "sender_id": message.sender_id,
            "text": message.text if principal_id is not None else None,
            "text_sha256": (
                None
                if message.text is None
                else hashlib.sha256(message.text.encode()).hexdigest()
            ),
            "update_id": message.update_id,
        }

    def next_offset(self) -> int:
        states = self._timeline.list_checkpoints(
            thread_prefix="telegram-inbound:", limit=256
        )
        if not states:
            return 0
        decoded = tuple(
            (int(state.thread_id.rsplit(":", 1)[1]), state) for state in states
        )
        unfinished = [
            update_id
            for update_id, state in decoded
            if state.status not in {"completed", "needs_help"}
        ]
        if unfinished:
            return min(unfinished)
        return max(update_id for update_id, _state in decoded) + 1

    def reject(self, message: TelegramInboundMessage, *, reason: str) -> str:
        if reason not in {
            "group_consent_required",
            "sender_or_chat_not_authorized",
            "unsupported_non_text_message",
        }:
            raise ValueError("Telegram rejection reason is invalid")
        thread_id = self._thread_id(message.update_id)
        data = self._message_data(message, principal_id=None)
        data["reason"] = reason
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:rejected",
                operation_id=f"telegram-update:{message.update_id}",
                occurred_at=message.occurred_at,
                data=data,
                event_type="TELEGRAM_INGRESS_REJECTED",
                phase="ingress_rejected",
                status="completed",
            )
            return "rejected"
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            durable = events[0].payload.get("data") if events else None
            if (
                not events
                or events[0].event_type != "TELEGRAM_INGRESS_REJECTED"
                or durable != data
            ):
                raise RuntimeError(
                    "Telegram rejected update identity collision"
                ) from None
            return "already_rejected"

    def admit(
        self, message: TelegramInboundMessage, *, principal_id: str
    ) -> TelegramIngressJob | None:
        thread_id = self._thread_id(message.update_id)
        data = self._message_data(message, principal_id=principal_id)
        try:
            state = self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:admitted",
                operation_id=f"telegram-update:{message.update_id}",
                occurred_at=message.occurred_at,
                data=data,
                event_type="TELEGRAM_INGRESS_ADMITTED",
                phase="ingress_admitted",
                status="active",
            )
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            durable = events[0].payload.get("data") if events else None
            if (
                not events
                or events[0].event_type != "TELEGRAM_INGRESS_ADMITTED"
                or durable != data
            ):
                raise RuntimeError("Telegram update identity collision") from None
            state = self._timeline.load(thread_id)
        if state.status in {"completed", "needs_help"}:
            return None
        return TelegramIngressJob(
            message,
            principal_id,
            state.phase,
            state.revision,
            self._prepared_reply(thread_id),
        )

    def prepare_reply(
        self,
        job: TelegramIngressJob,
        reply: TelegramCoreReply,
        *,
        now: datetime,
    ) -> TelegramIngressJob:
        expected_conversation = f"telegram:{job.message.chat_id}"
        expected_message = f"telegram:{job.message.chat_id}:{job.message.message_id}"
        if (
            reply.conversation_id != expected_conversation
            or reply.message_id != expected_message
            or hashlib.sha256(reply.text.encode()).hexdigest() != reply.text_sha256
        ):
            raise RuntimeError("Telegram reply belongs to another ingress")
        state = self._timeline.transition(
            thread_id=self._thread_id(job.message.update_id),
            expected_revision=job.revision,
            checkpoint_id=f"telegram-inbound:{job.message.update_id}:reply-prepared",
            operation_id=f"telegram-update:{job.message.update_id}",
            occurred_at=now,
            data={
                "conversation_id": reply.conversation_id,
                "message_id": reply.message_id,
                "principal_id": job.principal_id,
                "reply_text": reply.text,
                "reply_text_sha256": reply.text_sha256,
                "update_id": job.message.update_id,
            },
            event_type="TELEGRAM_REPLY_PREPARED",
            phase="reply_prepared",
            status="active",
            retry_count=0,
            last_error_fingerprint=None,
        )
        return TelegramIngressJob(
            job.message, job.principal_id, state.phase, state.revision, reply
        )

    def fail_processing(
        self,
        job: TelegramIngressJob,
        error: Exception,
        *,
        now: datetime,
    ) -> None:
        current = self._timeline.load(self._thread_id(job.message.update_id))
        if current.status in {"completed", "needs_help"}:
            return
        self._timeline.record_recovery_failure(
            thread_id=current.thread_id,
            expected_revision=current.revision,
            checkpoint_id=(
                f"{current.thread_id}:processing-failed:{current.retry_count + 1}"
            ),
            operation_id=current.operation_id,
            error_text=f"{type(error).__name__}:{error}",
            occurred_at=now,
        )

    def claim_delivery(
        self, job: TelegramIngressJob, *, now: datetime
    ) -> TelegramIngressJob | None:
        thread_id = self._thread_id(job.message.update_id)
        current = self._timeline.load(thread_id)
        if current.status in {"completed", "needs_help"}:
            return None
        if current.phase == "delivery_claimed":
            if current.deadline is not None and current.deadline <= now:
                fingerprint = hashlib.sha256(
                    b"Telegram delivery acceptance is ambiguous after restart"
                ).hexdigest()
                self._timeline.transition(
                    thread_id=thread_id,
                    expected_revision=current.revision,
                    checkpoint_id=f"{thread_id}:delivery-ambiguous",
                    operation_id=current.operation_id,
                    occurred_at=now,
                    data={"reason": "provider acceptance cannot be proven"},
                    event_type="TELEGRAM_DELIVERY_AMBIGUOUS",
                    phase="delivery_ambiguous",
                    status="needs_help",
                    retry_count=3,
                    last_error_fingerprint=fingerprint,
                )
            return None
        if current.phase != "reply_prepared" or job.reply is None:
            raise RuntimeError("Telegram reply is not prepared for delivery")
        claimed = self._timeline.transition(
            thread_id=thread_id,
            expected_revision=current.revision,
            checkpoint_id=f"{thread_id}:delivery-claimed",
            operation_id=current.operation_id,
            occurred_at=now,
            deadline=now + timedelta(seconds=self._delivery_lease_seconds),
            data={
                "reply_text_sha256": job.reply.text_sha256,
                "update_id": job.message.update_id,
            },
            event_type="TELEGRAM_DELIVERY_CLAIMED",
            phase="delivery_claimed",
            status="waiting_for_proof",
        )
        return TelegramIngressJob(
            job.message,
            job.principal_id,
            claimed.phase,
            claimed.revision,
            job.reply,
        )

    def acknowledge_delivery(
        self,
        job: TelegramIngressJob,
        provider_message_id: str,
        *,
        now: datetime,
    ) -> None:
        if (
            not provider_message_id.startswith("telegram:")
            or len(provider_message_id) > 160
        ):
            raise ValueError("Telegram provider receipt is invalid")
        self._timeline.transition(
            thread_id=self._thread_id(job.message.update_id),
            expected_revision=job.revision,
            checkpoint_id=f"telegram-inbound:{job.message.update_id}:delivered",
            operation_id=f"telegram-update:{job.message.update_id}",
            occurred_at=now,
            data={
                "provider_message_id": provider_message_id,
                "update_id": job.message.update_id,
            },
            event_type="TELEGRAM_REPLY_DELIVERED",
            phase="reply_delivered",
            status="completed",
            retry_count=0,
        )

    def mark_delivery_ambiguous(
        self, job: TelegramIngressJob, error: Exception, *, now: datetime
    ) -> None:
        fingerprint = hashlib.sha256(
            f"{type(error).__name__}:{error}".encode()
        ).hexdigest()
        self._timeline.transition(
            thread_id=self._thread_id(job.message.update_id),
            expected_revision=job.revision,
            checkpoint_id=f"telegram-inbound:{job.message.update_id}:delivery-ambiguous",
            operation_id=f"telegram-update:{job.message.update_id}",
            occurred_at=now,
            data={
                "error_fingerprint": fingerprint,
                "reason": "provider acceptance cannot be proven",
                "update_id": job.message.update_id,
            },
            event_type="TELEGRAM_DELIVERY_AMBIGUOUS",
            phase="delivery_ambiguous",
            status="needs_help",
            retry_count=3,
            last_error_fingerprint=fingerprint,
        )

    def _prepared_reply(self, thread_id: str) -> TelegramCoreReply | None:
        for event in reversed(self._timeline.events(thread_id)):
            if event.event_type != "TELEGRAM_REPLY_PREPARED":
                continue
            data = event.payload.get("data")
            if not isinstance(data, dict):
                break
            values = (
                data.get("conversation_id"),
                data.get("message_id"),
                data.get("reply_text"),
                data.get("reply_text_sha256"),
            )
            if all(isinstance(value, str) for value in values):
                return TelegramCoreReply(*values)  # type: ignore[arg-type]
            break
        return None


__all__ = ["PostgresTelegramIngressStore"]
