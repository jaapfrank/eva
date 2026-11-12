"""Unit tests for the Fase 4 Unified Core Smoke Gate."""

from __future__ import annotations

from eva.operations.core_smoke_gate import run_smoke_gate


def test_core_smoke_gate_offline_verifies_bindings_workboard_and_posture() -> None:
    report = run_smoke_gate(check_live=False)
    assert report["contract"] == "EvaCoreSmokeGate/v1"
    assert report["status"] == "passed"
    assert report["sourceBindings"]["status"] == "passed"
    assert report["sourceBindings"]["verifiedCount"] == 10
    assert report["workboard"]["status"] == "valid"
    assert "proposal-only" in report["dependencyUpdatePosture"]
