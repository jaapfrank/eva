"""Keep an unfinished admitted task's original source within its existing retention.

No source copy, new memory, or new authorization is created. Explicit erasure and
stop remain authoritative; the normal day compactor resumes once work releases it.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.contracts.canonical_json import canonical_sha256
from eva.application.conversation import ConversationConflict


class WorkSourceRetention:
    def __init__(self, store):
        self.store = store

    def protected_sources(self, tenant_id, installation_id, now):
        w = delegated_work_runs.alias("retained_source_work")
        child = delegated_work_runs.alias("retained_source_child")
        source = w.c.spec_payload["source"]
        descendant = sa.exists(sa.select(1).where(
            child.c.tenant_id == w.c.tenant_id, child.c.installation_id == w.c.installation_id,
            child.c.owner_principal_id == w.c.owner_principal_id,
            child.c.principal_binding_id == w.c.principal_binding_id,
            child.c.spec_payload["parent_task_id"].astext == w.c.work_id,
            child.c.spec_payload["execution_id"].astext == w.c.spec_payload["execution_id"].astext))
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            rows = connection.execute(sa.select(w.c.spec_payload, w.c.spec_sha256).where(
                w.c.tenant_id == self.store.scope.tenant_id,
                w.c.installation_id == self.store.scope.installation_id,
                w.c.retention_until > now, w.c.stop_requested_at.is_(None),
                w.c.state.not_in(("succeeded", "stopped")), ~descendant,
                source["tenant_id"].astext == tenant_id,
                source["installation_id"].astext == installation_id,
                source["audience"]["kind"].astext == "private_person",
                source["audience"]["principal_id"].astext == source["principal_id"].astext,
            )).all()
        for payload, digest in rows:
            if canonical_sha256(payload) != bytes(digest).hex():
                raise RuntimeError("retained work source binding is corrupt; do not compact")
        protected = {(payload["source"]["principal_id"], payload["source"]["conversation_id"],
                      payload["source"]["message_id"]) for payload, _ in rows}
        # Existing owner appointments allow up to 30 days before admission, then
        # the same seven-day work retention. Cancellation releases this hold.
        from eva.adapters.postgres.operational_timeline import operational_events, operational_checkpoints
        e, c = operational_events.c, operational_checkpoints.c
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            pending = connection.execute(sa.select(e.payload, e.payload_sha256).join(
                operational_checkpoints, sa.and_(c.tenant_id == e.tenant_id,
                    c.installation_id == e.installation_id, c.thread_id == e.thread_id)).where(
                c.tenant_id == self.store.scope.tenant_id, c.installation_id == self.store.scope.installation_id,
                c.thread_id.startswith("scheduled-work:"), c.phase.in_(("scheduled", "launching")),
                c.status == "active", c.deadline > now - timedelta(days=7),
                e.occurred_at > now - timedelta(days=37), e.sequence == 1,
                e.event_type == "WORK_START_SCHEDULED",
                e.payload["data"]["source"]["tenant_id"].astext == tenant_id,
                e.payload["data"]["source"]["installation_id"].astext == installation_id,
                e.payload["data"]["source"]["audience"]["kind"].astext == "private_person",
            )).all()
        for payload, digest in pending:
            if canonical_sha256(payload) != bytes(digest).hex():
                raise RuntimeError("scheduled source binding is corrupt; do not compact")
            source = payload["data"]["source"]
            if source["audience"].get("principal_id") != source["principal_id"]:
                raise RuntimeError("scheduled source audience differs")
            protected.add((source["principal_id"], source["conversation_id"], source["message_id"]))
        return frozenset(protected)


class WorkSourceUnavailable(ConversationConflict):
    """No new attempt may be admitted from absent, changed or erased source text."""


def _scheduled_task_source(service, spec, row):
    """Read an exact, previously dispatched task input; never restore conversation text."""
    from eva.adapters.postgres.operational_timeline import operational_checkpoints, PostgresOperationalTimeline
    from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    w, c = delegated_work_runs.c, operational_checkpoints.c
    with service.store._engine.begin() as connection:
        service.store._scope(connection)
        candidates = connection.execute(sa.select(w.spec_payload, w.spec_sha256, c.thread_id).join(
            operational_checkpoints, sa.and_(c.tenant_id == w.tenant_id,
                c.installation_id == w.installation_id, c.operation_id == w.work_id)).where(
            w.tenant_id == service.store.scope.tenant_id, w.installation_id == service.store.scope.installation_id,
            w.owner_principal_id == UUID(spec.owner_principal_id),
            w.principal_binding_id == UUID(spec.principal_binding_id), w.retention_until > service.now(),
            w.spec_payload["execution_id"].astext == spec.execution_id,
            c.thread_id.startswith("scheduled-work:" + spec.owner_principal_id + ":"),
            c.phase == "dispatched", c.status == "completed",
        ).order_by(c.updated_at.desc()).limit(64)).all()
    timeline = PostgresOperationalTimeline(service.store._engine, service.store.scope)
    data = row.payload["data"]
    proof = PostgresConversationJournal._require_compacted_content(data)
    for payload, digest, thread in candidates:
        if canonical_sha256(payload) != bytes(digest).hex():
            raise WorkSourceUnavailable("Retained task source record is corrupt")
        if (payload.get("source") != spec.source.model_dump(mode="json")
                or payload.get("source_ingress_sha256") != spec.source_ingress_sha256
                or payload.get("grant") != spec.grant.model_dump(mode="json")
                or payload.get("task") != spec.task):
            continue
        events = timeline.events(thread)
        if (not events or events[0].event_type != "WORK_START_SCHEDULED"
                or events[-1].event_type != "SCHEDULED_WORK_ADMITTED"
                or events[-1].payload["data"].get("work_id") != payload["work_id"]):
            raise WorkSourceUnavailable("Retained task source admission differs")
        try:
            turn = ScheduledOwnerWork._turn(events[0].payload["data"])
            ingress = PostgresConversationJournal._ingress_data(turn)
        except (KeyError, TypeError, ValueError):
            raise WorkSourceUnavailable("Retained task source is invalid") from None
        if (canonical_sha256(ingress) != spec.source_ingress_sha256
                or canonical_sha256({**row.payload, "data": ingress}) != proof.ingress_payload_sha256
                or PostgresConversationJournal._timestamp(turn.occurred_at) != row.occurred_at):
            continue
        service.validate_authority(turn)
        PostgresConversationJournal(service.store._engine, service.conversation_scope).ensure_running(turn)
        return turn
    raise WorkSourceUnavailable("No exact retained task input is available")


@contextmanager
def retained_source_guard(service, spec, *, supplied_turn=None):
    """Serialize source validation and queue commit with existing erasure fencing."""
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    if not isinstance(service.store, PostgresDelegatedWorkStore):
        yield supplied_turn
        return
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    source = spec.source.model_dump(mode="json")
    outbox = PostgresMemoryOutbox(service.store._engine,
        tenant_id=source["tenant_id"], installation_id=source["installation_id"])
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, operational_events
    with outbox.retention_guard(), service.store._engine.begin() as connection:
        PostgresOperationalTimeline(service.store._engine, service.conversation_scope)._scope(connection)
        e = operational_events.c
        row = connection.execute(sa.select(e.event_id, e.payload, e.payload_sha256, e.occurred_at).where(
            e.tenant_id == service.conversation_scope.tenant_id,
            e.installation_id == service.conversation_scope.installation_id,
            e.thread_id == "conversation:" + source["message_id"],
            e.event_type == "INGRESS_COMMITTED")).one_or_none()
        if (row is None or canonical_sha256(row.payload) != bytes(row.payload_sha256).hex()
                or connection.scalar(sa.text("SELECT eva_core.lock_conversation_source(:event_id,:digest)"),
                    {"event_id": row.event_id, "digest": bytes(row.payload_sha256)}) is not True):
            raise WorkSourceUnavailable("The original task source fence differs")
        data = row.payload.get("data", {})
        if "content_erasure" in data:
            raise WorkSourceUnavailable("The original task source was erased")
        if "content_compaction" in data:
            # The task's existing, hash-bound scheduled input has its own work
            # retention. This is not a replay or rewrite of an expired chat.
            loaded = _scheduled_task_source(service, spec, row)
        else:
            try:
                loaded = _SourceTurnLoader(service.store._engine, SimpleNamespace(
                    scope=service.conversation_scope, authorize_turn=service.validate_authority))(source)
            except ConversationConflict:
                raise WorkSourceUnavailable("The original task source is unavailable") from None
        ingress = PostgresConversationJournal._ingress_data(loaded)
        if (canonical_sha256(ingress) != spec.source_ingress_sha256
                or (supplied_turn is not None
                    and ingress != PostgresConversationJournal._ingress_data(supplied_turn))):
            raise WorkSourceUnavailable("The original task source binding differs")
        yield loaded



class TaskSourceLoader:
    """Task-aware input adapter; ordinary conversation loading keeps its old rules."""
    def __init__(self, service, original):
        self.service, self.original = service, original

    def __call__(self, source):
        return self.original(source)

    def for_task(self, spec):
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
        if type(spec) is not DelegatedDigitalWorkSpec:
            return self.original(spec.source.model_dump(mode="json"))
        with retained_source_guard(self.service, spec) as turn:
            return turn

    def validate_current(self, spec, turn):
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
        if type(spec) is not DelegatedDigitalWorkSpec:
            return  # Native and household routes retain their own source authority.
        with retained_source_guard(self.service, spec) as current:
            if current != turn:
                raise WorkSourceUnavailable("Active task source changed")
