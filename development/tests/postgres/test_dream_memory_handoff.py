"""The real PostgreSQL handoff, with no model or external provider calls."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from threading import Event, Thread
import time

import pytest

from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_background import HindsightColdPathWorker, _RetainJob
from tests.postgres.test_runtime_resource_usage import _document, TENANT, NOW


def _case(engine, tmp_path):
    installation = "dream-" + tmp_path.name
    return PostgresMemoryOutbox(engine, tenant_id=TENANT, installation_id=installation), installation


def _dream(installation, label, **changes):
    doc = _document(label, installation_id=installation)
    return replace(doc, document_id=f"{doc.conversation_id}:exchange:{doc.source_ref}",
                   retention_mode="dream", **changes)


def _receipt(doc, selected=1):
    return MemoryRetentionReceipt("bank", doc.document_id, hashlib.sha256(doc.content.encode()).hexdigest(),
        "retained" if selected else "not_selected", selected, "operation")


def _queue(outbox, *docs):
    return tuple(outbox.enqueue(doc) for doc in docs)


def _worker(outbox, jobs, memory, **kwargs):
    worker = HindsightColdPathWorker(memory, outbox=outbox, idle_seconds=0.01,
                                    retry_delay_seconds=0, **kwargs)
    worker._dream_pending = {job.document.document_id: _RetainJob(job.document, durable_job=job) for job in jobs}
    worker._last_activity = 0
    return worker


@pytest.mark.parametrize("change", [dict(privacy_class="confidential"),
    dict(principal_id="other", audience=None), dict(occurred_at="2026-08-30T09:00:00Z")])
def test_distinct_privacy_people_days_never_share_dream_cohort(postgres_engine, tmp_path, change):
    from eva.application.memory_audience import MemoryAudience
    outbox, installation = _case(postgres_engine, tmp_path)
    if change.get("principal_id"):
        change = {**change, "audience": MemoryAudience.private_person(principal_id="other")}
    jobs = _queue(outbox, _dream(installation, "first"), _dream(installation, "second", **change))
    batch = outbox.prepare_dream_batch(jobs, now=NOW)
    assert len(batch.jobs) == 1


def test_cohort_stays_exact_after_partial_ack_restart_and_new_message(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    jobs = _queue(outbox, _dream(installation, "one"), _dream(installation, "two"))
    batch = outbox.prepare_dream_batch(jobs, now=NOW)
    first = outbox.begin(batch.jobs[0], now=NOW)
    outbox.succeed(first, _receipt(first.document), now=NOW)
    second = outbox.begin(batch.jobs[1], now=NOW)
    outbox.fail(second, RuntimeError("network"), now=NOW)
    outbox.fail(first, RuntimeError("later failure"), now=NOW)
    restarted = PostgresMemoryOutbox(postgres_engine, tenant_id=TENANT, installation_id=installation)
    extra = outbox.enqueue(_dream(installation, "three"))
    resumed = restarted.prepare_dream_batch((*outbox.pending(), extra), now=NOW)
    assert resumed.state.thread_id == batch.state.thread_id
    assert tuple(job.document.document_id for job in resumed.jobs) == tuple(job.document.document_id for job in batch.jobs)
    assert resumed.jobs[0].state.status == "completed"


def test_blocked_batch_holds_only_its_members_and_does_not_starve_next_person(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    jobs = _queue(outbox, _dream(installation, "one"), _dream(installation, "two"))
    batch = outbox.prepare_dream_batch(jobs, now=NOW)
    outbox.block_dream_batch(batch.state.thread_id, RuntimeError("uncertain"), now=NOW)
    later = outbox.enqueue(_dream(installation, "later", occurred_at="2026-08-30T09:00:00Z"))
    assert outbox.pending(limit=1) == (later,)
    resumed = outbox.prepare_dream_batch((*jobs, later), now=NOW)
    assert tuple(job.document.document_id for job in resumed.jobs) == (later.document.document_id,)
    assert outbox._timeline.load(batch.state.thread_id).status == "needs_help"


def test_erased_stale_queue_entry_cannot_start_a_new_batch(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    jobs = _queue(outbox, _dream(installation, "one"))
    outbox.erase_content(principal_sha256=hashlib.sha256(jobs[0].document.principal_id.encode()).hexdigest(),
        document_id=jobs[0].document.document_id, operation_sha256="a"*64, erased_at=NOW)
    assert outbox.prepare_dream_batch(jobs, now=NOW) is None


def test_lost_idle_capacity_does_not_consume_a_provider_attempt(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    jobs = _queue(outbox, _dream(installation, "one"))
    calls = []
    def capacity():
        calls.append(1)
        return len(calls) == 1
    worker = _worker(outbox, jobs, object(), idle_capacity=capacity)
    worker._flush_dream_if_idle()
    assert outbox._timeline.load(jobs[0].state.thread_id).retry_count == 0
    assert outbox._timeline.load(jobs[0].state.thread_id).phase == "memory_queued"


def test_day_sources_wait_during_foreground_then_get_individual_selection_receipts(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    docs = (_dream(installation, "one", content="hello"), _dream(installation, "two", content="later useful"))
    delivered = Event()
    calls = []
    class Memory:
        def dream_operation_identity(self, documents):
            return "bank", "operation"
        def retain_dream_conversations(self, documents):
            calls.append(tuple(doc.document_id for doc in documents))
            delivered.set()
            return tuple(_receipt(doc, int(doc.content != "hello")) for doc in documents)
        def retain_conversation(self, document):
            raise AssertionError("ordinary day source must not use the immediate path")
        def maintain_person_scope(self, *scope):
            pass
    worker = HindsightColdPathWorker(Memory(), outbox=outbox, idle_seconds=0.02, retry_delay_seconds=0)
    with worker.foreground():
        worker.start()
        for doc in docs:
            worker.submit(doc)
        assert not delivered.wait(0.08)
        assert len(outbox.pending()) == 2
    try:
        deadline = time.monotonic()+2
        while outbox.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not outbox.pending(), worker.last_error
    finally:
        worker.stop()
    assert calls == [tuple(doc.document_id for doc in docs)]
    assert [outbox._timeline.load(outbox._thread_id(doc.document_id)).phase for doc in docs] == ["memory_not_selected", "memory_retained"]


def test_durable_guard_serializes_two_worker_instances(postgres_engine, tmp_path):
    first, installation = _case(postgres_engine, tmp_path)
    second = PostgresMemoryOutbox(postgres_engine, tenant_id=TENANT, installation_id=installation)
    waiting, entered = Event(), Event()
    def other():
        waiting.set()
        with second.retention_guard():
            entered.set()
    with first.retention_guard():
        thread = Thread(target=other)
        thread.start()
        assert waiting.wait(1)
        assert not entered.wait(0.05)
    thread.join(1)
    assert entered.is_set()


def test_forget_never_deletes_while_bound_native_operation_is_still_running(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    jobs = _queue(outbox, _dream(installation, "one"))
    batch = outbox.prepare_dream_batch(jobs, now=NOW)
    outbox.begin(jobs[0], now=NOW)
    outbox.bind_dream_operation(batch, bank_id="bank", operation_id="operation", now=NOW)
    class Memory:
        def settle_dream_operation(self, **kwargs):
            raise RuntimeError("pending native write")
        def remove_conversation_document(self, **kwargs):
            raise AssertionError("DELETE before settlement is forbidden")
    worker = _worker(outbox, jobs, Memory())
    with pytest.raises(RuntimeError, match="pending native write"):
        worker._settle_dream_erasure(document_id=jobs[0].document.document_id, now=NOW)
    assert outbox._timeline.load(batch.state.thread_id).status == "needs_help"
    assert outbox.pending() == ()
    assert outbox.read_dream_batch(batch.state.thread_id).jobs[0].document.content == jobs[0].document.content


def test_settled_batch_reconciles_other_members_before_forget_without_redispatch(postgres_engine, tmp_path):
    outbox, installation = _case(postgres_engine, tmp_path)
    jobs = _queue(outbox, _dream(installation, "one"), _dream(installation, "two"))
    batch = outbox.prepare_dream_batch(jobs, now=NOW)
    for job in jobs:
        claimed = outbox.begin(job, now=NOW)
        outbox.fail(claimed, RuntimeError("accepted POST, poll timed out"), now=NOW)
    outbox.bind_dream_operation(batch, bank_id="bank", operation_id="operation", now=NOW)
    calls = []
    class Memory:
        def settle_dream_operation(self, **kwargs):
            calls.append("settle")
            return "completed"
        def read_dream_operation_receipts(self, documents, **kwargs):
            calls.append("read")
            return tuple(_receipt(doc, index % 2) for index, doc in enumerate(documents))
        def retain_dream_conversations(self, documents):
            raise AssertionError("forget recovery must never POST")
    worker = _worker(outbox, jobs, Memory())
    worker._settle_dream_erasure(document_id=jobs[0].document.document_id, now=NOW)
    assert calls == ["settle", "read"]
    assert all(outbox._timeline.load(job.state.thread_id).status == "completed" for job in jobs)
    assert outbox._timeline.load(batch.state.thread_id).phase == "memory_dream_completed"
    assert worker._dream_pending == {}
    from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
    overview = object.__new__(PostgresPeopleMemoryOverview)
    overview._tenant_id, overview._installation_id = TENANT, installation
    overview._timeline = outbox._timeline
    for job in jobs:
        state = outbox._timeline.load(job.state.thread_id)
        assert state.retry_count == 1
        assert overview._validate_memory_timeline(state, outbox._timeline.events(state.thread_id)) == "up_to_date"


def _public_erasure_case(engine, tmp_path):
    from types import SimpleNamespace
    from eva.adapters.postgres.runtime_resource_usage import PostgresMemoryStorageBudget
    from tests.postgres.test_memory_selection_storage import _physical_budget
    outbox, installation = _case(engine, tmp_path)
    storage = PostgresMemoryStorageBudget(engine, tenant_id=TENANT,
        installation_id=installation, resource_budget=_physical_budget(installation))
    docs = []
    for label in ("one", "two"):
        doc = _dream(installation, label, content="synthetic durable source "+label)
        admission = storage.admit_memory(doc, now=NOW)
        doc = replace(doc, retention_until=admission.retention_until)
        outbox.enqueue(doc)
        docs.append(doc)
    calls = []
    class Authority:
        def advance_erasure_generation(self, **kwargs):
            calls.append("authority")
            return SimpleNamespace(operation_sha256=kwargs["operation_sha256"],
                erasure_generation=1, authority_sha256="b"*64, replayed=False)
    class Conversation:
        def erase_message_content(self, **kwargs):
            calls.append("conversation")
            return SimpleNamespace(operation_sha256=kwargs["operation_sha256"],
                principal_sha256=kwargs["principal_sha256"],
                message_sha256=hashlib.sha256(kwargs["message_id"].encode()).hexdigest(),
                replayed=False, redacted_event_count=1)
    class Memory:
        settled = "pending"
        def document_retention_address(self, document):
            return "bank", document.document_id
        def settle_dream_operation(self, **kwargs):
            calls.append("settle")
            if self.settled == "pending":
                raise RuntimeError("native operation still pending")
            return self.settled
        def read_dream_operation_receipts(self, documents, **kwargs):
            calls.append("read_receipts")
            return tuple(_receipt(doc, index % 2) for index, doc in enumerate(documents))
        def remove_conversation_document(self, **kwargs):
            calls.append("delete")
        def retain_dream_conversations(self, documents):
            raise AssertionError("forget must not dispatch another extraction")
    memory = Memory()
    def worker():
        return HindsightColdPathWorker(memory, outbox=outbox, storage_budget=storage,
            conversation_erasure=Conversation(), restore_authority_erasure=Authority(),
            idle_seconds=0.01, retry_delay_seconds=0)
    return outbox, storage, tuple(docs), memory, calls, worker


@pytest.mark.parametrize("supply_document", [True, False])
def test_public_forget_erases_queued_day_context_without_extraction(postgres_engine, tmp_path, supply_document):
    outbox, storage, docs, memory, calls, worker = _public_erasure_case(postgres_engine, tmp_path)
    doc = docs[0]
    assert worker().erase_document(principal_id=doc.principal_id, document_id=doc.document_id,
        source_message_id=doc.source_ref, document=doc if supply_document else None, now=NOW)
    assert calls == ["authority", "delete", "conversation"]
    state = storage._timeline.load("memory-storage:"+hashlib.sha256(doc.document_id.encode()).hexdigest())
    assert state.phase == "memory_storage_removed"
    events = outbox._timeline.events(outbox._thread_id(doc.document_id))
    assert "content" not in events[0].payload["data"]
    assert events[0].payload["data"]["content_erasure"]["status"] == "erased"
    assert not any(event.event_type == "MEMORY_STORAGE_RETAINED" for event in storage._timeline.events(state.thread_id))


def test_public_forget_survives_pending_native_operation_and_process_restart(postgres_engine, tmp_path):
    outbox, storage, docs, memory, calls, worker = _public_erasure_case(postgres_engine, tmp_path)
    batch = outbox.prepare_dream_batch(outbox.pending(), now=NOW)
    for queued in batch.jobs:
        claimed = outbox.begin(queued, now=NOW)
        outbox.fail(claimed, RuntimeError("POST accepted but polling timed out"), now=NOW)
    outbox.bind_dream_operation(batch, bank_id="bank", operation_id="operation", now=NOW)
    target = batch.jobs[0].document
    with pytest.raises(RuntimeError, match="still pending"):
        worker().erase_document(principal_id=target.principal_id, document_id=target.document_id,
            source_message_id=target.source_ref, document=target, now=NOW)
    assert "delete" not in calls and "conversation" not in calls
    checkpoint = storage._timeline.load("memory-storage:"+hashlib.sha256(target.document_id.encode()).hexdigest())
    assert checkpoint.phase == "memory_storage_removing"
    assert "content" in outbox._timeline.events(outbox._thread_id(target.document_id))[0].payload["data"]
    memory.settled = "completed"
    recovery_time = NOW+timedelta(hours=1)
    jobs = storage.claim_expired(now=recovery_time)
    target_job = next(job for job in jobs if job.document_id == target.document_id)
    worker()._remove_job(target_job, now=recovery_time)
    assert calls.index("read_receipts") < calls.index("delete") < calls.index("conversation")
    assert outbox._timeline.load(batch.state.thread_id).phase == "memory_dream_completed"
    assert storage._timeline.load(checkpoint.thread_id).phase == "memory_storage_removed"
    other = next(doc for doc in docs if doc.document_id != target.document_id)
    assert outbox._timeline.load(outbox._thread_id(other.document_id)).phase == "memory_retained"
    assert storage._timeline.load("memory-storage:"+hashlib.sha256(other.document_id.encode()).hexdigest()).phase == "memory_storage_retained"
    assert "content" not in outbox._timeline.events(outbox._thread_id(target.document_id))[0].payload["data"]


def test_public_forget_rejects_cross_source_binding_before_acquiring_lease(postgres_engine, tmp_path):
    outbox, storage, docs, memory, calls, worker = _public_erasure_case(postgres_engine, tmp_path)
    with pytest.raises(RuntimeError, match="binding differs"):
        worker().erase_document(principal_id=docs[0].principal_id, document_id=docs[0].document_id,
            source_message_id=docs[1].source_ref, document=docs[0], now=NOW)
    assert calls == []
    assert storage._timeline.load("memory-storage:"+hashlib.sha256(docs[0].document_id.encode()).hexdigest()).phase == "memory_storage_reserved"
