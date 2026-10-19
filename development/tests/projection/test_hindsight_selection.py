from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import pytest

from eva.adapters.projection.hindsight import (
    HindsightMemoryEngine, HindsightProjectionConfig, HindsightProjectionProtocolError,
)
from eva.application.memory import ConversationMemoryDocument
from eva.contracts.canonical_json import canonical_sha256
from eva.application.memory_audience import MemoryAudience


def _document():
    return ConversationMemoryDocument(
        tenant_id="tenant", installation_id="installation", principal_id="person",
        audience=MemoryAudience.private_person(principal_id="person"),
        conversation_id="chat", document_id="chat:exchange:message", source_ref="message",
        evidence_ref="message", content="Gebruiker (directe bron): Hallo.",
        occurred_at="2026-10-19T10:00:00Z", trust="direct_user", privacy_class="confidential",
    )


def _engine(monkeypatch, outcome, *, bad_ack=False):
    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://memory.local"))
    monkeypatch.setattr(engine, "ensure_bank", lambda *_: "bank")
    calls = []
    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if method == "POST":
            return 200, {"success": True, "items_count": 1, "async": True,
                         "operation_id": "other" if bad_ack else body["operation_id"]}
        operation = path.rsplit("/", 1)[-1]
        return 200, {"operation_id": operation, "status": "completed", **outcome}
    monkeypatch.setattr(engine, "_request", request)
    return engine, calls


@pytest.mark.parametrize(("metadata", "selected", "disposition"), [
    ({"unit_ids_count": 0, "extraction_errors_count": 0}, 0, "not_selected"),
    ({"unit_ids_count": 1, "extraction_errors_count": 0}, 1, "retained"),
    ({"unit_ids_count": 0, "extraction_errors_count": 1}, 0, "retained"),
    ({"unit_ids_count": 0}, 0, "retained"),
    ({}, None, "retained"),
])
def test_only_confirmed_zero_selection_is_not_retained(monkeypatch, metadata, selected, disposition):
    engine, calls = _engine(monkeypatch, {"result_metadata": metadata})
    doc = _document()
    receipt = engine.retain_conversation(doc)
    assert receipt.disposition == disposition
    assert receipt.selected_fact_count == selected
    bank, item = engine._conversation_item(doc)
    expected = str(uuid5(NAMESPACE_URL, "EvaMemoryRetain/v2:" + canonical_sha256([bank, item])))
    assert receipt.operation_id == expected
    assert [c[0] for c in calls] == ["POST", "GET"]
    assert all(c[0] != "DELETE" for c in calls)


def test_absent_selection_metadata_is_unknown(monkeypatch):
    engine, _ = _engine(monkeypatch, {})
    receipt = engine.retain_conversation(_document())
    assert receipt.disposition == "retained" and receipt.selected_fact_count is None


@pytest.mark.parametrize("value", [False, True, -1, "0", 0.0])
@pytest.mark.parametrize("key", ["unit_ids_count", "extraction_errors_count"])
def test_selection_count_must_be_measured_nonnegative_integer(monkeypatch, key, value):
    metadata = {"unit_ids_count": 0, "extraction_errors_count": 0, key: value}
    engine, _ = _engine(monkeypatch, {"result_metadata": metadata})
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_conversation(_document())


def test_receipt_cannot_borrow_unrelated_operation(monkeypatch):
    engine, calls = _engine(monkeypatch, {}, bad_ack=True)
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_conversation(_document())
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    {"document_id": "typed-learning"}, {"trust": "host_policy"},
    {"document_id": "chat:exchange:other-source"},
])
def test_typed_or_differently_bound_source_remains_available(monkeypatch, change):
    engine, _ = _engine(monkeypatch, {"result_metadata": {
        "unit_ids_count": 0, "extraction_errors_count": 0}})
    receipt = engine.retain_conversation(replace(_document(), **change))
    assert receipt.disposition == "retained"
