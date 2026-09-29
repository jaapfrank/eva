"""Start an exact precreated restored gateway and verify authenticated inventories.

No gateway registration, tool execution, document resumption or core activation.
Failed started targets remain for inspection; no retry, stop or deletion occurs.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_external_restore import _pin
from tools.installation_document_connection import _command, _inspect_network
from tools.installation_gateway_binding import _private_target_bytes
from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular
from tools.portable_transport import MAX_ARCHIVE_BYTES

_SCRIPT = "/run/eva2/start-contextforge.sh"
_SECRET_ENV = ("EVA_CONTEXTFORGE_JWT_SECRET_FILE", "EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE", "EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE")


class RestoredGatewayStartError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("restored gateway start not confirmed; retain target for inspection, do not retry")


def validate_gateway_start_configuration(configuration, *, external_configuration, binding_configuration):
    fields = {"container", "network_id", "access_token_path"}
    if not isinstance(configuration, dict) or set(configuration) not in (fields, fields | {"egress_network_id"}):
        raise ValueError("restored gateway start configuration differs")
    _pin(configuration["container"])
    if configuration["container"] not in external_configuration.get("writers", []):
        raise ValueError("gateway target must be one exact previously stopped external writer")
    if not isinstance(configuration["network_id"], str) or not re.fullmatch(r"[a-f0-9]{64}", configuration["network_id"]):
        raise ValueError("gateway target network must be pinned")
    if "egress_network_id" in configuration:
        egress = configuration["egress_network_id"]
        if (not isinstance(egress, str) or not re.fullmatch(r"[a-f0-9]{64}", egress)
                or egress == configuration["network_id"]):
            raise ValueError("gateway egress must be a separate explicitly pinned network")
    token = configuration["access_token_path"]
    if (not isinstance(token, str) or not token.startswith("/run/") or len(token) > 1024
            or str(PurePosixPath(token)) != token or ".." in PurePosixPath(token).parts
            or any(c in token for c in ("\x00", "\\"))):
        raise ValueError("gateway access token must be a private mounted file under /run")
    if (not isinstance(binding_configuration, dict)
            or binding_configuration.get("target_path") != external_configuration.get("gateway", {}).get("target_path")):
        raise ValueError("gateway start must use the bound restored database")


def _host_file(container_path, mounts, *, readonly):
    selected = [m for m in mounts if container_path == m.get("Destination")
                or container_path.startswith(m.get("Destination", "") + "/")]
    if not selected:
        raise ValueError("gateway file is not explicitly mounted")
    mount = max(selected, key=lambda m: len(m["Destination"]))
    if mount.get("Type") != "bind" or mount.get("RW") is not (not readonly):
        raise ValueError("gateway mount mode differs")
    source = Path(mount["Source"] + container_path[len(mount["Destination"]):])
    if not source.is_absolute() or source.resolve() != source:
        raise ValueError("gateway mount source contains links")
    return source, mount


def _private_file(path, *, minimum, maximum):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o400 or not minimum <= info.st_size <= maximum):
        raise ValueError("gateway credential must be a private operator-owned file")
    with _root_descriptor(path.parent) as fd:
        raw = _read_regular(fd, path.name, maximum=maximum)
    if b"\r" in raw or b"\n" in raw:
        raise ValueError("gateway credential contains a line break")


def _configured_network(row, network_id):
    """Resolve a never-started Docker network without inventing an endpoint."""
    networks = row.get("NetworkSettings", {}).get("Networks", {})
    if len(networks) != 1:
        raise ValueError("restore target network count differs")
    name, endpoint = next(iter(networks.items()))
    if endpoint.get("NetworkID") == network_id:
        return endpoint
    state = row.get("State", {})
    if (endpoint.get("NetworkID") != "" or endpoint.get("EndpointID") not in (None, "")
            or state.get("Running") is not False or state.get("Status") != "created"
            or state.get("Pid") != 0 or state.get("Paused") or state.get("Restarting")
            or row.get("HostConfig", {}).get("NetworkMode") not in {name, network_id}):
        raise ValueError("restore target network identity differs")
    actual = json.loads(_command(["docker", "network", "inspect", name]))
    if (len(actual) != 1 or actual[0].get("Id") != network_id
            or actual[0].get("Name") != name or actual[0].get("Internal") is not True):
        raise ValueError("restore configured network was replaced or is not internal")
    return endpoint


def _gateway_networks(row, configuration):
    """Permit only the private restore network and explicitly selected egress.

    Memory and writer checks retain the single-network contract. This optional
    gateway-only binding preserves the existing external connector route without
    attaching the source document network or accepting arbitrary extra networks.
    """
    egress_id = configuration.get("egress_network_id")
    if egress_id is None:
        return _configured_network(row, configuration["network_id"])
    networks = row.get("NetworkSettings", {}).get("Networks", {})
    if len(networks) != 2:
        raise ValueError("gateway must have exactly its pinned private and egress networks")
    matches = []
    for name, endpoint in networks.items():
        if endpoint.get("NetworkID") == egress_id:
            matches.append((name, endpoint))
        elif endpoint.get("NetworkID") == "" and row.get("State", {}).get("Status") == "created":
            actual = json.loads(_command(["docker", "network", "inspect", name]))
            if len(actual) == 1 and actual[0].get("Id") == egress_id:
                matches.append((name, endpoint))
    if len(matches) != 1:
        raise ValueError("gateway egress endpoint does not match its pin")
    name, endpoint = matches[0]
    actual = json.loads(_command(["docker", "network", "inspect", name]))
    if (len(actual) != 1 or actual[0].get("Id") != egress_id
            or actual[0].get("Name") != name or actual[0].get("Internal") is not False
            or actual[0].get("Driver") != "bridge"):
        raise ValueError("gateway egress network was replaced or is not the selected bridge")
    primary = {key: value for key, value in networks.items() if key != name}
    return _configured_network({**row, "NetworkSettings": {"Networks": primary}}, configuration["network_id"])


def _target(configuration, target_path, root, *, running):
    pin = configuration["container"]
    rows = json.loads(_command(["docker", "inspect", pin["id"]]))
    if len(rows) != 1:
        raise ValueError("gateway target is absent")
    row = rows[0]; host = row["HostConfig"]; config = row["Config"]; state = row["State"]
    _gateway_networks(row, configuration)
    if (row.get("Id") != pin["id"] or row.get("Name") != "/" + pin["name"] or row.get("Image") != pin["image"]
            or state.get("Running") is not running or state.get("Paused") or state.get("Restarting")
            or (not running and (state.get("Status") not in {"created", "exited"} or state.get("Pid") != 0))
            or host.get("RestartPolicy", {}).get("Name") != "no"
            or not host.get("ReadonlyRootfs") or host.get("Privileged")
            or host.get("PortBindings") or host.get("PublishAllPorts")
            or config.get("User") != f"{os.geteuid()}:{os.getegid()}"):
        raise ValueError("gateway target identity, isolation or operator ownership differs")
    if not ((config.get("Entrypoint") == ["/bin/sh"] and config.get("Cmd") == [_SCRIPT])
            or (config.get("Entrypoint") == [_SCRIPT] and not config.get("Cmd"))):
        raise ValueError("gateway target must use the maintained private-file startup script")
    mounts = row.get("Mounts", [])
    startup, _ = _host_file(_SCRIPT, mounts, readonly=True)
    maintained = Path(__file__).resolve().parents[1] / "experiments/contextforge-gateway-v1/start-contextforge.sh"
    if startup.read_bytes() != maintained.read_bytes():
        raise ValueError("gateway startup script differs from maintained source")
    environment = dict(item.split("=", 1) for item in config.get("Env", []))
    url = environment.get("DATABASE_URL", "")
    if not url.startswith("sqlite:////") or "?" in url or "#" in url:
        raise ValueError("gateway database URL must name the restored SQLite file")
    database_path = url.removeprefix("sqlite:///")
    if str(PurePosixPath(database_path)) != database_path or ".." in PurePosixPath(database_path).parts:
        raise ValueError("gateway database path differs")
    mapped, database_mount = _host_file(database_path, mounts, readonly=False)
    if (mapped != target_path or database_mount.get("Source") != str(target_path.parent)
            or database_mount.get("Destination") != str(PurePosixPath(database_path).parent)
            or any(m.get("Type") == "bind" and m.get("RW") and m != database_mount for m in mounts)):
        raise ValueError("gateway writable mount must contain only its configured restored database directory")
    if environment.get("PORT", "4444") != "4444":
        raise ValueError("gateway verification requires the existing internal port")
    for name in _SECRET_ENV:
        value = environment.get(name, "")
        if not value.startswith("/run/"):
            raise ValueError("gateway startup credentials must use private file references")
        path, _ = _host_file(value, mounts, readonly=True)
        if path == root or root in path.parents:
            raise ValueError("gateway credentials must be separately provisioned")
        _private_file(path, minimum=16 if name.endswith("PASSWORD_FILE") else 32, maximum=512)
    token, _ = _host_file(configuration["access_token_path"], mounts, readonly=True)
    if token == root or root in token.parents:
        raise ValueError("gateway access token must be separately provisioned")
    _private_file(token, minimum=32, maximum=32768)
    return row


def _document_targets(connection_receipt, binding_configuration, network_id):
    expected = {item["provider_alias"] for item in binding_configuration["providers"]}
    targets = connection_receipt.get("providers", [])
    if (len(targets) != len(expected) or {item.get("gateway_alias") for item in targets} != expected):
        raise ValueError("document target alias coverage differs")
    for target in targets:
        if target.get("documents_verified") is not True or target.get("status") != "connected_read_only":
            raise ValueError("document target verification is absent")
        rows = json.loads(_command(["docker", "inspect", target["container_id"]]))
        if len(rows) != 1:
            raise ValueError("document target is absent")
        row = rows[0]; networks = row.get("NetworkSettings", {}).get("Networks", {})
        if (row.get("Id") != target["container_id"] or row.get("Image") != target["image_id"]
                or row.get("Name") != "/" + target["container_name"]
                or row.get("State", {}).get("Running") is not True
                or len(networks) != 1 or next(iter(networks.values())).get("NetworkID") != network_id
                or target["gateway_alias"] not in (next(iter(networks.values())).get("Aliases") or [])):
            raise ValueError("document target is not running on the gateway's verified alias network")


def _inventory(raw):
    with tempfile.TemporaryDirectory(prefix="eva-gateway-inventory-") as temporary:
        snapshot = Path(temporary) / "verified.sqlite"
        snapshot.write_bytes(raw); snapshot.chmod(0o400)
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA trusted_schema=OFF")
            db.set_progress_handler(lambda: 1, 1_000_000)
            gateways = db.execute("SELECT id,url,transport FROM gateways ORDER BY id LIMIT 10001").fetchall()
            tools = db.execute("SELECT id,gateway_id FROM tools ORDER BY id LIMIT 10001").fetchall()
    if len(gateways) > 10000 or len(tools) > 10000:
        raise ValueError("gateway inventory exceeds verification bound")
    return {"gateways": [{"id": x[0], "url_sha256": hashlib.sha256(x[1].encode()).hexdigest(), "transport": x[2]} for x in gateways],
            "tools": [{"id": x[0], "gateway_id": x[1]} for x in tools]}


_READBACK = r"""
import hashlib,json,os,socket,stat,sys,time,urllib.parse,urllib.request
path=sys.argv[1]
fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o400 or info.st_nlink!=1 or not 32<=info.st_size<=32768: raise RuntimeError('token file refused')
    token=os.read(fd,32769).decode('ascii')
    if len(token.encode())!=info.st_size or '\n' in token or '\r' in token: raise RuntimeError('token bytes refused')
finally: os.close(fd)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise RuntimeError('redirect refused')
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
end=time.monotonic()+45
while True:
    try:
        with socket.create_connection(('127.0.0.1',4444),timeout=.25): pass
        break
    except OSError:
        if time.monotonic()>=end: raise RuntimeError('gateway readiness deadline')
        time.sleep(.1)
def get(path):
    req=urllib.request.Request('http://127.0.0.1:4444'+path,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    with client.open(req,timeout=15) as response:
        data=response.read(8*1024*1024+1)
        if len(data)>8*1024*1024: raise RuntimeError('gateway response exceeds bound')
    return json.loads(data)
if get('/health').get('status') != 'healthy': raise RuntimeError('gateway health not healthy')
def inventory(kind):
    rows=[];cursor=None;seen=set()
    for _ in range(50):
        query={'include_inactive':'true','include_pagination':'true','limit':200}
        if cursor is not None: query['cursor']=cursor
        data=get('/'+kind+'?'+urllib.parse.urlencode(query))
        if not isinstance(data,dict) or not isinstance(data.get(kind),list): raise RuntimeError('inventory response differs')
        for item in data[kind]:
            identifier=item['id']
            if not isinstance(identifier,str) or len(identifier)>240 or identifier in seen: raise RuntimeError('inventory ID differs')
            seen.add(identifier)
            if kind=='gateways':
                rows.append({'id':identifier,'url_sha256':hashlib.sha256(item['url'].encode()).hexdigest(),'transport':item['transport']})
            else:
                rows.append({'id':identifier,'gateway_id':item.get('gatewayId',item.get('gateway_id'))})
        cursor=data.get('nextCursor')
        if not cursor: return sorted(rows,key=lambda item:item['id'])
    raise RuntimeError('inventory pagination exceeded bound')
print(json.dumps({'gateways':inventory('gateways'),'tools':inventory('tools')},ensure_ascii=False))
"""


def start_restored_gateway(package_root, *, configuration, external_configuration,
                           binding_configuration, connection_receipt, guard):
    receipt = {"contract": "EvaRestoredGatewayStart/v1", "status": "not_started",
               "gateway_started": False, "core_activated": False, "tool_calls_executed": False}
    try:
        validate_gateway_start_configuration(configuration, external_configuration=external_configuration,
                                             binding_configuration=binding_configuration)
        if not callable(guard):
            raise ValueError("gateway start requires the current restore guard")
        guard()
        root = Path(package_root).resolve(); package = read_installation_package(root)
        pin = configuration["container"]; target = Path(binding_configuration["target_path"])
        gateway = connection_receipt.get("gateway_binding", {})
        if (connection_receipt.get("contract") != "EvaDocumentConnection/v1"
                or connection_receipt.get("status") != "connected_read_only"
                or connection_receipt.get("documents_verified") is not True
                or connection_receipt.get("provider_verified") is not True
                or connection_receipt.get("archive_sha256") != package["archive_sha256"]
                or connection_receipt.get("core_activated") is not False
                or gateway.get("aliases_attached") is not True or gateway.get("status") != "validated_unchanged"
                or gateway.get("archive_sha256") != package["archive_sha256"]
                or gateway.get("target_binding_sha256") != hashlib.sha256(str(target).encode()).hexdigest()):
            raise ValueError("gateway start requires verified document connections and preserved bindings")
        images = package["manifest"]["source_components"]["gateway"]
        if len(images) != 1 or pin["image"] != "sha256:" + images[0]["image_sha256"]:
            raise ValueError("gateway image differs from approved package")
        entry = package["manifest"]["files"]["gateway/contextforge.sqlite"]
        with _root_descriptor(root) as fd:
            raw = _read_regular(fd, "gateway/contextforge.sqlite", maximum=MAX_ARCHIVE_BYTES)
        if (len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]
                or gateway.get("gateway_sha256") != entry["sha256"] or _private_target_bytes(target) != raw):
            raise ValueError("gateway target differs from approved archive before start")
        expected = _inventory(raw)
        for item in binding_configuration["providers"]:
            endpoint = "http://" + item["provider_alias"] + ":9000/mcp"
            if {"id": item["gateway_id"], "url_sha256": hashlib.sha256(endpoint.encode()).hexdigest(), "transport": "STREAMABLEHTTP"} not in expected["gateways"]:
                raise ValueError("gateway preserved provider inventory differs")
        _inspect_network(configuration["network_id"])
        _target(configuration, target, root, running=False)
        _document_targets(connection_receipt, binding_configuration, configuration["network_id"])
        receipt.update(archive_sha256=package["archive_sha256"], gateway_source_sha256=entry["sha256"],
                       container_id=pin["id"], image_id=pin["image"], inventory_sha256=canonical_sha256(expected))
        guard()
        if _private_target_bytes(target) != raw:
            raise ValueError("gateway restored database changed before start")
        receipt.update(status="start_attempted", gateway_started=None)
        _command(["docker", "start", pin["id"]], deadline=30, limit=4096)
        guard()
        _target(configuration, target, root, running=True)
        receipt["gateway_started"] = True
        observed = json.loads(_command(["docker", "exec", pin["id"], "python3", "-c", _READBACK,
                                       configuration["access_token_path"]], deadline=120, limit=4*1024*1024))
        if observed != expected:
            raise ValueError("authenticated gateway and tool inventories differ from preserved SQLite")
        guard()
        _inspect_network(configuration["network_id"])
        _target(configuration, target, root, running=True)
        _document_targets(connection_receipt, binding_configuration, configuration["network_id"])
        receipt.update(status="gateway_started_verified", gateway_started=True,
                       gateway_count=len(expected["gateways"]), tool_count=len(expected["tools"]),
                       authenticated_inventory_verified=True,
                       verification_scope="Gateway health and authenticated preserved registration/tool inventory only; no tool RPC, encrypted-provider credential use, project resumption or core activation")
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise RestoredGatewayStartError(receipt) from error
