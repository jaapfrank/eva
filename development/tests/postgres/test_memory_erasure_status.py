"""Status follows real erasure lifecycle without hiding strict history faults."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib

import pytest

from eva.adapters.postgres.memory_outbox import DurableMemoryRetentionJob, PostgresMemoryOutbox
from eva.adapters.postgres.operational_timeline import OperationalTimelineError
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.application.memory import MemoryRetentionReceipt
from eva.application.people_memory_overview import PeopleMemoryOverview, PeopleMemoryOverviewError
from tests.postgres.test_people_memory_overview import NOW, _document, people_memory_engine, selected_private_case


def _setup(engine, label):
    tenant, installation = "erasure-status-" + label, "erasure-status-home"
    document = replace(_document(label), tenant_id=tenant, installation_id=installation)
    outbox = PostgresMemoryOutbox(engine, tenant_id=tenant, installation_id=installation)
    overview = PostgresPeopleMemoryOverview(engine, tenant_id=tenant, installation_id=installation)
    return document, outbox, overview


def _erase(document, outbox, when):
    return outbox.erase_content(principal_sha256=hashlib.sha256(document.principal_id.encode()).hexdigest(),
        document_id=document.document_id, operation_sha256="ab" * 32, erased_at=when)


@pytest.mark.parametrize("stage", ["queued", "attempted", "retry", "needs_help", "retained", "not_selected"])
def test_real_erasure_is_terminal_without_reconstructing_deleted_content(people_memory_engine, stage):
    document, outbox, overview = _setup(people_memory_engine, stage)
    job = outbox.enqueue(document)
    if stage != "queued":
        job = outbox.begin(job, now=NOW + timedelta(seconds=1))
    if stage in {"retry", "needs_help"}:
        for attempt in range(1, 4 if stage == "needs_help" else 2):
            state = outbox.fail(job, RuntimeError("synthetic historical error"), now=NOW + timedelta(seconds=attempt * 2))
            if attempt < 3 and stage == "needs_help":
                job = outbox.begin(DurableMemoryRetentionJob(document, state), now=NOW + timedelta(seconds=attempt * 2 + 1))
    if stage in {"retained", "not_selected"}:
        receipt = MemoryRetentionReceipt("bank-canary", document.document_id,
            hashlib.sha256(document.content.encode()).hexdigest(),
            disposition=stage if stage == "not_selected" else "retained",
            selected_fact_count=0 if stage == "not_selected" else None,
            operation_id="native-completed-canary" if stage == "not_selected" else None)
        outbox.succeed(job, receipt, now=NOW + timedelta(seconds=2))
    before = overview._timeline.events(job.state.thread_id)
    _erase(document, outbox, NOW + timedelta(seconds=10))
    after = overview._timeline.events(job.state.thread_id)
    assert "content" not in after[0].payload["data"]
    assert after[0].payload["data"]["content_erasure"]["status"] == "erased"
    assert len(after) == len(before) + (0 if stage in {"retained", "not_selected"} else 1)
    result = overview.snapshot(now=NOW + timedelta(seconds=11), hindsight_state="ready")
    assert result.memory_flow == "no_signals"
    assert result.memory_diagnostic is None
    assert document.content not in str(result.to_payload())
    # Erasure is idempotent and cannot create a new retention attempt.
    assert _erase(document, outbox, NOW + timedelta(seconds=10)).replayed is True
    assert overview._timeline.events(job.state.thread_id) == after


@pytest.mark.parametrize("tamper", ["principal", "message", "operation", "time", "extra", "content", "terminal_document", "terminal_phase"])
def test_erasure_marker_does_not_excuse_invalid_evidence(people_memory_engine, tamper):
    document, outbox, overview = _setup(people_memory_engine, "tamper-" + tamper)
    job = outbox.enqueue(document)
    _erase(document, outbox, NOW + timedelta(seconds=10))
    events = list(deepcopy(overview._timeline.events(job.state.thread_id)))
    data = events[0].payload["data"]
    if tamper in {"principal", "message"}:
        data["content_erasure"][tamper + "_sha256"] = "cd" * 32
    elif tamper == "operation":
        data["content_erasure"]["operation_sha256"] = "cd" * 32
    elif tamper == "time":
        data["content_erasure"]["erased_at"] = "2026-08-26T12:00:09Z"
    elif tamper == "extra":
        data["content_erasure"]["unrecognized"] = True
    elif tamper == "content":
        data["content"] = "Do not restore erased text"
    elif tamper == "terminal_document":
        events[-1].payload["data"]["document_sha256"] = "cd" * 32
    else:
        events[-1].payload["checkpoint"]["phase"] = "memory_retained"
    state = overview._timeline.load(job.state.thread_id)
    with pytest.raises((PeopleMemoryOverviewError, ValueError, RuntimeError)):
        overview._validate_memory_timeline(state, tuple(events))


def test_legitimate_erasure_does_not_hide_another_broken_history(people_memory_engine):
    document, outbox, overview = _setup(people_memory_engine, "strict-other")
    job = outbox.enqueue(document)
    _erase(document, outbox, NOW + timedelta(seconds=10))
    overview._timeline.create_thread(thread_id="memory-retain:zz-malformed", checkpoint_id="malformed",
        occurred_at=NOW, data={"kind": "invalid"}, event_type="MEMORY_RETAIN_QUEUED", phase="memory_queued", status="active")
    result = overview.snapshot(now=NOW + timedelta(seconds=11), hindsight_state="ready")
    assert result.memory_flow == "unavailable"
    assert result.memory_diagnostic is None


def test_real_clock_regression_gets_specific_diagnostic_without_becoming_green(people_memory_engine):
    document, outbox, overview = _setup(people_memory_engine, "backwards")
    job = outbox.enqueue(document)
    outbox.begin(job, now=NOW - timedelta(seconds=1))
    result = overview.snapshot(now=NOW, hindsight_state="ready")
    assert result.memory_flow == "unavailable"
    assert result.hindsight == "ready"
    assert result.memory_diagnostic == "historical_timeline_attention"
    assert "tijdvolgordefout" in result.to_payload()["memory"]["explanation"]
    # Underlying integrity validation is unchanged; diagnosis is not permission to continue.
    with pytest.raises(OperationalTimelineError, match="time moved backwards"):
        list(overview._timeline.iter_threads(thread_prefix="memory-retain:"))
    assert document.content not in str(result.to_payload())


@pytest.mark.parametrize("diagnostic,state", [("arbitrary content", "unavailable"), ("historical_timeline_attention", "up_to_date")])
def test_memory_diagnostic_is_closed_and_cannot_describe_green_state(diagnostic, state):
    with pytest.raises(ValueError, match="diagnostic"):
        PeopleMemoryOverview("no_extra_account", "private_only", state, "ready", memory_diagnostic=diagnostic)


def test_compacted_then_erased_source_keeps_no_text_and_valid_status(selected_private_case):
    from tests.postgres.test_compacted_memory_sources import _ready
    case, _ref, _query = _ready(selected_private_case)
    overview = PostgresPeopleMemoryOverview(selected_private_case.engine,
        tenant_id=case.document.tenant_id, installation_id=case.document.installation_id)
    assert overview.snapshot(now=NOW, hindsight_state="ready").memory_flow == "up_to_date"
    _erase(case.document, case.outbox, NOW)
    snapshot = overview.snapshot(now=NOW, hindsight_state="ready")
    assert snapshot.memory_flow == "no_signals"
    assert snapshot.memory_diagnostic is None
    assert case.document.content not in str(snapshot.to_payload())


def _legacy_closed(people_memory_engine, label):
    document, outbox, overview = _setup(people_memory_engine, label)
    job = outbox.enqueue(document)
    begun = outbox.begin(job, now=NOW + timedelta(seconds=1))
    state = outbox.fail(begun, RuntimeError("old failure"), now=NOW + timedelta(seconds=2))
    begun = outbox.begin(DurableMemoryRetentionJob(document, state), now=NOW + timedelta(seconds=3))
    timeline = outbox._timeline
    state = timeline.transition(thread_id=state.thread_id, expected_revision=begun.state.revision,
        checkpoint_id=state.thread_id + ":legacy-attempt", operation_id=state.operation_id,
        phase="memory_retaining", status="waiting_for_proof", retry_count=3,
        last_error_fingerprint=state.last_error_fingerprint, occurred_at=NOW + timedelta(seconds=4),
        event_type="MEMORY_RETAIN_ATTEMPTED", data={"attempt": 3, "document_id": document.document_id})
    digest = hashlib.sha256(document.content.encode()).hexdigest()
    state = timeline.transition(thread_id=state.thread_id, expected_revision=state.revision,
        checkpoint_id=state.thread_id + ":legacy-success", operation_id=state.operation_id,
        phase="memory_retained", status="completed", retry_count=3,
        occurred_at=NOW + timedelta(seconds=5), event_type="MEMORY_RETAIN_SUCCEEDED",
        data={"bank_id": "legacy-bank", "content_sha256": digest, "document_id": document.document_id})
    error = "cd" * 32
    state = timeline.transition(thread_id=state.thread_id, expected_revision=state.revision,
        checkpoint_id=state.thread_id + ":legacy-needs-help", operation_id=state.operation_id,
        phase="memory_needs_help", status="needs_help", retry_count=3, last_error_fingerprint=error,
        occurred_at=NOW + timedelta(seconds=6), event_type="MEMORY_RETAIN_NEEDS_HELP",
        data={"document_id": document.document_id, "error_fingerprint": error, "retry_count": 3})
    job = DurableMemoryRetentionJob(document, state)
    receipt = MemoryRetentionReceipt("legacy-bank", document.document_id, digest,
        disposition="not_selected", selected_fact_count=0, operation_id="legacy-original-native-operation")
    return outbox, overview, job, receipt


def test_bound_closed_zero_result_does_not_legitimize_invalid_legacy_prefix(people_memory_engine):
    outbox, overview, job, receipt = _legacy_closed(people_memory_engine, "legacy-bound")
    before = outbox._timeline.events(job.state.thread_id)
    result = overview.snapshot(now=NOW + timedelta(seconds=7), hindsight_state="ready")
    assert result.memory_flow == "unavailable" and result.memory_diagnostic == "historical_protocol_attention"
    outbox.reconcile_closed_retention(job, receipt, proof_sha256="ab" * 32, now=NOW + timedelta(seconds=7))
    after = outbox._timeline.events(job.state.thread_id)
    assert after[:-1] == before
    result = overview.snapshot(now=NOW + timedelta(seconds=8), hindsight_state="ready")
    assert result.memory_flow == "unavailable"
    assert result.memory_diagnostic == "historical_reconciled_attention"
    assert "oorspronkelijke proceshistorie" in result.to_payload()["memory"]["explanation"]
    # Even with native outcome proof, the old malformed prefix remains rejected.
    with pytest.raises(PeopleMemoryOverviewError, match="out of order"):
        overview._validate_memory_timeline(outbox._timeline.load(job.state.thread_id), after)


@pytest.mark.parametrize("key", ["source_history_sha256", "content_sha256", "document_id", "selected_fact_count", "proof_sha256"])
def test_unbound_legacy_reconciliation_gets_no_reconciled_diagnostic(people_memory_engine, key, monkeypatch):
    outbox, overview, job, receipt = _legacy_closed(people_memory_engine, "legacy-invalid-" + key)
    outbox.reconcile_closed_retention(job, receipt, proof_sha256="ab" * 32, now=NOW + timedelta(seconds=7))
    events = list(deepcopy(outbox._timeline.events(job.state.thread_id)))
    data = events[-1].payload["data"]
    data[key] = 1 if key == "selected_fact_count" else "unbound-canary"
    state = outbox._timeline.load(job.state.thread_id)
    assert overview._bound_reconciled_history(state, tuple(events)) is False
    monkeypatch.setattr(overview._timeline, "iter_threads", lambda **kw: iter(((state, tuple(events)),)))
    result = overview.snapshot(now=NOW + timedelta(seconds=8), hindsight_state="ready")
    assert result.memory_flow == "unavailable" and result.memory_diagnostic == "historical_protocol_attention"


def test_valid_closed_reconciliation_can_later_be_forgotten(people_memory_engine):
    from tests.postgres.test_memory_closed_reconciliation import failed, receipt
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id="erase-reconciled", installation_id="erase-home")
    job = failed(outbox, "erase-valid-reconciled")
    outbox.reconcile_closed_retention(job, receipt(job), proof_sha256="ab" * 32, now=NOW + timedelta(seconds=7))
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id="erase-reconciled", installation_id="erase-home")
    assert overview.snapshot(now=NOW + timedelta(seconds=8), hindsight_state="ready").memory_flow == "up_to_date"
    _erase(job.document, outbox, NOW + timedelta(seconds=9))
    result = overview.snapshot(now=NOW + timedelta(seconds=10), hindsight_state="ready")
    assert result.memory_flow == "no_signals" and result.memory_diagnostic is None
    assert job.document.content not in str(result.to_payload())
