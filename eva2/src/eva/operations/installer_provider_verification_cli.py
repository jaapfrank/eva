"""Operator CLI for read-only installer provider verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from eva.adapters.connectors.installer_codex_model_probe import (
    CodexInstallerModelProbe,
)
from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.adapters.connectors.installer_home_assistant_probe import (
    HomeAssistantInstallerReadOnlyProbe,
)
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


def _default_probe(
    slot_id: str,
    home_assistant_url: str | None,
    agentmail_inbox_id: str | None,
) -> InstallerProviderProbe:
    if (
        slot_id == "model_provider"
        and home_assistant_url is None
        and agentmail_inbox_id is None
    ):
        return CodexInstallerModelProbe()
    if (
        slot_id == "telegram"
        and home_assistant_url is None
        and agentmail_inbox_id is None
    ):
        return TelegramInstallerReadOnlyProbe()
    if (
        slot_id == "home_assistant"
        and home_assistant_url is not None
        and agentmail_inbox_id is None
    ):
        return HomeAssistantInstallerReadOnlyProbe(base_url=home_assistant_url)
    if (
        slot_id == "email"
        and home_assistant_url is None
        and agentmail_inbox_id is not None
    ):
        return AgentMailInstallerReadOnlyProbe(inbox_id=agentmail_inbox_id)
    raise ValueError("provider verification configuration is invalid")


def run_installer_provider_verification(
    argv: list[str] | None = None,
    *,
    probes: Mapping[str, InstallerProviderProbe] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="eva-installer-verify")
    parser.add_argument(
        "--slot",
        choices=("model_provider", "telegram", "home_assistant", "email"),
        required=True,
    )
    parser.add_argument("--home-assistant-url")
    parser.add_argument("--agentmail-inbox-id")
    parser.add_argument("--capability-workspace", type=Path, required=True)
    parser.add_argument("--secret-workspace", type=Path, required=True)
    parser.add_argument("--evidence-workspace", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    arguments = parser.parse_args(argv)
    try:
        selected_probes: Mapping[str, InstallerProviderProbe] = (
            {
                arguments.slot: _default_probe(
                    arguments.slot,
                    arguments.home_assistant_url,
                    arguments.agentmail_inbox_id,
                )
            }
            if probes is None
            else probes
        )
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
        ValueError,
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
            "provider_scope_manifest_sha256": (
                result.provider_scope_manifest_sha256
            ),
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
