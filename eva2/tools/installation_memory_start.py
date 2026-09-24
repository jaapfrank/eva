"""Start one precreated restored memory API; preserve partial starts for inspection.

Readiness means local health and bank inventory access, not upstream model success.
The caller verifies restored data before this entrypoint and keeps source/core fenced.
"""
from pathlib import Path
import hmac
import json
from urllib.parse import urlsplit, unquote
from eva.operations.model_credentials import build_codex_consumer_auth
from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from tools.installation_external_restore import _pin, _pins_current, validate_external_configuration
from tools.installation_document_connection import _command, _inspect_network
from tools.installation_gateway_start import _host_file
from tools.installation_runtime_authorization import runtime_profile_sha256, _path, _digest
from tools.installation_recovery_package import read_installation_package


class RestoredMemoryStartError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("restored memory start unconfirmed; retain target for inspection, do not retry")


def validate_memory_start_configuration(configuration, *, external_configuration):
    validate_external_configuration(external_configuration)
    if not isinstance(configuration, dict) or set(configuration) != {
        "container", "network_id", "profile_sha256", "model_authority_file", "model_auth_path", "source_memory_writers"
    }:
        raise ValueError("memory API configuration differs")
    _pin(configuration["container"])
    if configuration["container"] not in external_configuration["writers"]:
        raise ValueError("memory API must be a precreated external writer")
    sources = configuration["source_memory_writers"]
    if not isinstance(sources, list) or not sources or len(sources) > 64:
        raise ValueError("source memory writer pins are required")
    for source in sources:
        _pin(source)
        if source not in external_configuration["writers"] or source == configuration["container"]:
            raise ValueError("source memory writer binding differs")
    if len({source["id"] for source in sources}) != len(sources):
        raise ValueError("source memory writer pins overlap")
    _digest(configuration["network_id"]); _digest(configuration["profile_sha256"])
    _path(configuration["model_authority_file"])
    if configuration["model_auth_path"] != "/home/hindsight/.codex/auth.json":
        raise ValueError("memory API consumer authentication path differs")


def _inspect(pin):
    rows = json.loads(_command(["docker", "inspect", pin["id"]]))
    if len(rows) != 1 or any(rows[0].get(key) != value for key, value in
                           (("Id", pin["id"]), ("Name", "/" + pin["name"]), ("Image", pin["image"]))):
        raise ValueError("memory container identity differs")
    return rows[0]


def _target(root, configuration, external_configuration, *, running):
    _pins_current({**external_configuration, "writers": configuration["source_memory_writers"]})
    row = _inspect(configuration["container"])
    state, host, config = row["State"], row["HostConfig"], row["Config"]
    if (state.get("Running") is not running or state.get("Paused") or state.get("Restarting")
            or (not running and (state.get("Status") not in {"created", "exited"} or state.get("Pid") != 0))
            or host.get("RestartPolicy", {}).get("Name") != "no"
            or not host.get("ReadonlyRootfs") or host.get("Privileged")
            or host.get("PortBindings") or host.get("PublishAllPorts")
            or runtime_profile_sha256(row) != configuration["profile_sha256"]
            or config.get("User") != "1000:1000"):
        raise ValueError("memory API state or approved profile differs")
    networks = row.get("NetworkSettings", {}).get("Networks", {})
    if len(networks) != 1 or next(iter(networks.values())).get("NetworkID") != configuration["network_id"]:
        raise ValueError("memory API network differs")
    _inspect_network(configuration["network_id"])
    environment = dict(item.split("=", 1) for item in config.get("Env", []))
    if (environment.get("HINDSIGHT_API_DATABASE_URL") or
            environment.get("HINDSIGHT_API_LLM_PROVIDER") != "openai-codex"):
        raise ValueError("memory API provider or database configuration differs")
    authority = Path(configuration["model_authority_file"])
    dsn_path, _ = _host_file(environment.get("HINDSIGHT_API_DATABASE_URL_FILE", ""), row["Mounts"], readonly=True)
    auth_path, _ = _host_file(configuration["model_auth_path"], row["Mounts"], readonly=True)
    for path in (dsn_path, auth_path, authority):
        if path == root or root in path.parents:
            raise ValueError("memory API credentials must be separately provisioned")
    read = lambda path: read_private_runtime_file_bytes(path, setting_name="restored memory credential", effective_uid=1000)
    if not hmac.compare_digest(read(auth_path), build_codex_consumer_auth(authority)):
        raise ValueError("memory API current model authorization differs")
    raw = read(dsn_path)
    if len(raw) > 4096:
        raise ValueError("memory API database reference exceeds bound")
    try:
        url = urlsplit(raw.decode().strip())
        valid = (url.scheme in {"postgres", "postgresql"} and not url.query and not url.fragment
                 and unquote(url.path) == "/" + external_configuration["memory"]["database"]["name"]
                 and unquote(url.username or "") == external_configuration["memory"]["user"]
                 and url.port in {None, 5432})
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("memory API database binding differs")
    database = _inspect(external_configuration["memory"]["container"])
    matching = [n for n in database.get("NetworkSettings", {}).get("Networks", {}).values()
                if n.get("NetworkID") == configuration["network_id"]]
    if (database.get("State", {}).get("Running") is not True or len(matching) != 1
            or url.hostname not in {external_configuration["memory"]["container"]["name"], *(matching[0].get("Aliases") or [])}):
        raise ValueError("memory API database network binding differs")
    return row


def _core_route(configuration, runtime_configuration):
    target = runtime_configuration["target"]
    pins = [pin for pin in target["workers"] if pin["name"] == target["core_name"]]
    if len(pins) != 1:
        raise ValueError("memory API requires one pinned target core")
    core = _inspect(pins[0])
    api = _inspect(configuration["container"])
    environment = dict(item.split("=", 1) for item in core.get("Config", {}).get("Env", []))
    try:
        url = urlsplit(environment.get("EVA_HINDSIGHT_URL", ""))
        valid = (url.scheme == "http" and url.port == 8888 and url.path in {"", "/"}
                 and not url.query and not url.fragment and url.username is None and url.password is None)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("target core memory URL differs")
    network_id = configuration["network_id"]
    core_networks = [item for item in core.get("NetworkSettings", {}).get("Networks", {}).values()
                     if item.get("NetworkID") == network_id]
    api_networks = [item for item in api.get("NetworkSettings", {}).get("Networks", {}).values()
                    if item.get("NetworkID") == network_id]
    if (len(core_networks) != 1 or len(api_networks) != 1
            or url.hostname not in {configuration["container"]["name"], *(api_networks[0].get("Aliases") or [])}
            or core.get("HostConfig", {}).get("ExtraHosts")):
        raise ValueError("target core does not use the restored memory API network route")


_READINESS = r"""
import json,time,urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k): raise RuntimeError('redirect refused')
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def get(path):
    with client.open('http://127.0.0.1:8888'+path,timeout=3) as response:
        raw=response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024: raise RuntimeError('response exceeds bound')
    return json.loads(raw)
end=time.monotonic()+45
while True:
    try:
        if get('/health').get('status')!='healthy': raise RuntimeError('unhealthy')
        banks=get('/v1/default/banks').get('banks')
        if not isinstance(banks,list): raise RuntimeError('bank inventory differs')
        print(json.dumps({'healthy':True,'bank_count':len(banks)})); break
    except Exception:
        if time.monotonic()>=end: raise RuntimeError('memory readiness deadline') from None
        time.sleep(.25)
"""


def _verify(root, configuration, external_configuration, runtime_configuration, guard):
    guard()
    _target(root, configuration, external_configuration, running=True)
    _core_route(configuration, runtime_configuration)
    result = json.loads(_command(["docker", "exec", configuration["container"]["id"],
                                 "python", "-c", _READINESS], deadline=55))
    if result.get("healthy") is not True or type(result.get("bank_count")) is not int or result["bank_count"] < 0:
        raise ValueError("memory API readiness differs")
    _target(root, configuration, external_configuration, running=True)
    _core_route(configuration, runtime_configuration)
    guard()
    return result


def start_restored_memory_api(package_root, *, configuration, external_configuration, runtime_configuration, guard):
    validate_memory_start_configuration(configuration, external_configuration=external_configuration)
    if not callable(guard):
        raise ValueError("memory API requires current fencing")
    root = Path(package_root).resolve()
    package = read_installation_package(root)
    guard()
    _target(root, configuration, external_configuration, running=False)
    _core_route(configuration, runtime_configuration)
    receipt = {"contract": "EvaRestoredMemoryApi/v1", "archive_sha256": package["archive_sha256"],
               "container_id": configuration["container"]["id"], "profile_sha256": configuration["profile_sha256"],
               "start_attempted": True, "ready": False, "upstream_model_verified": False}
    try:
        guard()
        _command(["docker", "start", configuration["container"]["id"]])
        receipt.update(_verify(root, configuration, external_configuration, runtime_configuration, guard))
        receipt["ready"] = True
        return receipt
    except Exception:
        raise RestoredMemoryStartError(receipt) from None


def verify_restored_memory_api(package_root, *, configuration, external_configuration, runtime_configuration, guard, receipt):
    validate_memory_start_configuration(configuration, external_configuration=external_configuration)
    root = Path(package_root).resolve()
    package = read_installation_package(root)
    if (receipt.get("contract") != "EvaRestoredMemoryApi/v1" or receipt.get("ready") is not True
            or receipt.get("archive_sha256") != package["archive_sha256"]
            or receipt.get("container_id") != configuration["container"]["id"]
            or receipt.get("profile_sha256") != configuration["profile_sha256"]):
        raise ValueError("memory API start receipt binding differs")
    return {**receipt, **_verify(root, configuration, external_configuration, runtime_configuration, guard)}
