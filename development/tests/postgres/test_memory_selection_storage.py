"""Selection affects semantic reflection, never physical source accounting."""
from dataclasses import replace
from datetime import timedelta
import hashlib

import pytest

from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.runtime_resource_usage import PostgresMemoryStorageBudget
from eva.adapters.postgres.operational_timeline import OperationalTimelineError
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_runtime_resource_usage import (
    _document, _limited_storage_budget, TENANT, NOW,
)


def _physical_budget(installation):
    budget = _limited_storage_budget(installation)
    payload = {**budget.budget_payload(), "retention_days": 365}
    return replace(budget, retention_days=365, source_budget_sha256=canonical_sha256(payload))


def _case(postgres_engine, tmp_path):
    installation = "selection-" + tmp_path.name
    storage = PostgresMemoryStorageBudget(postgres_engine, tenant_id=TENANT,
        installation_id=installation, resource_budget=_physical_budget(installation))
    outbox = PostgresMemoryOutbox(postgres_engine, tenant_id=TENANT, installation_id=installation)
    document = _document("telegram:synthetic:exchange:selection", content="Synthetic direct source.",
        installation_id=installation)
    admitted = storage.admit_memory(document, now=NOW)
    document = replace(document, retention_until=admitted.retention_until)
    queued = outbox.enqueue(document)
    job = outbox.begin(queued, now=NOW)
    receipt = MemoryRetentionReceipt("synthetic-bank", document.document_id,
        hashlib.sha256(document.content.encode()).hexdigest(), "not_selected", 0, "operation:zero")
    return storage, outbox, document, queued, job, receipt


def test_zero_selection_keeps_physical_capacity_and_deadline_until_delete_ack(postgres_engine, tmp_path):
    storage, outbox, doc, queued, job, receipt = _case(postgres_engine, tmp_path)
    original = outbox._timeline.events(job.state.thread_id)[0]
    state = storage.mark_retained(doc, receipt, now=NOW)
    done = outbox.succeed(job, receipt, now=NOW)
    # This event proves document retention, not selection of semantic facts.
    assert state.phase == "memory_storage_retained" and state.status == "active"
    assert state.deadline == NOW + timedelta(days=365)
    assert done.phase == "memory_not_selected" and done.status == "completed"
    events = outbox._timeline.events(job.state.thread_id)
    assert events[0] == original and events[0].payload["data"]["content"] == doc.content
    assert events[-1].event_type == "MEMORY_RETAIN_NOT_SELECTED"
    assert events[-1].payload["data"]["selected_fact_count"] == 0
    assert events[-1].payload["data"]["operation_id"] == receipt.operation_id
    assert not any(e.event_type == "MEMORY_RETAIN_SUCCEEDED" for e in events)
    assert outbox.pending() == () and outbox.pending_reflection_scopes(now=NOW) == ()
    assert not outbox.claim_reflection_scope(doc.tenant_id, doc.installation_id, doc.principal_id, now=NOW)
    assert outbox.begin(queued, now=NOW) is None
    assert outbox.succeed(job, receipt, now=NOW) == done
    assert storage.mark_retained(doc, receipt, now=NOW) == state
    assert len(outbox._timeline.events(job.state.thread_id)) == len(events)
    assert outbox.fail(job, RuntimeError("Stale failed attempt"), now=NOW) == done
    replay = storage.admit_memory(doc, now=NOW)
    assert replay.replayed and replay.admitted and replay.person_records_used == 1
    assert replay.person_bytes_used == len(doc.content.encode())
    assert replay.retention_until == doc.retention_until
    assert storage.claim_expired(now=NOW + timedelta(days=365, seconds=-1)) == ()
    expired = storage.claim_expired(now=NOW + timedelta(days=365))
    assert len(expired) == 1 and expired[0].document_id == doc.document_id
    # Claiming deletion alone must not report physical space as free.
    assert storage.admit_memory(doc, now=NOW + timedelta(days=365)).person_records_used == 1
    storage.mark_removed(expired[0], now=NOW + timedelta(days=365))
    assert storage.admit_memory(doc, now=NOW + timedelta(days=365)).person_records_used == 0


@pytest.mark.parametrize("field", ["bank_id", "operation_id", "document_id", "content_sha256", "disposition"])
def test_zero_selection_outbox_replay_rejects_changed_receipt(postgres_engine, tmp_path, field):
    storage, outbox, doc, _, job, receipt = _case(postgres_engine, tmp_path)
    storage.mark_retained(doc, receipt, now=NOW)
    outbox.succeed(job, receipt, now=NOW)
    value = {"bank_id": "foreign-bank", "operation_id": "operation:other",
        "document_id": "different-source", "content_sha256": "f" * 64,
        "disposition": "retained"}[field]
    with pytest.raises(RuntimeError):
        outbox.succeed(job, replace(receipt, **{field: value}), now=NOW)


@pytest.mark.parametrize("field", ["principal", "content", "installation", "project"])
def test_zero_selection_requires_exact_original_queued_source(postgres_engine, tmp_path, field):
    _, outbox, doc, _, job, receipt = _case(postgres_engine, tmp_path)
    changed = {"principal": replace(doc, principal_id="person:foreign",
            audience=MemoryAudience.private_person(principal_id="person:foreign")),
        "content": replace(doc, content="Different immutable content"),
        "installation": replace(doc, installation_id="different-installation"),
        "project": replace(doc, project_id="project:foreign")}[field]
    changed_receipt = replace(receipt, content_sha256=hashlib.sha256(changed.content.encode()).hexdigest())
    with pytest.raises(RuntimeError):
        outbox.succeed(replace(job, document=changed), changed_receipt, now=NOW)
    assert outbox._timeline.load(job.state.thread_id).phase == "memory_retaining"


def test_zero_selection_cannot_reclassify_existing_retained_or_unknown_outbox(postgres_engine, tmp_path):
    storage, outbox, doc, _, job, zero = _case(postgres_engine, tmp_path)
    legacy = MemoryRetentionReceipt(zero.bank_id, zero.document_id, zero.content_sha256)
    state = storage.mark_retained(doc, legacy, now=NOW)
    done = outbox.succeed(job, legacy, now=NOW)
    assert state.phase == "memory_storage_retained" and state.deadline is not None
    assert done.phase == "memory_retained" and legacy.selected_fact_count is None
    with pytest.raises(RuntimeError):
        outbox.succeed(job, zero, now=NOW)
    assert outbox.pending_reflection_scopes(now=NOW) == ((doc.tenant_id, doc.installation_id, doc.principal_id),)


def test_retry_requires_current_attempt_and_not_selected_survives_restart(postgres_engine, tmp_path):
    storage, outbox, doc, _, old_job, receipt = _case(postgres_engine, tmp_path)
    failed = outbox.fail(old_job, RuntimeError("Synthetic unavailable"), now=NOW)
    assert failed.phase == "memory_retry"
    restarted = PostgresMemoryOutbox(postgres_engine, tenant_id=doc.tenant_id, installation_id=doc.installation_id)
    pending = restarted.pending()
    assert len(pending) == 1
    current = restarted.begin(pending[0], now=NOW)
    with pytest.raises(RuntimeError, match="no longer current"):
        restarted.succeed(old_job, receipt, now=NOW)
    storage.mark_retained(doc, receipt, now=NOW)
    restarted.succeed(current, receipt, now=NOW)
    assert restarted.pending() == () and restarted.pending_reflection_scopes(now=NOW) == ()


def test_explicit_forget_uses_unchanged_physical_erasure_and_source_redaction(postgres_engine, tmp_path):
    from eva.application.memory_background import SemanticMemoryColdPathWorker
    storage, outbox, doc, _, attempted, receipt = _case(postgres_engine, tmp_path)
    storage.mark_retained(doc, receipt, now=NOW)
    outbox.succeed(attempted, receipt, now=NOW)
    with pytest.raises(OperationalTimelineError, match="scope differs"):
        storage.request_erasure(principal_id="person:foreign", document_id=doc.document_id,
            source_message_id=doc.source_ref, now=NOW)
    erase = dict(principal_id=doc.principal_id, document_id=doc.document_id,
        source_message_id=doc.source_ref, now=NOW)
    lease = storage.request_erasure(**erase)
    assert lease.retention_until == doc.retention_until
    assert lease.state.phase == "memory_storage_removing"
    assert storage.request_erasure(**erase) is None
    assert storage.admit_memory(doc, now=NOW).person_records_used == 1
    restarted = PostgresMemoryStorageBudget(postgres_engine, tenant_id=doc.tenant_id,
        installation_id=doc.installation_id, resource_budget=_physical_budget(doc.installation_id))
    recovery_time = NOW + timedelta(seconds=60)
    jobs = restarted.claim_expired(now=recovery_time)
    assert len(jobs) == 1 and jobs[0].source_message_id == doc.source_ref
    assert jobs[0].retention_until == doc.retention_until and jobs[0].bank_id == receipt.bank_id
    assert jobs[0].content_sha256 == receipt.content_sha256
    principal_sha = hashlib.sha256(doc.principal_id.encode()).hexdigest()
    operation_sha = SemanticMemoryColdPathWorker._erasure_operation_sha256(
        principal_sha256=principal_sha, document_id=doc.document_id, source_message_id=doc.source_ref)
    erasure = dict(principal_sha256=principal_sha, document_id=doc.document_id,
        operation_sha256=operation_sha, erased_at=recovery_time)
    first = outbox.erase_content(**erasure)
    again = outbox.erase_content(**erasure)
    assert first.redacted_event_count == 1 and not first.replayed
    assert again.redacted_event_count == 0 and again.replayed
    source = outbox._timeline.events(attempted.state.thread_id)[0].payload["data"]
    assert "content" not in source and source["content_erasure"]["operation_sha256"] == operation_sha
    removed = restarted.mark_removed(jobs[0], now=recovery_time)
    assert removed.phase == "memory_storage_removed"
    assert restarted.mark_removed(jobs[0], now=recovery_time) == removed
    assert restarted.request_erasure(**{**erase, "now": recovery_time}) is None
    assert restarted.claim_expired(now=recovery_time + timedelta(days=1)) == ()
    assert restarted.admit_memory(doc, now=recovery_time).person_records_used == 0
    assert outbox.pending() == () and outbox.pending_reflection_scopes(now=recovery_time) == ()
    with pytest.raises(RuntimeError, match="source differs"):
        outbox.succeed(attempted, receipt, now=recovery_time)


@pytest.mark.parametrize("selected_count", [None, 0, 2])
def test_retained_receipt_metadata_preserves_real_owner_memory_reader_contract(postgres_engine, tmp_path, selected_count):
    from tests.postgres.test_delegated_work_store import _seed_authority, _id
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    label = "selection-reader-" + tmp_path.name
    scope, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    installation = "reader-" + tmp_path.name
    document = replace(_document("dashboard:synthetic:exchange:reader", installation_id=installation),
        principal_id=str(owner), audience=MemoryAudience.private_person(principal_id=str(owner)),
        conversation_id="dashboard:" + str(binding), retention_until="2027-08-29T09:00:00Z")
    outbox = PostgresMemoryOutbox(postgres_engine, tenant_id=TENANT, installation_id=installation)
    job = outbox.begin(outbox.enqueue(document), now=NOW)
    receipt = MemoryRetentionReceipt("synthetic-bank", document.document_id,
        hashlib.sha256(document.content.encode()).hexdigest(), selected_fact_count=selected_count,
        operation_id="operation:retained-metadata")
    outbox.succeed(job, receipt, now=NOW)
    data = outbox._timeline.events(job.state.thread_id)[-1].payload["data"]
    assert set(data) == {"bank_id", "document_id", "content_sha256"}
    reader = PostgresOwnerMemorySources(postgres_engine, tenant_id=TENANT, installation_id=installation,
        owner=owner, binding=binding, authority_scope=scope)
    page = reader.page(now=NOW)
    assert len(page.sources) == 1 and page.sources[0].retention_state == "retained"
    resolved, state = reader.document(page.sources[0].source_id, now=NOW)
    assert resolved == document and state == "retained"
