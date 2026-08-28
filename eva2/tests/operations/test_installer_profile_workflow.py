from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eva.operations.installer_profile_workflow import (
    InstallerProfileWorkflow,
    InstallerProfileWorkflowError,
)


def _workflow(
    root: Path, *, moments: list[str] | None = None
) -> InstallerProfileWorkflow:
    workspace = root / "profile-workspace"
    workspace.mkdir(mode=0o700)
    values = iter(
        moments
        or [
            "2026-08-29T02:00:00Z",
            "2026-08-29T02:01:00Z",
        ]
    )
    return InstallerProfileWorkflow(workspace=workspace, clock=lambda: next(values))


def _stage(workflow: InstallerProfileWorkflow):
    return workflow.stage(
        installation_name="Eva thuis",
        owner_label="Jaap",
        language="nl-NL",
        time_zone="Europe/Amsterdam",
    )


def test_profile_stages_confirms_and_resumes_from_private_canonical_state(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)

    assert workflow.status().phase == "empty"
    staged = _stage(workflow)
    assert staged.phase == "staged"
    assert staged.revision == 1
    assert staged.already_in_phase is False

    resumed = InstallerProfileWorkflow(
        workspace=workflow.workspace,
        clock=lambda: "2026-08-29T02:01:00Z",
    )
    status = resumed.status()
    assert status.phase == "staged"
    assert status.profile is not None
    assert status.profile.installation_name == "Eva thuis"
    assert status.profile.owner_label == "Jaap"
    assert status.profile.language == "nl-NL"
    assert status.profile.time_zone == "Europe/Amsterdam"
    assert status.profile_sha256 == staged.profile_sha256

    confirmed = resumed.confirm(expected_profile_sha256=staged.profile_sha256)
    assert confirmed.phase == "ready"
    assert confirmed.revision == 2
    assert confirmed.already_in_phase is False

    restarted = InstallerProfileWorkflow(workspace=workflow.workspace)
    ready = restarted.status()
    assert ready.phase == "ready"
    assert ready.revision == 2
    assert (
        restarted.confirm(
            expected_profile_sha256=staged.profile_sha256
        ).already_in_phase
        is True
    )
    assert _stage(restarted).already_in_phase is True

    metadata = workflow.state_path.stat()
    assert metadata.st_mode & 0o077 == 0
    assert metadata.st_nlink == 1
    raw = workflow.state_path.read_bytes()
    assert (
        raw
        == json.dumps(
            json.loads(raw), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installation_name", ""),
        ("installation_name", " Eva thuis"),
        ("owner_label", "Jaap\nroot"),
        ("language", "dutch"),
        ("time_zone", "Amsterdam"),
    ],
)
def test_profile_rejects_invalid_human_choices_before_writing(
    private_tmp_path: Path, field: str, value: str
) -> None:
    workflow = _workflow(private_tmp_path)
    values = {
        "installation_name": "Eva thuis",
        "owner_label": "Jaap",
        "language": "nl-NL",
        "time_zone": "Europe/Amsterdam",
    }
    values[field] = value

    with pytest.raises(InstallerProfileWorkflowError):
        workflow.stage(**values)

    assert list(workflow.workspace.iterdir()) == []


def test_profile_rejects_conflict_wrong_confirmation_and_tampering(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(private_tmp_path)
    _stage(workflow)

    with pytest.raises(InstallerProfileWorkflowError, match="different"):
        workflow.stage(
            installation_name="Andere Eva",
            owner_label="Jaap",
            language="nl-NL",
            time_zone="Europe/Amsterdam",
        )
    with pytest.raises(InstallerProfileWorkflowError, match="hash"):
        workflow.confirm(expected_profile_sha256="0" * 64)

    payload = json.loads(workflow.state_path.read_text())
    payload["profile"]["owner_label"] = "Mallory"
    workflow.state_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    os.chmod(workflow.state_path, 0o600)
    with pytest.raises(InstallerProfileWorkflowError, match="hash differs"):
        workflow.status()


def test_profile_rejects_unsafe_workspace_state_and_lock(
    private_tmp_path: Path,
) -> None:
    unsafe = private_tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(InstallerProfileWorkflowError, match="owner-only"):
        InstallerProfileWorkflow(workspace=unsafe)

    safe = private_tmp_path / "safe"
    safe.mkdir(mode=0o700)
    workflow = _workflow(safe)
    target = workflow.workspace / "outside"
    target.write_text("lock")
    (workflow.workspace / ".profile-workflow.lock").symlink_to(target)
    with pytest.raises(InstallerProfileWorkflowError, match="lock"):
        workflow.status()


def test_profile_refuses_a_confirmation_clock_before_staging(
    private_tmp_path: Path,
) -> None:
    workflow = _workflow(
        private_tmp_path,
        moments=["2026-08-29T02:00:00Z", "2026-08-29T01:59:59Z"],
    )
    staged = _stage(workflow)

    with pytest.raises(InstallerProfileWorkflowError, match="precedes"):
        workflow.confirm(expected_profile_sha256=staged.profile_sha256)

    assert workflow.status().phase == "staged"
