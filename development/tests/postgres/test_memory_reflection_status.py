"""Real reflection claims remain retention evidence, never learning completion."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import pytest

from eva.adapters.postgres.memory_outbox import DurableMemoryRetentionJob
from eva.adapters.postgres.operational_timeline import OperationalTimelineError
from eva.application.memory import MemoryRetentionReceipt
from eva.application.people_memory_overview import PeopleMemoryOverviewError
from tests.postgres.test_people_memory_overview import NOW, people_memory_engine
from tests.postgres.test_memory_erasure_status import _setup, _erase


def _retained_and_claimed(engine, name, retry=False):
    document, outbox, overview = _setup(engine, "reflection-" + name)
    job = outbox.begin(outbox.enqueue(document), now=NOW + timedelta(seconds=1))
    if retry:
        failed = outbox.fail(job, RuntimeError("old transient error"), now=NOW + timedelta(seconds=2))
        job = outbox.begin(DurableMemoryRetentionJob(document, failed), now=NOW + timedelta(seconds=3))
    receipt = MemoryRetentionReceipt("reflection-bank", document.document_id,
        hashlib.sha256(document.content.encode()).hexdigest())
    outbox.succeed(job, receipt, now=NOW + timedelta(seconds=4))
    assert outbox.claim_reflection_scope(document.tenant_id, document.installation_id,
        document.principal_id, now=NOW + timedelta(seconds=5))
    return document, outbox, overview, job.state.thread_id


@pytest.mark.parametrize("retry", [False, True])
def test_real_reflection_dispatch_claim_keeps_successful_retention_status(people_memory_engine, retry):
    document, outbox, overview, thread = _retained_and_claimed(people_memory_engine, str(retry), retry)
    events = outbox._timeline.events(thread)
    assert events[-1].event_type == "MEMORY_REFLECTION_CLAIMED"
    assert events[-1].payload["data"]["meaning"] == "dispatch claimed; completion and outcome recorded separately"
    assert events[-1].payload["checkpoint"]["retry_count"] == (2 if retry else 1)
    assert events[-1].payload["checkpoint"]["last_error_fingerprint"] is None
    snapshot = overview.snapshot(now=NOW + timedelta(seconds=6), hindsight_state="ready")
    assert snapshot.memory_flow == "up_to_date" and snapshot.memory_diagnostic is None
    assert document.content not in str(snapshot.to_payload())
    assert not outbox.claim_reflection_scope(document.tenant_id, document.installation_id,
        document.principal_id, now=NOW + timedelta(seconds=6))
    assert outbox._timeline.events(thread) == events
    assert all(event.event_type != "REFLECTION_COMPLETED" for event in events)


def test_real_reflection_claim_source_can_be_forgotten_without_false_status_failure(people_memory_engine):
    document, outbox, overview, thread = _retained_and_claimed(people_memory_engine, "forgotten")
    before = outbox._timeline.events(thread)
    _erase(document, outbox, NOW + timedelta(seconds=6))
    after = outbox._timeline.events(thread)
    assert len(after) == len(before)  # It was already terminal: no erasure transition appended.
    assert "content" not in after[0].payload["data"]
    snapshot = overview.snapshot(now=NOW + timedelta(seconds=7), hindsight_state="ready")
    assert snapshot.memory_flow == "no_signals" and snapshot.memory_diagnostic is None


@pytest.mark.parametrize("tamper", ["principal_sha256", "document_sha256", "meaning", "extra", "phase", "retry_count", "last_error_fingerprint"])
def test_reflection_claim_requires_exact_scope_and_producer_state(people_memory_engine, tamper):
    _document, outbox, overview, thread = _retained_and_claimed(people_memory_engine, "invalid-" + tamper)
    events = list(deepcopy(outbox._timeline.events(thread)))
    if tamper in {"principal_sha256", "document_sha256", "meaning"}:
        events[-1].payload["data"][tamper] = "unrelated-canary"
    elif tamper == "extra":
        events[-1].payload["data"]["completion"] = True
    else:
        events[-1].payload["checkpoint"][tamper] = 3 if tamper == "retry_count" else "wrong-state"
    with pytest.raises(PeopleMemoryOverviewError):
        overview._validate_memory_timeline(outbox._timeline.load(thread), tuple(events))


def test_consecutive_attempts_stay_invalid_but_get_an_honest_protocol_explanation(people_memory_engine):
    document, outbox, overview = _setup(people_memory_engine, "protocol-attempts")
    first = outbox.begin(outbox.enqueue(document), now=NOW + timedelta(seconds=1))
    outbox.begin(first, now=NOW + timedelta(seconds=2))
    snapshot = overview.snapshot(now=NOW + timedelta(seconds=3), hindsight_state="ready")
    assert snapshot.memory_flow == "unavailable"
    assert snapshot.memory_diagnostic == "historical_protocol_attention"
    assert "ongeldige volgorde" in snapshot.to_payload()["memory"]["explanation"]
    assert snapshot.hindsight == "ready"


@pytest.mark.parametrize("message", ["append-only event payload hash differs from its payload", "checkpoint projection differs from append-only timeline"])
def test_hash_and_checkpoint_faults_never_get_the_historical_protocol_diagnostic(people_memory_engine, monkeypatch, message):
    _document, _outbox, overview = _setup(people_memory_engine, "strict-" + message[:10])
    def fault(**kw):raise OperationalTimelineError(message)
    monkeypatch.setattr(overview._timeline, "iter_threads", fault)
    snapshot = overview.snapshot(now=NOW, hindsight_state="ready")
    assert snapshot.memory_flow == "unavailable" and snapshot.memory_diagnostic is None
