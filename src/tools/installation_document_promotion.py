"""Promote verified retained document targets; never release or start the core.

Old read-only containers are stopped and disconnected, never deleted. Any
partial promotion is retained for operator inspection without automatic retry.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from tools.installation_document_catalog import canonical_document_catalog

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_document_connection import (
    _command, _input_mounts, _inspect_network, _verify_running, _provider_command,
    _require_free_alias, _CATALOG, _VERIFY_MATERIAL, _validate_material_report,
    validate_document_connection_configuration,
)
from tools.installation_document_restore import validate_document_configuration
from tools.installation_recovery_package import read_installation_package


class DocumentProviderPromotionError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("document promotion stopped; retain old and new targets, do not retry")


def validate_document_promotion_configuration(configuration, *, connection_configuration,
                                               preparation_configuration):
    validate_document_configuration(preparation_configuration)
    validate_document_connection_configuration(connection_configuration)
    if (not isinstance(configuration, dict) or set(configuration) != {"providers"}
            or not isinstance(configuration["providers"], list)
            or not 1 <= len(configuration["providers"]) <= 16):
        raise ValueError("document promotion configuration differs")
    expected = set(range(len(preparation_configuration["providers"])))
    if {item["preparation_index"] for item in connection_configuration["providers"]} != expected:
        raise ValueError("document connection coverage differs")
    names = {item["container_name"] for item in connection_configuration["providers"]}
    indices = set()
    for item in configuration["providers"]:
        if not isinstance(item, dict) or set(item) != {"preparation_index", "container_name"}:
            raise ValueError("document promotion fields differ")
        index, name = item["preparation_index"], item["container_name"]
        if type(index) is not int or index in indices or index not in expected:
            raise ValueError("document promotion preparation index differs")
        if (not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", name)
                or name in names):
            raise ValueError("document promotion needs distinct new target names")
        indices.add(index); names.add(name)
    if indices != expected:
        raise ValueError("document promotion requires complete provider coverage")


def _verify_stopped(container_id, config, preparation, *, disconnected=False):
    rows = json.loads(_command(["docker", "inspect", container_id]))
    if len(rows) != 1:
        raise ValueError("old document target disappeared")
    row = rows[0]; state = row.get("State", {})
    networks = row.get("NetworkSettings", {}).get("Networks", {})
    if (row.get("Id") != container_id or row.get("Name") != "/" + config["container_name"]
            or row.get("Image") != preparation["image"] or state.get("Running") is not False
            or state.get("Status") != "exited" or state.get("Pid") != 0
            or state.get("Paused") or state.get("Restarting")
            or row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") != "no"
            or (disconnected and networks)
            or (not disconnected and (len(networks) != 1
                or next(iter(networks.values())).get("NetworkID") != config["network_id"]))):
        raise ValueError("old document target was not retained stopped as expected")


def promote_document_providers(package_root, *, preparation_configuration,
                               preparation_receipt, connection_configuration,
                               connection_receipt, gateway_receipt, configuration, guard):
    receipt = {"contract": "EvaDocumentWritePromotion/v1", "status": "not_started",
               "providers": [], "providers_verified": False, "services_started": False,
               "core_activated": False, "tool_calls_executed": False}
    try:
        validate_document_promotion_configuration(configuration,
            connection_configuration=connection_configuration, preparation_configuration=preparation_configuration)
        if not callable(guard):
            raise ValueError("document promotion requires a current restore guard")
        guard()
        root = Path(package_root).resolve(); package = read_installation_package(root)
        archive = package["archive_sha256"]; receipt["archive_sha256"] = archive
        preparations = preparation_configuration["providers"]
        prepared = preparation_receipt.get("providers", [])
        old_receipts = connection_receipt.get("providers", [])
        if (preparation_receipt.get("contract") != "EvaDocumentRelocationPreparation/v1"
                or preparation_receipt.get("status") != "prepared_not_connected"
                or preparation_receipt.get("archive_sha256") != archive
                or len(prepared) != len(preparations)
                or connection_receipt.get("contract") != "EvaDocumentConnection/v1"
                or connection_receipt.get("status") != "connected_read_only"
                or connection_receipt.get("archive_sha256") != archive
                or connection_receipt.get("documents_verified") is not True
                or connection_receipt.get("provider_verified") is not True
                or connection_receipt.get("gateway_binding", {}).get("aliases_attached") is not True
                or len(old_receipts) != len(preparations)
                or gateway_receipt.get("contract") != "EvaRestoredGatewayStart/v1"
                or gateway_receipt.get("status") != "gateway_started_verified"
                or gateway_receipt.get("archive_sha256") != archive
                or gateway_receipt.get("gateway_started") is not True
                or gateway_receipt.get("authenticated_inventory_verified") is not True
                or any(value.get("core_activated") is not False for value in (connection_receipt, gateway_receipt))):
            raise ValueError("document promotion needs complete verified preparation, connection and gateway receipts")
        configs = {item["preparation_index"]: item for item in connection_configuration["providers"]}
        existing = set(_command(["docker", "ps", "--all", "--format", "{{.Names}}"]).splitlines())
        plans = []; old_ids = set(); aliases = set()
        for target in configuration["providers"]:
            index = target["preparation_index"]; config = configs[index]; prep = preparations[index]
            found = [item for item in old_receipts if item.get("container_name") == config["container_name"]]
            if len(found) != 1 or target["container_name"] in existing:
                raise ValueError("document promotion source or new target identity differs")
            old = found[0]; identifier = old.get("container_id"); alias = old.get("gateway_alias")
            if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier)
                    or identifier in old_ids or old.get("status") != "connected_read_only"
                    or old.get("documents_verified") is not True or old.get("provider_verified") is not True
                    or old.get("image_id") != prep["image"] or not isinstance(alias, str)
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", alias)
                    or alias in aliases):
                raise ValueError("document promotion source verification differs")
            if any(item["preparation_index"] != index and item["container_name"].casefold() == alias
                   for item in configuration["providers"]):
                raise ValueError("new document target name conflicts with another gateway alias")
            old_ids.add(identifier); aliases.add(alias)
            mounts = _input_mounts(root, package, prep, prepared[index], config)
            _inspect_network(config["network_id"])
            _verify_running(identifier, config, prep, mounts, alias=alias)
            if target["container_name"].casefold() != alias:
                _require_free_alias(config["network_id"], target["container_name"].casefold())
            plans.append((target, config, prep, mounts, old))
        for target, config, prep, mounts, old in plans:
            index = target["preparation_index"]; identifier = old["container_id"]; alias = old["gateway_alias"]
            guard()
            if read_installation_package(root)["archive_sha256"] != archive:
                raise ValueError("document promotion source package changed")
            if _input_mounts(root, package, prep, prepared[index], config) != mounts:
                raise ValueError("document promotion mounts changed")
            _verify_running(identifier, config, prep, mounts, alias=alias)
            item = {"preparation_index": index, "old_container_id": identifier,
                    "old_container_name": config["container_name"], "container_name": target["container_name"],
                    "image_id": prep["image"], "gateway_alias": alias, "status": "stop_attempted"}
            receipt["providers"].append(item)
            guard(); _command(["docker", "stop", "--time", "30", identifier], deadline=40, limit=4096); guard()
            _verify_stopped(identifier, config, prep)
            item["status"] = "disconnect_attempted"
            guard(); _command(["docker", "network", "disconnect", config["network_id"], identifier], limit=4096); guard()
            _verify_stopped(identifier, config, prep, disconnected=True)
            _inspect_network(config["network_id"])
            for name in {alias, target["container_name"].casefold()}:
                _require_free_alias(config["network_id"], name)
            replacement = {**config, "container_name": target["container_name"]}
            item["status"] = "start_attempted"; receipt["services_started"] = None
            guard()
            new_id = _command(_provider_command(replacement, prep, mounts, alias=alias, writable=True),
                              deadline=30, limit=4096).strip()
            item["container_id"] = new_id
            guard()
            if not re.fullmatch(r"[a-f0-9]{64}", new_id) or new_id == identifier:
                raise ValueError("writable target acknowledgement differs")
            _verify_running(new_id, replacement, prep, mounts, alias=alias, writable=True)
            receipt["services_started"] = True
            catalog = json.loads(_command(["docker", "exec", new_id, "python3", "-c", _CATALOG], deadline=40))
            catalog = canonical_document_catalog(catalog, prep)
            if canonical_sha256(catalog) != prep["logical_catalog_sha256"]:
                raise ValueError("writable document provider catalog differs")
            material = json.loads(_command(["docker", "exec", new_id, "node", "--input-type=module",
                "-e", _VERIFY_MATERIAL], deadline=20 + 10 * len(prep["project_keys"])))
            _validate_material_report(material, prep, prepared[index])
            guard(); _inspect_network(config["network_id"])
            _verify_running(new_id, replacement, prep, mounts, alias=alias, writable=True)
            _verify_stopped(identifier, config, prep, disconnected=True)
            item.update(status="writable_verified", provider_verified=True, documents_verified=True,
                        catalog_sha256=canonical_sha256(catalog), material_verification=material)
        receipt.update(status="writable_verified", providers_verified=True, services_started=True,
                       verification_scope="Writable mount readback, unchanged MCP catalog and native retained material/history; no write request, completed task or core activation")
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise DocumentProviderPromotionError(receipt) from error
