from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from eva.application.memory import MemoryRetentionReceipt
from tools import import_eva1_memory as importer
from tools.import_eva1_memory import (
    Eva1MemoryImportError, ImportScope, apply, build_documents, prepare, preview, select_records,
)


OWNER = "33333333-3333-3333-8333-333333333333"
OTHER = "55555555-5555-5555-8555-555555555555"


class Memory:
    def __init__(self) -> None:
        self.documents = {"existing-eva2-source": None}
        self.written = []
        self.removed = []

    def retain_conversation(self, document):
        self.documents[document.document_id] = document
        self.written.append(document)
        return MemoryRetentionReceipt("eva2-" + "a" * 32, document.document_id,
                                      hashlib.sha256(document.content.encode()).hexdigest())

    def remove_conversation_document(self, **values):
        self.removed.append(values)
        self.documents.pop(values["document_id"], None)


@pytest.fixture
def import_fixture(tmp_path: Path):
    tmp_path.chmod(0o700)
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, ts TEXT, person TEXT, category TEXT, content TEXT, archived INTEGER, archived_reason TEXT)")
    binding = tmp_path / "bindings.json"
    binding.write_text(json.dumps({"contract": "EvaTelegramPrincipalBindings/v2",
                                  "bindings": [{"principal_id": OWNER}, {"principal_id": OTHER}]}))
    binding.chmod(0o600)
    scope = ImportScope(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", source_sqlite=str(source),
                        tenant_id="runtime-tenant", installation_id="runtime-installation",
                        canonical_tenant_id="11111111-1111-1111-8111-111111111111",
                        canonical_installation_id="22222222-2222-2222-8222-222222222222",
                        label_principals={"Owner": OWNER, "": OWNER, "Other": OTHER},
                        binding_file=str(binding), binding_sha256=hashlib.sha256(binding.read_bytes()).hexdigest(),
                        hindsight_url="http://localhost:8888", bank_namespace="eva2")
    return tmp_path, source, scope, Memory()


def add(source: Path, row_id: int, label: str | None, content: str, *, archived=0, reason=None, category="preference", ts="2026-09-01T12:00:00Z"):
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?)", (row_id, ts, label, category, content, archived, reason))


def test_real_import_retains_history_and_each_person_without_asserting_new_facts(import_fixture):
    directory, source, scope, memory = import_fixture
    add(source, 1, "Owner", "prefers quiet evenings")
    add(source, 2, "Other", "prefers morning appointments")
    add(source, 3, None, "a historical shared observation")
    summary = prepare(scope, directory)
    result = apply(scope, directory, memory)
    assert summary["selected_row_count"] == result["selected_row_count"] == 3
    assert result["written_documents"] == 3
    owner_documents = [d for d in memory.written if d.principal_id == OWNER]
    other_documents = [d for d in memory.written if d.principal_id == OTHER]
    assert len(owner_documents) == 2 and len(other_documents) == 1
    assert all("morning appointments" not in d.content for d in owner_documents)
    assert "quiet evenings" not in other_documents[0].content
    assert all(d.audience.kind == "private_person" and d.audience.principal_id == d.principal_id
               and d.conversation_id == d.principal_id and d.trust == "legacy_unverified"
               and d.privacy_class == "confidential" and d.retention_until is None
               and d.source_ref.startswith("eva1-memory:")
               and d.evidence_ref.startswith("eva1-snapshot:sha256:") for d in memory.written)
    assert any("[niet toegeschreven]" in d.content for d in owner_documents)
    assert "existing-eva2-source" in memory.documents


def test_reimport_deduplicates_across_new_snapshots_and_corrects_same_source_bucket(import_fixture):
    directory, source, scope, memory = import_fixture
    add(source, 1, "Owner", "earlier preference")
    add(source, 2, "Owner", "another memory")
    prepare(scope, directory)
    first = apply(scope, directory, memory)
    assert first["written_documents"] == 1  # two rows, one native extraction.
    original_document_id = memory.written[0].document_id
    add(source, 70, "unmapped", "unmapped history changes snapshot only")
    prepare(scope, directory)
    second = apply(scope, directory, memory)
    assert first["snapshot_sha256"] != second["snapshot_sha256"]
    assert second["written_documents"] == 0 and second["unchanged_documents"] == 1
    assert not memory.removed
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET content='corrected preference' WHERE id=1")
    prepare(scope, directory)
    third = apply(scope, directory, memory)
    assert third["written_documents"] == 1
    assert third["replaced_documents"] == 1
    assert memory.removed[-1]["document_id"] == original_document_id
    assert memory.written[-1].document_id == original_document_id
    assert "corrected preference" in memory.documents[original_document_id].content
    assert "earlier preference" not in memory.documents[original_document_id].content


def test_archived_or_retracted_rows_are_removed_without_touching_existing_eva2(import_fixture):
    directory, source, scope, memory = import_fixture
    add(source, 1, "Owner", "first historical memory")
    add(source, 2, "Owner", "second historical memory")
    prepare(scope, directory)
    apply(scope, directory, memory)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET archived=1 WHERE id=1")
    prepare(scope, directory)
    replaced = apply(scope, directory, memory)
    assert replaced["written_documents"] == 1
    assert replaced["replaced_documents"] == 1
    assert memory.removed[-1]["principal_sha256"] == hashlib.sha256(OWNER.encode()).hexdigest()
    assert "first historical memory" not in memory.written[-1].content
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET archived_reason='retracted' WHERE id=2")
    prepare(scope, directory)
    removed = apply(scope, directory, memory)
    assert removed["removed_documents"] == 1 and removed["retained_documents"] == 0
    assert set(memory.documents) == {"existing-eva2-source"}


def test_excludes_credentials_exact_secrets_test_material_and_unknown_people(import_fixture):
    directory, source, scope, _memory = import_fixture
    secrets = directory / "secrets.json"
    secrets.write_text(json.dumps({"values": ["registered-private-value"]}))
    secrets.chmod(0o600)
    scope = replace(scope, secret_values_file=str(secrets))
    for row_id, label, content in ((1,"Owner","sk-proj-abcdefghijklmnop"),
                                   (2,"Owner","contains registered-private-value"),
                                   (3,"Owner","synthetic fixture memory"),
                                   (4,"unknown","ordinary historical memory"),
                                   (5,"Owner","ordinary historical memory")):
        add(source, row_id, label, content)
    summary = prepare(scope, directory)
    assert summary["selected_row_count"] == 1
    assert summary["excluded_counts"] == {"secret":2,"test_material":1,"unmapped_person":1}
    assert "registered-private-value" not in json.dumps(summary)


def test_retargets_or_changed_identity_binding_fail_before_any_provider_write(import_fixture):
    directory, source, scope, memory = import_fixture
    add(source, 1, "Owner", "a historical memory")
    prepare(scope, directory)
    apply(scope, directory, memory)
    with pytest.raises(Eva1MemoryImportError, match="another source"):
        prepare(replace(scope, label_principals={"Owner":OTHER}), directory)
    binding = Path(scope.binding_file)
    binding.write_text(binding.read_text() + "\n")
    writes_before = len(memory.written)
    with pytest.raises(Eva1MemoryImportError, match="changed"):
        apply(scope, directory, memory)
    assert len(memory.written) == writes_before


def test_tampered_plan_snapshot_is_rejected_and_private_source_is_unchanged(import_fixture):
    directory, source, scope, memory = import_fixture
    add(source, 1, "Owner", "a historical memory")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    prepare(scope, directory)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    plan = json.loads((directory / "plan.json").read_text())
    snapshot = Path(plan["snapshot_path"])
    snapshot.write_bytes(snapshot.read_bytes() + b"altered")
    with pytest.raises(Eva1MemoryImportError, match="snapshot"):
        apply(scope, directory, memory)
    assert not memory.written


def test_verified_exact_reimport_plans_and_dispatches_no_native_write(import_fixture):
    directory, source, scope, memory = import_fixture
    add(source, 1, "Owner", "a historical memory")
    prepare(scope, directory)
    apply(scope, directory, memory)
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["last_result"]["ordinary_recall_verified"] = True
    receipt_path.write_text(json.dumps(receipt))
    assert preview(scope, directory) == {"unchanged_documents":1, "retain_documents":0,
                                         "delete_documents":0,"replace_documents":0}
    class NoDispatch:
        def __getattr__(self, name):
            raise AssertionError("unchanged import may not dispatch a native request")
    result = apply(scope, directory, NoDispatch())
    assert result["written_documents"] == result["removed_documents"] == 0
    assert result["unchanged_documents"] == 1
    assert result["ordinary_recall_verified"] is True


class ReplayMemory(Memory):
    """Native completed-operation replay acknowledges without reinserting."""
    def __init__(self):
        super().__init__()
        self.operations = {}
        self.requests = []
        self.fail_retain = False
        self.crash_after_remove = False

    def retain_conversations(self, documents):
        operation = tuple((d.document_id, d.content, d.source_ref, d.evidence_ref,
                           d.occurred_at, d.principal_id, d.trust) for d in documents)
        self.requests.append(operation)
        if self.fail_retain:
            raise RuntimeError("simulated native interruption")
        if operation not in self.operations:
            self.operations[operation] = tuple(self.retain_conversation(d) for d in documents)
        return self.operations[operation]

    def remove_conversation_document(self, **values):
        super().remove_conversation_document(**values)
        if self.crash_after_remove:
            self.crash_after_remove = False
            raise SystemExit("simulated crash after acknowledged remote erasure")


def test_completed_batch_before_receipt_save_resumes_without_erasing_or_false_ack(import_fixture, monkeypatch):
    directory, source, scope, _ = import_fixture
    memory = ReplayMemory()
    add(source, 1, "Owner", "earlier preference")
    add(source, 251, "Owner", "earlier appointment")
    prepare(scope, directory)
    apply(scope, directory, memory)
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["last_result"]["ordinary_recall_verified"] = True
    receipt_path.write_text(json.dumps(receipt))
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET content='corrected history' WHERE person='Owner'")
    prepare(scope, directory)
    write_json = importer._write_json
    def crash_before_batch_receipt(path, state):
        if path.name == "receipt.json" and not state["pending_operations"]:
            raise SystemExit("simulated crash after completed native batch")
        write_json(path, state)
    with monkeypatch.context() as patch:
        patch.setattr(importer, "_write_json", crash_before_batch_receipt)
        with pytest.raises(SystemExit):
            apply(scope, directory, memory)
    receipt = json.loads((directory / "receipt.json").read_text())
    assert receipt["last_result"]["ordinary_recall_verified"] is False
    assert len(receipt["pending_operations"]) == 2
    assert all(op["phase"] == "retain_pending" for op in receipt["pending_operations"].values())
    assert all("corrected history" in document.content for document in memory.documents.values() if document)
    removed_before, written_before = len(memory.removed), len(memory.written)
    completed_operation = memory.requests[-1]
    result = apply(scope, directory, memory)
    assert result["ordinary_recall_verified"] is False
    assert result["written_documents"] == result["replaced_documents"] == 2
    assert len(memory.removed) == removed_before
    assert len(memory.written) == written_before  # completed operation replayed
    assert memory.requests[-1] == completed_operation
    assert all("corrected history" in document.content for document in memory.documents.values() if document)
    assert not json.loads((directory / "receipt.json").read_text())["pending_operations"]


def test_crash_after_remote_delete_keeps_intent_then_restores_exact_revision(import_fixture):
    directory, source, scope, _ = import_fixture
    memory = ReplayMemory()
    add(source, 1, "Owner", "earlier preference")
    prepare(scope, directory)
    apply(scope, directory, memory)
    document_id = memory.written[0].document_id
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET content='corrected preference' WHERE id=1")
    prepare(scope, directory)
    memory.crash_after_remove = True
    with pytest.raises(SystemExit):
        apply(scope, directory, memory)
    receipt = json.loads((directory / "receipt.json").read_text())
    assert receipt["pending_operations"][document_id]["phase"] == "remove_pending"
    assert document_id not in memory.documents
    result = apply(scope, directory, memory)
    assert result["written_documents"] == result["replaced_documents"] == 1
    assert "corrected preference" in memory.documents[document_id].content
    assert memory.documents[document_id].source_ref.endswith(":generation:1")


def test_failed_retain_cannot_replan_or_revert_to_deleted_old_revision(import_fixture):
    directory, source, scope, _ = import_fixture
    memory = ReplayMemory()
    add(source, 1, "Owner", "earlier preference")
    prepare(scope, directory)
    first_plan = (directory / "plan.json").read_bytes()
    apply(scope, directory, memory)
    document_id = memory.written[0].document_id
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET content='corrected preference' WHERE id=1")
    prepare(scope, directory)
    exact_pending_plan = (directory / "plan.json").read_bytes()
    memory.fail_retain = True
    with pytest.raises(RuntimeError):
        apply(scope, directory, memory)
    assert document_id not in memory.documents
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET content='earlier preference' WHERE id=1")
    with pytest.raises(Eva1MemoryImportError, match="pending import"):
        prepare(scope, directory)
    assert (directory / "plan.json").read_bytes() == exact_pending_plan
    (directory / "plan.json").write_bytes(first_plan)
    requests_before, removals_before = len(memory.requests), len(memory.removed)
    with pytest.raises(Eva1MemoryImportError, match="pending import"):
        apply(scope, directory, memory)
    assert len(memory.requests) == requests_before and len(memory.removed) == removals_before
    (directory / "plan.json").write_bytes(exact_pending_plan)
    memory.fail_retain = False
    apply(scope, directory, memory)
    assert "corrected preference" in memory.documents[document_id].content
    assert len(memory.removed) == removals_before
    prepare(scope, directory)
    apply(scope, directory, memory)
    assert "earlier preference" in memory.documents[document_id].content
    assert memory.documents[document_id].source_ref.endswith(":generation:2")


def test_erased_source_can_be_readded_from_identical_old_snapshot_without_completed_replay(import_fixture):
    directory, source, scope, _ = import_fixture
    memory = ReplayMemory()
    add(source, 1, "Owner", "historical preference")
    prepare(scope, directory)
    old_plan = (directory / "plan.json").read_bytes()
    apply(scope, directory, memory)
    old_document = memory.written[0]
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET archived=1 WHERE id=1")
    prepare(scope, directory)
    apply(scope, directory, memory)
    assert old_document.document_id not in memory.documents
    # The exact old snapshot/body would hit the original completed native
    # operation unless the importer preserves an erasure generation tombstone.
    (directory / "plan.json").write_bytes(old_plan)
    result = apply(scope, directory, memory)
    restored = memory.documents[old_document.document_id]
    assert result["written_documents"] == 1
    assert restored.document_id == old_document.document_id
    assert restored.content == old_document.content and restored.evidence_ref == old_document.evidence_ref
    assert restored.source_ref == old_document.source_ref + ":generation:2"
    assert memory.requests[-1] != memory.requests[0]
    assert len(memory.operations) == 2
    assert "existing-eva2-source" in memory.documents


def test_saved_batch_before_final_result_does_not_reuse_old_recall_verification(import_fixture, monkeypatch):
    directory, source, scope, _ = import_fixture
    memory = ReplayMemory()
    add(source, 1, "Owner", "earlier preference")
    prepare(scope, directory)
    apply(scope, directory, memory)
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["last_result"]["ordinary_recall_verified"] = True
    receipt_path.write_text(json.dumps(receipt))
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE memories SET content='corrected preference' WHERE id=1")
    prepare(scope, directory)
    write_json = importer._write_json
    def crash_after_batch_receipt(path, state):
        write_json(path, state)
        if path.name == "receipt.json" and not state["pending_operations"]:
            raise SystemExit("simulated crash after durable batch receipt before final result")
    with monkeypatch.context() as patch:
        patch.setattr(importer, "_write_json", crash_after_batch_receipt)
        with pytest.raises(SystemExit):
            apply(scope, directory, memory)
    receipt = json.loads(receipt_path.read_text())
    assert not receipt["pending_operations"]
    assert receipt["last_result"]["ordinary_recall_verified"] is False
    requests_before, removals_before = len(memory.requests), len(memory.removed)
    result = apply(scope, directory, memory)
    assert result["written_documents"] == result["replaced_documents"] == 0
    assert result["unchanged_documents"] == 1
    assert result["ordinary_recall_verified"] is False
    assert len(memory.requests) == requests_before and len(memory.removed) == removals_before
    assert "corrected preference" in memory.written[-1].content
