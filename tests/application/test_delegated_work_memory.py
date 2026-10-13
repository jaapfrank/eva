"""Work outcomes keep their source, uncertainty and person boundary."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationTurn
from eva.application.delegated_work_memory import outcome_document
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.contracts.canonical_json import canonical_sha256


def sample():
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    source = ConversationTurn.private("tenant:test", "installation:test", "owner:test",
        "chat:first", "message:first", "Vergelijk de notities en bewaar het advies.",
        now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    name = "bootstrap/delegated_digital_runner.py"
    root = Path(__file__).resolve().parents[2] / "src/eva"
    profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((name, hashlib.sha256((root / name).read_bytes()).hexdigest()),))
    ingress = canonical_sha256(PostgresConversationJournal._ingress_data(source))
    spec = profile.build_spec(source={k: getattr(source, k) for k in (
        "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")} |
        {"audience": {"kind": "private_person", "principal_id": source.principal_id}},
        source_ingress_sha256=ingress, work_id="work:test", episode_id="episode:test",
        generation=1, parent_task_id="parent:test", owner_principal_id="owner:test",
        principal_binding_id="binding:test", task=source.text, created_at=now,
        deadline=now + timedelta(minutes=5))
    output = "Het advies is opgeslagen in /proof-state/advies.txt."
    result = SimpleNamespace(spec_sha256=spec.sha256, work_id=spec.work_id, call_id="call:test",
        output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest())
    work = SimpleNamespace(spec=spec, state="succeeded", completed_at=now + timedelta(seconds=30),
        result=result, call_id=result.call_id, error_code=None)
    return source, ingress, work


def test_result_is_a_person_scoped_report_not_an_independent_fact():
    source, ingress, work = sample()
    document = outcome_document(work, source, ingress_sha256=ingress)
    assert work.result.output in document.content and source.text in document.content
    assert document.principal_id == source.principal_id
    assert document.audience == source.audience and document.project_id == source.project_id
    assert document.trust == "model_inference_unconfirmed"
    assert document.privacy_class == "confidential"
    assert work.result.output_sha256 in document.content
    assert "geen onafhankelijk bevestigd" in document.content
    work.state = "verified"
    assert outcome_document(work, source, ingress_sha256=ingress) == document


@pytest.mark.parametrize("state", ["failed", "stopped", "needs_help"])
def test_failure_and_stop_are_never_retained_as_success(state):
    source, ingress, work = sample()
    work.state, work.result, work.error_code = state, None, "incomplete_work"
    doc = outcome_document(work, source, ingress_sha256=ingress)
    assert "Geen bevestigd eindresultaat" in doc.content
    assert "niet als succes" in doc.content.lower()
    assert "incomplete_work" in doc.content


@pytest.mark.parametrize("change", ["person", "message", "ingress", "result", "state"])
def test_incorrect_sources_and_nonterminal_work_are_rejected(change):
    source, ingress, work = sample()
    if change == "person":
        source = ConversationTurn.private(source.tenant_id, source.installation_id,
            "other:person", source.conversation_id, source.message_id, source.text, source.occurred_at)
    elif change == "message":
        source = replace(source, message_id="message:other")
    elif change == "ingress":
        ingress = "f" * 64
    elif change == "result":
        work.result.output += " invented"
    else:
        work.state = "call_started"
    with pytest.raises(ValueError):
        outcome_document(work, source, ingress_sha256=ingress)


def test_long_output_is_explicitly_bounded_without_losing_original_digest():
    source, ingress, work = sample()
    work.result.output = "z" * 15000
    work.result.output_sha256 = hashlib.sha256(work.result.output.encode()).hexdigest()
    doc = outcome_document(work, source, ingress_sha256=ingress)
    assert len(doc.content) < 11000
    assert "[Ingekort;" in doc.content and work.result.output_sha256 in doc.content



def test_revoked_source_alias_does_not_starve_later_valid_work():
    from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
    from eva.bootstrap.delegated_work_memory import DelegatedWorkMemory
    source, ingress, work = sample()
    class Store:
        def __init__(self):
            self.work = work
        def next_memory_outcome(self, **kwargs):
            return self.work
    store = Store()
    projection = object.__new__(DelegatedWorkMemory)
    projection._service = SimpleNamespace(store=store, _owner={})
    projection._clock = lambda: work.completed_at
    projection._active_since = work.completed_at
    projection._next_poll = 0.0
    projected, submitted = [], []
    def source_loader(_):
        raise DelegatedWorkConflict("original private alias is no longer current")
    projection._source = source_loader
    def record(work, now, disposition, document):
        projected.append((disposition, document))
        store.work = None
    projection._record = record
    projection._submit = submitted.append
    projection._recorded = lambda document: None
    assert projection.run_once() == "skipped"
    assert projected == [("source_unavailable", None)] and submitted == []
    assert projection.run_once() == "idle"
    # A later valid outcome is still processed.
    store.work = work
    projection._next_poll = 0.0
    projection._source = lambda _: source
    assert projection.run_once() == "queued"
    assert len(submitted) == 1



def test_crash_after_enqueue_honours_an_existing_erasure_without_new_submit():
    from eva.bootstrap.delegated_work_memory import DelegatedWorkMemory
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    source, ingress, work = sample()
    doc = outcome_document(work, source, ingress_sha256=ingress)
    data = PostgresMemoryOutbox._document_data(doc)
    data.pop("content")
    data["content_erasure"] = {"status": "erased"}
    projection = object.__new__(DelegatedWorkMemory)
    projection._memory_timeline = SimpleNamespace(events=lambda _: (
        SimpleNamespace(event_type="MEMORY_RETAIN_QUEUED", payload={"data": data}),))
    assert projection._recorded(doc) == "erased"
    assert "content" not in data
    # A different person cannot use the same tombstone to authorize their source.
    other = replace(doc, principal_id="other:person",
        audience=type(doc.audience).private_person(principal_id="other:person"))
    with pytest.raises(RuntimeError, match="replay differs"):
        projection._recorded(other)
