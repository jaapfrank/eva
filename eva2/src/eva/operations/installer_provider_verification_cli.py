"""Operator CLI for read-only installer provider verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderProbe,
    InstallerProviderVerificationError,
    InstallerProviderVerifier,
)
from eva.operations.installer_secret_store import (
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


def run_installer_provider_verification(
    argv: list[str] | None = None,
    *,
    probes: Mapping[str, InstallerProviderProbe] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="eva-installer-verify")
    parser.add_argument("--slot", choices=("telegram",), required=True)
    parser.add_argument("--capability-workspace", type=Path, required=True)
    parser.add_argument("--secret-workspace", type=Path, required=True)
    parser.add_argument("--evidence-workspace", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    arguments = parser.parse_args(argv)
    selected_probes: Mapping[str, InstallerProviderProbe] = (
        {"telegram": TelegramInstallerReadOnlyProbe()}
        if probes is None
        else probes
    )
    try:
        verifier = InstallerProviderVerifier(
            capability_workflow=InstallerCapabilityWorkflow(
                workspace=arguments.capability_workspace
            ),
            secret_store=InstallerSecretStore(workspace=arguments.secret_workspace),
            evidence_workspace=arguments.evidence_workspace,
            probes=selected_probes,
        )
        result = verifier.verify(
            slot_id=arguments.slot,
            timeout_seconds=arguments.timeout_seconds,
        )
    except (
        InstallerCapabilityWorkflowError,
        InstallerProviderVerificationError,
        InstallerSecretStoreError,
        OSError,
    ):
        _output(
            {
                "contract": "EvaInstallerProviderVerificationCliResult/v1",
                "error": "provider verification could not be completed",
                "provider_payload_retained": False,
                "secret_returned": False,
                "status": "failed",
            }
        )
        return 2
    _output(
        {
            "capability_revision": result.capability_revision,
            "contract": "EvaInstallerProviderVerificationCliResult/v1",
            "evidence_sha256": result.evidence_sha256,
            "outcome": result.outcome,
            "provider_payload_retained": False,
            "reason_code": result.reason_code,
            "secret_returned": False,
            "slot_id": result.slot_id,
            "status": "passed" if result.outcome == "ready" else "blocked",
        }
    )
    return 0 if result.outcome == "ready" else 2


def main() -> int:
    return run_installer_provider_verification()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_installer_provider_verification"]
