"""Durable PostgreSQL outbox for asynchronous Hindsight retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import (
    MemoryAudience,
    memory_audience_from_wire,
    memory_audience_to_wire,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


@dataclass(frozen=True, slots=True)
class DurableMemoryRetentionJob:
    document: ConversationMemoryDocument
    state: CheckpointState


@dataclass(frozen=True, slots=True)
class MemoryOutboxContentErasureReceipt:
    """Content-free proof that one durable retain payload was redacted."""

    operation_sha256: str
    principal_sha256: str
    document_sha256: str
    source_message_sha256: str
    erased_at: str
    redacted_event_count: int
    replayed: bool


def conversation_memory_document_from_outbox_data(
    data: object,
) -> ConversationMemoryDocument:
    """Rebuild and validate the exact document stored at the durable boundary."""

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
    required_keys = audience_keys if "audience" in data else legacy_keys
    optional_keys = {"project_id", "retention_until"}
    if not required_keys <= set(data) or set(data) - required_keys - optional_keys:
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
    try:
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
            retention_until=data.get("retention_until"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("durable memory outbox document is invalid") from error


class PostgresMemoryOutbox(PostgresScopedRole):
    """Own a retry-bounded memory delivery timeline for one installation."""

    _role = "eva_runtime"

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("memory outbox scope is required")
        self._engine = engine
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._scope_value = TimelineScope(
            tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
            installation_id=uuid5(
                NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
            ),
        )
        self._timeline = PostgresOperationalTimeline(engine, self._scope_value)

    @staticmethod
    def _thread_id(document_id: str) -> str:
        digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        namespace = "memory-source" if document_id.startswith("procedure-reference:") else "memory-retain"
        return f"{namespace}:{digest}"

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
        if document.retention_until is not None:
            data["retention_until"] = document.retention_until
        return data

    @staticmethod
    def _document_from_data(data: object) -> ConversationMemoryDocument:
        return conversation_memory_document_from_outbox_data(data)

    def enqueue(self, document: ConversationMemoryDocument) -> DurableMemoryRetentionJob:
        if document.document_id.startswith("procedure-reference:"):
            raise ValueError("procedure reference requires source-only storage")
        return self._store(document, source_only=False)

    def record_source(self, document: ConversationMemoryDocument) -> DurableMemoryRetentionJob:
        """Store an explicit reference without sending it into personal semantic memory."""
        if not document.document_id.startswith("procedure-reference:") or document.trust != "model_inference_unconfirmed":
            raise ValueError("source-only document contract differs")
        return self._store(document, source_only=True)

    def resolve_source(self, document_id: str, *, principal_id: str,
                       project_id: str | None) -> ConversationMemoryDocument | None:
        if not document_id.startswith("procedure-reference:"):
            return None
        thread_id = self._thread_id(document_id)
        events = self._timeline.events(thread_id)
        if not events:
            return None
        state = self._timeline.load(thread_id)
        if state.phase != "memory_source_recorded" or state.status != "completed":
            return None
        data = events[0].payload.get("data")
        if not isinstance(data, dict) or "content" not in data or data.get("content_erasure"):
            return None
        document = self._document_from_data(data)
        if document.principal_id != principal_id or document.project_id != project_id:
            return None
        return document

    def matching_sources(self, *, principal_id: str, project_id: str | None,
                         query: str, now: str) -> tuple[ConversationMemoryDocument, ...]:
        """Bounded lexical candidates using PostgreSQL's existing Dutch dictionary.

        Search original records only; ranking is not semantic applicability or authority.
        """
        if not isinstance(query, str) or not query.strip():
            return ()
        statement = sa.text("""
            WITH scoped AS (
                SELECT e.payload->'data' AS data
                FROM eva_core.operational_events e
                WHERE e.tenant_id=:tenant AND e.installation_id=:installation
                  AND e.sequence=1 AND e.thread_id LIKE 'memory-source:%'
                  AND e.payload->'data'->>'principal_id'=:principal
                  AND (e.payload->'data'->>'project_id') IS NOT DISTINCT FROM :project
                  AND NOT (e.payload->'data' ? 'content_erasure')
                  AND (e.payload->'data'->>'retention_until')::timestamptz > CAST(:now AS timestamptz)
            ), candidates AS (
                SELECT data,
                    tsvector_to_array(to_tsvector('dutch',
                        (data->>'content')::jsonb->>'use_when')) AS words,
                    tsvector_to_array(to_tsvector('dutch', :query)) AS question
                FROM scoped
            )
            SELECT data->>'document_id' AS document_id,
                (SELECT count(*) FROM unnest(words) AS word WHERE word=ANY(question)) AS matches
            FROM candidates WHERE words && question
            ORDER BY matches DESC, data->>'document_id' LIMIT 8
        """)
        with self._engine.connect() as connection:
            self._scope(connection)
            rows = connection.execute(statement, {"tenant": self._scope_value.tenant_id,
                "installation": self._scope_value.installation_id, "principal": principal_id,
                "project": project_id, "query": query[:4096], "now": now}).all()
        documents = []
        for row in rows:
            doc = self.resolve_source(row.document_id, principal_id=principal_id, project_id=project_id)
            if doc is not None:
                documents.append(doc)
        return tuple(documents)

    def list_sources(self, *, principal_id: str, project_id: str | None,
                     cursor: str = "") -> tuple[tuple[ConversationMemoryDocument, ...], str | None]:
        """Page the original source records; no derived search index or fact publication."""
        if cursor and (not cursor.startswith("memory-source:") or len(cursor) != 78
                       or any(c not in "0123456789abcdef" for c in cursor[14:])):
            raise ValueError("invalid source cursor")
        e = operational_events
        data = e.c.payload["data"]
        with self._engine.connect() as connection:
            self._scope(connection)
            rows = connection.execute(sa.select(e.c.thread_id, data["document_id"].astext.label("document_id")).where(
                e.c.tenant_id == self._scope_value.tenant_id,
                e.c.installation_id == self._scope_value.installation_id,
                e.c.thread_id.startswith("memory-source:"), e.c.sequence == 1,
                data["principal_id"].astext == principal_id,
                data["project_id"].astext == project_id,
                sa.not_(data.has_key("content_erasure")),
                e.c.thread_id > cursor,
            ).order_by(e.c.thread_id).limit(9)).all()
        documents = []
        for row in rows[:8]:
            document = self.resolve_source(row.document_id, principal_id=principal_id, project_id=project_id)
            if document is not None:
                documents.append(document)
        return tuple(documents), (rows[7].thread_id if len(rows) > 8 else None)

    def _store(self, document: ConversationMemoryDocument, *, source_only: bool) -> DurableMemoryRetentionJob:
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
                phase="memory_source_recorded" if source_only else "memory_queued",
                status="completed" if source_only else "active",
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
        if source_only and state.phase != "memory_source_recorded":
            raise RuntimeError("source-only replay is unavailable")
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

    def _reflection_candidates(self, connection, *, principal_id=None, now, lock=False, recovery=False):
        """Read only validated, retained private documents without a dispatch claim."""
        c, e = operational_checkpoints, operational_events
        query = sa.select(c, e.c.payload.label("source_payload")).select_from(c.join(e,
            sa.and_(c.c.tenant_id == e.c.tenant_id,
                    c.c.installation_id == e.c.installation_id,
                    c.c.thread_id == e.c.thread_id))).where(
            c.c.tenant_id == self._scope_value.tenant_id,
            c.c.installation_id == self._scope_value.installation_id,
            c.c.thread_id.startswith("memory-retain:"),
            c.c.phase == "memory_retained", c.c.status == "completed",
            e.c.sequence == 1, e.c.event_type == "MEMORY_RETAIN_QUEUED",
        ).order_by(c.c.thread_id)
        if principal_id is not None:
            query = query.where(e.c.payload["data"]["principal_id"].astext == principal_id)
        if lock:
            query = query.with_for_update(of=c)
        rows = connection.execute(query).mappings().all()
        if not rows:
            return []
        # One post-lock read preserves the concurrent-claim fence without an
        # extra event query for every retained document.
        events_by_thread = {}
        for event in connection.execute(sa.select(
                e.c.thread_id, e.c.event_type, e.c.payload, e.c.occurred_at).where(
                e.c.tenant_id == self._scope_value.tenant_id,
                e.c.installation_id == self._scope_value.installation_id,
                e.c.thread_id.in_([row["thread_id"] for row in rows]),
                e.c.event_type.in_(("MEMORY_RETAIN_SUCCEEDED", "MEMORY_REFLECTION_CLAIMED")),
        )):
            events_by_thread.setdefault(event.thread_id, []).append(event)
        reserved_until = {}
        if recovery:
            principal = e.c.payload["data"]["principal_sha256"].astext
            reserved_until = dict(connection.execute(sa.select(
                principal, sa.func.max(e.c.occurred_at)).where(
                e.c.tenant_id == self._scope_value.tenant_id,
                e.c.installation_id == self._scope_value.installation_id,
                e.c.event_type == "RUNTIME_REFLECTION_RESERVED",
                e.c.payload["data"]["contract"].astext == "EvaDailyReflectionBudget/v1",
            ).group_by(principal)).all())
        candidates = []
        for row in rows:
            payload = row["source_payload"]
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or data.get("content_erasure"):
                continue
            try:
                document = self._document_from_data(data)
                if (document.tenant_id != self._tenant_id
                        or document.installation_id != self._installation_id
                        or document.audience.kind != "private_person"
                        or (document.retention_until is not None and
                            datetime.fromisoformat(document.retention_until.replace("Z", "+00:00")) <= now)):
                    continue
            except (RuntimeError, ValueError, TypeError):
                continue
            events = events_by_thread.get(row["thread_id"], ())
            if any(event.event_type == "MEMORY_REFLECTION_CLAIMED" for event in events):
                continue
            content_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
            if not any(event.event_type == "MEMORY_RETAIN_SUCCEEDED"
                       and event.payload.get("data", {}).get("document_id") == document.document_id
                       and event.payload.get("data", {}).get("content_sha256") == content_hash
                       for event in events):
                continue
            if recovery:
                # Older runtimes did not write source claims. A prior reservation
                # may have reached the provider even without completion evidence;
                # never turn that ambiguity into another automatic request.
                retained_at = max(event.occurred_at for event in events
                                  if event.event_type == "MEMORY_RETAIN_SUCCEEDED")
                principal_hash = hashlib.sha256(document.principal_id.encode()).hexdigest()
                reserved = reserved_until.get(principal_hash)
                if reserved is not None and reserved >= retained_at:
                    continue
            candidates.append((row, document))
        return candidates

    def pending_reflection_scopes(self, *, now: datetime | None = None
                                  ) -> tuple[tuple[str, str, str], ...]:
        """Recover retained private scopes, without re-enqueueing semantic retention."""
        with self._engine.begin() as connection:
            self._scope(connection)
            candidates = self._reflection_candidates(connection, now=now or self._now(), recovery=True)
            return tuple(sorted({(doc.tenant_id, doc.installation_id, doc.principal_id)
                                 for _, doc in candidates}))

    def claim_reflection_scope(self, tenant_id: str, installation_id: str,
                               principal_id: str, *, now: datetime) -> bool:
        """Consume retained sources before dispatch; this is not completion evidence."""
        if (tenant_id, installation_id) != (self._tenant_id, self._installation_id):
            raise ValueError("reflection claim crossed its runtime scope")
        if not principal_id or not principal_id.strip():
            raise ValueError("reflection claim principal is required")
        with self._engine.begin() as connection:
            self._scope(connection)
            candidates = self._reflection_candidates(
                connection, principal_id=principal_id, now=now, lock=True)
            for row, document in candidates:
                transition_timeline_thread(connection, self._scope_value,
                    thread_id=row["thread_id"], expected_revision=row["revision"],
                    checkpoint_id=row["thread_id"] + ":reflection-claimed",
                    operation_id=row["operation_id"], phase="memory_retained",
                    status="completed", retry_count=row["retry_count"],
                    occurred_at=now, event_type="MEMORY_REFLECTION_CLAIMED",
                    data={"principal_sha256": hashlib.sha256(principal_id.encode()).hexdigest(),
                          "document_sha256": hashlib.sha256(document.document_id.encode()).hexdigest(),
                          "meaning": "dispatch claimed; completion and outcome recorded separately"})
            return bool(candidates)

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

    def erase_content(
        self,
        *,
        principal_sha256: str,
        document_id: str,
        operation_sha256: str,
        erased_at: datetime,
    ) -> MemoryOutboxContentErasureReceipt:
        """Atomically stop replay and redact one queued Hindsight document."""

        sha256_pattern = re.compile(r"^[0-9a-f]{64}$")
        if not sha256_pattern.fullmatch(principal_sha256):
            raise ValueError("memory outbox erasure principal hash is invalid")
        if not document_id.strip() or len(document_id) > 1_024:
            raise ValueError("memory outbox erasure document is invalid")
        if not sha256_pattern.fullmatch(operation_sha256):
            raise ValueError("memory outbox erasure operation hash is invalid")
        if (
            erased_at.tzinfo is None
            or erased_at.utcoffset() is None
            or erased_at.microsecond
        ):
            raise ValueError("memory outbox erasure time must be whole-second UTC")
        erased_text = erased_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        document_sha256 = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        thread_id = self._thread_id(document_id)

        with self._engine.begin() as connection:
            self._scope(connection)
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                )
                .with_for_update()
            ).one_or_none()
            queued = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == thread_id,
                    operational_events.c.event_type == "MEMORY_RETAIN_QUEUED",
                )
                .limit(1)
            ).one_or_none()
            if checkpoint is None or queued is None:
                raise RuntimeError("memory outbox erasure target does not exist")
            payload = dict(queued.payload)
            value = payload.get("data")
            if not isinstance(value, dict):
                raise RuntimeError("memory outbox erasure target is malformed")
            durable_principal = value.get("principal_id")
            source_message_id = value.get("source_ref")
            if (
                value.get("kind") != "hindsight_memory_document"
                or value.get("document_id") != document_id
                or not isinstance(durable_principal, str)
                or hashlib.sha256(durable_principal.encode("utf-8")).hexdigest()
                != principal_sha256
                or not isinstance(source_message_id, str)
                or not source_message_id
            ):
                raise RuntimeError("memory outbox erasure scope differs")
            source_message_sha256 = hashlib.sha256(
                source_message_id.encode("utf-8")
            ).hexdigest()
            expected_erasure = {
                "contract": "EvaConversationContentErasure/v1",
                "erased_at": erased_text,
                "message_sha256": source_message_sha256,
                "operation_sha256": operation_sha256,
                "principal_sha256": principal_sha256,
                "status": "erased",
            }
            existing = value.get("content_erasure")
            if "content" not in value:
                if existing != expected_erasure:
                    raise RuntimeError("memory outbox erasure proof differs")
                return MemoryOutboxContentErasureReceipt(
                    operation_sha256=operation_sha256,
                    principal_sha256=principal_sha256,
                    document_sha256=document_sha256,
                    source_message_sha256=source_message_sha256,
                    erased_at=erased_text,
                    redacted_event_count=0,
                    replayed=True,
                )
            if existing is not None:
                raise RuntimeError("memory outbox content and erasure proof coexist")

            if str(checkpoint.status) != "completed":
                last_error_fingerprint = (
                    None
                    if checkpoint.last_error_fingerprint is None
                    else bytes(checkpoint.last_error_fingerprint).hex()
                )
                transition_timeline_thread(
                    connection,
                    self._scope_value,
                    thread_id=thread_id,
                    expected_revision=int(checkpoint.revision),
                    checkpoint_id=f"{thread_id}:erased",
                    operation_id=(
                        None
                        if checkpoint.operation_id is None
                        else str(checkpoint.operation_id)
                    ),
                    occurred_at=erased_at.astimezone(UTC),
                    data={
                        "contract": "EvaMemoryOutboxErasure/v1",
                        "document_sha256": document_sha256,
                        "operation_sha256": operation_sha256,
                        "principal_sha256": principal_sha256,
                        "status": "erased",
                    },
                    event_type="MEMORY_RETAIN_ERASED",
                    phase="memory_erased",
                    status="completed",
                    retry_count=int(checkpoint.retry_count),
                    last_error_fingerprint=last_error_fingerprint,
                )

            redacted_data = dict(value)
            redacted_data.pop("content")
            redacted_data["content_erasure"] = expected_erasure
            redacted_payload = dict(payload)
            redacted_payload["data"] = redacted_data
            changed = connection.scalar(
                sa.text(
                    "SELECT eva_core.erase_conversation_event_content("
                    ":event_id, :expected_hash, :payload, :new_hash)"
                ),
                {
                    "event_id": queued.event_id,
                    "expected_hash": bytes(queued.payload_sha256),
                    "payload": canonical_json_bytes(redacted_payload).decode("utf-8"),
                    "new_hash": bytes.fromhex(canonical_sha256(redacted_payload)),
                },
            )
            if changed is not True:
                raise RuntimeError("memory outbox erasure target vanished")

        return MemoryOutboxContentErasureReceipt(
            operation_sha256=operation_sha256,
            principal_sha256=principal_sha256,
            document_sha256=document_sha256,
            source_message_sha256=source_message_sha256,
            erased_at=erased_text,
            redacted_event_count=1,
            replayed=False,
        )


__all__ = [
    "DurableMemoryRetentionJob",
    "MemoryOutboxContentErasureReceipt",
    "PostgresMemoryOutbox",
    "conversation_memory_document_from_outbox_data",
]
