"""Operator CLI for fail-closed installer secret rotation and removal."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat

from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_secret_lifecycle import (
    InstallerSecretLifecycle,
    InstallerSecretLifecycleError,
)
from eva.operations.installer_secret_store import (
    SECRET_SLOTS,
    InstallerSecretStore,
    InstallerSecretStoreError,
)


def _output(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _read_private_secret(path: Path) -> bytearray:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or metadata.st_nlink != 1
            or not 16 <= metadata.st_size <= 8192
        ):
            raise OSError("replacement secret file is not private")
        raw = bytearray()
        while len(raw) <= 8192:
            chunk = os.read(descriptor, min(4096, 8193 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) != metadata.st_size or not 16 <= len(raw) <= 8192:
            raise OSError("replacement secret file changed while reading")
        return raw
    except OSError as exc:
        raise InstallerSecretLifecycleError(
            "replacement secret file is unavailable"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def run_installer_secret_lifecycle(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eva-installer-secret")
    parser.add_argument(
        "--operation", choices=("rotate", "remove", "recover"), required=True
    )
    parser.add_argument("--slot", choices=SECRET_SLOTS, required=True)
    parser.add_argument("--new-secret-file", type=Path)
    parser.add_argument("--capability-workspace", type=Path, required=True)
    parser.add_argument("--secret-workspace", type=Path, required=True)
    parser.add_argument("--evidence-workspace", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if (arguments.operation == "rotate") != (
        arguments.new_secret_file is not None
    ):
        _output(
            {
                "contract": "EvaInstallerSecretLifecycleCliResult/v1",
                "error": "secret lifecycle input is invalid",
                "secret_returned": False,
                "status": "failed",
            }
        )
        return 2
    secret_buffer: bytearray | None = None
    try:
        lifecycle = InstallerSecretLifecycle(
            capability_workflow=InstallerCapabilityWorkflow(
                workspace=arguments.capability_workspace
            ),
            secret_store=InstallerSecretStore(workspace=arguments.secret_workspace),
            evidence_workspace=arguments.evidence_workspace,
        )
        if arguments.operation == "rotate":
            assert arguments.new_secret_file is not None
            secret_buffer = _read_private_secret(arguments.new_secret_file)
            result = lifecycle.rotate(
                slot_id=arguments.slot,
                secret=secret_buffer,
            )
        elif arguments.operation == "remove":
            result = lifecycle.remove(slot_id=arguments.slot)
        else:
            result = lifecycle.recover(slot_id=arguments.slot)
    except (
        InstallerCapabilityWorkflowError,
        InstallerSecretLifecycleError,
        InstallerSecretStoreError,
        OSError,
        ValueError,
    ):
        _output(
            {
                "contract": "EvaInstallerSecretLifecycleCliResult/v1",
                "error": "secret lifecycle operation could not be completed",
                "secret_returned": False,
                "status": "failed",
            }
        )
        return 2
    finally:
        if secret_buffer is not None:
            secret_buffer[:] = b"\x00" * len(secret_buffer)
    _output(
        {
            "capability_revision": result.capability_revision,
            "completion_evidence_sha256": result.completion_evidence_sha256,
            "contract": "EvaInstallerSecretLifecycleCliResult/v1",
            "generation": result.generation,
            "invalidation_evidence_sha256": (
                result.invalidation_evidence_sha256
            ),
            "operation": result.operation,
            "secret_returned": False,
            "slot_id": result.slot_id,
            "state": result.state,
            "status": "passed",
        }
    )
    return 0


def main() -> int:
    return run_installer_secret_lifecycle()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_installer_secret_lifecycle"]
