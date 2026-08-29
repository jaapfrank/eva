#!/usr/bin/env python3
"""Prove connector-neutral secret rotation/removal across CLI processes."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "src/eva/operations/installer_capability_workflow.py",
    "src/eva/operations/installer_provider_verification.py",
    "src/eva/operations/installer_secret_lifecycle.py",
    "src/eva/operations/installer_secret_lifecycle_cli.py",
    "src/eva/operations/installer_secret_store.py",
    "tests/operations/test_installer_provider_verification.py",
    "tests/operations/test_installer_secret_lifecycle.py",
    "tests/operations/test_installer_secret_lifecycle_cli.py",
    "tests/operations/test_installer_secret_store.py",
    "tools/prove_installer_secret_lifecycle.py",
)
TEST_PATHS = (
    "tests/operations/test_installer_secret_store.py",
    "tests/operations/test_installer_secret_lifecycle.py",
    "tests/operations/test_installer_secret_lifecycle_cli.py",
    "tests/operations/test_installer_capability_workflow.py",
    "tests/operations/test_installer_provider_verification.py",
    "tests/operations/test_installer_provider_verification_cli.py",
    "tests/connectors/test_installer_telegram_probe.py",
    "tests/connectors/test_installer_home_assistant_probe.py",
    "tests/api/test_installer_wizard.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"proof subprocess failed: {command[0]} returned {completed.returncode}"
        )
    return completed


def _cli(
    *,
    operation: str,
    slot: str,
    capability_workspace: Path,
    secret_workspace: Path,
    evidence_workspace: Path,
    env: dict[str, str],
    new_secret_file: Path | None = None,
) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "eva.operations.installer_secret_lifecycle_cli",
        "--operation",
        operation,
        "--slot",
        slot,
        "--capability-workspace",
        str(capability_workspace),
        "--secret-workspace",
        str(secret_workspace),
        "--evidence-workspace",
        str(evidence_workspace),
    ]
    if new_secret_file is not None:
        command.extend(("--new-secret-file", str(new_secret_file)))
    completed = _run(command, env=env)
    if completed.stderr:
        raise RuntimeError("secret lifecycle CLI wrote unexpected stderr")
    payload = json.loads(completed.stdout)
    if payload.get("status") != "passed":
        raise RuntimeError("secret lifecycle CLI did not pass")
    return payload


def prove(source_commit: str) -> dict[str, object]:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from eva.operations.installer_capability_workflow import (  # noqa: PLC0415
        InstallerCapabilityWorkflow,
    )
    from eva.operations.installer_secret_store import (  # noqa: PLC0415
        InstallerSecretStore,
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    old_secret = "synthetic-proof-old-telegram-secret-123456"
    new_secret = "synthetic-proof-new-telegram-secret-654321"
    third_secret = "synthetic-proof-third-telegram-secret-abcdef"
    with tempfile.TemporaryDirectory(prefix="eva2-installer-secret-proof-") as raw:
        proof_root = Path(raw)
        os.chmod(proof_root, 0o700)
        capability_workspace = proof_root / "capabilities"
        secret_workspace = proof_root / "secrets"
        evidence_workspace = proof_root / "evidence"
        for path in (capability_workspace, secret_workspace, evidence_workspace):
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        workflow = InstallerCapabilityWorkflow(workspace=capability_workspace)
        store = InstallerSecretStore(workspace=secret_workspace)
        workflow.select(step_id="telegram", wanted=True)
        first = store.store(slot_id="telegram", secret=old_secret)
        workflow.record_verification(
            step_id="telegram",
            outcome="ready",
            evidence_sha256="c" * 64,
        )
        replacement = proof_root / "replacement.secret"
        replacement.write_text(new_secret)
        os.chmod(replacement, 0o600)

        rotated = _cli(
            operation="rotate",
            slot="telegram",
            capability_workspace=capability_workspace,
            secret_workspace=secret_workspace,
            evidence_workspace=evidence_workspace,
            env=env,
            new_secret_file=replacement,
        )
        restarted_store = InstallerSecretStore(workspace=secret_workspace)
        restarted_workflow = InstallerCapabilityWorkflow(
            workspace=capability_workspace
        )
        if restarted_store.read_secret(slot_id="telegram") != new_secret.encode():
            raise RuntimeError("rotated secret did not survive a new process object")
        if restarted_workflow.status().step("telegram").state != "blocked":
            raise RuntimeError("old provider authority survived rotation")

        removed = _cli(
            operation="remove",
            slot="telegram",
            capability_workspace=capability_workspace,
            secret_workspace=secret_workspace,
            evidence_workspace=evidence_workspace,
            env=env,
        )
        after_remove = InstallerSecretStore(workspace=secret_workspace)
        removed_status = next(
            item for item in after_remove.status() if item.slot_id == "telegram"
        )
        if removed_status.state != "missing":
            raise RuntimeError("removed secret remains available")
        third = after_remove.store(slot_id="telegram", secret=third_secret)
        if (first.generation, rotated.get("generation"), third.generation) != (
            1,
            2,
            3,
        ):
            raise RuntimeError("secret generations are not monotonic")

        retained = b"".join(path.read_bytes() for path in evidence_workspace.iterdir())
        for forbidden in (
            old_secret.encode(),
            new_secret.encode(),
            third_secret.encode(),
            str(replacement).encode(),
        ):
            if forbidden in retained:
                raise RuntimeError("secret lifecycle evidence contains private input")
        evidence_count = len(tuple(evidence_workspace.iterdir()))

        test_env = dict(env)
        test_env["TMPDIR"] = str(proof_root)
        tests = _run(
            [sys.executable, "-m", "pytest", "-q", *TEST_PATHS], env=test_env
        )
        if "88 passed" not in tests.stdout:
            raise RuntimeError("focused installer regression count differs")

    return {
        "contract": "EvaInstallerSecretLifecycleProof/v1",
        "evidence_file_count": evidence_count,
        "focused_test_count": 88,
        "generation_sequence": [1, 2, 3],
        "live_provider_called": False,
        "live_runtime_changed": False,
        "network_used": False,
        "operations": [rotated["operation"], removed["operation"]],
        "private_file_input": True,
        "process_count": 2,
        "proof_completed_at": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "recovery_cases": [
            "rotation_after_first_rename",
            "removal_before_retired_bundle_cleanup",
        ],
        "secret_or_path_retained_in_evidence": False,
        "source_commit": source_commit,
        "source_sha256": {
            path: _sha256(ROOT / path) for path in SOURCE_PATHS
        },
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            prove(arguments.source_commit),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
