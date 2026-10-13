"""An evaluation locator binds the actual draft and source without granting authority."""
from copy import deepcopy

import pytest

from eva.application.component_development import make_scoped_component_evaluation_binding
from eva.contracts.canonical_json import canonical_sha256


def inputs():
    return {"task_id": "work-original", "draft_step": 2, "evaluation_step": 3,
        "evaluation_call_id": "evaluate-original", "spec_sha256": "a" * 64,
        "source_ingress_sha256": "b" * 64, "draft_thread_id": "component-draft:" + "c" * 64,
        "draft_event_payload_sha256": "d" * 64, "path": "bootstrap/delegated_digital_runner.py",
        "baseline_sha256": "e" * 64, "candidate_sha256": "f" * 64, "suite_sha256": "0" * 64}


def test_exact_hash_input_is_reconstructible_from_existing_host_records():
    values = inputs()
    binding = make_scoped_component_evaluation_binding(**values)
    locator = {"contract": "EvaScopedComponentEvaluationBinding/v1", "task_id": "work-original",
        "draft_step": 2, "evaluation_step": 3, "evaluation_call_id": "evaluate-original"}
    context = {k: v for k, v in values.items() if k not in locator}
    assert binding == {**locator, "execution_binding_ref": canonical_sha256({"binding": locator, **context})}
    assert set(binding) == set(locator) | {"execution_binding_ref"}
    assert make_scoped_component_evaluation_binding(**deepcopy(values)) == binding


@pytest.mark.parametrize("field,replacement", [
    ("task_id", "different-work"), ("draft_step", 1), ("evaluation_step", 4),
    ("evaluation_call_id", "different-call"), ("spec_sha256", "1" * 64),
    ("source_ingress_sha256", "1" * 64), ("draft_thread_id", "component-draft:" + "1" * 64),
    ("draft_event_payload_sha256", "1" * 64), ("path", "application/another.py"),
    ("baseline_sha256", "1" * 64), ("candidate_sha256", "1" * 64), ("suite_sha256", "1" * 64),
])
def test_any_original_locator_or_evidence_change_has_a_different_reference(field, replacement):
    values = inputs()
    original = make_scoped_component_evaluation_binding(**values)
    values[field] = replacement
    assert make_scoped_component_evaluation_binding(**values)["execution_binding_ref"] != original["execution_binding_ref"]


@pytest.mark.parametrize("field,value", [
    ("draft_step", True), ("draft_step", -1), ("evaluation_step", 2),
    ("evaluation_step", False), ("task_id", ""), ("task_id", "a" * 257),
    ("evaluation_call_id", "call\nchanged"), ("path", ""),
    ("draft_thread_id", "other-thread"), ("candidate_sha256", "F" * 64),
    ("spec_sha256", None), ("draft_event_payload_sha256", "short"),
])
def test_malformed_locator_or_evidence_cannot_get_a_reference(field, value):
    values = inputs()
    values[field] = value
    with pytest.raises(ValueError):
        make_scoped_component_evaluation_binding(**values)
