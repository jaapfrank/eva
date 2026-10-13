from __future__ import annotations

import json
import os
from pathlib import Path

from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_secret_lifecycle_cli import (
    run_installer_secret_lifecycle,
)
from eva.operations.installer_secret_store import InstallerSecretStore


def _paths(root: Path) -> tuple[Path, Path, Path]:
    paths = (root / "capabilities", root / "secrets", root / "development/evidence")
    for path in paths:
        path.mkdir(mode=0o700)
    return paths


def _ready(capabilities: Path, secrets: Path, *, secret: str) -> None:
    workflow = InstallerCapabilityWorkflow(workspace=capabilities)
    workflow.select(step_id="telegram", wanted=True)
    InstallerSecretStore(workspace=secrets).store(
        slot_id="telegram", secret=secret
    )
    workflow.record_verification(
        step_id="telegram", outcome="ready", evidence_sha256="b" * 64
    )


def _arguments(
    capabilities: Path, secrets: Path, evidence: Path, operation: str
) -> list[str]:
    return [
        "--operation",
        operation,
        "--slot",
        "telegram",
        "--capability-workspace",
        str(capabilities),
        "--secret-workspace",
        str(secrets),
        "--evidence-workspace",
        str(evidence),
    ]


def test_cli_rotates_from_private_file_without_returning_secret_or_path(
    private_tmp_path: Path, capsys: object
) -> None:
    capabilities, secrets, evidence = _paths(private_tmp_path)
    old_secret = "synthetic-cli-old-secret-123456"
    new_secret = "synthetic-cli-new-secret-654321"
    _ready(capabilities, secrets, secret=old_secret)
    input_path = private_tmp_path / "replacement.secret"
    input_path.write_text(new_secret)
    os.chmod(input_path, 0o600)

    code = run_installer_secret_lifecycle(
        [
            *_arguments(capabilities, secrets, evidence, "rotate"),
            "--new-secret-file",
            str(input_path),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert payload["generation"] == 2
    assert payload["secret_returned"] is False
    assert new_secret not in output
    assert str(input_path) not in output
    assert InstallerSecretStore(workspace=secrets).read_secret(
        slot_id="telegram"
    ) == new_secret.encode()


def test_cli_rejects_group_readable_replacement_without_invalidating_old_secret(
    private_tmp_path: Path, capsys: object
) -> None:
    capabilities, secrets, evidence = _paths(private_tmp_path)
    old_secret = "synthetic-cli-old-secret-123456"
    _ready(capabilities, secrets, secret=old_secret)
    input_path = private_tmp_path / "unsafe.secret"
    input_path.write_text("synthetic-cli-new-secret-654321")
    os.chmod(input_path, 0o640)

    code = run_installer_secret_lifecycle(
        [
            *_arguments(capabilities, secrets, evidence, "rotate"),
            "--new-secret-file",
            str(input_path),
        ]
    )

    assert code == 2
    output = capsys.readouterr().out
    assert str(input_path) not in output
    assert InstallerCapabilityWorkflow(workspace=capabilities).status().step(
        "telegram"
    ).state == "ready"
    assert InstallerSecretStore(workspace=secrets).read_secret(
        slot_id="telegram"
    ) == old_secret.encode()


def test_cli_removes_secret_and_rejects_irrelevant_secret_file(
    private_tmp_path: Path, capsys: object
) -> None:
    capabilities, secrets, evidence = _paths(private_tmp_path)
    _ready(
        capabilities,
        secrets,
        secret="synthetic-cli-remove-secret-123456",
    )
    irrelevant = private_tmp_path / "irrelevant.secret"
    irrelevant.write_text("synthetic-irrelevant-secret-123456")
    os.chmod(irrelevant, 0o600)

    rejected = run_installer_secret_lifecycle(
        [
            *_arguments(capabilities, secrets, evidence, "remove"),
            "--new-secret-file",
            str(irrelevant),
        ]
    )
    assert rejected == 2
    assert json.loads(capsys.readouterr().out)["status"] == "failed"

    removed = run_installer_secret_lifecycle(
        _arguments(capabilities, secrets, evidence, "remove")
    )
    payload = json.loads(capsys.readouterr().out)
    assert removed == 0
    assert payload["state"] == "missing"
    assert InstallerCapabilityWorkflow(workspace=capabilities).status().step(
        "telegram"
    ).state == "blocked"
