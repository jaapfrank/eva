"""Composition root for Eva's loopback installer wizard."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Callable

import uvicorn

from eva.application.installer_restore import InstallerRestorePort

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.adapters.connectors.installer_codex_model_probe import CodexInstallerModelProbe
from eva.adapters.connectors.installer_configured_probe_factory import (
    configured_installer_probe,
)
from eva.adapters.connectors.installer_telegram_probe import (
    TelegramInstallerReadOnlyProbe,
)
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_budget_workflow import (
    InstallerBudgetWorkflow,
    InstallerBudgetWorkflowError,
)
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle
from eva.operations.installer_installation_plan import (
    build_installer_installation_plan,
)
from eva.operations.installer_preflight import (
    InstallerPreflightError,
    inspect_host,
    load_platform_lock,
    preflight,
)
from eva.operations.installer_profile_workflow import (
    InstallerProfileWorkflow,
    InstallerProfileWorkflowError,
)
from eva.operations.installer_provider_configuration import (
    InstallerProviderConfigurationError,
    InstallerProviderConfigurationStore,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderVerificationError,
    InstallerProviderVerifier,
)
from eva.operations.installer_secret_store import (
    InstallerSecretStore,
    InstallerSecretStoreError,
)
from eva.operations.installer_secret_lifecycle import (
    InstallerSecretLifecycle,
    InstallerSecretLifecycleError,
)


def _loopback_host(value: str) -> str:
    try:
        if not ipaddress.ip_address(value).is_loopback:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "installer bind address must be loopback"
        ) from error
    return value


def _private_workspace(path: Path) -> Path:
    if not path.exists():
        path.mkdir(mode=0o700)
    return path


def run_installer_wizard(
    argv: list[str] | None = None, *, restore_workflow: InstallerRestorePort | None = None,
    installation_preparer: Callable[[dict[str, object]], None] | None = None,
    installation_preparation_status: Callable[[], str] | None = None,
) -> int:
    from eva.adapters.api.installer_wizard import (
        InstallerBootstrap,
        create_installer_wizard,
    )

    parser = argparse.ArgumentParser(prog="eva-installer")
    parser.add_argument(
        "--platform-lock", type=Path, default=Path("release/platform-lock.json")
    )
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--identity-current", type=Path)
    parser.add_argument("--identity-trust", type=Path)
    parser.add_argument("--identity-private-key", type=Path)
    parser.add_argument("--identity-workspace", type=Path)
    parser.add_argument("--profile-workspace", type=Path)
    parser.add_argument("--capability-workspace", type=Path)
    parser.add_argument("--secret-workspace", type=Path)
    parser.add_argument("--budget-workspace", type=Path)
    parser.add_argument("--provider-evidence-workspace", type=Path)
    parser.add_argument("--provider-configuration-workspace", type=Path)
    parser.add_argument("--home-assistant-url")
    parser.add_argument("--agentmail-inbox-id")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("installer port must be between 1024 and 65535")
    identity_paths = (
        args.identity_current,
        args.identity_trust,
        args.identity_private_key,
        args.identity_workspace,
    )
    if any(path is not None for path in identity_paths) and not all(
        path is not None for path in identity_paths
    ):
        parser.error("all four identity lifecycle paths must be supplied together")
    if args.provider_evidence_workspace is not None and (
        args.capability_workspace is None or args.secret_workspace is None
    ):
        parser.error(
            "provider verification requires capability and secret workspaces"
        )
    if args.provider_configuration_workspace is not None and (
        args.provider_evidence_workspace is None
    ):
        parser.error("provider configuration requires provider verification")
    if (args.home_assistant_url is not None or args.agentmail_inbox_id is not None) and (
        args.provider_configuration_workspace is None
    ):
        parser.error(
            "provider endpoint input requires a provider configuration workspace"
        )
    if args.budget_workspace is not None and args.capability_workspace is None:
        parser.error("budget configuration requires a capability workspace")
    try:
        receipt = preflight(load_platform_lock(args.platform_lock), inspect_host())
    except InstallerPreflightError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 2

    identity_lifecycle = None
    profile_workflow = None
    capability_workflow = None
    secret_store = None
    provider_verifier = None
    provider_configuration_store = None
    secret_lifecycle = None
    budget_workflow = None
    if all(path is not None for path in identity_paths):
        assert args.identity_workspace is not None
        try:
            _private_workspace(args.identity_workspace)
            identity_lifecycle = InstallerIdentityLifecycle(
                current_path=args.identity_current,
                trust_path=args.identity_trust,
                private_key_path=args.identity_private_key,
                workspace=args.identity_workspace,
            )
        except (IdentityPackLifecycleError, OSError):
            print(
                json.dumps(
                    {"status": "failed", "error": "identity authority is invalid"},
                    sort_keys=True,
                )
            )
            return 2
    try:
        if args.profile_workspace is not None:
            profile_workflow = InstallerProfileWorkflow(
                workspace=_private_workspace(args.profile_workspace)
            )
        if args.capability_workspace is not None:
            capability_workflow = InstallerCapabilityWorkflow(
                workspace=_private_workspace(args.capability_workspace)
            )
        if args.budget_workspace is not None:
            assert capability_workflow is not None
            budget_workflow = InstallerBudgetWorkflow(
                workspace=_private_workspace(args.budget_workspace),
                capability_workflow=capability_workflow,
            )
        if args.secret_workspace is not None:
            secret_store = InstallerSecretStore(
                workspace=_private_workspace(args.secret_workspace)
            )
        if args.provider_evidence_workspace is not None:
            assert capability_workflow is not None
            assert secret_store is not None
            probes = {
                "model_provider": CodexInstallerModelProbe(),
                "telegram": TelegramInstallerReadOnlyProbe(),
            }
            provider_verifier = InstallerProviderVerifier(
                capability_workflow=capability_workflow,
                secret_store=secret_store,
                evidence_workspace=_private_workspace(
                    args.provider_evidence_workspace
                ),
                probes=probes,
            )
            secret_lifecycle = InstallerSecretLifecycle(
                capability_workflow=capability_workflow,
                secret_store=secret_store,
                evidence_workspace=_private_workspace(
                    args.provider_evidence_workspace
                ),
            )
        if args.provider_configuration_workspace is not None:
            provider_configuration_store = InstallerProviderConfigurationStore(
                workspace=_private_workspace(args.provider_configuration_workspace)
            )
            if args.home_assistant_url is not None:
                configured_installer_probe(
                    "home_assistant", args.home_assistant_url
                )
                provider_configuration_store.configure(
                    slot_id="home_assistant", value=args.home_assistant_url
                )
            if args.agentmail_inbox_id is not None:
                configured_installer_probe("email", args.agentmail_inbox_id)
                provider_configuration_store.configure(
                    slot_id="email", value=args.agentmail_inbox_id
                )
    except (
        InstallerProfileWorkflowError,
        InstallerCapabilityWorkflowError,
        InstallerBudgetWorkflowError,
        InstallerSecretStoreError,
        InstallerProviderVerificationError,
        InstallerProviderConfigurationError,
        InstallerSecretLifecycleError,
        ValueError,
        OSError,
    ):
        print(
            json.dumps(
                {"status": "failed", "error": "installer workspace is invalid"},
                sort_keys=True,
            )
        )
        return 2
    bootstrap = InstallerBootstrap.create()
    print(f"Open lokaal: http://{args.host}:{args.port}/bootstrap#{bootstrap.token}")
    uvicorn.run(
        create_installer_wizard(
            receipt,
            bootstrap,
            restore_workflow=restore_workflow,
            identity=(
                load_current_identity_pack() if identity_lifecycle is None else None
            ),
            identity_lifecycle=identity_lifecycle,
            profile_workflow=profile_workflow,
            capability_workflow=capability_workflow,
            secret_store=secret_store,
            provider_verifier=provider_verifier,
            provider_configuration_store=provider_configuration_store,
            configured_probe_factory=configured_installer_probe,
            secret_lifecycle=secret_lifecycle,
            budget_workflow=budget_workflow,
            installation_plan_builder=build_installer_installation_plan,
            installation_preparer=installation_preparer,
            installation_preparation_status=installation_preparation_status,
        ),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


__all__ = ["run_installer_wizard"]
