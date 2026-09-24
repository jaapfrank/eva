"""Operator launcher: fixed private target configuration, existing wizard bootstrap."""

import argparse
import json
from functools import partial
from pathlib import Path

from sqlalchemy import create_engine
from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from eva.bootstrap.installer_wizard_cli import run_installer_wizard
from eva.operations.portable_restore_authority import read_private_restore_authority
from tools.installer_portable_restore import InstallerPortableRestore
from tools.object_keyring import FileObjectKeyResolver
from tools.restore_runtime_guard import verify_restore_runtime


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-config", type=Path, required=True)
    args, remaining = parser.parse_known_args(argv)
    config = json.loads(
        read_private_runtime_file_bytes(
            args.restore_config, setting_name="installer restore target"
        )
    )
    required = {"dsn", "authority", "workspace", "objects", "keyring", "traffic_closed", "runtime_guard"}
    if (not isinstance(config, dict) or not required <= set(config)
            or not set(config) <= required | {"runtime_identity", "recovery_materialization", "external_recovery", "document_recovery", "document_connection", "document_gateway_binding"}
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
    paths = {key: Path(config[key]) for key in ("authority", "workspace", "objects")}
    if not all(path.is_absolute() for path in paths.values()):
        raise ValueError("restore target paths must be absolute")
    keyring_path = Path(config["keyring"]) if config["keyring"] else None
    if keyring_path is not None and not keyring_path.is_absolute():
        raise ValueError("object keyring path must be absolute")
    resolver = FileObjectKeyResolver(keyring_path) if keyring_path else None
    engine = create_engine(config["dsn"])
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
    )
    try:
        return run_installer_wizard(remaining, restore_workflow=workflow)
    finally:
        workflow.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
