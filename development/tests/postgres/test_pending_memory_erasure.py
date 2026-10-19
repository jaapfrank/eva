"""Pending sources use the existing physical lease without invented retention."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib

import pytest

from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.runtime_resource_usage import PostgresMemoryStorageBudget
from eva.adapters.postgres.operational_timeline import OperationalTimelineError, CheckpointConflict
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from tests.postgres.test_runtime_resource_usage import (
    resource_engine, _document, _limited_storage_budget, TENANT, NOW,
)


def case(engine, tmp_path, *, queued=True):
    installation = "pending-" + tmp_path.name
    storage = PostgresMemoryStorageBudget(engine, tenant_id=TENANT, installation_id=installation,
        resource_budget=_limited_storage_budget(installation))
    doc = replace(_document("chat:exchange:synthetic", installation_id=installation,
                           content="Synthetic pending memory source"),
                  conversation_id="chat", source_ref="synthetic", evidence_ref="conversation-reply:synthetic",
                  retention_mode="dream")
    admission = storage.admit_memory(doc, now=NOW)
    doc = replace(doc, retention_until=admission.retention_until)
    outbox = PostgresMemoryOutbox(engine, tenant_id=TENANT, installation_id=installation)
    if queued:
        outbox.enqueue(doc)
    return storage, outbox, doc


def request(storage, doc, **changes):
    args = {"bank_id": "eva2-pending-test", "now": NOW, "source_message_id": doc.source_ref, **changes}
    return storage.request_pending_erasure(doc, **args)


def test_pending_lease_preserves_capacity_source_and_original_admission(resource_engine, tmp_path):
    storage, outbox, doc = case(resource_engine, tmp_path)
    thread = "memory-storage:" + hashlib.sha256(doc.document_id.encode()).hexdigest()
    first = storage._timeline.events(thread)[0]
    lease = request(storage, doc)
    assert lease.state.phase == "memory_storage_removing" and lease.state.status == "waiting_for_proof"
    assert lease.source_message_id == doc.source_ref and lease.retention_until == doc.retention_until
    events = storage._timeline.events(thread)
    assert events[0] == first and len(events) == 2
    assert events[-1].event_type == "MEMORY_STORAGE_PENDING_ERASURE_CLAIMED"
    assert all(e.event_type != "MEMORY_STORAGE_RETAINED" for e in events)
    assert doc.content not in str(events[-1].payload)
    assert len(outbox.pending()) == 1
    usage = storage.admit_memory(doc, now=NOW)
    assert not usage.admitted and usage.person_records_used == 1
    assert usage.person_bytes_used == len(doc.content.encode())
    assert request(storage, doc) is None and storage._timeline.events(thread) == events
    storage.mark_removed(lease, now=NOW)
    assert request(storage, doc) is None
    replay = storage.admit_memory(doc, now=NOW)
    assert not replay.admitted and replay.person_records_used == 0


def test_pending_erasure_reclaims_after_restart_and_late_receipt_cannot_overwrite(resource_engine, tmp_path):
    storage, outbox, doc = case(resource_engine, tmp_path)
    lease = request(storage, doc)
    restarted = PostgresMemoryStorageBudget(resource_engine, tenant_id=TENANT,
        installation_id=doc.installation_id, resource_budget=_limited_storage_budget(doc.installation_id))
    assert restarted.claim_expired(now=NOW + timedelta(seconds=59)) == ()
    jobs = restarted.claim_expired(now=NOW + timedelta(seconds=60))
    assert len(jobs) == 1
    current = jobs[0]
    assert current.bank_id == lease.bank_id and current.document_id == doc.document_id
    assert current.source_message_id == doc.source_ref and current.content_sha256 == lease.content_sha256
    assert current.principal_sha256 == hashlib.sha256(doc.principal_id.encode()).hexdigest()
    real_receipt = MemoryRetentionReceipt(lease.bank_id, doc.document_id, lease.content_sha256)
    assert restarted.mark_retained(doc, real_receipt, now=NOW + timedelta(seconds=60)) == current.state
    assert not any(e.event_type == "MEMORY_STORAGE_RETAINED" for e in restarted._timeline.events(current.state.thread_id))
    with pytest.raises(CheckpointConflict):
        restarted.mark_removed(lease, now=NOW + timedelta(seconds=60))
    assert restarted.mark_removed(current, now=NOW + timedelta(seconds=60)).phase == "memory_storage_removed"
    assert restarted.mark_removed(current, now=NOW + timedelta(seconds=61)).phase == "memory_storage_removed"
    assert restarted.claim_expired(now=NOW + timedelta(days=8)) == ()


@pytest.mark.parametrize("change", ["principal", "content", "retention", "source", "evidence", "installation", "tenant"])
def test_pending_erasure_requires_exact_original_owner_source_and_deadline(resource_engine, tmp_path, change):
    storage, outbox, doc = case(resource_engine, tmp_path)
    changed = {
        "principal": lambda: replace(doc, principal_id="person:other", audience=MemoryAudience.private_person(principal_id="person:other")),
        "content": lambda: replace(doc, content="Different source"),
        "retention": lambda: replace(doc, retention_until="2026-09-10T09:00:00Z"),
        "source": lambda: replace(doc, source_ref="other-source", retention_mode="immediate"),
        "evidence": lambda: replace(doc, evidence_ref="other-evidence"),
        "installation": lambda: replace(doc, installation_id="other-installation"),
        "tenant": lambda: replace(doc, tenant_id="other-tenant"),
    }[change]()
    with pytest.raises((OperationalTimelineError, ValueError)):
        request(storage, changed)
    assert storage._timeline.load("memory-storage:" + hashlib.sha256(doc.document_id.encode()).hexdigest()).phase == "memory_storage_reserved"


def test_pending_erasure_rejects_missing_admission_or_missing_queued_source(resource_engine, tmp_path):
    storage, outbox, doc = case(resource_engine, tmp_path, queued=False)
    with pytest.raises(OperationalTimelineError, match="queued source"):
        request(storage, doc)
    missing = replace(doc, document_id="chat:exchange:missing", source_ref="missing")
    with pytest.raises(OperationalTimelineError):
        request(storage, missing)
    with pytest.raises(ValueError):
        request(storage, doc, source_message_id="other")
    with pytest.raises(ValueError):
        request(storage, doc, bank_id="../other")


def test_retained_target_uses_existing_erasure_lease_without_pending_event(resource_engine, tmp_path):
    storage, outbox, doc = case(resource_engine, tmp_path)
    digest = hashlib.sha256(doc.content.encode()).hexdigest()
    storage.mark_retained(doc, MemoryRetentionReceipt("eva2-pending-test", doc.document_id, digest), now=NOW)
    lease = request(storage, doc)
    events = storage._timeline.events(lease.state.thread_id)
    assert events[-1].event_type == "MEMORY_STORAGE_ERASURE_CLAIMED"
    assert not any(e.event_type == "MEMORY_STORAGE_PENDING_ERASURE_CLAIMED" for e in events)
    assert storage.request_erasure(principal_id=doc.principal_id, document_id=doc.document_id, now=NOW) is None


@pytest.mark.parametrize("field", ["bank_id", "document_id", "document_sha256", "content_sha256", "principal_sha256", "source_message_id", "retention_until"])
def test_pending_removal_ack_requires_exact_leased_address(resource_engine, tmp_path, field):
    storage, outbox, doc = case(resource_engine, tmp_path)
    lease = request(storage, doc)
    value = {"bank_id": "other-bank", "document_id": "other-document", "document_sha256": "0" * 64,
             "content_sha256": "0" * 64, "principal_sha256": "0" * 64,
             "source_message_id": "other-source", "retention_until": "2026-09-10T09:00:00Z"}[field]
    with pytest.raises(OperationalTimelineError):
        storage.mark_removed(replace(lease, **{field: value}), now=NOW)
    assert storage.admit_memory(doc, now=NOW).person_records_used == 1
    storage.mark_removed(lease, now=NOW)


def test_concurrent_pending_requests_produce_one_lease_and_budget_exhaustion_stays_visible(resource_engine, tmp_path):
    storage, outbox, doc = case(resource_engine, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(storage, doc), range(2)))
    assert sum(job is not None for job in results) == 1
    job = next(job for job in results if job is not None)
    assert len(storage.claim_expired(now=NOW + timedelta(seconds=60))) == 1
    assert len(storage.claim_expired(now=NOW + timedelta(seconds=120))) == 1
    assert storage.claim_expired(now=NOW + timedelta(seconds=180)) == ()
    assert storage._timeline.load(job.state.thread_id).phase == "memory_storage_remove_needs_help"
    replay = storage.admit_memory(doc, now=NOW + timedelta(seconds=180))
    assert not replay.admitted and replay.person_records_used == 1
    with pytest.raises(OperationalTimelineError):
        request(storage, doc, now=NOW + timedelta(seconds=180))


def test_pending_replay_rejects_different_bank_or_raw_source_before_ack(resource_engine, tmp_path):
    storage, outbox, doc = case(resource_engine, tmp_path)
    request(storage, doc)
    with pytest.raises(OperationalTimelineError):
        request(storage, doc, bank_id="different-bank")
    altered = replace(doc, source_ref="other", retention_mode="immediate")
    with pytest.raises(OperationalTimelineError):
        request(storage, altered)
