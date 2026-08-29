from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import (
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
    InstallerProviderVerificationError,
    InstallerProviderVerifier,
    installer_provider_scope_manifest_sha256,
)
from eva.operations.installer_secret_lifecycle import InstallerSecretLifecycle
from eva.operations.installer_secret_store import InstallerSecretStore


class Probe:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0
        self.secret_view: memoryview | None = None
        self.timeout_seconds: float | None = None

    def read_only_probe(self, *, secret: memoryview, timeout_seconds: float):
        self.calls += 1
        self.secret_view = secret
        self.timeout_seconds = timeout_seconds
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _fixture(root: Path, probe: Probe, *, slot_id: str = "telegram"):
    capability_workspace = root / "capabilities"
    secret_workspace = root / "secrets"
    evidence_workspace = root / "evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    moments = iter(
        [
            "2026-08-29T05:00:00Z",
            "2026-08-29T05:01:00Z",
            "2026-08-29T05:02:00Z",
        ]
    )
    ticks = iter([10.0, 10.125])
    workflow = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T05:02:00Z",
    )
    store = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T05:00:00Z",
    )
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        probes={slot_id: probe},
        clock=lambda: next(moments),
        monotonic=lambda: next(ticks),
    )
    return workflow, store, verifier, evidence_workspace


def test_ready_probe_writes_sanitized_receipt_then_marks_capability_ready(
    private_tmp_path: Path,
) -> None:
    token = "synthetic-telegram-token-123456789"
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret=token)

    result = verifier.verify(slot_id="telegram", timeout_seconds=3)

    assert result.outcome == "ready"
    assert result.reason_code == "verified"
    assert workflow.status().step("telegram").state == "ready"
    assert workflow.status().step("telegram").evidence_sha256 == result.evidence_sha256
    assert probe.calls == 1
    assert probe.timeout_seconds == 3.0
    assert probe.secret_view is not None
    assert bytes(probe.secret_view) == b"\x00" * len(token)
    receipts = list(evidence_workspace.iterdir())
    assert len(receipts) == 1
    raw = receipts[0].read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result.evidence_sha256
    assert token.encode() not in raw
    payload = json.loads(raw)
    assert raw == canonical_json_bytes(payload)
    assert payload == {
        "billable_request_attempted": False,
        "check_id": "telegram.get_me",
        "completed_at": "2026-08-29T05:01:00Z",
        "configuration_sha256": None,
        "contract": "EvaInstallerProviderVerificationReceipt/v4",
        "cost_measurement": "not_applicable",
        "duration_ms": 125,
        "input_tokens": None,
        "mutation_attempted": False,
        "outcome": "ready",
        "output_tokens": None,
        "provider_operation": "read_only_identity",
        "provider_payload_retained": False,
        "provider_scope_manifest_sha256": installer_provider_scope_manifest_sha256(
            "telegram"
        ),
        "provider_scopes": ["telegram.bot_identity.read"],
        "reason_code": "verified",
        "secret_generation": 1,
        "secret_reference": "installer-secret://telegram/v1",
        "secret_returned": False,
        "slot_id": "telegram",
        "started_at": "2026-08-29T05:00:00Z",
        "usage_measurement": "not_applicable",
    }
    assert receipts[0].stat().st_mode & 0o077 == 0


def test_provider_scope_manifests_are_closed_stable_and_slot_specific() -> None:
    hashes = {
        slot_id: installer_provider_scope_manifest_sha256(slot_id)
        for slot_id in ("model_provider", "telegram", "home_assistant", "email")
    }

    assert len(set(hashes.values())) == 4
    assert all(len(value) == 64 for value in hashes.values())
    with pytest.raises(InstallerProviderVerificationError, match="slot is invalid"):
        installer_provider_scope_manifest_sha256("unknown")


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (InstallerProviderProbeError("unauthorized"), "unauthorized"),
        (TimeoutError("contains synthetic-token-value"), "timeout"),
        (RuntimeError("contains synthetic-token-value"), "provider_error"),
    ],
)
def test_provider_failures_are_sanitized_and_recorded_blocked(
    private_tmp_path: Path, failure: BaseException, reason: str
) -> None:
    probe = Probe(failure)
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")

    result = verifier.verify(slot_id="telegram")

    assert result.outcome == "blocked"
    assert result.reason_code == reason
    assert workflow.status().step("telegram").state == "blocked"
    raw = next(evidence_workspace.iterdir()).read_bytes()
    assert b"synthetic-token-value" not in raw
    assert str(failure).encode() not in raw


@pytest.mark.parametrize(
    "observation",
    [
        object(),
        InstallerProviderProbeObservation("wrong.check", "ready", "verified"),
        InstallerProviderProbeObservation("telegram.get_me", "ready", "timeout"),
        InstallerProviderProbeObservation("telegram.get_me", "blocked", "verified"),
        InstallerProviderProbeObservation(
            "telegram.get_me",
            "ready",
            "verified",
            usage_measurement="measured",
            input_tokens=1,
            output_tokens=1,
        ),
        InstallerProviderProbeObservation(
            "telegram.get_me",
            "ready",
            "verified",
            provider_operation="bounded_generation",
        ),
    ],
)
def test_invalid_provider_observation_never_marks_ready(
    private_tmp_path: Path, observation: object
) -> None:
    probe = Probe(observation)
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")

    result = verifier.verify(slot_id="telegram")

    assert result.outcome == "blocked"
    assert result.reason_code == "invalid_response"
    assert workflow.status().step("telegram").state == "blocked"
    assert len(list(evidence_workspace.iterdir())) == 1


def test_verifier_requires_selection_secret_and_registered_probe(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    with pytest.raises(InstallerProviderVerificationError, match="selected"):
        verifier.verify(slot_id="telegram")
    workflow.select(step_id="telegram", wanted=True)
    with pytest.raises(InstallerProviderVerificationError, match="not available"):
        verifier.verify(slot_id="telegram")
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")
    with pytest.raises(InstallerProviderVerificationError, match="probe"):
        InstallerProviderVerifier(
            capability_workflow=workflow,
            secret_store=store,
            evidence_workspace=evidence_workspace,
            probes={"home_assistant": probe},
        ).verify(slot_id="telegram")
    assert probe.calls == 0
    assert list(evidence_workspace.iterdir()) == []


def test_late_success_is_recorded_as_timeout_not_ready(private_tmp_path: Path) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")
    object.__setattr__(verifier, "monotonic", iter([10.0, 12.1]).__next__)

    result = verifier.verify(slot_id="telegram", timeout_seconds=2)

    assert result.outcome == "blocked"
    assert result.reason_code == "timeout"
    assert json.loads(next(evidence_workspace.iterdir()).read_text())["duration_ms"] == 2100


def test_capability_race_leaves_receipt_but_never_commits_ready(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")
    original = probe.read_only_probe

    def racing_probe(*, secret: memoryview, timeout_seconds: float):
        workflow.select(step_id="telegram", wanted=False)
        return original(secret=secret, timeout_seconds=timeout_seconds)

    probe.read_only_probe = racing_probe  # type: ignore[method-assign]
    with pytest.raises(InstallerProviderVerificationError, match="state changed"):
        verifier.verify(slot_id="telegram")

    assert workflow.status().step("telegram").state == "skipped"
    assert workflow.status().step("telegram").evidence_sha256 is None
    assert len(list(evidence_workspace.iterdir())) == 1


def test_secret_rotation_during_probe_cannot_revalidate_old_generation(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret="synthetic-old-token-123456789")
    lifecycle = InstallerSecretLifecycle(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        clock=lambda: "2026-08-29T05:00:30Z",
    )
    original = probe.read_only_probe

    def racing_probe(*, secret: memoryview, timeout_seconds: float):
        lifecycle.rotate(
            slot_id="telegram", secret="synthetic-new-token-987654321"
        )
        return original(secret=secret, timeout_seconds=timeout_seconds)

    probe.read_only_probe = racing_probe  # type: ignore[method-assign]
    with pytest.raises(InstallerProviderVerificationError, match="state changed"):
        verifier.verify(slot_id="telegram")

    capability = workflow.status().step("telegram")
    assert capability.state == "blocked"
    assert store.read_secret(slot_id="telegram") == b"synthetic-new-token-987654321"
    assert len(list(evidence_workspace.iterdir())) == 3


def test_ready_capability_cannot_be_reprobed_after_restart(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, evidence_workspace = _fixture(private_tmp_path, probe)
    workflow.select(step_id="telegram", wanted=True)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")
    verifier.verify(slot_id="telegram")

    restarted_probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    restarted = InstallerProviderVerifier(
        capability_workflow=InstallerCapabilityWorkflow(workspace=workflow.workspace),
        secret_store=InstallerSecretStore(workspace=store.workspace),
        evidence_workspace=evidence_workspace,
        probes={"telegram": restarted_probe},
    )
    with pytest.raises(InstallerProviderVerificationError, match="selected"):
        restarted.verify(slot_id="telegram")
    assert restarted_probe.calls == 0


def test_verifier_rejects_unsafe_workspace_registry_and_timeout(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier, _ = _fixture(private_tmp_path, probe)
    unsafe = private_tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(InstallerProviderVerificationError, match="owner-only"):
        InstallerProviderVerifier(workflow, store, unsafe, {"telegram": probe})
    safe = private_tmp_path / "safe"
    safe.mkdir(mode=0o700)
    with pytest.raises(InstallerProviderVerificationError, match="registry"):
        InstallerProviderVerifier(workflow, store, safe, {"unknown": probe})
    with pytest.raises(InstallerProviderVerificationError, match="registry"):
        InstallerProviderVerifier(workflow, store, safe, {"telegram": object()})  # type: ignore[dict-item]
    for value in (0, 31, True, "10"):
        with pytest.raises(InstallerProviderVerificationError, match="timeout"):
            verifier.verify(slot_id="telegram", timeout_seconds=value)
    assert probe.calls == 0
