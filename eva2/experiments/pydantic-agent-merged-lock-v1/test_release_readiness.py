from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "pydantic_release_readiness_checker", HERE / "check_release_readiness.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def _value() -> dict[str, object]:
    return json.loads(checker.REQUIREMENTS.read_bytes())


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def test_committed_release_requirements_are_explicitly_historical() -> None:
    receipt = checker.inspect_release_readiness(checker.REQUIREMENTS.read_bytes())
    assert receipt["status"] == "historical"
    assert receipt["historicalMissingGateCountAtDecision"] == 11
    assert receipt["currentReleaseGate"] is False
    assert receipt["claimsPydanticAi237"] is False
    assert (
        set(receipt["historicalMissingGatesAtDecision"]) == checker.EXPECTED_FRESH_IDS
    )


def test_requirements_refuse_marking_a_gate_complete_without_evidence() -> None:
    value = _value()
    value["requiredFreshArtifactsAtDecision"][0]["status"] = "passed"
    with pytest.raises(checker.ReleaseReadinessError, match="was not missing"):
        checker.inspect_release_readiness(_canonical(value))


def test_requirements_refuse_calling_ephemeral_wheelhouse_durable() -> None:
    value = _value()
    value["ephemeralValidationAtDecision"]["durableReceiptPresent"] = True
    with pytest.raises(checker.ReleaseReadinessError, match="validation differs"):
        checker.inspect_release_readiness(_canonical(value))


def test_every_dependency_artifact_is_bound_to_the_candidate_lock() -> None:
    value = _value()
    changed = copy.deepcopy(value)
    gate = next(
        item
        for item in changed["requiredFreshArtifactsAtDecision"]
        if item["id"] == "candidate_wheel_coverage"
    )
    gate["acceptance"]["candidateLockSha256"] = "0" * 64
    with pytest.raises(checker.ReleaseReadinessError, match="not candidate-bound"):
        checker.inspect_release_readiness(_canonical(changed))


def test_requirements_refuse_historical_binding_drift() -> None:
    value = _value()
    value["historicalReleaseInputs"][0]["sha256"] = "0" * 64
    with pytest.raises(checker.ReleaseReadinessError, match="binding differs"):
        checker.inspect_release_readiness(_canonical(value))


def test_requirements_refuse_omitted_or_duplicate_release_gate() -> None:
    value = _value()
    value["requiredFreshArtifactsAtDecision"].pop()
    with pytest.raises(checker.ReleaseReadinessError, match="gate set differs"):
        checker.inspect_release_readiness(_canonical(value))
    value = _value()
    value["requiredFreshArtifactsAtDecision"].append(
        value["requiredFreshArtifactsAtDecision"][0]
    )
    with pytest.raises(checker.ReleaseReadinessError, match="gate set differs"):
        checker.inspect_release_readiness(_canonical(value))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("contract", "Eva2PydanticAgentReleaseReadinessRequirements/v1"),
        ("status", "passed"),
    ),
)
def test_historical_snapshot_cannot_be_relabelled_as_current(
    field: str, value: str
) -> None:
    document = _value()
    document[field] = value
    with pytest.raises(checker.ReleaseReadinessError, match="classification differs"):
        checker.inspect_release_readiness(_canonical(document))


def test_historical_snapshot_explicitly_disclaims_active_237() -> None:
    document = _value()
    document["nonClaims"][1] = "This historical snapshot proves Pydantic AI 2.37.0."
    with pytest.raises(checker.ReleaseReadinessError, match="non-claims differ"):
        checker.inspect_release_readiness(_canonical(document))
