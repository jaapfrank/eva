"""Large-history status must stay complete, scoped and consistent under writes."""
from dataclasses import replace
from datetime import timedelta
import hashlib

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.memory_outbox import DurableMemoryRetentionJob, PostgresMemoryOutbox
from eva.adapters.postgres.operational_timeline import OperationalTimelineError, operational_checkpoints
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.application.memory import MemoryRetentionReceipt
from tests.postgres.test_people_memory_overview import (
    NOW, TENANT, INSTALLATION, _document, _timeline, people_memory_engine,
)


@pytest.fixture(scope="module")
def large_history(people_memory_engine):
    tenant = "large-history-tenant"
    installation = "large-history-home"
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    for index in range(270):
        document = replace(_document(f"large-{index}"), tenant_id=tenant, installation_id=installation)
        job = outbox.begin(outbox.enqueue(document), now=NOW)
        outbox.succeed(job, MemoryRetentionReceipt("test-bank", document.document_id,
            hashlib.sha256(document.content.encode()).hexdigest()), now=NOW)
    return tenant, installation, outbox


def test_large_history_is_complete_without_per_source_round_trips(people_memory_engine, large_history):
    tenant, installation, _ = large_history
    timeline = _timeline(people_memory_engine, tenant=tenant, installation=installation)
    statements = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "eva_core.operational_" in statement:
            statements.append(statement)
    sa.event.listen(people_memory_engine, "before_cursor_execute", observe)
    try:
        rows = list(timeline.iter_threads(thread_prefix="memory-retain:"))
    finally:
        sa.event.remove(people_memory_engine, "before_cursor_execute", observe)
    assert len(rows) == 270
    assert len(statements) == 4  # Two checkpoint pages and two bulk event reads.
    assert all(state.status == "completed" and len(events) == 3 for state, events in rows)
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id=installation)
    assert overview.snapshot(now=NOW, hindsight_state="ready").memory_flow == "up_to_date"
    other = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id="other-home")
    assert other.snapshot(now=NOW, hindsight_state="ready").memory_flow == "no_signals"
    assert "privé geheugen canary" not in str(overview.snapshot(now=NOW, hindsight_state="ready").to_payload())


def test_attention_and_corrupt_tail_are_not_hidden_by_large_history(people_memory_engine, large_history):
    tenant, installation, outbox = large_history
    document = replace(_document("attention-after-large-history"), tenant_id=tenant, installation_id=installation)
    job = outbox.enqueue(document)
    for attempt in range(3):
        started = outbox.begin(job, now=NOW + timedelta(seconds=attempt * 2 + 1))
        failed = outbox.fail(started, RuntimeError("synthetic failure"),
                             now=NOW + timedelta(seconds=attempt * 2 + 2))
        job = DurableMemoryRetentionJob(document, failed)
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id=installation)
    assert overview.snapshot(now=NOW + timedelta(seconds=9), hindsight_state="ready").memory_flow == "needs_help"
    # This sorts after all real source hashes: ignoring a later page must not go green.
    timeline = _timeline(people_memory_engine, tenant=tenant, installation=installation)
    timeline.create_thread(thread_id="memory-retain:zz-malformed", checkpoint_id="malformed:queued",
        operation_id="malformed", occurred_at=NOW, data={"kind":"corrupted-canary"},
        event_type="MEMORY_RETAIN_QUEUED", phase="memory_queued", status="active")
    assert overview.snapshot(now=NOW, hindsight_state="ready").memory_flow == "unavailable"


def test_page_snapshot_does_not_mix_concurrent_appends(people_memory_engine):
    timeline = _timeline(people_memory_engine, tenant="snapshot-tenant", installation="snapshot-home")
    def create(index):
        return timeline.create_thread(thread_id=f"snapshot:{index:03d}", checkpoint_id=f"snapshot:{index}:queued",
            operation_id=f"operation:{index}", occurred_at=NOW, data={"index":index},
            event_type="QUEUED", phase="queued", status="active")
    for index in range(5): create(index)
    rows = timeline.iter_threads(thread_prefix="snapshot:", page_size=2)
    first = next(rows)
    timeline.transition(thread_id="snapshot:004", expected_revision=1, checkpoint_id="snapshot:4:done",
        phase="done", status="completed", event_type="DONE", occurred_at=NOW + timedelta(seconds=1), data={})
    create(6)
    old_snapshot = [first, *rows]
    assert len(old_snapshot) == 5
    assert all(state.revision == 1 and len(events) == 1 for state, events in old_snapshot)
    fresh_snapshot = list(timeline.iter_threads(thread_prefix="snapshot:", page_size=2))
    assert len(fresh_snapshot) == 6
    assert fresh_snapshot[4][0].revision == 2


def test_later_page_projection_corruption_stays_visible(people_memory_engine):
    timeline = _timeline(people_memory_engine, tenant="projection-tenant", installation="projection-home")
    for index in range(3):
        timeline.create_thread(thread_id=f"corrupt:{index}", checkpoint_id=f"corrupt:{index}:queued",
            occurred_at=NOW, data={}, event_type="QUEUED", phase="queued", status="active")
    with people_memory_engine.begin() as connection:
        timeline._scope(connection)
        connection.execute(sa.update(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == timeline._scope_value.tenant_id,
            operational_checkpoints.c.installation_id == timeline._scope_value.installation_id,
            operational_checkpoints.c.thread_id == "corrupt:2",
        ).values(phase="changed-without-event"))
    with pytest.raises(OperationalTimelineError, match="differs"):
        list(timeline.iter_threads(thread_prefix="corrupt:", page_size=2))


@pytest.mark.parametrize("page_size", [0, 257, True, 1.5])
def test_invalid_page_size_rejected_without_database_read(people_memory_engine, page_size):
    timeline = _timeline(people_memory_engine, tenant=TENANT, installation=INSTALLATION)
    with pytest.raises(ValueError, match="page size"):
        list(timeline.iter_threads(thread_prefix="memory-retain:", page_size=page_size))
