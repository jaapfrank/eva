"""Feed completed private work into Eva's existing durable cold memory path."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict, PostgresOperationalTimeline, TimelineNotFound,
)
from eva.application.conversation import ConversationConflict
from eva.application.delegated_work_memory import outcome_document, document_binding_sha256
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
from eva.providers.mcp_autonomy import _SourceTurnLoader


class DelegatedWorkMemory:
    """No new model loop: project once; existing memory budget/outbox handle retention."""

    def __init__(self, engine, service, submit, *, clock=None):
        self._service = service
        self._submit = submit
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self._active_since = None
        self._next_poll = 0.0
        self._timeline = PostgresOperationalTimeline(engine, service.store.scope)
        self._memory_timeline = PostgresOperationalTimeline(engine, service.conversation_scope)
        self._source = _SourceTurnLoader(engine, SimpleNamespace(
            scope=service.conversation_scope, authorize_turn=service.validate_authority))

    def run_once(self) -> str:
        if time.monotonic() < self._next_poll:
            return "idle"
        now = self._clock()
        if self._active_since is None:
            self._active_since = self._activate(now)
        work = self._service.store.next_memory_outcome(
            **self._service._owner, now=now, completed_since=self._active_since)
        if work is None:
            self._next_poll = time.monotonic() + 5.0
            return "idle"
        try:
            if type(work.spec) is DelegatedInitiativeDigitalWorkSpec:
                loader = self._service.store._initiative_authority_loader
                if loader is None:
                    raise DelegatedWorkConflict("initiative memory source is unavailable")
                source = loader().work_memory_source(work, now=now)
            else:
                source = self._source(work.spec.source.model_dump(mode="json"))
        except (ConversationConflict, DelegatedWorkConflict):
            # Forgotten/missing source content must never be reconstructed from the task.
            self._record(work, now, "source_unavailable", None)
            return "skipped"
        document = outcome_document(work, source, ingress_sha256=canonical_sha256(
            PostgresConversationJournal._ingress_data(source)))
        existing = self._recorded(document)
        if existing is not None:
            self._record(work, now, existing, document)
            return "recorded" if existing == "queued" else "skipped"
        admission = self._submit(document)
        admitted = getattr(admission, "admitted", True)
        if not isinstance(admitted, bool):
            raise RuntimeError("work memory admission is invalid")
        self._record(work, now, "queued" if admitted else "budget_declined", document)
        return "queued" if admitted else "skipped"

    def _activate(self, now):
        # Persist once so a restart never imports old trials or reopens forgotten outcomes.
        thread = "work-memory:activation:v1"
        try:
            self._timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":activated",
                operation_id=None, occurred_at=now,
                data={"kind": "private_work_outcome_memory", "version": 1},
                event_type="WORK_MEMORY_ACTIVATED", phase="work_memory_active", status="completed")
        except CheckpointConflict:
            pass
        events = self._timeline.events(thread)
        if (not events or events[0].event_type != "WORK_MEMORY_ACTIVATED"
                or events[0].payload.get("data") != {
                    "kind": "private_work_outcome_memory", "version": 1}):
            raise RuntimeError("work memory activation is invalid")
        return events[0].occurred_at

    def _recorded(self, document):
        """Recover enqueue-before-marker without repeating or resurrecting retention."""
        thread = PostgresMemoryOutbox._thread_id(document.document_id)
        try:
            events = self._memory_timeline.events(thread)
        except TimelineNotFound:
            return None
        if not events:
            return None
        first = events[0]
        if first.event_type != "MEMORY_RETAIN_QUEUED":
            raise RuntimeError("work memory source event differs")
        stored = dict(first.payload.get("data", {}))
        expected = PostgresMemoryOutbox._document_data(document)
        for value in (stored, expected):
            value.pop("retention_until", None)
        erasure = stored.pop("content_erasure", None)
        if erasure is not None:
            if (not isinstance(erasure, dict) or erasure.get("status") != "erased"
                    or "content" in stored):
                raise RuntimeError("work memory source erasure is invalid")
            expected.pop("content")
        if stored != expected:
            raise RuntimeError("work memory source replay differs")
        return "erased" if erasure is not None else "queued"

    def owns(self, document) -> bool:
        """Authorize erasure only of an exactly projected, server-loaded source."""
        if not re.fullmatch(r"work-outcome:[0-9a-f]{64}", document.document_id):
            return False
        thread = "work-memory:" + document.document_id.removeprefix("work-outcome:")
        try:
            events = self._timeline.events(thread)
        except TimelineNotFound:
            return False
        if not events or events[0].event_type != "WORK_MEMORY_PROJECTED":
            return False
        data = events[0].payload.get("data", {})
        return (data.get("disposition") == "queued"
                and data.get("document_id") == document.document_id
                and data.get("binding_sha256") == document_binding_sha256(document)
                and data.get("content_sha256") == hashlib.sha256(document.content.encode()).hexdigest())

    def _record(self, work, now, disposition, document):
        digest = hashlib.sha256(work.spec.work_id.encode()).hexdigest()
        thread = "work-memory:" + digest
        data = {"work_id": work.spec.work_id, "spec_sha256": work.spec.sha256,
                "disposition": disposition,
                "binding_sha256": None if document is None else document_binding_sha256(document),
                "document_id": None if document is None else document.document_id,
                "content_sha256": None if document is None else hashlib.sha256(
                    document.content.encode()).hexdigest()}
        try:
            self._timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":projected",
                operation_id=None, occurred_at=now, data=data,
                event_type="WORK_MEMORY_PROJECTED", phase="work_memory_projected", status="completed")
        except CheckpointConflict:
            events = self._timeline.events(thread)
            if not events or events[0].payload.get("data") != data:
                raise RuntimeError("work memory projection replay differs") from None
