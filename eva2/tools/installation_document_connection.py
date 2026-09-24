"""Connect prepared document providers as isolated read-only verification targets.

This does not register a gateway, enable writes, release the core restore hold,
or resume document projects. Failed targets remain for operator inspection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_evaluation import _capture
from tools.installation_document_restore import validate_document_configuration, _validate_sources
from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular

_FIELDS = {"preparation_index", "container_name", "network_id", "jwt_secret_file",
           "encryption_secret_file", "admin_password_file"}
_SECRET_FILES = (("jwt_secret_file", "jwt", 32), ("encryption_secret_file", "encryption", 32),
                 ("admin_password_file", "admin", 16))
_ENTRYPOINT = "/opt/eva2/atomic/start-atomic-workspace.sh"


class DocumentProviderConnectionError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("document connection stopped; retain targets for inspection, do not retry")


def _absolute(value):
    if (not isinstance(value, str) or not value or len(value) > 1024
            or any(c in value for c in (",", "\\", "\x00"))
            or not Path(value).is_absolute() or str(Path(value)) != value
            or Path(value).resolve() != Path(value)):
        raise ValueError("document connection path must be absolute without links")


def validate_document_connection_configuration(configuration):
    if (not isinstance(configuration, dict) or set(configuration) != {"providers"}
            or not isinstance(configuration["providers"], list)
            or not 1 <= len(configuration["providers"]) <= 16):
        raise ValueError("document connection configuration differs")
    indices, names = set(), set()
    for item in configuration["providers"]:
        if not isinstance(item, dict) or set(item) != _FIELDS:
            raise ValueError("document connection fields differ")
        index = item["preparation_index"]
        if type(index) is not int or not 0 <= index < 16 or index in indices:
            raise ValueError("document connection preparation index differs")
        name = item["container_name"]
        if (not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", name)
                or name in names):
            raise ValueError("document connection needs distinct explicit target names")
        if not isinstance(item["network_id"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["network_id"]):
            raise ValueError("document connection needs a pinned Docker network ID")
        paths = []
        for field, _, _ in _SECRET_FILES:
            _absolute(item[field]); paths.append(item[field])
        if len(set(paths)) != len(paths):
            raise ValueError("document connection secret files must be distinct")
        indices.add(index); names.add(name)


def _command(args, *, deadline=15, limit=1024 * 1024):
    result = _capture(args, deadline=deadline, limit=limit)
    if (result["exit_code"] != 0 or result["timed_out"]
            or result["stdout_truncated"] or result.get("stderr_truncated", False)):
        raise RuntimeError("document connection command did not complete")
    return result["stdout"]


def _inspect_network(identifier):
    rows = json.loads(_command(["docker", "network", "inspect", identifier]))
    if len(rows) != 1 or rows[0].get("Id") != identifier or rows[0].get("Internal") is not True:
        raise ValueError("document connection network is not the pinned internal network")


def _input_mounts(root, package, preparation, receipt, config):
    _validate_sources(root, package, preparation)
    output = Path(preparation["output_directory"])
    if (receipt.get("status") != "prepared_not_connected"
            or receipt.get("image_id") != preparation["image"]
            or receipt.get("output_binding_sha256") != hashlib.sha256(str(output).encode()).hexdigest()):
        raise ValueError("document preparation receipt binding differs")
    filenames = {"binding.json", *(key + ".relocation" for key in preparation["project_keys"])}
    if set(receipt.get("files", {})) != filenames:
        raise ValueError("document preparation receipt inventory differs")
    mounts = {"/workspace": str(root / preparation["document_directory"]),
              "/journal": str(root / preparation["journal_directory"])}
    with _root_descriptor(output) as fd:
        for name in sorted(filenames):
            raw = _read_regular(fd, name)
            info = (output / name).lstat()
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o400
                    or info.st_uid != preparation["runtime_uid"] or info.st_gid != preparation["runtime_gid"]
                    or info.st_nlink != 1
                    or receipt["files"][name] != {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}):
                raise ValueError("prepared document authority bytes or ownership differ")
            target = ("/run/eva2/atomic-binding.json" if name == "binding.json"
                      else "/run/eva2/relocations/" + name)
            mounts[target] = str(output / name)
    archive_hashes = {entry["sha256"] for entry in package["manifest"]["files"].values()}
    for field, target, minimum in _SECRET_FILES:
        path = Path(config[field])
        if path == root or root in path.parents or path == output or output in path.parents:
            raise ValueError("document connection secrets must be separately provisioned outside recovery material")
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o400
                or info.st_uid != preparation["runtime_uid"] or info.st_gid != preparation["runtime_gid"]
                or info.st_nlink != 1 or not minimum <= info.st_size <= 512):
            raise ValueError("document connection secret file ownership or size differs")
        with _root_descriptor(path.parent) as fd:
            raw = _read_regular(fd, path.name)
        if len(raw) != info.st_size or b"\r" in raw or b"\n" in raw or hashlib.sha256(raw).hexdigest() in archive_hashes:
            raise ValueError("document connection requires fresh private secret material")
        mounts["/run/eva2/" + target] = str(path)
    return mounts


_CATALOG = r"""
import asyncio,json,socket,time
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
end=time.monotonic()+20
while True:
    try:
        with socket.create_connection(('127.0.0.1',9000),timeout=.25): pass
        break
    except OSError:
        if time.monotonic()>=end: raise RuntimeError('provider readiness deadline')
        time.sleep(.1)
async def main():
    async with streamablehttp_client('http://127.0.0.1:9000/mcp') as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=[];cursor=None
            for _ in range(16):
                result=await session.list_tools(cursor=cursor)
                tools.extend(tool.model_dump(mode='json',by_alias=True,exclude_none=True,exclude_unset=True) for tool in result.tools)
                cursor=result.nextCursor
                if not cursor: break
            else: raise RuntimeError('catalog pagination exceeded bound')
            print(json.dumps(tools,ensure_ascii=False))
asyncio.run(main())
"""


def _verify_running(container_id, config, preparation, mounts):
    rows = json.loads(_command(["docker", "inspect", container_id]))
    if len(rows) != 1:
        raise ValueError("document target inspect differs")
    actual = rows[0]
    host, runtime = actual["HostConfig"], actual["Config"]
    networks = actual.get("NetworkSettings", {}).get("Networks", {})
    actual_mounts = {item["Destination"]: item for item in actual.get("Mounts", [])}
    # tmpfs is represented separately by Docker and cannot expose host files.
    bound = {key: item for key, item in actual_mounts.items() if item.get("Type") != "tmpfs"}
    if (actual.get("Id") != container_id or actual.get("Name") != "/" + config["container_name"]
            or actual.get("Image") != preparation["image"] or actual.get("State", {}).get("Running") is not True
            or runtime.get("User") != f"{preparation['runtime_uid']}:{preparation['runtime_gid']}"
            or runtime.get("Entrypoint") != [_ENTRYPOINT]
            or not host.get("ReadonlyRootfs") or host.get("Privileged")
            or host.get("PortBindings") or host.get("PublishAllPorts")
            or len(networks) != 1 or next(iter(networks.values())).get("NetworkID") != config["network_id"]
            or set(bound) != set(mounts)):
        raise ValueError("running document target identity or confinement differs")
    for destination, source in mounts.items():
        item = bound[destination]
        if item.get("Type") != "bind" or item.get("Source") != source or item.get("RW") is not False:
            raise ValueError("running document target mount differs")


def connect_document_providers(package_root, *, preparation_configuration,
                               preparation_receipt, configuration, guard):
    receipt = {"contract": "EvaDocumentConnection/v1", "providers": [],
               "provider_verified": False, "documents_verified": False, "services_started": False, "core_activated": False}
    try:
        validate_document_configuration(preparation_configuration)
        validate_document_connection_configuration(configuration)
        if not callable(guard):
            raise ValueError("document connection requires a current restore guard")
        root = Path(package_root).resolve()
        package = read_installation_package(root)
        prepared = preparation_configuration["providers"]
        receipts = preparation_receipt.get("providers", [])
        if (preparation_receipt.get("contract") != "EvaDocumentRelocationPreparation/v1"
                or preparation_receipt.get("status") != "prepared_not_connected"
                or preparation_receipt.get("archive_sha256") != package["archive_sha256"]
                or any(preparation_receipt.get(key) is not False for key in
                       ("connected", "provider_verified", "services_started"))
                or len(receipts) != len(prepared)
                or {item["preparation_index"] for item in configuration["providers"]} != set(range(len(prepared)))):
            raise ValueError("document connection needs complete matching preparation")
        receipt["archive_sha256"] = package["archive_sha256"]
        plans = []
        # Validate every target before starting any of them.
        existing = set(_command(["docker", "ps", "--all", "--format", "{{.Names}}"]).splitlines())
        for config in configuration["providers"]:
            if config["container_name"] in existing:
                raise ValueError("document connection target already exists; inspect it instead of retrying")
            prep = prepared[config["preparation_index"]]
            mounts = _input_mounts(root, package, prep, receipts[config["preparation_index"]], config)
            _inspect_network(config["network_id"])
            images = json.loads(_command(["docker", "image", "inspect", prep["image"]]))
            if len(images) != 1 or images[0].get("Id") != prep["image"]:
                raise ValueError("document connection image pin differs")
            plans.append((config, prep, mounts))
        for config, prep, mounts in plans:
            guard()
            _inspect_network(config["network_id"])
            if read_installation_package(root)["archive_sha256"] != package["archive_sha256"]:
                raise ValueError("document connection source package changed")
            _input_mounts(root, package, prep, receipts[config["preparation_index"]], config)
            item = {"container_name": config["container_name"], "image_id": prep["image"],
                    "status": "connection_attempted", "provider_verified": False}
            receipt["providers"].append(item)
            command = ["docker", "run", "--detach", "--name", config["container_name"], "--pull", "never",
                "--network", config["network_id"], "--read-only", "--restart", "no", "--no-healthcheck",
                "--log-driver", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "64",
                "--user", f"{prep['runtime_uid']}:{prep['runtime_gid']}",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777", "--entrypoint", _ENTRYPOINT]
            for target, source in sorted(mounts.items()):
                command.extend(["--mount", f"type=bind,src={source},dst={target},readonly"])
            for name, target in (("JWT_SECRET", "jwt"), ("ENCRYPTION_SECRET", "encryption"), ("ADMIN_PASSWORD", "admin")):
                command.extend(["--env", f"EVA_CONTEXTFORGE_{name}_FILE=/run/eva2/{target}"])
            command.append(prep["image"])
            receipt["services_start_attempted"] = True
            receipt["services_started"] = None  # A lost start acknowledgement is not proof of absence.
            container_id = _command(command, deadline=30, limit=4096).strip()
            if not re.fullmatch(r"[a-f0-9]{64}", container_id):
                raise ValueError("document target creation acknowledgement differs")
            item["container_id"] = container_id
            guard()
            _verify_running(container_id, config, prep, mounts)
            receipt["services_started"] = True
            observed = json.loads(_command(["docker", "exec", container_id, "python3", "-c", _CATALOG], deadline=40))
            if not isinstance(observed, list) or canonical_sha256(observed) != prep["logical_catalog_sha256"]:
                raise ValueError("running document provider catalog differs from preserved catalog")
            guard()
            _inspect_network(config["network_id"])
            _verify_running(container_id, config, prep, mounts)
            item.update(status="connected_read_only", provider_verified=True,
                        catalog_sha256=canonical_sha256(observed), verification_scope="MCP initialization and tools/list catalog only")
        receipt.update(status="connected_read_only", provider_verified=True, services_started=True)
        receipt["verification_scope"] = "Running image and read-only mounts; SDK initialization and exact catalog. No document observation, writes, resumption or core activation."
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise DocumentProviderConnectionError(receipt) from error
