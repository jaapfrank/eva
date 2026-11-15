"""Durable PostgreSQL outbox for asynchronous Hindsight retention."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace, asdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
    insert_initial_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_source_lifecycle import (
    CompactedPrivateMemorySource, MemorySourceCompactionProof,
    compacted_private_memory_source_from_data, source_identity_sha256,
)
from eva.application.memory_audience import (
    MemoryAudience,
    memory_audience_from_wire,
    memory_audience_to_wire,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


@dataclass(frozen=True, slots=True)
class DurableMemoryRetentionJob:
    document: ConversationMemoryDocument | CompactedPrivateMemorySource
    state: CheckpointState


@dataclass(frozen=True, slots=True)
class DurableDreamMemoryBatch:
    state: CheckpointState
    jobs: tuple[DurableMemoryRetentionJob, ...]


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
) -> ConversationMemoryDocument | CompactedPrivateMemorySource:
    """Return raw source or typed compacted metadata; never invent raw content."""
    if isinstance(data, dict) and "content_compaction" in data:
        try:
            return compacted_private_memory_source_from_data(data)
        except ValueError as error:
            raise RuntimeError("durable compacted memory source is invalid") from error

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
    optional_keys = {"project_id", "retention_until", "retention_mode"}
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
            retention_mode=data.get("retention_mode", "immediate"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("durable memory outbox document is invalid") from error


class PostgresMemoryOutbox(PostgresScopedRole):
    """Own a retry-bounded memory delivery timeline for one installation."""

    _role = "eva_runtime"

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str, work_source_retention=None) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("memory outbox scope is required")
        if work_source_retention is not None and not callable(getattr(work_source_retention, "protected_sources", None)):
            raise ValueError("work source retention must be a trusted source registry")
        self._work_source_retention = work_source_retention
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
        if document.retention_mode != "immediate":
            data["retention_mode"] = document.retention_mode
        return data

    @staticmethod
    def _document_from_data(data: object) -> ConversationMemoryDocument | CompactedPrivateMemorySource:
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
        if not isinstance(document, ConversationMemoryDocument):
            raise ValueError("compacted memory metadata cannot enter retention")
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
            if first is None or first.event_type != "MEMORY_RETAIN_QUEUED":
                raise RuntimeError("memory outbox replay differs") from None
            if first_data != data:
                # A source admitted by an earlier runtime retains its original
                # scheduling policy. Never rewrite the immutable queued source.
                previous = self._document_from_data(first_data)
                if self._document_data(replace(previous, retention_mode=document.retention_mode)) != data:
                    raise RuntimeError("memory outbox replay differs") from None
                document = previous
            state = self._timeline.load(thread_id)
        if source_only and state.phase != "memory_source_recorded":
            raise RuntimeError("source-only replay is unavailable")
        return DurableMemoryRetentionJob(document, state)

    def pending_erasure_document(self, *, principal_id: str, document_id: str,
                                 source_message_id: str | None):
        """Resolve only the caller's exact, still-present ordinary source."""
        events = self._timeline.events(self._thread_id(document_id))
        source = events[0].payload.get("data") if events else None
        if not isinstance(source, dict) or source.get("content_erasure"):
            return None
        document = self._document_from_data(source)
        if isinstance(document, CompactedPrivateMemorySource) or document.retention_mode != "dream":
            return None
        if (document.principal_id != principal_id or document.document_id != document_id
                or document.source_ref != source_message_id
                or document.tenant_id != self._tenant_id or document.installation_id != self._installation_id):
            raise RuntimeError("pending erasure source binding differs")
        return document

    def pending(self, *, limit: int = 256) -> tuple[DurableMemoryRetentionJob, ...]:
        if not 1 <= limit <= 256:
            raise ValueError("memory pending limit is invalid")
        c, e = operational_checkpoints.c, operational_events.c
        with self._engine.begin() as connection:
            self._scope(connection)
            held = set()
            blocked = connection.execute(sa.select(c.thread_id, e.payload).select_from(
                operational_checkpoints.join(operational_events, sa.and_(
                    c.tenant_id == e.tenant_id, c.installation_id == e.installation_id,
                    c.thread_id == e.thread_id, e.sequence == 1))).where(
                c.tenant_id == self._scope_value.tenant_id,
                c.installation_id == self._scope_value.installation_id,
                c.thread_id.startswith("memory-dream:"), c.status == "needs_help",
            )).all()
            for thread_id, payload in blocked:
                held.update(self._thread_id(member["document_id"])
                            for member in self._dream_members(payload.get("data"), thread_id))
            query = sa.select(c.thread_id).where(
                c.tenant_id == self._scope_value.tenant_id,
                c.installation_id == self._scope_value.installation_id,
                c.thread_id.startswith("memory-retain:"),
                c.phase.in_(("memory_queued", "memory_retaining", "memory_retry")),
                c.status.in_(("active", "failed", "waiting_for_proof")),
            )
            if held:
                query = query.where(c.thread_id.not_in(held))
            thread_ids = tuple(connection.scalars(query.order_by(c.updated_at.desc(), c.thread_id).limit(limit)))
        states = tuple(self._timeline.load(thread_id) for thread_id in thread_ids)
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

    @contextmanager
    def retention_guard(self):
        """Serialize native retention and erasure across this installation's workers."""
        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"memory-native-stage:{self._scope_value.tenant_id}:{self._scope_value.installation_id}"},
            )
            yield

    @staticmethod
    def _dream_members(data, thread_id):
        if (not isinstance(data, dict) or set(data) != {"contract", "members"}
                or data["contract"] != "EvaDreamMemoryBatch/v1"
                or not isinstance(data["members"], list) or not 1 <= len(data["members"]) <= 32
                or thread_id != "memory-dream:" + canonical_sha256(data)):
            raise RuntimeError("dream batch manifest differs")
        for member in data["members"]:
            if (not isinstance(member, dict)
                    or set(member) != {"document_id", "source_payload_sha256"}
                    or not isinstance(member["document_id"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", member["source_payload_sha256"])):
                raise RuntimeError("dream batch source binding is invalid")
        if len({member["document_id"] for member in data["members"]}) != len(data["members"]):
            raise RuntimeError("dream batch contains duplicate sources")
        return data["members"]

    def prepare_dream_batch(self, candidates: tuple[DurableMemoryRetentionJob, ...], *, now: datetime):
        """Pin a bounded, immutable private cohort; blocked cohorts hold only their members."""
        c, e = operational_checkpoints.c, operational_events.c
        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"memory-dream:{self._scope_value.tenant_id}:{self._scope_value.installation_id}"})
            rows = connection.execute(sa.select(c.thread_id, c.status, e.payload).select_from(
                operational_checkpoints.join(operational_events, sa.and_(
                    c.tenant_id == e.tenant_id, c.installation_id == e.installation_id,
                    c.thread_id == e.thread_id, e.sequence == 1))).where(
                c.tenant_id == self._scope_value.tenant_id,
                c.installation_id == self._scope_value.installation_id,
                c.thread_id.startswith("memory-dream:"), c.status != "completed",
            ).order_by(c.updated_at, c.thread_id)).mappings().all()
            held = set()
            thread_id = None
            for row in rows:
                members = self._dream_members(row["payload"].get("data"), row["thread_id"])
                held.update(member["document_id"] for member in members)
                if row["status"] != "needs_help" and thread_id is None:
                    thread_id = row["thread_id"]
            if thread_id is None:
                available = []
                # Queue entries can outlive an erasure or another process's acknowledgement.
                # Re-read the authoritative source before making it part of a new manifest.
                for job in candidates:
                    if job.document.document_id in held:
                        continue
                    state = self._timeline.load(job.state.thread_id)
                    if state.status in {"completed", "needs_help"}:
                        continue
                    events = self._timeline.events(state.thread_id)
                    source = events[0].payload.get("data") if events else None
                    if not isinstance(source, dict) or source.get("content_erasure"):
                        continue
                    doc = self._document_from_data(source)
                    if self._document_data(job.document) != source:
                        raise RuntimeError("dream source changed before assignment")
                    if doc.retention_mode != "dream":
                        raise ValueError("dream batch contains an immediate record")
                    available.append(DurableMemoryRetentionJob(doc, state))
                if not available:
                    return None
                ordered = sorted(available, key=lambda job: (job.document.occurred_at, job.document.document_id))
                first = ordered[0].document
                def cohort_key(doc):
                    return (doc.tenant_id, doc.installation_id, doc.principal_id,
                            doc.audience.audience_id, doc.privacy_class, doc.occurred_at[:10])
                key = cohort_key(first)
                members = []
                size = 0
                for job in ordered:
                    doc = job.document
                    if cohort_key(doc) != key:
                        continue
                    payload = self._document_data(doc)
                    length = len(canonical_json_bytes(payload))
                    if members and (len(members) >= 32 or size + length > 131072):
                        break
                    size += length
                    members.append({"document_id": doc.document_id, "source_payload_sha256": canonical_sha256(payload)})
                manifest = {"contract": "EvaDreamMemoryBatch/v1", "members": members}
                thread_id = "memory-dream:" + canonical_sha256(manifest)
                insert_initial_timeline_thread(connection, self._scope_value,
                    thread_id=thread_id, checkpoint_id=thread_id+":assigned",
                    operation_id=thread_id, phase="memory_dream_assigned", status="active",
                    event_type="MEMORY_DREAM_BATCH_ASSIGNED", occurred_at=now, data=manifest)
        try:
            return self.read_dream_batch(thread_id)
        except RuntimeError as error:
            self.block_dream_batch(thread_id, error, now=now)
            return None  # Preserve the bad cohort, but do not block later people/days.

    def bind_dream_operation(self, batch: DurableDreamMemoryBatch, *, bank_id: str,
                             operation_id: str, now: datetime):
        if not bank_id or not operation_id:
            raise ValueError("dream native operation binding is missing")
        data = {"bank_id": bank_id, "operation_id": operation_id}
        events = self._timeline.events(batch.state.thread_id)
        prior = [event for event in events if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
        if prior:
            if len(prior) != 1 or prior[0].payload.get("data") != data:
                raise RuntimeError("dream native operation binding changed")
            return
        current = self._timeline.load(batch.state.thread_id)
        if current.status in {"completed", "needs_help"}:
            raise RuntimeError("closed dream batch cannot dispatch")
        self._timeline.transition(thread_id=current.thread_id, expected_revision=current.revision,
            checkpoint_id=current.thread_id+":dispatch-bound", operation_id=current.operation_id,
            occurred_at=now, event_type="MEMORY_DREAM_DISPATCH_BOUND",
            phase="memory_dream_dispatched", status="waiting_for_proof", data=data)

    def block_dream_batch(self, thread_id: str, error: Exception, *, now: datetime):
        current = self._timeline.load(thread_id)
        if current.status in {"completed", "needs_help"}:
            return current
        fingerprint = hashlib.sha256(f"{type(error).__name__}:{error}".encode()).hexdigest()
        return self._timeline.transition(thread_id=current.thread_id, expected_revision=current.revision,
            checkpoint_id=current.thread_id+":needs-help", operation_id=current.operation_id,
            occurred_at=now, event_type="MEMORY_DREAM_BATCH_NEEDS_HELP",
            phase="memory_dream_needs_help", status="needs_help",
            last_error_fingerprint=fingerprint, data={"error_fingerprint": fingerprint})

    def dream_batches_for_erasure(self, document_id: str):
        batches = []
        c, e = operational_checkpoints.c, operational_events.c
        with self._engine.begin() as connection:
            self._scope(connection)
            thread_ids = tuple(connection.scalars(sa.select(c.thread_id).select_from(
                operational_checkpoints.join(operational_events, sa.and_(
                    c.tenant_id == e.tenant_id, c.installation_id == e.installation_id,
                    c.thread_id == e.thread_id, e.sequence == 1))).where(
                c.tenant_id == self._scope_value.tenant_id,
                c.installation_id == self._scope_value.installation_id,
                c.thread_id.startswith("memory-dream:"),
                e.payload["data"]["members"].contains([{"document_id": document_id}]),
            )))
        for thread_id in thread_ids:
            state = self._timeline.load(thread_id)
            events = self._timeline.events(state.thread_id)
            if not events:
                raise RuntimeError("dream batch has no manifest")
            members = self._dream_members(events[0].payload.get("data"), state.thread_id)
            if not any(member["document_id"] == document_id for member in members):
                continue
            bound = [event.payload.get("data") for event in events
                     if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
            if len(bound) > 1:
                raise RuntimeError("dream operation has multiple bindings")
            binding = bound[0] if bound else None
            if binding is not None and (not isinstance(binding, dict)
                    or set(binding) != {"bank_id", "operation_id"}
                    or not all(isinstance(value, str) and value for value in binding.values())):
                raise RuntimeError("dream operation binding is invalid")
            batches.append((state, binding))
        return tuple(batches)

    def cancel_undispatched_dream_batch(self, thread_id: str, *, now: datetime):
        events = self._timeline.events(thread_id)
        if any(event.event_type == "MEMORY_DREAM_DISPATCH_BOUND" for event in events):
            raise RuntimeError("dispatched dream batch must settle before erasure")
        current = self._timeline.load(thread_id)
        if current.status == "completed":
            return current
        return self._timeline.transition(thread_id=thread_id, expected_revision=current.revision,
            checkpoint_id=thread_id+":cancelled", operation_id=current.operation_id,
            occurred_at=now, event_type="MEMORY_DREAM_BATCH_CANCELLED",
            phase="memory_dream_cancelled", status="completed", data={"reason": "source_erasure_before_dispatch"})

    def cancel_absent_dream_batch(self, thread_id: str, *, bank_id: str,
                                 operation_id: str, now: datetime):
        bindings = [event.payload.get("data") for event in self._timeline.events(thread_id)
                    if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
        if bindings != [{"bank_id": bank_id, "operation_id": operation_id}]:
            raise RuntimeError("absent dream operation binding differs")
        current = self._timeline.load(thread_id)
        if current.status == "completed":
            return current
        return self._timeline.transition(thread_id=thread_id, expected_revision=current.revision,
            checkpoint_id=thread_id+":absent", operation_id=current.operation_id,
            occurred_at=now, event_type="MEMORY_DREAM_BATCH_CANCELLED",
            phase="memory_dream_cancelled", status="completed",
            data={"reason": "native_operation_absent_before_erasure",
                  "bank_id": bank_id, "operation_id": operation_id})

    def cancel_settled_failed_dream_batch(self, thread_id: str, *, bank_id: str,
                                          operation_id: str, now: datetime):
        events = self._timeline.events(thread_id)
        if not events:
            raise RuntimeError("dream batch has no manifest")
        members = self._dream_members(events[0].payload.get("data"), thread_id)
        bindings = [event.payload.get("data") for event in events
                    if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
        if bindings != [{"bank_id": bank_id, "operation_id": operation_id}]:
            raise RuntimeError("settled failed dream operation binding differs")
        for member in members:
            source_events = self._timeline.events(self._thread_id(member["document_id"]))
            source = source_events[0].payload.get("data") if source_events else None
            if not isinstance(source, dict) or not source.get("content_erasure"):
                raise RuntimeError("settled failed dream batch still has non-erased member")
        current = self._timeline.load(thread_id)
        if current.status == "completed":
            return current
        return self._timeline.transition(thread_id=thread_id, expected_revision=current.revision,
            checkpoint_id=thread_id+":failed-erased", operation_id=current.operation_id,
            occurred_at=now, event_type="MEMORY_DREAM_BATCH_CANCELLED",
            phase="memory_dream_cancelled", status="completed",
            data={"reason": "native_operation_failed_and_all_members_erased",
                  "bank_id": bank_id, "operation_id": operation_id})

    def read_dream_batch(self, thread_id: str) -> DurableDreamMemoryBatch:
        if not re.fullmatch('memory-dream:[0-9a-f]{64}', thread_id):
            raise ValueError('dream batch identity is invalid')
        events = self._timeline.events(thread_id)
        first = events[0] if events else None
        data = None if first is None else first.payload.get('data')
        if (first is None or first.event_type != 'MEMORY_DREAM_BATCH_ASSIGNED'
                or not isinstance(data, dict) or set(data) != {'contract', 'members'}
                or data['contract'] != 'EvaDreamMemoryBatch/v1'
                or not isinstance(data['members'], list) or not 1 <= len(data['members']) <= 32
                or thread_id != 'memory-dream:' + canonical_sha256(data)):
            raise RuntimeError('dream batch manifest differs')
        jobs = []
        for member in data['members']:
            if not isinstance(member, dict) or set(member) != {'document_id', 'source_payload_sha256'}:
                raise RuntimeError('dream batch source binding is invalid')
            source_thread = self._thread_id(member['document_id'])
            original = self._timeline.events(source_thread)
            source = original[0].payload.get('data') if original else None
            if not isinstance(source, dict) or source.get('content_erasure'):
                raise RuntimeError('dream batch source was erased or changed; do not redispatch')
            document = self._document_from_data(source)
            source_state = self._timeline.load(source_thread)
            if isinstance(document, CompactedPrivateMemorySource):
                proof = document.content_compaction
                if (self._timeline.load(thread_id).status != "completed"
                        or source_state.status != "completed"
                        or proof.source_payload_sha256 != member['source_payload_sha256']
                        or proof.cohort_manifest_sha256 != thread_id.removeprefix("memory-dream:")):
                    raise RuntimeError("compacted dream source binding differs")
            elif canonical_sha256(source) != member['source_payload_sha256']:
                raise RuntimeError('dream batch source was erased or changed; do not redispatch')
            if document.retention_mode != 'dream':
                raise RuntimeError('dream batch source scheduling differs')
            jobs.append(DurableMemoryRetentionJob(document, source_state))
        if len({job.document.document_id for job in jobs}) != len(jobs):
            raise RuntimeError('dream batch contains duplicate sources')
        return DurableDreamMemoryBatch(self._timeline.load(thread_id), tuple(jobs))

    def reconcile_dream_receipt(self, batch: DurableDreamMemoryBatch,
                                receipt: MemoryRetentionReceipt, *, now: datetime):
        """Acknowledge GET-only completion without inventing another extraction attempt."""
        current_batch = self.read_dream_batch(batch.state.thread_id)
        matches = [job for job in current_batch.jobs if job.document.document_id == receipt.document_id]
        bindings = [event.payload.get("data") for event in self._timeline.events(batch.state.thread_id)
                    if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
        binding = {"bank_id": receipt.bank_id, "operation_id": receipt.operation_id}
        if (len(matches) != 1 or bindings != [binding]
                or receipt.selected_fact_count is None
                or receipt.content_sha256 != (matches[0].document.content_compaction.content_sha256
                    if isinstance(matches[0].document, CompactedPrivateMemorySource)
                    else hashlib.sha256(matches[0].document.content.encode()).hexdigest())):
            raise RuntimeError("settled dream receipt does not match its immutable operation/source")
        job = matches[0]
        if job.state.status == "completed":
            self.succeed(job, receipt, now=now)
            return job
        if job.state.phase == "memory_retaining":
            return job
        data = {**binding, "document_id": job.document.document_id,
                "batch_thread_id": batch.state.thread_id}
        state = self._timeline.transition(thread_id=job.state.thread_id,
            expected_revision=job.state.revision,
            checkpoint_id=job.state.thread_id+":dream-reconciled",
            operation_id=job.state.operation_id, occurred_at=now,
            event_type="MEMORY_DREAM_SOURCE_RECONCILED", phase="memory_retaining",
            status="waiting_for_proof", retry_count=job.state.retry_count,
            last_error_fingerprint=job.state.last_error_fingerprint, data=data)
        return DurableMemoryRetentionJob(job.document, state)

    def bind_dream_receipts(self, batch, receipts, *, now):
        """Persist exact per-document native acknowledgements, separately from old receipts."""
        current = self.read_dream_batch(batch.state.thread_id)
        bindings = [event.payload.get("data") for event in self._timeline.events(current.state.thread_id)
                    if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
        if len(bindings) != 1 or not isinstance(receipts, tuple):
            raise RuntimeError("dream receipts lack one immutable dispatch binding")
        binding = bindings[0]
        expected = {job.document.document_id: job.document for job in current.jobs}
        records = []
        for receipt in receipts:
            if not isinstance(receipt, MemoryRetentionReceipt) or receipt.document_id not in expected:
                raise RuntimeError("dream receipt source differs")
            document = expected[receipt.document_id]
            digest = (document.content_compaction.content_sha256
                if isinstance(document, CompactedPrivateMemorySource)
                else hashlib.sha256(document.content.encode()).hexdigest())
            if (receipt.content_sha256 != digest or receipt.bank_id != binding.get("bank_id")
                    or receipt.operation_id != binding.get("operation_id")
                    or type(receipt.selected_fact_count) is not int or receipt.selected_fact_count < 0
                    or receipt.disposition != ("not_selected" if receipt.selected_fact_count == 0 else "retained")):
                raise RuntimeError("dream receipt acknowledgement differs")
            records.append(asdict(receipt))
        if len(records) != len(expected) or {v["document_id"] for v in records} != set(expected):
            raise RuntimeError("dream receipt membership differs")
        records.sort(key=lambda value: value["document_id"])
        data = {"contract": "EvaDreamReceipts/v1",
                "source_manifest_sha256": current.state.thread_id.removeprefix("memory-dream:"),
                "receipts": records}
        previous = [event for event in self._timeline.events(current.state.thread_id)
                    if event.event_type == "MEMORY_DREAM_RECEIPTS_BOUND"]
        if previous:
            if len(previous) != 1 or previous[0].payload.get("data") != data:
                raise RuntimeError("dream acknowledgement replay differs")
            return self._timeline.load(current.state.thread_id)
        return self._timeline.transition(thread_id=current.state.thread_id,
            expected_revision=current.state.revision, checkpoint_id=current.state.thread_id+":receipts",
            operation_id=current.state.operation_id, occurred_at=now,
            event_type="MEMORY_DREAM_RECEIPTS_BOUND",
            phase="memory_dream_completed" if current.state.status == "completed" else "memory_dream_receipts_bound",
            status="completed" if current.state.status == "completed" else "active", data=data)

    def compact_day_sources(self, *, now, limit=32):
        """Compact completed cohorts only after their full 24-hour day context."""
        if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now)
                or now.microsecond or type(limit) is not int or not 1 <= limit <= 128):
            raise ValueError("private day compaction bounds are invalid")
        results = []
        with self.retention_guard():
            protected_sources = (frozenset() if self._work_source_retention is None else
                self._work_source_retention.protected_sources(self._tenant_id, self._installation_id, now))
            with self._engine.begin() as connection:
                self._scope(connection)
                c, e = operational_checkpoints.c, operational_events.c
                ack_event = operational_events.alias("compaction_ack")
                manifest_event = operational_events.alias("compaction_manifest")
                raw_event = operational_events.alias("compaction_raw_source")
                has_ack = sa.exists(sa.select(1).where(
                    ack_event.c.tenant_id == c.tenant_id,
                    ack_event.c.installation_id == c.installation_id,
                    ack_event.c.thread_id == c.thread_id,
                    ack_event.c.event_type == "MEMORY_DREAM_RECEIPTS_BOUND"))
                has_raw_member = sa.exists(sa.select(1).select_from(manifest_event.join(raw_event,
                    sa.and_(manifest_event.c.tenant_id == raw_event.c.tenant_id,
                            manifest_event.c.installation_id == raw_event.c.installation_id))).where(
                    manifest_event.c.tenant_id == c.tenant_id,
                    manifest_event.c.installation_id == c.installation_id,
                    manifest_event.c.thread_id == c.thread_id, manifest_event.c.sequence == 1,
                    manifest_event.c.event_type == "MEMORY_DREAM_BATCH_ASSIGNED",
                    raw_event.c.sequence == 1, raw_event.c.event_type == "MEMORY_RETAIN_QUEUED",
                    raw_event.c.payload["data"].has_key("content"),
                    sa.tuple_(raw_event.c.payload["data"]["principal_id"].astext,
                              raw_event.c.payload["data"]["conversation_id"].astext,
                              raw_event.c.payload["data"]["source_ref"].astext).not_in(tuple(protected_sources)),
                    ~raw_event.c.payload["data"].has_key("content_erasure"),
                    ~raw_event.c.payload["data"].has_key("content_compaction"),
                    raw_event.c.payload["data"]["occurred_at"].astext < (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    manifest_event.c.payload["data"]["members"].contains(sa.func.jsonb_build_array(
                        sa.func.jsonb_build_object("document_id", raw_event.c.payload["data"]["document_id"].astext)))))
                has_erased_member = sa.exists(sa.select(1).select_from(manifest_event.join(raw_event,
                    sa.and_(manifest_event.c.tenant_id == raw_event.c.tenant_id,
                            manifest_event.c.installation_id == raw_event.c.installation_id))).where(
                    manifest_event.c.tenant_id == c.tenant_id,
                    manifest_event.c.installation_id == c.installation_id,
                    manifest_event.c.thread_id == c.thread_id, manifest_event.c.sequence == 1,
                    raw_event.c.sequence == 1, raw_event.c.event_type == "MEMORY_RETAIN_QUEUED",
                    raw_event.c.payload["data"].has_key("content_erasure"),
                    manifest_event.c.payload["data"]["members"].contains(sa.func.jsonb_build_array(
                        sa.func.jsonb_build_object("document_id", raw_event.c.payload["data"]["document_id"].astext)))))
                cohorts = tuple(connection.scalars(sa.select(c.thread_id).where(
                    c.tenant_id == self._scope_value.tenant_id,
                    c.installation_id == self._scope_value.installation_id,
                    c.phase == "memory_dream_completed", c.status == "completed", has_ack, has_raw_member,
                    ~has_erased_member,
                ).order_by(c.updated_at, c.thread_id).limit(limit)))
                for thread_id in cohorts:
                    batch = self.read_dream_batch(thread_id)
                    if any(job.document.occurred_at >= (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ") for job in batch.jobs):
                        continue
                    ledger = self._timeline.events(thread_id)
                    ack = [event.payload.get("data") for event in ledger
                           if event.event_type == "MEMORY_DREAM_RECEIPTS_BOUND"]
                    binding = [event.payload.get("data") for event in ledger
                               if event.event_type == "MEMORY_DREAM_DISPATCH_BOUND"]
                    if not ack:
                        continue  # Legacy completion has no measured per-source acknowledgement.
                    if (len(ack) != 1 or len(binding) != 1
                            or ack[0].get("contract") != "EvaDreamReceipts/v1"
                            or ack[0].get("source_manifest_sha256") != thread_id.removeprefix("memory-dream:")):
                        raise RuntimeError("compaction cohort acknowledgement differs")
                    records = {v["document_id"]: v for v in ack[0]["receipts"]}
                    if (len(ack[0]["receipts"]) != len(batch.jobs) or len(records) != len(batch.jobs)
                            or set(records) != {j.document.document_id for j in batch.jobs}):
                        raise RuntimeError("compaction receipt membership differs")
                    if any(j.state.status != "completed" or j.state.phase not in {"memory_retained", "memory_not_selected"}
                           for j in batch.jobs):
                        continue
                    for job in batch.jobs:
                        if len(results) >= limit:
                            return tuple(results)
                        if isinstance(job.document, CompactedPrivateMemorySource):
                            continue
                        document = job.document
                        if (document.principal_id, document.conversation_id, document.source_ref) in protected_sources:
                            continue
                        receipt = records[document.document_id]
                        if (document.audience.kind != "private_person" or document.project_id is not None
                                or document.trust != "direct_user" or document.retention_mode != "dream"
                                or receipt["bank_id"] != binding[0]["bank_id"]
                                or receipt["operation_id"] != binding[0]["operation_id"]):
                            raise RuntimeError("compaction source is outside its acknowledgement")
                        queue = connection.execute(sa.select(operational_events).where(
                            e.tenant_id == self._scope_value.tenant_id, e.installation_id == self._scope_value.installation_id,
                            e.thread_id == job.state.thread_id, e.sequence == 1)).mappings().one()
                        journal = connection.execute(sa.select(operational_events).where(
                            e.tenant_id == self._scope_value.tenant_id, e.installation_id == self._scope_value.installation_id,
                            e.thread_id == "conversation:" + document.source_ref,
                            e.event_type.in_(("INGRESS_COMMITTED", "REPLY_COMMITTED")),
                        ).order_by(e.sequence)).mappings().all()
                        if len(journal) != 2 or [row["event_type"] for row in journal] != ["INGRESS_COMMITTED", "REPLY_COMMITTED"]:
                            raise RuntimeError("compaction requires one completed original exchange")
                        ingress, reply = journal
                        proof = MemorySourceCompactionProof(
                            contract="EvaPrivateMemorySourceCompaction/v1", compacted_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            queued_payload_sha256=bytes(queue["payload_sha256"]).hex(),
                            content_sha256=hashlib.sha256(document.content.encode()).hexdigest(),
                            source_payload_sha256=canonical_sha256(queue["payload"]["data"]),
                            ingress_payload_sha256=bytes(ingress["payload_sha256"]).hex(),
                            ingress_text_sha256=hashlib.sha256(ingress["payload"]["data"]["text"].encode()).hexdigest(),
                            reply_payload_sha256=bytes(reply["payload_sha256"]).hex(),
                            reply_text_sha256=hashlib.sha256(reply["payload"]["data"]["text"].encode()).hexdigest(),
                            cohort_manifest_sha256=thread_id.removeprefix("memory-dream:"),
                            bank_id=receipt["bank_id"], operation_id=receipt["operation_id"],
                            selected_fact_count=receipt["selected_fact_count"], disposition=receipt["disposition"],
                            receipt_sha256=canonical_sha256(receipt),
                            source_identity_sha256=source_identity_sha256(queue["payload"]["data"]))
                        wire = proof.to_wire()
                        replacements = []
                        for row, raw_key in ((queue, "content"), (ingress, "text"), (reply, "text")):
                            payload = dict(row["payload"])
                            data = dict(payload["data"])
                            data.pop(raw_key)
                            data["content_compaction"] = wire
                            payload["data"] = data
                            replacements.append(canonical_json_bytes(payload).decode())
                        compacted_private_memory_source_from_data(json.loads(replacements[0])["data"])
                        changed = connection.scalar(sa.text("SELECT eva_core.compact_private_memory_source("
                            ":queued, :ingress, :reply, :proof, :qpayload, :ipayload, :rpayload)"),
                            {"queued": queue["event_id"], "ingress": ingress["event_id"], "reply": reply["event_id"],
                             "proof": canonical_json_bytes(wire).decode(), "qpayload": replacements[0],
                             "ipayload": replacements[1], "rpayload": replacements[2]})
                        if changed is not True:
                            raise RuntimeError("private source compaction did not acknowledge exact exchange")
                        results.append({"document_id": document.document_id,
                            "proof_sha256": canonical_sha256(wire), "compacted_at": wire["compacted_at"]})
        return tuple(results)

    def finish_dream_batch(self, batch: DurableDreamMemoryBatch, *, now: datetime):
        current = self.read_dream_batch(batch.state.thread_id)
        if any(job.state.status != 'completed' for job in current.jobs):
            raise RuntimeError('dream batch has unacknowledged sources')
        if current.state.status == 'completed':
            return current.state
        return self._timeline.transition(thread_id=current.state.thread_id,
            expected_revision=current.state.revision, checkpoint_id=current.state.thread_id+':completed',
            operation_id=current.state.operation_id, occurred_at=now,
            event_type='MEMORY_DREAM_BATCH_COMPLETED', phase='memory_dream_completed', status='completed',
            data={'member_count': len(current.jobs), 'source_manifest_sha256': current.state.thread_id.removeprefix('memory-dream:')})

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
            content_hash = (document.content_compaction.content_sha256
                if isinstance(document, CompactedPrivateMemorySource)
                else hashlib.sha256(document.content.encode("utf-8")).hexdigest())
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
        if isinstance(job.document, CompactedPrivateMemorySource):
            if current.status != "completed":
                raise RuntimeError("compacted source cannot be a pending retention job")
            return None
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
        expected_hash = (job.document.content_compaction.content_sha256
            if isinstance(job.document, CompactedPrivateMemorySource)
            else hashlib.sha256(job.document.content.encode("utf-8")).hexdigest())
        if isinstance(job.document, CompactedPrivateMemorySource):
            if canonical_sha256(asdict(receipt)) != job.document.content_compaction.receipt_sha256:
                raise RuntimeError("compacted acknowledgement replay differs")
        if receipt.content_sha256 != expected_hash:
            raise RuntimeError("Hindsight receipt content hash differs")
        disposition = getattr(receipt, "disposition", "retained")
        if disposition not in {"retained", "not_selected"}:
            raise RuntimeError("Hindsight receipt disposition differs")
        not_selected = disposition == "not_selected"
        selected = getattr(receipt, "selected_fact_count", None)
        operation = getattr(receipt, "operation_id", None)
        if not_selected and (type(selected) is not int or selected != 0
                or not isinstance(operation, str) or not operation.strip()):
            raise RuntimeError("Hindsight zero selection evidence is incomplete")
        data = {"bank_id": receipt.bank_id, "content_sha256": receipt.content_sha256,
                "document_id": receipt.document_id}
        if not_selected:
            data["selected_fact_count"] = selected
            data["operation_id"] = operation
        event_type = "MEMORY_RETAIN_NOT_SELECTED" if not_selected else "MEMORY_RETAIN_SUCCEEDED"
        current = self._timeline.load(job.state.thread_id)
        if not_selected:
            original = self._timeline.events(current.thread_id)
            if (not original or original[0].event_type != "MEMORY_RETAIN_QUEUED"
                    or (not isinstance(job.document, CompactedPrivateMemorySource)
                        and original[0].payload.get("data") != self._document_data(job.document))):
                raise RuntimeError("Hindsight zero selection source differs")
        if current.status == "completed":
            completed = next((event for event in reversed(self._timeline.events(current.thread_id))
                if event.event_type in {"MEMORY_RETAIN_SUCCEEDED", "MEMORY_RETAIN_NOT_SELECTED"}), None)
            if (completed is None or completed.event_type != event_type
                    or completed.payload.get("data") != data):
                raise RuntimeError("Hindsight completed receipt replay differs")
            return current
        if not_selected and (current.phase != "memory_retaining"
                or current.revision != job.state.revision):
            raise RuntimeError("Hindsight zero selection attempt is no longer current")
        return self._timeline.transition(
            thread_id=current.thread_id,
            expected_revision=current.revision,
            checkpoint_id=f"{current.thread_id}:" + ("not-selected" if not_selected else "retained"),
            operation_id=current.operation_id,
            occurred_at=now or self._now(),
            data=data,
            event_type=event_type,
            phase="memory_not_selected" if not_selected else "memory_retained",
            status="completed",
            retry_count=current.retry_count,
        )

    def reconcile_closed_retention(
        self, job: DurableMemoryRetentionJob, receipt: MemoryRetentionReceipt,
        *, proof_sha256: str, now: datetime,
    ) -> CheckpointState:
        """Adopt an independently verified closed zero-result without redispatch.

        The caller verifies the original native operation/source proof. This local
        journal boundary binds that proof and receipt to the unchanged failed job;
        it cannot turn a positive, pending or unknown result into zero selection.
        """
        import re
        if (isinstance(job.document, CompactedPrivateMemorySource)
                or not isinstance(proof_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", proof_sha256)
                or receipt.disposition != "not_selected"
                or type(receipt.selected_fact_count) is not int
                or receipt.selected_fact_count != 0
                or not isinstance(receipt.operation_id, str) or not receipt.operation_id
                or receipt.document_id != job.document.document_id
                or receipt.content_sha256 != hashlib.sha256(job.document.content.encode()).hexdigest()
                or job.state.thread_id != self._thread_id(job.document.document_id)):
            raise RuntimeError("closed memory reconciliation proof differs")
        current = self._timeline.load(job.state.thread_id)
        events = self._timeline.events(current.thread_id)
        if (not events or events[0].event_type != "MEMORY_RETAIN_QUEUED"
                or events[0].payload.get("data") != self._document_data(job.document)):
            raise RuntimeError("closed memory reconciliation source changed")
        prefix = events[:-1] if events[-1].event_type == "MEMORY_RETAIN_RESULT_RECONCILED" else events
        history_sha256 = canonical_sha256([
            {"event_id": str(event.event_id), "sequence": event.sequence,
             "event_type": event.event_type, "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "payload_sha256": event.payload_sha256} for event in prefix])
        data = {"document_id": receipt.document_id, "bank_id": receipt.bank_id,
                "content_sha256": receipt.content_sha256, "operation_id": receipt.operation_id,
                "selected_fact_count": 0, "disposition": "not_selected", "proof_sha256": proof_sha256,
                "source_history_sha256": history_sha256}
        if current.status == "completed":
            if (events[-1].event_type != "MEMORY_RETAIN_RESULT_RECONCILED"
                    or events[-1].payload.get("data") != data):
                raise RuntimeError("closed memory reconciliation replay differs")
            return current
        prior_isolated_needs_help = (
            events[-1].event_type == "MEMORY_RETAIN_CHRONOLOGY_ISOLATED"
            and isinstance(events[-1].payload.get("data"), dict)
            and events[-1].payload["data"].get("contract") == "EvaHistoricalChronologyIsolation/v1"
            and events[-1].payload["data"].get("terminal_disposition") == "needs_help"
        )
        if (current.revision != job.state.revision
                or (current.phase, current.status) not in {
                    ("memory_needs_help", "needs_help"), ("memory_retry", "failed")}
                or (events[-1].event_type not in {
                    "MEMORY_RETAIN_NEEDS_HELP", "MEMORY_RETAIN_RETRY_SCHEDULED"}
                    and not prior_isolated_needs_help)):
            raise RuntimeError("closed memory reconciliation job is no longer failed")
        return self._timeline.transition(
            thread_id=current.thread_id, expected_revision=current.revision,
            checkpoint_id=current.thread_id + ":result-reconciled",
            operation_id=current.operation_id, occurred_at=now, data=data,
            event_type="MEMORY_RETAIN_RESULT_RECONCILED", phase="memory_not_selected",
            status="completed", retry_count=current.retry_count,
            last_error_fingerprint=current.last_error_fingerprint,
        )

    def isolate_historical_chronology(
        self,
        thread_id: str,
        *,
        proof_sha256: str,
        now: datetime,
        cutoff_utc: datetime | None = None,
    ) -> CheckpointState:
        """Append an explicit, hash-bound boundary isolating historical chronology defects."""
        if (
            not isinstance(thread_id, str)
            or not re.fullmatch(r"memory-retain:[0-9a-f]{64}", thread_id)
            or not isinstance(proof_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", proof_sha256)
        ):
            raise RuntimeError("historical chronology isolation parameters are invalid")
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != UTC.utcoffset(now)
            or now.microsecond
        ):
            raise ValueError("historical chronology isolation time must be whole-second UTC")
        if cutoff_utc is not None and (
            not isinstance(cutoff_utc, datetime)
            or cutoff_utc.tzinfo is None
            or cutoff_utc.utcoffset() != UTC.utcoffset(cutoff_utc)
            or cutoff_utc.microsecond
        ):
            raise ValueError("historical chronology cutoff time must be whole-second UTC")
        current = self._timeline.load(thread_id)
        events = self._timeline.events(thread_id)
        isolated_indices = [
            index
            for index, item in enumerate(events)
            if item.event_type == "MEMORY_RETAIN_CHRONOLOGY_ISOLATED"
        ]
        if len(isolated_indices) > 1:
            raise RuntimeError("historical chronology has multiple isolation boundaries")
        if isolated_indices:
            iso_index = isolated_indices[0]
            if iso_index < 1 or any(
                item.event_type not in {
                    "MEMORY_REFLECTION_CLAIMED",
                    "MEMORY_RETAIN_ERASED",
                    "MEMORY_RETAIN_RESULT_RECONCILED",
                }
                for item in events[iso_index + 1:]
            ):
                raise RuntimeError("historical chronology post-isolation events are invalid")
            prefix = events[:iso_index]
        else:
            iso_index = None
            prefix = events
        validated = validate_historical_retain_prefix(
            self._timeline,
            tenant_id=self._tenant_id,
            installation_id=self._installation_id,
            thread_id=thread_id,
            prefix=prefix,
        )
        if cutoff_utc is not None and prefix[0].occurred_at > cutoff_utc:
            raise RuntimeError("historical chronology source is newer than cutoff")
        if validated.defect_code is None:
            raise RuntimeError("historical chronology is already valid")
        tail_history_sha256 = canonical_sha256([
            {
                "event_id": str(event.event_id),
                "sequence": event.sequence,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "payload_sha256": event.payload_sha256,
            }
            for event in prefix[1:]
        ])
        if iso_index is not None:
            existing_data = events[iso_index].payload.get("data")
            if (
                not isinstance(existing_data, dict)
                or existing_data.get("contract") != "EvaHistoricalChronologyIsolation/v1"
                or existing_data.get("document_id") != validated.document_id
                or existing_data.get("tail_history_sha256") != tail_history_sha256
                or existing_data.get("proof_sha256") != proof_sha256
            ):
                raise RuntimeError("historical chronology isolation replay differs")
            if validated.erased_at is None:
                if (
                    existing_data.get("content_sha256") != validated.source_content_sha256
                    or existing_data.get("source_history_sha256") != validated.source_history_sha256
                ):
                    raise RuntimeError("historical chronology isolation replay differs")
            elif existing_data.get("terminal_disposition") == "erased":
                if existing_data.get("content_sha256") != "erased":
                    raise RuntimeError("historical chronology isolation replay differs")
            elif validated.erased_at < events[iso_index].occurred_at:
                raise RuntimeError("historical chronology isolation replay differs")
            return current
        if now < max(event.occurred_at for event in prefix):
            raise RuntimeError("historical chronology isolation precedes latest event")
        if validated.terminal_disposition is None or validated.target_phase is None or validated.target_status is None:
            raise RuntimeError("historical chronology state cannot be isolated")
        content_sha256 = (
            "erased"
            if validated.terminal_disposition == "erased"
            else str(validated.source_content_sha256)
        )
        data = {
            "contract": "EvaHistoricalChronologyIsolation/v1",
            "document_id": validated.document_id,
            "content_sha256": content_sha256,
            "terminal_disposition": validated.terminal_disposition,
            "source_history_sha256": validated.source_history_sha256,
            "tail_history_sha256": tail_history_sha256,
            "proof_sha256": proof_sha256,
        }
        return self._timeline.transition(
            thread_id=current.thread_id,
            expected_revision=current.revision,
            checkpoint_id=current.thread_id + ":chronology-isolated",
            operation_id=current.operation_id,
            occurred_at=now,
            data=data,
            event_type="MEMORY_RETAIN_CHRONOLOGY_ISOLATED",
            phase=validated.target_phase,
            status=validated.target_status,  # type: ignore[arg-type]
            retry_count=validated.prefix_retry_count,
            last_error_fingerprint=validated.prefix_last_error_fingerprint,
        )

    def fail(
        self,
        job: DurableMemoryRetentionJob,
        error: Exception,
        *,
        now: datetime | None = None,
    ) -> CheckpointState:
        current = self._timeline.load(job.state.thread_id)
        if current.status == "completed":
            return current  # A stale failure cannot reopen any acknowledged source.
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
            if "content" not in value and existing is not None:
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
            if "content" not in value:
                compacted_private_memory_source_from_data(value)

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
            redacted_data.pop("content", None)
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


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_SNAPSHOT_KEYS = frozenset({
    "checkpoint_id",
    "deadline",
    "last_error_fingerprint",
    "operation_id",
    "phase",
    "retry_count",
    "revision",
    "status",
    "updated_at",
})


@dataclass(frozen=True, slots=True)
class ValidatedHistoricalRetainPrefix:
    """Verified structural, identity, and receipt facts for one retain timeline prefix."""

    document_id: str
    principal_id: str
    audience: MemoryAudience
    retention_mode: str
    source_content_sha256: str | None
    observed_content_sha256: str | None
    source_history_sha256: str
    erased_at: datetime | None
    erasure: dict[str, object] | None
    defect_code: str | None
    terminal_disposition: str | None
    target_phase: str | None
    target_status: str | None
    prefix_retry_count: int
    prefix_last_error_fingerprint: str | None


def _parse_utc_second_text(value: object, message: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(message)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise RuntimeError(message) from error


def _validate_retain_event_envelope(event: TimelineEvent) -> tuple[dict[str, object], dict[str, object]]:
    if not isinstance(event.payload, dict) or set(event.payload) != {"checkpoint", "data"}:
        raise RuntimeError("historical chronology event payload is malformed")
    checkpoint = event.payload.get("checkpoint")
    data = event.payload.get("data")
    if not isinstance(checkpoint, dict) or not isinstance(data, dict):
        raise RuntimeError("historical chronology event payload is malformed")
    if set(checkpoint) != _CHECKPOINT_SNAPSHOT_KEYS:
        raise RuntimeError("historical chronology event checkpoint is malformed")
    retry_count = checkpoint.get("retry_count")
    fingerprint = checkpoint.get("last_error_fingerprint")
    if (
        checkpoint["checkpoint_id"] != event.checkpoint_id
        or checkpoint["operation_id"] != event.operation_id
        or checkpoint["revision"] != event.sequence
        or checkpoint["updated_at"] != event.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        or checkpoint["deadline"] is not None
        or type(retry_count) is not int
        or not 0 <= retry_count <= 3
        or (fingerprint is not None and (not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint)))
    ):
        raise RuntimeError("historical chronology event checkpoint differs")
    return checkpoint, data


def _validate_erased_queued_data(
    data: dict[str, object],
) -> tuple[str, str, str, str, MemoryAudience, str, str | None, str | None, datetime, dict[str, object]]:
    required = {
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
        "content_erasure",
    }
    optional = {"audience", "project_id", "retention_until", "retention_mode", "content_compaction"}
    if (
        not required <= set(data)
        or set(data) - required - optional
        or data["kind"] != "hindsight_memory_document"
        or any(
            not isinstance(data[key], str) or not data[key]
            for key in required - {"content_erasure"}
        )
    ):
        raise RuntimeError("erased memory source metadata is malformed")
    proof = data["content_erasure"]
    if (
        not isinstance(proof, dict)
        or set(proof) != {"contract", "erased_at", "message_sha256", "operation_sha256", "principal_sha256", "status"}
        or proof["contract"] != "EvaConversationContentErasure/v1"
        or proof["status"] != "erased"
        or any(
            not isinstance(proof[key], str) or not _SHA256_RE.fullmatch(proof[key])
            for key in ("message_sha256", "operation_sha256", "principal_sha256")
        )
        or proof["message_sha256"] != hashlib.sha256(str(data["source_ref"]).encode("utf-8")).hexdigest()
        or proof["principal_sha256"] != hashlib.sha256(str(data["principal_id"]).encode("utf-8")).hexdigest()
    ):
        raise RuntimeError("erased memory source proof differs")
    occurred = _parse_utc_second_text(data["occurred_at"], "erased memory source time is malformed")
    erased_at = _parse_utc_second_text(proof["erased_at"], "erased memory source time is malformed")
    if erased_at < occurred:
        raise RuntimeError("memory erasure precedes its source")
    if "content_compaction" in data:
        compacted = compacted_private_memory_source_from_data(
            {key: value for key, value in data.items() if key != "content_erasure"}
        )
        return (
            compacted.tenant_id,
            compacted.installation_id,
            compacted.document_id,
            compacted.principal_id,
            compacted.audience,
            compacted.retention_mode,
            compacted.content_compaction.content_sha256,
            compacted.content_compaction.source_payload_sha256,
            erased_at,
            proof,
        )
    conversation_id = str(data["conversation_id"])
    principal_id = str(data["principal_id"])
    if "audience" in data:
        audience = memory_audience_from_wire(data["audience"])
    elif conversation_id.startswith("telegram:-"):
        raise RuntimeError("erased group memory lacks its original audience")
    else:
        audience = MemoryAudience.private_person(principal_id=principal_id)
    mode = str(data.get("retention_mode", "immediate"))
    if (
        audience.principal_id != principal_id
        or mode not in {"dream", "immediate"}
        or data["privacy_class"] not in {"public", "internal", "confidential", "restricted"}
        or data["trust"] not in {
            "direct_user",
            "verified_external",
            "host_policy",
            "model_inference_unconfirmed",
            "legacy_unverified",
        }
    ):
        raise RuntimeError("erased memory source scope is invalid")
    project = data.get("project_id")
    if project is not None and (not isinstance(project, str) or not project.strip() or len(project) > 200):
        raise RuntimeError("erased memory source project is invalid")
    if data.get("retention_until") is not None:
        retention_until = _parse_utc_second_text(
            data["retention_until"], "erased memory retention time is malformed"
        )
        if retention_until < occurred:
            raise RuntimeError("erased memory retention precedes its source")
    document_id = str(data["document_id"])
    if mode == "dream" and (
        audience.kind != "private_person"
        or data["trust"] != "direct_user"
        or project is not None
        or document_id != f"{conversation_id}:exchange:{data['source_ref']}"
    ):
        raise RuntimeError("erased dream source scope is invalid")
    if audience.kind == "household":
        prefix_conv = f"household:{audience.household_id}:conversation:"
        if (
            not conversation_id.startswith(prefix_conv)
            or not conversation_id.removeprefix(prefix_conv)
            or project is not None
            or any(not str(data[key]).startswith(conversation_id + ":") for key in ("document_id", "source_ref", "evidence_ref"))
        ):
            raise RuntimeError("erased household source has cross-scope provenance")
    return (
        str(data["tenant_id"]),
        str(data["installation_id"]),
        document_id,
        principal_id,
        audience,
        mode,
        None,
        None,
        erased_at,
        proof,
    )


def validate_historical_retain_prefix(
    timeline: PostgresOperationalTimeline,
    *,
    tenant_id: str,
    installation_id: str,
    thread_id: str,
    prefix: tuple[TimelineEvent, ...],
) -> ValidatedHistoricalRetainPrefix:
    """Validate every event in a retain prefix and classify any chronology-only defect."""
    if len(prefix) < 2 or prefix[0].event_type != "MEMORY_RETAIN_QUEUED":
        raise RuntimeError("historical chronology isolation requires queued history")
    if tuple(item.sequence for item in prefix) != tuple(range(1, len(prefix) + 1)):
        raise RuntimeError("historical chronology sequence is non-contiguous")
    first_cp, queued_data = _validate_retain_event_envelope(prefix[0])
    if (
        first_cp["phase"] != "memory_queued"
        or first_cp["status"] != "active"
        or first_cp["retry_count"] != 0
        or first_cp["last_error_fingerprint"] is not None
    ):
        raise RuntimeError("historical chronology initial checkpoint is malformed")
    if "content_erasure" in queued_data:
        (
            doc_tenant,
            doc_installation,
            document_id,
            principal_id,
            audience,
            retention_mode,
            source_content_sha256,
            source_payload_sha256,
            erased_at,
            erasure,
        ) = _validate_erased_queued_data(queued_data)
    else:
        document = conversation_memory_document_from_outbox_data(queued_data)
        doc_tenant = document.tenant_id
        doc_installation = document.installation_id
        document_id = document.document_id
        principal_id = document.principal_id
        audience = document.audience
        retention_mode = document.retention_mode
        source_content_sha256 = (
            document.content_compaction.content_sha256
            if isinstance(document, CompactedPrivateMemorySource)
            else hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        )
        source_payload_sha256 = (
            document.content_compaction.source_payload_sha256
            if isinstance(document, CompactedPrivateMemorySource)
            else canonical_sha256(queued_data)
        )
        erased_at = None
        erasure = None
    if (
        not document_id
        or document_id.startswith("procedure-reference:")
        or thread_id != PostgresMemoryOutbox._thread_id(document_id)
        or doc_tenant != tenant_id
        or doc_installation != installation_id
    ):
        raise RuntimeError("historical chronology isolation source scope differs")
    operation_id = str(uuid5(NAMESPACE_URL, document_id))
    if any(item.operation_id != operation_id for item in prefix):
        raise RuntimeError("historical chronology isolation operation binding differs")

    compaction = queued_data.get("content_compaction")
    first_hash = (
        compaction.get("queued_payload_sha256")
        if isinstance(compaction, dict) and isinstance(compaction.get("queued_payload_sha256"), str)
        else prefix[0].payload_sha256
    )
    history_records = [
        {
            "event_id": str(item.event_id),
            "sequence": item.sequence,
            "event_type": item.event_type,
            "occurred_at": item.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "payload_sha256": first_hash if idx == 0 else item.payload_sha256,
        }
        for idx, item in enumerate(prefix)
    ]
    source_history_sha256 = canonical_sha256(history_records)

    observed_content_sha256 = source_content_sha256
    has_succeeded_receipt = False
    has_zero_receipt = False
    defect_code: str | None = None
    strict_attempt = 0
    strict_prior_type = prefix[0].event_type
    strict_last_error: str | None = None
    strict_result = "processing"

    for index, event in enumerate(prefix[1:], 1):
        cp, data = _validate_retain_event_envelope(event)
        etype = event.event_type
        if etype == "MEMORY_RETAIN_ATTEMPTED":
            attempt_val = data.get("attempt")
            if (
                set(data) != {"attempt", "document_id"}
                or data.get("document_id") != document_id
                or type(attempt_val) is not int
                or not 1 <= attempt_val <= 3
                or cp["phase"] != "memory_retaining"
                or cp["status"] != "waiting_for_proof"
                or cp["retry_count"] != attempt_val
            ):
                raise RuntimeError("historical chronology attempt event is malformed")
            if defect_code is None:
                next_attempt = strict_attempt + 1
                if (
                    strict_prior_type not in {"MEMORY_RETAIN_QUEUED", "MEMORY_RETAIN_RETRY_SCHEDULED"}
                    or next_attempt > 3
                    or attempt_val != next_attempt
                    or cp["last_error_fingerprint"] != strict_last_error
                ):
                    defect_code = "attempt_out_of_order"
                else:
                    strict_attempt = next_attempt
                    strict_result = "processing"
        elif etype == "MEMORY_DREAM_SOURCE_RECONCILED":
            if (
                retention_mode != "dream"
                or set(data) != {"document_id", "batch_thread_id", "bank_id", "operation_id"}
                or data["document_id"] != document_id
                or any(not isinstance(data[k], str) or not str(data[k]).strip() for k in ("batch_thread_id", "bank_id", "operation_id"))
                or cp["phase"] != "memory_retaining"
                or cp["status"] != "waiting_for_proof"
            ):
                raise RuntimeError("historical chronology dream reconciliation is malformed")
            batch_events = timeline.events(str(data["batch_thread_id"]))
            if not batch_events or batch_events[0].event_type != "MEMORY_DREAM_BATCH_ASSIGNED":
                raise RuntimeError("historical chronology dream manifest is missing")
            _, manifest_data = _validate_retain_event_envelope(batch_events[0])
            members = PostgresMemoryOutbox._dream_members(manifest_data, str(data["batch_thread_id"]))
            bindings = [
                _validate_retain_event_envelope(candidate)[1]
                for candidate in batch_events
                if candidate.event_type == "MEMORY_DREAM_DISPATCH_BOUND"
            ]
            member_matches = [
                item
                for item in members
                if item["document_id"] == document_id
                and (source_payload_sha256 is None or item["source_payload_sha256"] == source_payload_sha256)
            ]
            if len(member_matches) != 1 or bindings != [{"bank_id": data["bank_id"], "operation_id": data["operation_id"]}]:
                raise RuntimeError("historical chronology dream binding differs")
            if defect_code is None:
                if (
                    strict_prior_type not in {"MEMORY_RETAIN_RETRY_SCHEDULED", "MEMORY_RETAIN_NEEDS_HELP"}
                    or cp["retry_count"] != strict_attempt
                    or cp["last_error_fingerprint"] != strict_last_error
                ):
                    defect_code = "attempt_out_of_order"
                else:
                    strict_result = "processing"
        elif etype == "MEMORY_RETAIN_SUCCEEDED":
            if set(data) != {"bank_id", "content_sha256", "document_id"}:
                raise RuntimeError("historical chronology success event is malformed")
            receipt = MemoryRetentionReceipt(
                bank_id=data.get("bank_id"),  # type: ignore[arg-type]
                document_id=data.get("document_id"),  # type: ignore[arg-type]
                content_sha256=data.get("content_sha256"),  # type: ignore[arg-type]
            )
            if (
                receipt.document_id != document_id
                or (observed_content_sha256 is not None and receipt.content_sha256 != observed_content_sha256)
                or cp["phase"] != "memory_retained"
                or cp["status"] != "completed"
                or cp["last_error_fingerprint"] is not None
            ):
                raise RuntimeError("historical chronology success receipt differs")
            observed_content_sha256 = receipt.content_sha256
            has_succeeded_receipt = True
            if defect_code is None:
                if (
                    strict_prior_type not in {"MEMORY_RETAIN_ATTEMPTED", "MEMORY_DREAM_SOURCE_RECONCILED"}
                    or cp["retry_count"] != strict_attempt
                ):
                    defect_code = "success_out_of_order"
                else:
                    strict_result = "up_to_date"
        elif etype == "MEMORY_REFLECTION_CLAIMED":
            expected_claim = {
                "principal_sha256": hashlib.sha256(principal_id.encode("utf-8")).hexdigest(),
                "document_sha256": hashlib.sha256(document_id.encode("utf-8")).hexdigest(),
                "meaning": "dispatch claimed; completion and outcome recorded separately",
            }
            if (
                audience.kind != "private_person"
                or data != expected_claim
                or cp["phase"] != "memory_retained"
                or cp["status"] != "completed"
                or cp["last_error_fingerprint"] is not None
            ):
                raise RuntimeError("historical chronology reflection claim is malformed")
            if defect_code is None:
                if strict_prior_type != "MEMORY_RETAIN_SUCCEEDED" or cp["retry_count"] != strict_attempt:
                    defect_code = "success_out_of_order"
                else:
                    strict_result = "up_to_date"
        elif etype == "MEMORY_RETAIN_NOT_SELECTED":
            content_digest = data.get("content_sha256")
            if (
                set(data) != {"bank_id", "content_sha256", "document_id", "selected_fact_count", "operation_id"}
                or data["document_id"] != document_id
                or not isinstance(content_digest, str)
                or not _SHA256_RE.fullmatch(content_digest)
                or (observed_content_sha256 is not None and content_digest != observed_content_sha256)
                or type(data["selected_fact_count"]) is not int
                or data["selected_fact_count"] != 0
                or not isinstance(data["bank_id"], str)
                or not data["bank_id"].strip()
                or not isinstance(data["operation_id"], str)
                or not data["operation_id"].strip()
                or cp["phase"] != "memory_not_selected"
                or cp["status"] != "completed"
                or cp["last_error_fingerprint"] is not None
            ):
                raise RuntimeError("historical chronology zero-selection receipt differs")
            observed_content_sha256 = content_digest
            has_zero_receipt = True
            if defect_code is None:
                if (
                    strict_prior_type not in {"MEMORY_RETAIN_ATTEMPTED", "MEMORY_DREAM_SOURCE_RECONCILED"}
                    or cp["retry_count"] != strict_attempt
                ):
                    defect_code = "success_out_of_order"
                else:
                    strict_result = "up_to_date"
        elif etype == "MEMORY_RETAIN_RESULT_RECONCILED":
            content_digest = data.get("content_sha256")
            prev_event = prefix[index - 1]
            prev_cp, _ = _validate_retain_event_envelope(prev_event)
            expected_subhistory = canonical_sha256(history_records[:index])
            if (
                set(data) != {
                    "document_id",
                    "bank_id",
                    "content_sha256",
                    "operation_id",
                    "selected_fact_count",
                    "disposition",
                    "proof_sha256",
                    "source_history_sha256",
                }
                or data["document_id"] != document_id
                or not isinstance(content_digest, str)
                or not _SHA256_RE.fullmatch(content_digest)
                or (observed_content_sha256 is not None and content_digest != observed_content_sha256)
                or not isinstance(data["source_history_sha256"], str)
                or not _SHA256_RE.fullmatch(data["source_history_sha256"])
                or (erased_at is None and data["source_history_sha256"] != expected_subhistory)
                or type(data["selected_fact_count"]) is not int
                or data["selected_fact_count"] != 0
                or data["disposition"] != "not_selected"
                or not isinstance(data["proof_sha256"], str)
                or not _SHA256_RE.fullmatch(data["proof_sha256"])
                or any(
                    not isinstance(data[key], str) or not str(data[key]).strip() or len(str(data[key])) > 512
                    for key in ("bank_id", "operation_id")
                )
                or cp["phase"] != "memory_not_selected"
                or cp["status"] != "completed"
            ):
                raise RuntimeError("historical chronology closed reconciliation is malformed")
            observed_content_sha256 = content_digest
            has_zero_receipt = True
            if defect_code is None:
                if (
                    prev_event.event_type not in {"MEMORY_RETAIN_NEEDS_HELP", "MEMORY_RETAIN_RETRY_SCHEDULED"}
                    or prev_cp["status"] not in {"failed", "needs_help"}
                    or cp["retry_count"] != prev_cp["retry_count"]
                    or cp["last_error_fingerprint"] != prev_cp["last_error_fingerprint"]
                ):
                    defect_code = "terminal_state_out_of_order"
                else:
                    strict_result = "up_to_date"
        elif etype == "MEMORY_RETAIN_ERASED":
            if (
                erasure is None
                or data != {
                    "contract": "EvaMemoryOutboxErasure/v1",
                    "document_sha256": hashlib.sha256(document_id.encode("utf-8")).hexdigest(),
                    "operation_sha256": erasure["operation_sha256"],
                    "principal_sha256": erasure["principal_sha256"],
                    "status": "erased",
                }
                or cp["phase"] != "memory_erased"
                or cp["status"] != "completed"
            ):
                raise RuntimeError("historical chronology erasure receipt differs")
            if defect_code is None:
                if (
                    event is not prefix[-1]
                    or strict_prior_type in {
                        "MEMORY_RETAIN_SUCCEEDED",
                        "MEMORY_RETAIN_NOT_SELECTED",
                        "MEMORY_RETAIN_RESULT_RECONCILED",
                        "MEMORY_REFLECTION_CLAIMED",
                        "MEMORY_RETAIN_ERASED",
                    }
                    or event.occurred_at != erased_at
                    or cp["retry_count"] != strict_attempt
                    or cp["last_error_fingerprint"] != strict_last_error
                ):
                    defect_code = "erased_not_terminal"
                else:
                    strict_result = "no_signals"
        elif etype == "MEMORY_RETAIN_RETRY_SCHEDULED":
            err_fp = data.get("error_fingerprint")
            retry_val = data.get("retry_count")
            if (
                set(data) != {"document_id", "error_fingerprint", "retry_count"}
                or data["document_id"] != document_id
                or type(retry_val) is not int
                or not 1 <= retry_val <= 3
                or not isinstance(err_fp, str)
                or not _SHA256_RE.fullmatch(err_fp)
                or cp["phase"] != "memory_retry"
                or cp["status"] != "failed"
                or cp["retry_count"] != retry_val
                or cp["last_error_fingerprint"] != err_fp
            ):
                raise RuntimeError("historical chronology retry event is malformed")
            if defect_code is None:
                if (
                    strict_prior_type != "MEMORY_RETAIN_ATTEMPTED"
                    or strict_attempt >= 3
                    or retry_val != strict_attempt
                ):
                    defect_code = "attempt_out_of_order"
                else:
                    strict_last_error = err_fp
                    strict_result = "retrying"
        elif etype == "MEMORY_RETAIN_NEEDS_HELP":
            failure_keys = {"document_id", "error_fingerprint", "retry_count"}
            restart_keys = {"document_id", "reason"}
            next_strict_error = strict_last_error
            if set(data) == failure_keys:
                err_fp = data.get("error_fingerprint")
                retry_val = data.get("retry_count")
                if (
                    data["document_id"] != document_id
                    or type(retry_val) is not int
                    or not 1 <= retry_val <= 3
                    or not isinstance(err_fp, str)
                    or not _SHA256_RE.fullmatch(err_fp)
                    or cp["retry_count"] != retry_val
                    or cp["last_error_fingerprint"] != err_fp
                ):
                    raise RuntimeError("historical chronology terminal failure is malformed")
                next_strict_error = err_fp
                strict_retry_matches = retry_val == 3
            elif set(data) == restart_keys:
                if (
                    data["document_id"] != document_id
                    or data["reason"] != "retry_budget_exhausted_after_ambiguous_restart"
                    or not 1 <= int(cp["retry_count"]) <= 3
                ):
                    raise RuntimeError("historical chronology terminal restart is malformed")
                strict_retry_matches = cp["retry_count"] == 3 and cp["last_error_fingerprint"] == strict_last_error
            else:
                raise RuntimeError("historical chronology terminal state is malformed")
            if cp["phase"] != "memory_needs_help" or cp["status"] != "needs_help":
                raise RuntimeError("historical chronology terminal checkpoint is malformed")
            if defect_code is None:
                if (
                    strict_prior_type != "MEMORY_RETAIN_ATTEMPTED"
                    or strict_attempt != 3
                    or not strict_retry_matches
                ):
                    defect_code = "terminal_state_out_of_order"
                else:
                    strict_last_error = next_strict_error
                    strict_result = "needs_help"
        else:
            raise RuntimeError("historical chronology event is unknown")
        strict_prior_type = etype

    last_cp, _ = _validate_retain_event_envelope(prefix[-1])
    prefix_phase = str(last_cp["phase"])
    prefix_status = str(last_cp["status"])
    prefix_retry_count = int(last_cp["retry_count"])
    prefix_last_error = (
        None if last_cp["last_error_fingerprint"] is None else str(last_cp["last_error_fingerprint"])
    )

    if defect_code is None and erased_at is not None:
        if (
            prefix_status != "completed"
            or strict_result not in {"up_to_date", "no_signals"}
            or erased_at < prefix[-1].occurred_at
        ):
            defect_code = "erased_not_terminal"
    if defect_code is None and any(
        later.occurred_at < earlier.occurred_at
        for earlier, later in zip(prefix, prefix[1:])
    ):
        defect_code = "backwards_timestamp"

    terminal_disposition: str | None = None
    target_phase: str | None = None
    target_status: str | None = None
    if erased_at is not None:
        terminal_disposition = "erased"
        target_phase = (
            prefix_phase
            if prefix_phase in {"memory_erased", "memory_not_selected", "memory_retained"}
            else "memory_erased"
        )
        target_status = "completed"
    elif (
        prefix_phase == "memory_retained"
        and prefix_status == "completed"
        and prefix[-1].event_type in {"MEMORY_RETAIN_SUCCEEDED", "MEMORY_REFLECTION_CLAIMED"}
        and has_succeeded_receipt
        and source_content_sha256 is not None
    ):
        terminal_disposition = "retained"
        target_phase = "memory_retained"
        target_status = "completed"
    elif (
        prefix_phase == "memory_not_selected"
        and prefix_status == "completed"
        and prefix[-1].event_type in {"MEMORY_RETAIN_NOT_SELECTED", "MEMORY_RETAIN_RESULT_RECONCILED"}
        and has_zero_receipt
        and source_content_sha256 is not None
    ):
        terminal_disposition = "not_selected"
        target_phase = "memory_not_selected"
        target_status = "completed"
    elif (
        prefix_phase == "memory_needs_help"
        and prefix_status == "needs_help"
        and prefix[-1].event_type == "MEMORY_RETAIN_NEEDS_HELP"
        and source_content_sha256 is not None
    ):
        terminal_disposition = "needs_help"
        target_phase = "memory_needs_help"
        target_status = "needs_help"

    return ValidatedHistoricalRetainPrefix(
        document_id=document_id,
        principal_id=principal_id,
        audience=audience,
        retention_mode=retention_mode,
        source_content_sha256=source_content_sha256,
        observed_content_sha256=observed_content_sha256,
        source_history_sha256=source_history_sha256,
        erased_at=erased_at,
        erasure=erasure,
        defect_code=defect_code,
        terminal_disposition=terminal_disposition,
        target_phase=target_phase,
        target_status=target_status,
        prefix_retry_count=prefix_retry_count,
        prefix_last_error_fingerprint=prefix_last_error,
    )


__all__ = [
    "DurableMemoryRetentionJob",
    "MemoryOutboxContentErasureReceipt",
    "PostgresMemoryOutbox",
    "ValidatedHistoricalRetainPrefix",
    "conversation_memory_document_from_outbox_data",
    "validate_historical_retain_prefix",
]
