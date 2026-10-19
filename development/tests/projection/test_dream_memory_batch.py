"""Synthetic native HTTP outcomes; no memory/model service is contacted."""
from dataclasses import replace
import hashlib
from urllib.parse import unquote
from uuid import uuid5, NAMESPACE_URL

import pytest

from eva.adapters.projection.hindsight import (
    HindsightMemoryEngine, HindsightProjectionConfig, HindsightProjectionProtocolError,
    HindsightMemoryScopeError, HindsightProjectionUnavailable,
)
from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience


STRATEGY = "eva-ordinary-dream-v1"


def document(source="a", **changes):
    return replace(ConversationMemoryDocument(
        tenant_id="tenant", installation_id="installation", principal_id="person",
        audience=MemoryAudience.private_person(principal_id="person"),
        conversation_id="chat", document_id=f"chat:exchange:{source}", source_ref=source,
        evidence_ref=source, content=f"Synthetic exchange {source}",
        occurred_at="2026-10-19T10:00:00Z", trust="direct_user",
        privacy_class="confidential", retention_mode="dream",
    ), **changes)


class NativeFixture:
    def __init__(self, engine, monkeypatch):
        self.calls = []
        self.bank = engine._bank_id("tenant", "installation")
        self.configuration = {"bank_id": self.bank, "config": {
            "store_document_text": True,
            "retain_strategies": {STRATEGY: {"store_document_text": False}},
        }}
        self.counts = {"chat:exchange:a": 0, "chat:exchange:b": 2}
        self.metadata = {"unit_ids_count": 2, "extraction_errors_count": 0}
        self.records = {}
        self.record_changes = {}
        self.ack_changes = {}
        self.failed = False
        self.submitted = False
        self.operation_ids = []
        monkeypatch.setattr(engine, "ensure_bank", lambda *_: self.bank)
        monkeypatch.setattr(engine, "_request", self.request)

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body))
        if path.endswith("/config"):
            return 200, self.configuration
        if "/documents/" in path:
            identity = unquote(path.split("/documents/", 1)[1])
            if identity not in self.records:
                if 404 in kwargs.get("allowed_statuses", ()):
                    return 404, {}
                raise HindsightProjectionProtocolError("document absent")
            return 200, {**self.records[identity], **self.record_changes.get(identity, {})}
        if path.endswith("/memories"):
            assert method == "POST"
            self.submitted = True
            self.operation_ids.append(body["operation_id"])
            for item in body["items"]:
                assert item["strategy"] == STRATEGY and item["update_mode"] == "replace"
                self.records[item["document_id"]] = {
                    "id": item["document_id"], "bank_id": self.bank,
                    "content_hash": hashlib.sha256(item["content"].encode()).hexdigest(),
                    "document_metadata": item["metadata"], "tags": item["tags"],
                    "original_text": None,
                    "memory_unit_count": self.counts.get(item["document_id"], 0),
                }
            return 200, {"success": True, "bank_id": self.bank, "async": True,
                         "items_count": len(body["items"]), "operation_id": body["operation_id"],
                         **self.ack_changes}
        if path.endswith("/retry"):
            assert method == "POST"
            self.failed = False
            return 200, {"success": True, "operation_id": path.split("/")[-2]}
        assert "/operations/" in path and method == "GET"
        return 200, {"operation_id": path.rsplit("/", 1)[-1],
                     "status": "failed" if self.failed else "completed",
                     "result_metadata": self.metadata}


@pytest.fixture
def native(monkeypatch):
    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://synthetic.invalid"))
    fixture = NativeFixture(engine, monkeypatch)
    return engine, fixture


def test_one_native_batch_returns_exact_source_receipts_without_raw_storage(native):
    engine, api = native
    engine._fast_presence_cache["old"] = (0, None)
    receipts = engine.retain_dream_conversations((document("b"), document("a")))
    assert [(r.document_id, r.disposition, r.selected_fact_count) for r in receipts] == [
        ("chat:exchange:b", "retained", 2), ("chat:exchange:a", "not_selected", 0)]
    assert len(set(r.operation_id for r in receipts)) == 1
    writes = [c for c in api.calls if c[0] != "GET"]
    assert len(writes) == 1 and writes[0][1].endswith("/memories")
    assert api.configuration["config"]["store_document_text"] is True
    assert not engine._fast_presence_cache and engine._fast_presence_generation == 1
    assert all(record["original_text"] is None for record in api.records.values())


def test_reordered_retry_reuses_exact_operation_and_existing_failure_resume(native):
    engine, api = native
    first = engine.retain_dream_conversations((document("a"), document("b")))
    api.failed = True
    second = engine.retain_dream_conversations((document("b"), document("a")))
    assert first[0].operation_id == second[0].operation_id
    assert len(set(api.operation_ids)) == 1
    assert len([c for c in api.calls if c[1].endswith("/retry")]) == 1


@pytest.mark.parametrize("entry", [None, {}, {"store_document_text": True},
    {"store_document_text": 0}, {"store_document_text": "false"},
    {"store_document_text": False, "retain_mission": "different"}])
def test_missing_or_different_strategy_cannot_fall_back_or_dispatch(native, entry):
    engine, api = native
    api.configuration["config"]["retain_strategies"][STRATEGY] = entry
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document(),))
    assert not api.submitted and all(c[0] == "GET" for c in api.calls)


@pytest.mark.parametrize("change", [
    {"retention_mode": "immediate"}, {"trust": "legacy_unverified"},
    {"project_id": "project"}, {"document_id": "task-result"},
])
def test_only_explicit_ordinary_private_dream_sources(native, change):
    engine, api = native
    if change == {"retention_mode": "immediate"}:
        with pytest.raises(HindsightMemoryScopeError):
            engine.retain_dream_conversations((document(**change),))
    else:
        # The shared source contract rejects these before provider admission.
        with pytest.raises(ValueError, match="ordinary private source exchange"):
            document(**change)
    assert api.calls == []


def test_cross_person_privacy_duplicate_and_batch_bounds(native):
    engine, api = native
    other = document("b", principal_id="other", audience=MemoryAudience.private_person(principal_id="other"))
    for docs in [(), (document(), other), (document(), document()),
                 (document(), document("b", occurred_at="2026-10-20T10:00:00Z")),
                 (document(), document("b", privacy_class="restricted")),
                 tuple(document(str(i)) for i in range(33)),
                 (document(content="x" * 262_144),)]:
        with pytest.raises(HindsightMemoryScopeError):
            engine.retain_dream_conversations(docs)
    assert not api.submitted


@pytest.mark.parametrize("metadata", [None, {}, {"unit_ids_count": 0},
    {"unit_ids_count": 0, "extraction_errors_count": 1},
    {"unit_ids_count": False, "extraction_errors_count": 0},
    {"unit_ids_count": 0, "extraction_errors_count": False},
    {"unit_ids_count": -1, "extraction_errors_count": 0}])
def test_unknown_partial_or_error_outcome_never_acknowledges_zero(native, metadata):
    engine, api = native
    api.metadata = metadata
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document(),))
    assert api.submitted and engine._fast_presence_generation == 1


@pytest.mark.parametrize("change", [
    {"id": "other"}, {"bank_id": "other"}, {"content_hash": "0" * 64},
    {"document_metadata": {}}, {"tags": []}, {"tags": [3, "bad"]},
    {"original_text": "raw duplicate"}, {"memory_unit_count": False},
    {"memory_unit_count": -1}, {"memory_unit_count": None},
])
def test_document_receipt_requires_exact_binding_and_measured_count(native, change):
    engine, api = native
    api.metadata = {"unit_ids_count": 0, "extraction_errors_count": 0}
    api.record_changes["chat:exchange:a"] = change
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document(),))


def test_existing_different_document_is_never_replaced(native):
    engine, api = native
    engine.retain_dream_conversations((document("a"), document("b")))
    api.calls.clear()
    api.submitted = False
    api.records["chat:exchange:a"]["content_hash"] = "0" * 64
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document("a"), document("b")))
    assert not api.submitted and all(c[0] == "GET" for c in api.calls)


def test_missing_document_is_not_zero_selection(native):
    engine, api = native
    old_request = api.request
    def missing(method, path, body=None, **kwargs):
        if api.submitted and "/documents/" in path:
            raise HindsightProjectionProtocolError("document absent")
        return old_request(method, path, body, **kwargs)
    engine._request = missing
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document("a"), document("b")))


def test_count_mismatch_keeps_same_operation_and_no_receipts(native):
    engine, api = native
    api.metadata = {"unit_ids_count": 9, "extraction_errors_count": 0}
    for _ in range(2):
        with pytest.raises(HindsightProjectionProtocolError):
            engine.retain_dream_conversations((document("a"), document("b")))
    assert len(set(api.operation_ids)) == 1


@pytest.mark.parametrize("ack", [{"operation_id": "unrelated"}, {"bank_id": "other"},
                                  {"items_count": True}, {"async": False}])
def test_batch_acknowledgement_must_be_exact(native, ack):
    engine, api = native
    api.ack_changes = ack
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document(),))
    assert not any("/operations/" in c[1] for c in api.calls)


def test_existing_same_id_with_different_source_bytes_is_not_replaced(native):
    engine, api = native
    engine.retain_dream_conversations((document("a"), document("b")))
    api.submitted = False
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document("a", content="Changed source"), document("b")))
    assert not api.submitted


def test_config_bank_binding_cannot_be_borrowed(native):
    engine, api = native
    api.configuration["bank_id"] = "different-bank"
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document(),))
    assert not api.submitted


def test_raw_body_and_missing_body_marker_are_not_equivalent(native):
    engine, api = native
    engine.retain_dream_conversations((document("a"), document("b")))
    del api.records["chat:exchange:a"]["original_text"]
    api.submitted = False
    with pytest.raises(HindsightProjectionProtocolError):
        engine.retain_dream_conversations((document("a"), document("b")))
    assert not api.submitted


def settled_response(operation_id, *, state="completed", children=None):
    if children is None:
        children = [{"operation_id": str(uuid5(NAMESPACE_URL, "synthetic-child")),
                     "status": "completed", "sub_batch_index": 1}]
    return {"operation_id": operation_id, "status": state,
            "result_metadata": {"is_parent": True, "num_sub_batches": 1,
                                "unit_ids_count": 2, "extraction_errors_count": 0},
            "child_operations": children}


def test_pre_dispatch_identity_is_pure_and_matches_actual_post(native, monkeypatch):
    engine, api = native
    docs = (document("a"), document("b"))
    bank, operation = engine.dream_operation_identity(docs)
    assert bank == api.bank and api.calls == []
    engine.retain_dream_conversations(docs)
    assert api.operation_ids == [operation]
    assert engine.dream_operation_identity(tuple(reversed(docs))) == (bank, operation)


@pytest.mark.parametrize("missing", ["http404", "not_found"])
def test_erasure_settle_unknown_operation_never_dispatches(native, monkeypatch, missing):
    engine, api = native
    bank, operation = engine.dream_operation_identity((document(),))
    calls = []
    def read(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        assert kwargs["allowed_statuses"] == frozenset({404})
        return (404, {}) if missing == "http404" else (200, {"operation_id": operation, "status": "not_found"})
    monkeypatch.setattr(engine, "_request", read)
    assert engine.settle_dream_operation(bank_id=bank, operation_id=operation) == "not_found"
    assert len(calls) == 1 and calls[0][0] == "GET" and calls[0][2] is None


@pytest.mark.parametrize("state", ["pending", "processing"])
def test_timeout_then_forget_cannot_ack_while_remote_work_can_write(native, monkeypatch, state):
    engine, api = native
    bank, operation = engine.dream_operation_identity((document(),))
    calls = []
    def read(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        return 200, {"operation_id": operation, "status": state}
    monkeypatch.setattr(engine, "_request", read)
    with pytest.raises(HindsightProjectionUnavailable):
        engine.settle_dream_operation(bank_id=bank, operation_id=operation)
    assert len(calls) == 1 and calls[0][0] == "GET"


@pytest.mark.parametrize("changes", [
    {"status": "cancelled"}, {"result_metadata": {}}, {"child_operations": []},
    {"child_operations": [{"operation_id": "invalid", "status": "completed"}]},
    {"child_operations": [{"operation_id": str(uuid5(NAMESPACE_URL, "child")), "status": "processing"}]},
    {"child_operations": [{"operation_id": str(uuid5(NAMESPACE_URL, "child")), "status": "cancelled"}]},
    {"child_operations": [{"operation_id": str(uuid5(NAMESPACE_URL, "child")), "status": "failed"}]},
])
def test_parent_terminal_without_actual_complete_child_proof_is_not_settled(native, monkeypatch, changes):
    engine, api = native
    bank, operation = engine.dream_operation_identity((document(),))
    calls = []
    def read(method, path, body=None, **kwargs):
        calls.append(method)
        return 200, {**settled_response(operation), **changes}
    monkeypatch.setattr(engine, "_request", read)
    with pytest.raises(HindsightProjectionProtocolError):
        engine.settle_dream_operation(bank_id=bank, operation_id=operation)
    assert calls == ["GET"]


def test_failed_parent_can_settle_only_after_failed_child_finished(native, monkeypatch):
    engine, api = native
    bank, operation = engine.dream_operation_identity((document(),))
    child = {"operation_id": str(uuid5(NAMESPACE_URL, "child")), "status": "failed"}
    calls = []
    def read(method, path, body=None, **kwargs):
        calls.append(method)
        return 200, settled_response(operation, state="failed", children=[child])
    monkeypatch.setattr(engine, "_request", read)
    assert engine.settle_dream_operation(bank_id=bank, operation_id=operation) == "failed"
    with pytest.raises(HindsightProjectionProtocolError):
        engine.read_dream_operation_receipts((document(),), bank_id=bank, operation_id=operation)
    assert calls == ["GET", "GET"]


def test_completed_batch_receipts_recover_before_member_erasure_without_any_write(native, monkeypatch):
    engine, api = native
    docs = (document("a"), document("b"))
    expected = engine.retain_dream_conversations(docs)
    bank, operation = engine.dream_operation_identity(docs)
    original_request = api.request
    api.calls.clear()
    def read(method, path, body=None, **kwargs):
        assert method == "GET", "forget recovery must not retry/repost"
        if "/operations/" in path:
            api.calls.append((method, path, body))
            return 200, settled_response(operation)
        return original_request(method, path, body, **kwargs)
    monkeypatch.setattr(engine, "_request", read)
    assert engine.settle_dream_operation(bank_id=bank, operation_id=operation) == "completed"
    receipts = engine.read_dream_operation_receipts(docs, bank_id=bank, operation_id=operation)
    assert receipts == expected
    assert len([c for c in api.calls if "/documents/" in c[1]]) == 2
    assert all(c[0] == "GET" and c[2] is None for c in api.calls)


@pytest.mark.parametrize("wrong", ["operation", "membership", "bytes", "bank"])
def test_readonly_receipt_recovery_requires_original_full_identity(native, wrong):
    engine, api = native
    docs = (document("a"), document("b"))
    bank, operation = engine.dream_operation_identity(docs)
    if wrong == "operation": operation = str(uuid5(NAMESPACE_URL, "unrelated"))
    if wrong == "bank": bank = "eva2-" + "0" * 32
    if wrong == "membership": docs = docs[:1]
    if wrong == "bytes": docs = (document("a", content="Changed"), docs[1])
    with pytest.raises(HindsightMemoryScopeError):
        engine.read_dream_operation_receipts(docs, bank_id=bank, operation_id=operation)
    assert api.calls == []


def test_readonly_recovery_does_not_hide_unmeasured_or_partial_result(native, monkeypatch):
    engine, api = native
    docs = (document("a"), document("b"))
    engine.retain_dream_conversations(docs)
    bank, operation = engine.dream_operation_identity(docs)
    def read(method, path, body=None, **kwargs):
        assert method == "GET"
        response = settled_response(operation)
        response["result_metadata"]["extraction_errors_count"] = 1
        return 200, response
    monkeypatch.setattr(engine, "_request", read)
    with pytest.raises(HindsightProjectionProtocolError):
        engine.read_dream_operation_receipts(docs, bank_id=bank, operation_id=operation)


def test_post_timeout_restart_then_forget_retains_recorded_identity_and_never_reposts(native, monkeypatch):
    engine, api = native
    docs = (document("a"), document("b"))
    recorded_bank, recorded_operation = engine.dream_operation_identity(docs)
    def timeout(*args, **kwargs):
        raise HindsightProjectionUnavailable("synthetic poll timeout after accepted POST")
    monkeypatch.setattr(engine, "_wait_for_operation", timeout)
    with pytest.raises(HindsightProjectionUnavailable):
        engine.retain_dream_conversations(docs)
    assert api.operation_ids == [recorded_operation]
    restarted = HindsightMemoryEngine(HindsightProjectionConfig("http://synthetic.invalid"))
    assert restarted.dream_operation_identity(docs) == (recorded_bank, recorded_operation)
    calls = []
    state = "processing"
    old_request = api.request
    def read_only(method, path, body=None, **kwargs):
        calls.append((method, path))
        assert method == "GET", "erasure after timeout must not resume or dispatch"
        if "/operations/" in path:
            if state == "processing":
                return 200, {"operation_id": recorded_operation, "status": state}
            return 200, settled_response(recorded_operation)
        return old_request(method, path, body, **kwargs)
    monkeypatch.setattr(restarted, "_request", read_only)
    with pytest.raises(HindsightProjectionUnavailable):
        restarted.settle_dream_operation(bank_id=recorded_bank, operation_id=recorded_operation)
    assert len(calls) == 1
    state = "completed"
    assert restarted.settle_dream_operation(bank_id=recorded_bank, operation_id=recorded_operation) == "completed"
    receipts = restarted.read_dream_operation_receipts(docs, bank_id=recorded_bank, operation_id=recorded_operation)
    assert [r.selected_fact_count for r in receipts] == [0, 2]
    assert api.operation_ids == [recorded_operation]
    assert all(method == "GET" for method, _ in calls)


def test_missing_named_strategy_is_provisioned_without_changing_defaults_or_other_strategies(native, monkeypatch):
    engine, api = native
    original = {"imports": {"store_document_text": True, "retain_mission": "keep typed import"}}
    api.configuration["config"]["retain_strategies"] = original.copy()
    request = api.request
    def provision(method, path, body=None, **kwargs):
        if method == "PATCH" and path.endswith("/config"):
            api.calls.append((method, path, body))
            assert set(body) == {"updates"} and set(body["updates"]) == {"retain_strategies"}
            api.configuration["config"]["retain_strategies"] = body["updates"]["retain_strategies"]
            return 200, {"success": True}
        return request(method, path, body, **kwargs)
    monkeypatch.setattr(engine, "_request", provision)
    engine.retain_dream_conversations((document(), document("b")))
    assert api.configuration["config"]["store_document_text"] is True
    assert api.configuration["config"]["retain_strategies"] == {**original, STRATEGY: {"store_document_text": False}}
    writes = [call for call in api.calls if call[0] == "PATCH"]
    assert len(writes) == 1


@pytest.mark.parametrize("fault", ["missing", "default_changed", "other_strategy_lost", "integer_false", "integer_default", "integer_other"])
def test_strategy_provision_requires_exact_readback_before_model_dispatch(native, monkeypatch, fault):
    engine, api = native
    api.configuration["config"]["retain_strategies"] = {"imports": {"store_document_text": True}}
    request = api.request
    def corrupt(method, path, body=None, **kwargs):
        if method == "PATCH" and path.endswith("/config"):
            api.calls.append((method, path, body))
            if fault != "missing":
                api.configuration["config"]["retain_strategies"] = body["updates"]["retain_strategies"].copy()
                if fault == "default_changed":
                    api.configuration["config"]["store_document_text"] = False
                elif fault == "other_strategy_lost":
                    del api.configuration["config"]["retain_strategies"]["imports"]
                elif fault == "integer_false":
                    api.configuration["config"]["retain_strategies"][STRATEGY] = {"store_document_text": 0}
                elif fault == "integer_default":
                    api.configuration["config"]["store_document_text"] = 1
                else:
                    api.configuration["config"]["retain_strategies"]["imports"] = {"store_document_text": 1}
            return 200, {"success": True}
        return request(method, path, body, **kwargs)
    monkeypatch.setattr(engine, "_request", corrupt)
    with pytest.raises(HindsightProjectionProtocolError, match="readback"):
        engine.retain_dream_conversations((document(),))
    assert not api.submitted
