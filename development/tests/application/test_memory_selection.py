"""Providerless selection receipts and existing cold-path accounting."""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
from types import SimpleNamespace

import pytest

from eva.application.memory import MemoryRetentionReceipt, BudgetedConversationMemoryWriter
from eva.application.memory_background import SemanticMemoryColdPathWorker
from tests.application.test_memory_background import _document

NOW = datetime(2026, 8, 29, 9, tzinfo=UTC)


def _receipt(document, **kwargs):
    return MemoryRetentionReceipt("synthetic-bank", document.document_id,
        hashlib.sha256(document.content.encode()).hexdigest(), **kwargs)


def test_legacy_receipt_preserves_unknown_count_instead_of_inventing_zero():
    receipt = _receipt(_document())
    assert receipt.disposition == "retained"
    assert receipt.selected_fact_count is None and receipt.operation_id is None


@pytest.mark.parametrize("kwargs", [
    {"selected_fact_count": True}, {"selected_fact_count": -1},
    {"selected_fact_count": 0.0}, {"selected_fact_count": "0"},
    {"operation_id": ""}, {"operation_id": " "}, {"operation_id": 1},
    {"disposition": "not_selected"},
    {"disposition": "not_selected", "selected_fact_count": 0},
    {"disposition": "not_selected", "selected_fact_count": 1, "operation_id": "operation:one"},
    {"disposition": "not_selected", "selected_fact_count": None, "operation_id": "operation:one"},
])
def test_selection_receipt_does_not_coerce_unknown_or_invalid_evidence(kwargs):
    with pytest.raises(ValueError):
        _receipt(_document(), **kwargs)


def test_budgeted_writer_forwards_exact_zero_selection_without_second_provider_call():
    document = _document()
    receipt = _receipt(document, disposition="not_selected", selected_fact_count=0,
        operation_id="operation:exact-zero")
    calls = []
    class Budget:
        def admit_memory(self, doc, *, now):
            calls.append("admit")
            return SimpleNamespace(admitted=True, retention_until="2026-09-05T09:00:00Z")
        def mark_retained(self, doc, result, *, now):
            assert result is receipt and doc.content == document.content
            assert doc.retention_until == "2026-09-05T09:00:00Z"
            calls.append("mark")
    class Provider:
        def retain_conversation(self, doc):
            calls.append("provider")
            return receipt
    assert BudgetedConversationMemoryWriter(Provider(), Budget()).retain_bounded(
        document, now=NOW) is receipt
    assert calls == ["admit", "provider", "mark"]


@pytest.mark.parametrize("outcome", ["not_selected", "retained", "legacy_fake", "failure"])
def test_cold_path_only_reflects_retained_sources_and_preserves_failure(outcome):
    document = _document()
    calls = []
    receipt = (_receipt(document, disposition="not_selected", selected_fact_count=0,
        operation_id="operation:exact-zero") if outcome == "not_selected"
        else _receipt(document) if outcome == "retained" else None)
    class Provider:
        def retain_conversation(self, doc):
            calls.append("provider")
            if outcome == "failure":
                raise RuntimeError("Synthetic provider unavailable")
            return receipt
    class Budget:
        def admit_memory(self, doc, *, now):
            return SimpleNamespace(admitted=True, retention_until="2026-09-05T09:00:00Z")
        def mark_retained(self, doc, result, *, now):
            assert result is receipt
            calls.append("marked")
    class Outbox:
        def enqueue(self, doc):
            return SimpleNamespace(document=doc)
        def begin(self, job, *, now):
            return job
        def succeed(self, job, result, *, now):
            assert result is receipt
            calls.append("succeeded")
            worker._stop.set()
        def fail(self, job, error, *, now):
            assert outcome == "failure"
            calls.append("failed")
            worker._stop.set()
            return SimpleNamespace(status="needs_help")
    worker = SemanticMemoryColdPathWorker(Provider(), storage_budget=Budget(),
        outbox=Outbox(), retry_delay_seconds=0)
    worker.submit(document)
    worker._run()  # One deterministic queued job; fake terminal callback stops it.
    if outcome == "failure":
        assert calls == ["provider", "failed"]
        assert worker._reflection_scopes == set()
    else:
        assert calls == ["provider", "marked", "succeeded"]
        expected = set() if outcome == "not_selected" else {
            (document.tenant_id, document.installation_id, document.principal_id)}
        assert worker._reflection_scopes == expected


def test_explicit_forget_removes_zero_selection_source_via_existing_erasure_callbacks():
    from tests.application.test_memory_background import _RestoreAuthority
    document = _document()
    principal_sha = hashlib.sha256(document.principal_id.encode()).hexdigest()
    calls = []
    job = SimpleNamespace(bank_id="synthetic-bank", document_id=document.document_id,
        principal_sha256=principal_sha, source_message_id=document.source_ref,
        retention_until="2027-08-29T09:00:00Z")
    class Budget:
        requested = False
        def request_erasure(self, **kwargs):
            assert kwargs["document_id"] == document.document_id
            assert kwargs["source_message_id"] == document.source_ref
            if self.requested:
                return None
            self.requested = True
            return job
        def mark_removed(self, actual_job, *, now):
            assert actual_job is job
            calls.append("removed")
    class Provider:
        def remove_conversation_document(self, **kwargs):
            assert kwargs == {"bank_id": job.bank_id, "document_id": job.document_id,
                "principal_sha256": principal_sha}
            calls.append("provider-erased")
    class Journal:
        def erase_message_content(self, **kwargs):
            assert kwargs["message_id"] == document.source_ref
            calls.append("journal-erased")
            return SimpleNamespace(operation_sha256=kwargs["operation_sha256"],
                principal_sha256=principal_sha,
                message_sha256=hashlib.sha256(document.source_ref.encode()).hexdigest(),
                replayed=False, redacted_event_count=2)
    class Outbox:
        def erase_content(self, **kwargs):
            assert kwargs["document_id"] == document.document_id
            calls.append("outbox-erased")
            return SimpleNamespace(operation_sha256=kwargs["operation_sha256"],
                principal_sha256=principal_sha,
                document_sha256=hashlib.sha256(document.document_id.encode()).hexdigest(),
                replayed=False, redacted_event_count=1)
    worker = SemanticMemoryColdPathWorker(Provider(), storage_budget=Budget(),
        outbox=Outbox(), conversation_erasure=Journal(),
        restore_authority_erasure=_RestoreAuthority(calls))
    kwargs = dict(principal_id=document.principal_id, document_id=document.document_id,
        source_message_id=document.source_ref, now=NOW)
    assert worker.erase_document(**kwargs) is True
    assert calls == ["authority", "provider-erased", "journal-erased", "outbox-erased", "removed"]
    assert worker.erase_document(**kwargs) is False
    assert len(calls) == 5
    assert worker._reflection_scopes == set()
