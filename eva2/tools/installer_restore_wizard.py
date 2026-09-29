"""Operator launcher: fixed private target configuration, existing wizard bootstrap."""

import argparse
import json
import os
from functools import partial
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from eva.bootstrap.installer_wizard_cli import run_installer_wizard
from eva.operations.portable_restore_authority import read_private_restore_authority
from tools.installer_portable_restore import InstallerPortableRestore
from tools.object_keyring import FileObjectKeyResolver
from tools.restore_runtime_guard import verify_restore_runtime


@contextmanager
def configured_restore_workflow(config):
    """Compose the existing restore route from trusted installer-owned settings.

    Browser input must not be passed as target configuration. The installer
    prepares these settings from its approved release and actual target resources.
    """
    required = {"dsn", "authority", "workspace", "objects", "keyring", "traffic_closed", "runtime_guard"}
    if (not isinstance(config, dict) or not required <= set(config)
            or not set(config) <= required | {"runtime_identity", "runtime_identity_restore", "recovery_materialization", "external_recovery", "document_recovery", "document_connection", "document_gateway_binding", "gateway_start", "document_write_access", "activation"}
            or config["traffic_closed"] is not True):
        raise ValueError("restore configuration requires pinned runtime_guard identities and closed traffic")
    if "document_recovery" in config and not {"external_recovery", "recovery_materialization"} <= set(config):
        raise ValueError("document recovery requires external recovery and materialization")
    if "document_connection" in config and "document_recovery" not in config:
        raise ValueError("document connection requires document preparation")
    if "document_gateway_binding" in config:
        if "document_connection" not in config:
            raise ValueError("gateway binding requires document connection")
        if (not isinstance(config["document_gateway_binding"], dict)
                or config["document_gateway_binding"].get("target_path") != config["external_recovery"]["gateway"]["target_path"]):
            raise ValueError("document gateway binding must use the restored gateway target")
    if "gateway_start" in config and "document_gateway_binding" not in config:
        raise ValueError("gateway start requires preserved document gateway bindings")
    if "document_write_access" in config and "gateway_start" not in config:
        raise ValueError("document write access requires gateway startup")
    if "activation" in config:
        activation = config["activation"]
        if not {"document_write_access", "runtime_identity"} <= set(config):
            raise ValueError("activation requires complete document promotion and runtime identity")
        if (not isinstance(activation, dict)
                or not {"native_authorization", "memory_api"} <= set(activation)
                or not set(activation) <= {"native_authorization", "memory_api", "startup_timeout_seconds"}):
            raise ValueError("activation configuration differs")
        timeout = activation.get("startup_timeout_seconds", 60)
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError("activation startup timeout is invalid")
    paths = {key: Path(config[key]) for key in ("authority", "workspace", "objects")}
    if not all(path.is_absolute() for path in paths.values()):
        raise ValueError("restore target paths must be absolute")
    keyring_path = Path(config["keyring"]) if config["keyring"] else None
    if keyring_path is not None and not keyring_path.is_absolute():
        raise ValueError("object keyring path must be absolute")
    resolver = FileObjectKeyResolver(keyring_path) if keyring_path else None
    identity_trust = identity_destination = identity_runtime_owner = None
    if "runtime_identity_restore" in config:
        from eva.application.identity_verifier import parse_identity_pack_trust_binding
        selected = config["runtime_identity_restore"]
        if (not isinstance(selected, dict) or not {"trust", "destination"} <= set(selected)
                or set(selected) - {"trust", "destination", "runtime_owner"}
                or any(not isinstance(value, str) or not Path(value).is_absolute()
                       for key, value in selected.items() if key != "runtime_owner")):
            raise ValueError("runtime identity restore requires absolute trusted paths")
        trust_path = Path(selected["trust"])
        identity_trust = parse_identity_pack_trust_binding(read_private_runtime_file_bytes(
            trust_path, setting_name="runtime identity restore trust", effective_uid=trust_path.stat().st_uid))
        identity_destination = Path(selected["destination"])
        identity_runtime_owner = selected.get("runtime_owner")
    engine = create_engine(config["dsn"])
    workflow = None
    try:
        external_restore = None
        if "external_recovery" in config:
            from tools.installation_external_restore import restore_external_components, validate_external_configuration
            validate_external_configuration(config["external_recovery"])
            external_restore = partial(restore_external_components, configuration=config["external_recovery"])
        document_prepare = None
        if "document_recovery" in config:
            from tools.installation_document_restore import prepare_document_relocations, validate_document_configuration
            validate_document_configuration(config["document_recovery"])
            document_prepare = partial(prepare_document_relocations, configuration=config["document_recovery"])
        document_connect = None
        if "document_connection" in config:
            from tools.installation_document_connection import connect_document_providers, validate_document_connection_configuration
            validate_document_connection_configuration(config["document_connection"])
            if "document_gateway_binding" in config:
                from tools.installation_gateway_binding import validate_gateway_binding_configuration
                validate_gateway_binding_configuration(config["document_gateway_binding"],
                    connections=config["document_connection"], preparations=config["document_recovery"])
            document_connect = partial(connect_document_providers,
                preparation_configuration=config["document_recovery"], configuration=config["document_connection"],
                gateway_binding=config.get("document_gateway_binding"))
        gateway_start = None
        if "gateway_start" in config:
            from tools.installation_gateway_start import start_restored_gateway, validate_gateway_start_configuration
            validate_gateway_start_configuration(config["gateway_start"],
                external_configuration=config["external_recovery"],
                binding_configuration=config["document_gateway_binding"])
            gateway_id = config["gateway_start"]["container"]["id"]
            if any(worker["id"] == gateway_id for side in ("source", "target")
                   for worker in config["runtime_guard"][side]["workers"]):
                raise ValueError("gateway start cannot select a core or task worker")
            gateway_start = partial(start_restored_gateway, configuration=config["gateway_start"],
                external_configuration=config["external_recovery"],
                binding_configuration=config["document_gateway_binding"])
        document_promote = None
        if "document_write_access" in config:
            from tools.installation_document_promotion import promote_document_providers, validate_document_promotion_configuration
            validate_document_promotion_configuration(config["document_write_access"],
                connection_configuration=config["document_connection"],
                preparation_configuration=config["document_recovery"])
            document_promote = partial(promote_document_providers,
                preparation_configuration=config["document_recovery"],
                connection_configuration=config["document_connection"],
                configuration=config["document_write_access"])
        verify_components = start_runtime = None
        if "activation" in config:
            from tools.installation_restore_verification import (
                verify_restored_installation, validate_restore_verification_configuration,
            )
            from tools.installation_runtime_start import start_restored_runtime
            verification = {
                "runtime": config["runtime_guard"], "external": config["external_recovery"],
                "gateway": config["gateway_start"], "binding": config["document_gateway_binding"],
                "document_preparation": config["document_recovery"],
                "document_connection": config["document_connection"],
                "document_promotion": config["document_write_access"],
                "native_authorization": config["activation"]["native_authorization"],
                "memory_api": config["activation"]["memory_api"],
            }
            validate_restore_verification_configuration(verification)
            verify_components = partial(verify_restored_installation, configuration=verification)
            start_runtime = partial(start_restored_runtime, engine,
                expected=config["runtime_guard"],
                timeout_seconds=config["activation"].get("startup_timeout_seconds", 60))
        workflow = InstallerPortableRestore(
            engine=engine,
            authority_reader=partial(read_private_restore_authority, paths["authority"]),
            workspace=paths["workspace"],
            object_store_root=paths["objects"],
            key_resolver=resolver,
            runtime_identity=config.get("runtime_identity"),
            runtime_guard=partial(verify_restore_runtime, expected=config["runtime_guard"]),
            recovery_materialization=config.get("recovery_materialization"),
            external_restore=external_restore,
            document_prepare=document_prepare,
            document_connect=document_connect,
            gateway_start=gateway_start,
            document_promote=document_promote,
            verify_components=verify_components,
            start_runtime=start_runtime,
            identity_trust=identity_trust,
            identity_destination=identity_destination,
            identity_runtime_owner=identity_runtime_owner,
        )
        yield workflow
    finally:
        try:
            if workflow is not None:
                workflow.close()
        finally:
            engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-config", type=Path)
    parser.add_argument("--update-config", type=Path)
    parser.add_argument("--workspace", type=Path)
    args, remaining = parser.parse_known_args(argv)
    update_workflow = None
    if args.update_config is not None:
        from tools.installation_runtime_update import InstallerRuntimeUpdate
        update_workflow = InstallerRuntimeUpdate(json.loads(read_private_runtime_file_bytes(
            args.update_config, setting_name="installer prepared update")))
    if args.restore_config is None:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        workspace = args.workspace or data_home / "eva2/installer"
        return run_installer_wizard([*remaining, "--workspace", str(workspace)], update_workflow=update_workflow)
    if args.workspace is not None:
        remaining.extend(["--workspace", str(args.workspace)])
    config = json.loads(
        read_private_runtime_file_bytes(
            args.restore_config, setting_name="installer restore target"
        )
    )
    with configured_restore_workflow(config) as workflow:
        return run_installer_wizard(remaining, restore_workflow=workflow, update_workflow=update_workflow)


if __name__ == "__main__":
    raise SystemExit(main())
