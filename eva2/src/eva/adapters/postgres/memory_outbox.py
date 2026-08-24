"""Durable PostgreSQL outbox for asynchronous Hindsight retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import (
    MemoryAudience,
    memory_audience_from_wire,
    memory_audience_to_wire,
)


@dataclass(frozen=True, slots=True)
class DurableMemoryRetentionJob:
    document: ConversationMemoryDocument
    state: CheckpointState


class PostgresMemoryOutbox:
    """Own a retry-bounded memory delivery timeline for one installation."""

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("memory outbox scope is required")
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )

    @staticmethod
    def _thread_id(document_id: str) -> str:
        digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        return f"memory-retain:{digest}"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC).replace(microsecond=0)

    @staticmethod
    def _document_data(document: ConversationMemoryDocument) -> dict[str, object]:
        data = {
            "content": document.content,
            "conversation_id": document.conversation_id,
            "document_id": document.document_id,
            "evidence_ref": document.evidence_ref,
            "installation_id": document.installation_id,
            "kind": "hindsight_memory_document",
            "occurred_at": document.occurred_at,
            "principal_id": document.principal_id,
            "privacy_class": document.privacy_class,
            "source_ref": document.source_ref,
            "tenant_id": document.tenant_id,
            "trust": document.trust,
            "audience": memory_audience_to_wire(document.audience),
        }
        if document.project_id is not None:
            data["project_id"] = document.project_id
        return data

    @staticmethod
    def _document_from_data(data: object) -> ConversationMemoryDocument:
        if not isinstance(data, dict) or data.get("kind") != "hindsight_memory_document":
            raise RuntimeError("durable memory outbox document is malformed")
        legacy_keys = {
            "content",
            "conversation_id",
            "document_id",
            "evidence_ref",
            "installation_id",
            "kind",
            "occurred_at",
            "principal_id",
            "privacy_class",
            "source_ref",
            "tenant_id",
            "trust",
        }
        audience_keys = {*legacy_keys, "audience"}
        if set(data) not in {
            frozenset(legacy_keys),
            frozenset((*legacy_keys, "project_id")),
            frozenset(audience_keys),
            frozenset((*audience_keys, "project_id")),
        }:
            raise RuntimeError("durable memory outbox document fields are malformed")
        scalar_keys = set(data) - {"audience"}
        if not all(isinstance(data[key], str) for key in scalar_keys):
            raise RuntimeError("durable memory outbox document fields are malformed")
        raw_audience = data.get("audience")
        if raw_audience is None:
            conversation_id = data["conversation_id"]
            principal_id = data["principal_id"]
            if not isinstance(conversation_id, str) or not isinstance(principal_id, str):
                raise RuntimeError("durable memory outbox document fields are malformed")
            if conversation_id.startswith("telegram:-"):
                raise RuntimeError("legacy Telegram group memory lacks an audience")
            audience = MemoryAudience.private_person(principal_id=principal_id)
        else:
            try:
                audience = memory_audience_from_wire(raw_audience)
            except ValueError as error:
                raise RuntimeError("durable memory outbox audience is invalid") from error
        return ConversationMemoryDocument(
            tenant_id=data["tenant_id"],
            installation_id=data["installation_id"],
            principal_id=data["principal_id"],
            conversation_id=data["conversation_id"],
            document_id=data["document_id"],
            content=data["content"],
            occurred_at=data["occurred_at"],
            source_ref=data["source_ref"],
            evidence_ref=data["evidence_ref"],
            privacy_class=data["privacy_class"],  # type: ignore[arg-type]
            trust=data["trust"],  # type: ignore[arg-type]
            audience=audience,
            project_id=data.get("project_id"),
        )

    def enqueue(self, document: ConversationMemoryDocument) -> DurableMemoryRetentionJob:
        if (
            document.tenant_id != self._tenant_id
            or document.installation_id != self._installation_id
        ):
            raise ValueError("memory outbox document crossed its runtime scope")
        thread_id = self._thread_id(document.document_id)
        data = self._document_data(document)
        try:
            state = self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:queued",
                operation_id=str(uuid5(NAMESPACE_URL, document.document_id)),
                occurred_at=datetime.fromisoformat(
                    document.occurred_at.replace("Z", "+00:00")
                ),
                data=data,
                event_type="MEMORY_RETAIN_QUEUED",
                phase="memory_queued",
                status="active",
            )
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            first = events[0] if events else None
            first_data = (
                first.payload.get("data")
                if first is not None and isinstance(first.payload, dict)
                else None
            )
            if first is None or first.event_type != "MEMORY_RETAIN_QUEUED" or first_data != data:
                raise RuntimeError("memory outbox replay differs") from None
            state = self._timeline.load(thread_id)
        return DurableMemoryRetentionJob(document, state)

    def pending(self, *, limit: int = 256) -> tuple[DurableMemoryRetentionJob, ...]:
        states = self._timeline.list_checkpoints(
            thread_prefix="memory-retain:",
            phases=("memory_queued", "memory_retaining", "memory_retry"),
            statuses=("active", "failed", "waiting_for_proof"),
            limit=limit,
        )
        jobs: list[DurableMemoryRetentionJob] = []
        for state in reversed(states):
            events = self._timeline.events(state.thread_id)
            queued = next(
                (event for event in events if event.event_type == "MEMORY_RETAIN_QUEUED"),
                None,
            )
            data = (
                queued.payload.get("data")
                if queued is not None and isinstance(queued.payload, dict)
                else None
            )
            jobs.append(DurableMemoryRetentionJob(self._document_from_data(data), state))
        return tuple(jobs)

    def begin(
        self, job: DurableMemoryRetentionJob, *, now: datetime | None = None
    ) -> DurableMemoryRetentionJob | None:
        current = self._timeline.load(job.state.thread_id)
        if current.status in {"completed", "needs_help"}:
            return None
        occurred_at = now or self._now()
        if current.retry_count >= 3:
            self._timeline.transition(
                thread_id=current.thread_id,
                expected_revision=current.revision,
                checkpoint_id=f"{current.thread_id}:needs-help",
                operation_id=current.operation_id,
                occurred_at=occurred_at,
                data={
                    "document_id": job.document.document_id,
                    "reason": "retry_budget_exhausted_after_ambiguous_restart",
                },
                event_type="MEMORY_RETAIN_NEEDS_HELP",
                phase="memory_needs_help",
                status="needs_help",
                retry_count=current.retry_count,
                last_error_fingerprint=current.last_error_fingerprint,
            )
            return None
        attempted = self._timeline.transition(
            thread_id=current.thread_id,
            expected_revision=current.revision,
            checkpoint_id=f"{current.thread_id}:attempt:{current.retry_count + 1}",
            operation_id=current.operation_id,
            occurred_at=occurred_at,
            data={
                "document_id": job.document.document_id,
                "attempt": current.retry_count + 1,
            },
            event_type="MEMORY_RETAIN_ATTEMPTED",
            phase="memory_retaining",
            status="waiting_for_proof",
            retry_count=current.retry_count + 1,
            last_error_fingerprint=current.last_error_fingerprint,
        )
        return DurableMemoryRetentionJob(job.document, attempted)

    def succeed(
        self,
        job: DurableMemoryRetentionJob,
        receipt: MemoryRetentionReceipt,
        *,
        now: datetime | None = None,
    ) -> CheckpointState:
        if receipt.document_id != job.document.document_id:
            raise RuntimeError("Hindsight receipt belongs to another document")
        expected_hash = hashlib.sha256(job.document.content.encode("utf-8")).hexdigest()
        if receipt.content_sha256 != expected_hash:
            raise RuntimeError("Hindsight receipt content hash differs")
        current = self._timeline.load(job.state.thread_id)
        if current.status == "completed":
            return current
        return self._timeline.transition(
            thread_id=current.thread_id,
            expected_revision=current.revision,
            checkpoint_id=f"{current.thread_id}:retained",
            operation_id=current.operation_id,
            occurred_at=now or self._now(),
            data={
                "bank_id": receipt.bank_id,
                "content_sha256": receipt.content_sha256,
                "document_id": receipt.document_id,
            },
            event_type="MEMORY_RETAIN_SUCCEEDED",
            phase="memory_retained",
            status="completed",
            retry_count=current.retry_count,
        )

    def fail(
        self,
        job: DurableMemoryRetentionJob,
        error: Exception,
        *,
        now: datetime | None = None,
    ) -> CheckpointState:
        current = self._timeline.load(job.state.thread_id)
        fingerprint = hashlib.sha256(
            f"{type(error).__name__}:{error}".encode("utf-8")
        ).hexdigest()
        terminal = current.retry_count >= 3
        return self._timeline.transition(
            thread_id=current.thread_id,
            expected_revision=current.revision,
            checkpoint_id=(
                f"{current.thread_id}:needs-help"
                if terminal
                else f"{current.thread_id}:retry:{current.retry_count}"
            ),
            operation_id=current.operation_id,
            occurred_at=now or self._now(),
            data={
                "document_id": job.document.document_id,
                "error_fingerprint": fingerprint,
                "retry_count": current.retry_count,
            },
            event_type=(
                "MEMORY_RETAIN_NEEDS_HELP"
                if terminal
                else "MEMORY_RETAIN_RETRY_SCHEDULED"
            ),
            phase="memory_needs_help" if terminal else "memory_retry",
            status="needs_help" if terminal else "failed",
            retry_count=current.retry_count,
            last_error_fingerprint=fingerprint,
        )


__all__ = ["DurableMemoryRetentionJob", "PostgresMemoryOutbox"]
