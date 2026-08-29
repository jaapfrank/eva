"""Command-line launcher for Eva's loopback installer wizard."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path

import uvicorn

from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle
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
from eva.operations.installer_secret_store import (
    InstallerSecretStore,
    InstallerSecretStoreError,
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


def run_installer_wizard(argv: list[str] | None = None) -> int:
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
    try:
        receipt = preflight(load_platform_lock(args.platform_lock), inspect_host())
    except InstallerPreflightError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 2

    identity_lifecycle = None
    profile_workflow = None
    capability_workflow = None
    secret_store = None
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
        if args.secret_workspace is not None:
            secret_store = InstallerSecretStore(
                workspace=_private_workspace(args.secret_workspace)
            )
    except (
        InstallerProfileWorkflowError,
        InstallerCapabilityWorkflowError,
        InstallerSecretStoreError,
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
            identity_lifecycle=identity_lifecycle,
            profile_workflow=profile_workflow,
            capability_workflow=capability_workflow,
            secret_store=secret_store,
        ),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


__all__ = ["run_installer_wizard"]
