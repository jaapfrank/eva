"""Saved artifacts keep source ownership and the existing variant/release rules."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from types import SimpleNamespace

import pytest

from eva.application.component_improvements import (
    draft_component_content, prepare_component_release, read_component_content,
)
from eva.application.component_development import compare_development_evaluations
from eva.application.procedure_references import ProcedureReference, ProcedureScope
from eva.contracts.canonical_json import canonical_sha256


def saved_source():
    raw = b"VALUE = 1\n"
    scope = asdict(ProcedureScope("tenant", "installation", "person", "project"))
    receipt = "a" * 64
    document_hash = canonical_sha256({"scope": scope, "receipt": receipt})
    target = {"kind": "saved_artifact", "procedure_reference_id": "procedure-reference:" + document_hash,
        "scope": scope, "artifact_path": "/own/helper.py",
        "content_sha256": hashlib.sha256(raw).hexdigest(), "receipt_sha256": receipt,
        "source_call_id": "save", "source_message_id": "message",
        "tool_configuration_sha256": "b" * 64, "retention_until": "2027-10-11T12:00:00Z"}
    path = "saved_artifact_" + document_hash + ".py"
    return raw, target, path


def proposal(raw, target, path, *, parent=None):
    source = raw.decode() if parent is None else parent["candidate_content"]
    old = "VALUE = 1" if parent is None else "VALUE = 2"
    return draft_component_content({"path": path,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "old_text": old, "new_text": "VALUE = 2" if parent is None else "VALUE = 3",
        "problem": "new ordinary input not handled", "expected_gain": "quality, not cost",
        "verification_plan": "fixed development suite then independent assessment"},
        raw, source_target=target, parent=parent)


def test_saved_candidate_generations_and_release_preserve_original_receipt():
    raw, target, path = saved_source()
    first = proposal(raw, target, path)
    second = proposal(raw, target, path, parent=first)
    assert read_component_content(path, raw)["content"] == "VALUE = 1\n"
    assert second["source_target"] == target
    assert second["baseline_content"] == raw.decode()
    assert second["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert second["candidate_content"] == "VALUE = 3\n"
    from tests.application.test_component_improvements import report
    measured = {**report(second), "source_target": deepcopy(target)}
    package = prepare_component_release(second, measured)
    assert package["source_target"] == target
    assert package["baseline_content"] == raw.decode()
    assert "verified artifact save" in package["required_before_installation"][-1]
    assert package["installed"] is False
    assert package["candidate_package_sha256"] == canonical_sha256({
        key: value for key, value in package.items() if key != "candidate_package_sha256"})


def test_rejects_parent_or_report_switched_to_another_saved_target():
    raw, target, path = saved_source()
    first = proposal(raw, target, path)
    changed = deepcopy(first)
    changed["source_target"]["scope"]["principal_id"] = "other-person"
    with pytest.raises(ValueError, match="target binding"):
        proposal(raw, target, path, parent=changed)
    from tests.application.test_component_improvements import report
    measured = {**report(first), "source_target": deepcopy(changed["source_target"])}
    with pytest.raises(ValueError, match="bindings differ"):
        prepare_component_release(first, measured)


def test_comparison_cannot_mix_saved_receipts_even_with_equal_baseline_bytes():
    from tests.application.test_component_development import entry
    first, second = entry(2, hits=3), entry(4, hits=4)
    _, target, _ = saved_source()
    first["report"]["source_target"] = deepcopy(target)
    second["report"]["source_target"] = deepcopy(target)
    second["report"]["source_target"]["receipt_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="basis differs"):
        compare_development_evaluations([first, second])


def test_virtual_source_is_freshly_resolved_and_changed_artifact_is_rejected():
    from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions
    from eva.application.conversation import ConversationConflict
    raw, target, path = saved_source()
    component = ComponentImprovementActions.__new__(ComponentImprovementActions)
    component.source = object()
    calls = []
    def resolve(turn, document):
        assert turn is component.source
        calls.append(document)
        return {"path": path, "baseline_content": raw.decode(), "source_target": deepcopy(target)}
    component.source_resolver = resolve
    read, _ = component._source(target["procedure_reference_id"])
    assert read["path"] == path and read["source_target"] == target
    draft = proposal(raw, target, path)
    component._revalidate(draft)
    assert calls == [target["procedure_reference_id"]] * 2
    component.source_resolver = lambda *a: {
        "path": path, "baseline_content": "VALUE = 4\n", "source_target": target}
    with pytest.raises(ConversationConflict, match="binding changed"):
        component._revalidate(draft)


def test_improvement_resolver_uses_existing_owner_scopes_and_verified_source(monkeypatch):
    import eva.providers.procedure_reference_actions as module
    raw, target, path = saved_source()
    scope = ProcedureScope(**target["scope"])
    reference = ProcedureReference(target["procedure_reference_id"], "Count active durations",
        target["artifact_path"], target["content_sha256"], target["source_call_id"],
        target["source_message_id"], target["tool_configuration_sha256"],
        target["receipt_sha256"], target["retention_until"])
    checks = []
    host = SimpleNamespace(require_scope=lambda s: checks.append(s), require_current=lambda: checks.append("current"))
    instance = module.ProcedureReferenceActions.__new__(module.ProcedureReferenceActions)
    instance.engine = object()
    instance._host = lambda t: host
    instance._scopes = lambda t: (scope,)
    instance.clock = lambda: "2026-10-11T12:00:00Z"
    monkeypatch.setattr(module, "PostgresMemoryOutbox", lambda *a, **kw: object())
    class References:
        def __init__(self, store, h): assert h is host
        def resolve_candidates(self, actual, ids, *, now):
            assert actual == scope and ids == (reference.document_id,)
            return (SimpleNamespace(reference=reference, untrusted_content=raw.decode()),)
    monkeypatch.setattr(module, "ProcedureReferences", References)
    turn = SimpleNamespace(tenant_id="tenant", installation_id="installation")
    resolved = instance.resolve_improvement_source(turn, reference.document_id)
    assert resolved == {"path": path, "baseline_content": raw.decode(), "source_target": target}
    assert checks == [scope, "current"]
