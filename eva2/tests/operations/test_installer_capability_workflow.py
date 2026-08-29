from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)


def _workflow(
    root: Path, moments: list[str] | None = None
) -> InstallerCapabilityWorkflow:
    workspace = root / "capability-workspace"
    workspace.mkdir(mode=0o700)
    values = iter(
        moments
        or [
            "2026-08-29T03:00:00Z",
            "2026-08-29T03:01:00Z",
            "2026-08-29T03:02:00Z",
            "2026-08-29T03:03:00Z",
        ]
    )
    return InstallerCapabilityWorkflow(workspace=workspace, clock=lambda: next(values))


def test_capability_choices_resume_with_separate_required_and_optional_steps(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    empty = workflow.status()
    assert empty.revision == 0
    assert [item.step_id for item in empty.steps] == [
        "model_provider",
        "telegram",
        "home_assistant",
        "email",
        "adaptive_connector",
        "budgets",
    ]
    assert all(item.state == "pending" for item in empty.steps)

    model = workflow.select(step_id="model_provider", wanted=True)
    telegram = workflow.select(step_id="telegram", wanted=True)
    email = workflow.select(step_id="email", wanted=False)
    assert (model.state, telegram.state, email.state) == (
        "selected",
        "selected",
        "skipped",
    )

    resumed = InstallerCapabilityWorkflow(workspace=workflow.workspace).status()
    assert resumed.revision == 3
    assert resumed.step("model_provider").required is True
    assert resumed.step("model_provider").state == "selected"
    assert resumed.step("telegram").state == "selected"
    assert resumed.step("email").state == "skipped"
    assert resumed.step("home_assistant").state == "pending"
    assert resumed.head_sha256 == email.head_sha256
    assert workflow.state_path.stat().st_mode & 0o077 == 0


def test_only_operator_verification_can_make_a_selected_step_ready(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    workflow.select(step_id="telegram", wanted=True)

    ready = workflow.record_verification(
        step_id="telegram",
        outcome="ready",
        evidence_sha256="a" * 64,
    )
    assert ready.state == "ready"
    assert ready.evidence_sha256 == "a" * 64
    assert ready.already_in_state is False
    assert (
        workflow.record_verification(
            step_id="telegram",
            outcome="ready",
            evidence_sha256="a" * 64,
        ).already_in_state
        is True
    )
    with pytest.raises(InstallerCapabilityWorkflowError, match="disconnect"):
        workflow.select(step_id="telegram", wanted=False)


def test_blocked_verification_can_be_selected_again_and_later_become_ready(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    workflow.select(step_id="home_assistant", wanted=True)
    blocked = workflow.record_verification(
        step_id="home_assistant",
        outcome="blocked",
        evidence_sha256="b" * 64,
    )
    assert blocked.state == "blocked"
    selected = workflow.select(step_id="home_assistant", wanted=True)
    assert selected.state == "selected"
    ready = workflow.record_verification(
        step_id="home_assistant",
        outcome="ready",
        evidence_sha256="c" * 64,
    )
    assert ready.state == "ready"
    assert ready.revision == 4


def test_verification_compare_and_swap_refuses_concurrent_workflow_change(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    workflow.select(step_id="telegram", wanted=True)
    snapshot = workflow.status()
    workflow.select(step_id="email", wanted=False)

    with pytest.raises(InstallerCapabilityWorkflowError, match="state changed"):
        workflow.record_verification(
            step_id="telegram",
            outcome="ready",
            evidence_sha256="f" * 64,
            expected_revision=snapshot.revision,
            expected_head_sha256=snapshot.head_sha256,
        )
    assert workflow.status().step("telegram").state == "selected"
    with pytest.raises(InstallerCapabilityWorkflowError, match="incomplete"):
        workflow.record_verification(
            step_id="telegram",
            outcome="ready",
            evidence_sha256="f" * 64,
            expected_revision=workflow.status().revision,
        )


def test_required_steps_cannot_be_skipped_and_unknown_steps_are_rejected(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    with pytest.raises(InstallerCapabilityWorkflowError, match="required"):
        workflow.select(step_id="model_provider", wanted=False)
    with pytest.raises(InstallerCapabilityWorkflowError, match="required"):
        workflow.select(step_id="budgets", wanted=False)
    with pytest.raises(InstallerCapabilityWorkflowError, match="unknown"):
        workflow.select(step_id="magic", wanted=True)
    assert list(workflow.workspace.iterdir()) == []


def test_verification_requires_selection_exact_outcome_and_evidence(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    with pytest.raises(InstallerCapabilityWorkflowError, match="selected"):
        workflow.record_verification(
            step_id="telegram", outcome="ready", evidence_sha256="d" * 64
        )
    workflow.select(step_id="email", wanted=False)
    with pytest.raises(InstallerCapabilityWorkflowError, match="selected"):
        workflow.record_verification(
            step_id="email", outcome="ready", evidence_sha256="d" * 64
        )
    workflow.select(step_id="telegram", wanted=True)
    with pytest.raises(InstallerCapabilityWorkflowError, match="outcome"):
        workflow.record_verification(
            step_id="telegram", outcome="working", evidence_sha256="d" * 64
        )
    with pytest.raises(InstallerCapabilityWorkflowError, match="hash"):
        workflow.record_verification(
            step_id="telegram", outcome="ready", evidence_sha256="secret"
        )


def test_capability_event_chain_rejects_tampering(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    workflow.select(step_id="telegram", wanted=True)
    payload = json.loads(workflow.state_path.read_text())
    payload["events"][0]["step_id"] = "email"
    workflow.state_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    os.chmod(workflow.state_path, 0o600)

    with pytest.raises(InstallerCapabilityWorkflowError, match="hash differs"):
        workflow.status()


def test_capability_workflow_rejects_unsafe_workspace_and_symlink_lock(
    private_tmp_path: Path,
) -> None:
    unsafe = private_tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(InstallerCapabilityWorkflowError, match="owner-only"):
        InstallerCapabilityWorkflow(workspace=unsafe)

    safe = private_tmp_path / "safe"
    safe.mkdir(mode=0o700)
    workflow = _workflow(safe)
    target = workflow.workspace / "outside"
    target.write_text("lock")
    (workflow.workspace / ".capability-workflow.lock").symlink_to(target)
    with pytest.raises(InstallerCapabilityWorkflowError, match="lock"):
        workflow.status()


def test_capability_workflow_refuses_a_clock_before_retained_history(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(
        private_tmp_path,
        ["2026-08-29T03:00:00Z", "2026-08-29T02:59:59Z"],
    )
    workflow.select(step_id="telegram", wanted=True)
    with pytest.raises(InstallerCapabilityWorkflowError, match="precedes"):
        workflow.select(step_id="email", wanted=True)
    assert workflow.status().revision == 1
