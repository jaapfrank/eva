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


def test_committed_release_requirements_remain_explicitly_blocked() -> None:
    receipt = checker.inspect_release_readiness(checker.REQUIREMENTS.read_bytes())
    assert receipt["status"] == "blocked"
    assert receipt["blockerCount"] == 11
    assert receipt["currentReleaseMetadataReusable"] is False
    assert receipt["offlineBuildMechanismsReusableAfterFreshInputs"] is True
    assert receipt["ephemeralInstallIsReleaseEvidence"] is False
    assert set(receipt["blockers"]) == checker.EXPECTED_FRESH_IDS


def test_requirements_refuse_marking_a_gate_complete_without_evidence() -> None:
    value = _value()
    value["requiredFreshArtifacts"][0]["status"] = "passed"
    with pytest.raises(checker.ReleaseReadinessError, match="explicitly missing"):
        checker.inspect_release_readiness(_canonical(value))


def test_requirements_refuse_calling_ephemeral_wheelhouse_durable() -> None:
    value = _value()
    value["ephemeralValidation"]["durableReceiptPresent"] = True
    with pytest.raises(checker.ReleaseReadinessError, match="overstated"):
        checker.inspect_release_readiness(_canonical(value))


def test_every_dependency_artifact_is_bound_to_the_candidate_lock() -> None:
    value = _value()
    changed = copy.deepcopy(value)
    gate = next(
        item
        for item in changed["requiredFreshArtifacts"]
        if item["id"] == "candidate_wheel_coverage"
    )
    gate["acceptance"]["candidateLockSha256"] = "0" * 64
    with pytest.raises(checker.ReleaseReadinessError, match="not candidate-bound"):
        checker.inspect_release_readiness(_canonical(changed))


def test_requirements_refuse_bound_current_artifact_drift() -> None:
    value = _value()
    value["currentReleaseInputs"][0]["sha256"] = "0" * 64
    with pytest.raises(checker.ReleaseReadinessError, match="digest differs"):
        checker.inspect_release_readiness(_canonical(value))


def test_requirements_refuse_omitted_or_duplicate_release_gate() -> None:
    value = _value()
    value["requiredFreshArtifacts"].pop()
    with pytest.raises(checker.ReleaseReadinessError, match="gate set differs"):
        checker.inspect_release_readiness(_canonical(value))
    value = _value()
    value["requiredFreshArtifacts"].append(value["requiredFreshArtifacts"][0])
    with pytest.raises(checker.ReleaseReadinessError, match="gate set differs"):
        checker.inspect_release_readiness(_canonical(value))
