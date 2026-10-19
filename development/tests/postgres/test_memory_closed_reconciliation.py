"""Closed native zero receipts may settle failed history without another retain."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import pytest
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox, DurableMemoryRetentionJob
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.application.memory import MemoryRetentionReceipt
from tests.postgres.test_people_memory_overview import NOW, _document, people_memory_engine


def failed(outbox, name):
    document = replace(_document(name), tenant_id=outbox._tenant_id, installation_id=outbox._installation_id)
    job = outbox.enqueue(document)
    for index in range(3):
        begun = outbox.begin(job, now=NOW + timedelta(seconds=2*index+1))
        state = outbox.fail(begun, RuntimeError("historical authentication failure"),
            now=NOW + timedelta(seconds=2*index+2))
        job = DurableMemoryRetentionJob(document, state)
    return job


def receipt(job):
    return MemoryRetentionReceipt("bank:closed", job.document.document_id,
        hashlib.sha256(job.document.content.encode()).hexdigest(),
        disposition="not_selected", selected_fact_count=0, operation_id="original-native-operation")


def test_closed_zero_reconciliation_preserves_history_and_replay(people_memory_engine):
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id="tenant:closed", installation_id="home:closed")
    job = failed(outbox, "closed-zero")
    old = outbox._timeline.events(job.state.thread_id)
    result = outbox.reconcile_closed_retention(job, receipt(job), proof_sha256="ac"*32,
        now=NOW + timedelta(seconds=7))
    assert (result.phase, result.status, result.retry_count, result.last_error_fingerprint) == (
        "memory_not_selected", "completed", 3, job.state.last_error_fingerprint)
    events = outbox._timeline.events(job.state.thread_id)
    assert events[:-1] == old
    assert events[-1].payload["data"]["operation_id"] == "original-native-operation"
    assert outbox.begin(DurableMemoryRetentionJob(job.document, result)) is None
    assert outbox.reconcile_closed_retention(job, receipt(job), proof_sha256="ac"*32,
        now=NOW + timedelta(seconds=8)) == result
    assert len(outbox._timeline.events(job.state.thread_id)) == len(old)+1
    with pytest.raises(RuntimeError, match="replay differs"):
        outbox.reconcile_closed_retention(job, receipt(job), proof_sha256="bc"*32,
            now=NOW + timedelta(seconds=8))
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id="tenant:closed", installation_id="home:closed")
    assert overview.snapshot(now=NOW + timedelta(seconds=8), hindsight_state="ready").memory_flow == "up_to_date"


def test_closed_reconciliation_rejects_changed_source_positive_and_active(people_memory_engine):
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id="tenant:closed-denied", installation_id="home:closed-denied")
    job = failed(outbox, "closed-denied")
    wrong = DurableMemoryRetentionJob(replace(job.document, content="different source"), job.state)
    with pytest.raises(RuntimeError, match="source changed"):
        outbox.reconcile_closed_retention(wrong, receipt(wrong), proof_sha256="ab"*32, now=NOW+timedelta(seconds=7))
    with pytest.raises(RuntimeError, match="proof differs"):
        outbox.reconcile_closed_retention(job, replace(receipt(job), disposition="retained", selected_fact_count=1),
            proof_sha256="ab"*32, now=NOW+timedelta(seconds=7))
    active = outbox.enqueue(replace(_document("still-active"), tenant_id=outbox._tenant_id, installation_id=outbox._installation_id))
    with pytest.raises(RuntimeError, match="no longer failed"):
        outbox.reconcile_closed_retention(active, receipt(active), proof_sha256="ab"*32, now=NOW+timedelta(seconds=7))
    assert outbox._timeline.load(job.state.thread_id).status == "needs_help"
    assert outbox._timeline.load(active.state.thread_id).status == "active"
