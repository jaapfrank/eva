"""Discover restored upstream tools through ContextForge's maintained API.

Refresh may update catalog metadata and timestamps. No src/tools/call is issued.
Failed or ambiguous refreshes remain for inspection and are never retried here.
"""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_document_connection import _command, _inspect_network
from tools.installation_gateway_start import _READBACK, _inventory, _target, validate_gateway_start_configuration
from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular
from tools.portable_transport import MAX_ARCHIVE_BYTES


class GatewayProviderVerificationError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("restored upstream discovery not verified; inspect target without automatic retry")


def _catalog(raw):
    result = _inventory(raw)
    with tempfile.TemporaryDirectory(prefix="eva-provider-catalog-") as temporary:
        path = Path(temporary) / "catalog.sqlite"
        path.write_bytes(raw); path.chmod(0o400)
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True)) as database:
            database.execute("PRAGMA query_only=ON")
            database.execute("PRAGMA trusted_schema=OFF")
            database.set_progress_handler(lambda: 1, 1_000_000)
            gateways = dict(database.execute("SELECT id,enabled FROM gateways"))
            rows = database.execute("SELECT id,gateway_id,original_name,input_schema,output_schema,enabled FROM tools ORDER BY id LIMIT 10001").fetchall()
    if len(rows) > 10000:
        raise ValueError("restored tool catalog exceeds bound")
    result["tools"] = [{"id": row[0], "gateway_id": row[1], "original_name": row[2],
                        "input_schema": json.loads(row[3]), "output_schema": json.loads(row[4]) if row[4] else None,
                        "enabled": bool(row[5])}
                       for row in rows]
    for gateway in result["gateways"]:
        gateway["enabled"] = bool(gateways[gateway["id"]])
    return result


# Reuse the existing private-token reader, loopback-only HTTP client and bounded
# inventory pagination. Extend only the projected tool fields for comparison.
_OLD_TOOL = "rows.append({'id':identifier,'gateway_id':item.get('gatewayId',item.get('gateway_id'))})"
_NEW_TOOL = "rows.append({'id':identifier,'gateway_id':item.get('gatewayId',item.get('gateway_id')),'original_name':item.get('originalName',item.get('original_name')),'input_schema':item.get('inputSchema',item.get('input_schema')),'output_schema':item.get('outputSchema',item.get('output_schema')),'enabled':item.get('enabled')})"
if _READBACK.count(_OLD_TOOL) != 1:
    raise RuntimeError("maintained gateway inventory projection changed")
_BASE = _READBACK.replace(_OLD_TOOL, _NEW_TOOL).split("print(json.dumps({'gateways':inventory('gateways')")[0]
_BASE = _BASE.replace("'transport':item['transport']}", "'transport':item['transport'],'enabled':item.get('enabled')}")
_CATALOG = _BASE + "\nprint(json.dumps({'gateways':inventory('gateways'),'tools':inventory('tools')},ensure_ascii=False))"
_SUSPEND = _BASE + r"""
identifier=sys.argv[2]
route='/gateways/'+urllib.parse.quote(identifier,safe='')+'/state?activate=false'
req=urllib.request.Request('http://127.0.0.1:4444'+route,data=b'',method='POST',headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
with client.open(req,timeout=30) as response:
    raw=response.read(1024*1024+1)
    if len(raw)>1024*1024:raise RuntimeError('state response exceeds bound')
result=json.loads(raw)
gateway=result.get('gateway',{})
print(json.dumps({'gateway_id':gateway.get('id'),'disabled':gateway.get('enabled') is False,'success':result.get('status')=='success'}))
"""
_REFRESH = _BASE + r"""
from datetime import datetime,timezone
identifier=sys.argv[2]
route='/gateways/'+urllib.parse.quote(identifier,safe='')
def stamp(item):
    value=item.get('lastRefreshAt',item.get('last_refresh_at'))
    if not value:return None
    return datetime.fromisoformat(value.replace('Z','+00:00')).replace(tzinfo=timezone.utc) if datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None else datetime.fromisoformat(value.replace('Z','+00:00'))
initial=get(route)
if initial.get('enabled') is not True:raise RuntimeError('disabled gateway cannot be refreshed')
reactivated=False
if initial.get('reachable') is not True:
    # Native refresh skips unreachable gateways and returns an empty success.
    # The state API rediscovers the upstream, but also enables its tools. Permit
    # that transition only when every retained bound tool is already enabled.
    if sys.argv[3]!='allow_reactivation':raise RuntimeError('reactivation would change retained tool activation')
    req=urllib.request.Request('http://127.0.0.1:4444'+route+'/state?activate=true',data=b'',method='POST',headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    with client.open(req,timeout=60) as response:
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise RuntimeError('state response exceeds bound')
    state=json.loads(raw)
    observed=get(route)
    if state.get('status')!='success' or observed.get('enabled') is not True or observed.get('reachable') is not True:raise RuntimeError('native reactivation not confirmed')
    reactivated=True
before=stamp(get(route))
started=datetime.now(timezone.utc)
req=urllib.request.Request('http://127.0.0.1:4444'+route+'/tools/refresh?include_resources=false&include_prompts=false',data=b'',method='POST',headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
with client.open(req,timeout=60) as response:
    raw=response.read(1024*1024+1)
    if len(raw)>1024*1024:raise RuntimeError('refresh response exceeds bound')
result=json.loads(raw)
after=stamp(get(route))
counts={key:result.get(key,result.get(''.join([key.split('_')[0]]+[part.title() for part in key.split('_')[1:]]))) for key in ('tools_added','tools_updated','tools_removed','resources_added','resources_updated','resources_removed','prompts_added','prompts_updated','prompts_removed')}
valid=result.get('validation_errors',result.get('validationErrors'))
print(json.dumps({'gateway_id':identifier,'success':result.get('success') is True,'fresh':after is not None and after>=started and (before is None or after>before),'validation_clean':valid==[],'counts':counts,'refreshed_at':after.isoformat() if after else None,'reactivated':reactivated}))
"""


def validate_suspended_gateway_ids(identifiers):
    """Accept only explicit operator selections, never endpoint heuristics."""
    if (not isinstance(identifiers, (list, tuple)) or len(identifiers) > 10000
            or any(not isinstance(value, str) or not value or len(value) > 240 for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError("suspended gateway selection differs")


def verify_gateway_providers(package_root, *, configuration, external_configuration, binding_configuration, guard,
                             suspended_gateway_ids=()):
    receipt = {"contract":"EvaRestoredGatewayProviders/v1", "all_verified":False,
               "tool_calls_executed":False, "refresh_attempted":[], "probes":[],
               "catalog_metadata_may_change":False, "suspended_gateway_ids":[], "suspension_attempted":[]}
    try:
        validate_gateway_start_configuration(configuration, external_configuration=external_configuration,
                                             binding_configuration=binding_configuration)
        if not callable(guard): raise ValueError("fresh restore guard required")
        root = Path(package_root).resolve()
        package = read_installation_package(root)
        entry = package["manifest"]["files"]["gateway/contextforge.sqlite"]
        with _root_descriptor(root) as descriptor:
            raw = _read_regular(descriptor,"gateway/contextforge.sqlite",maximum=MAX_ARCHIVE_BYTES)
        if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("approved gateway snapshot differs")
        expected = _catalog(raw)
        validate_suspended_gateway_ids(suspended_gateway_ids)
        selected = set(suspended_gateway_ids)
        if not selected <= {item["id"] for item in expected["gateways"]}:
            raise ValueError("suspended gateway is absent from retained snapshot")
        suspended = deepcopy(expected)
        for gateway in suspended["gateways"]:
            if gateway["id"] in selected:
                gateway["enabled"] = False
        for tool in suspended["tools"]:
            if tool["gateway_id"] in selected:
                tool["enabled"] = False
        pin = configuration["container"]
        images = package["manifest"]["source_components"]["gateway"]
        if len(images) != 1 or pin["image"] != "sha256:"+images[0]["image_sha256"]:
            raise ValueError("gateway image differs from approved package")
        receipt.update(archive_sha256=package["archive_sha256"], container_id=pin["id"], image_id=pin["image"],
                       catalog_sha256=canonical_sha256(expected))
        def check():
            guard(); _inspect_network(configuration["network_id"])
            target = _target(configuration, Path(binding_configuration["target_path"]), root, running=True)
            environment = dict(value.split("=", 1) for value in target.get("Config", {}).get("Env", []))
            # With passthrough enabled, the local administrator token could be
            # forwarded to a no-auth provider and substitute for its identity.
            if environment.get("ENABLE_HEADER_PASSTHROUGH", "false").lower() not in {"false", "0", "no", "off"}:
                raise ValueError("discovery requires header passthrough disabled on restored target")
        def execute(script, *arguments):
            return json.loads(_command(["docker","exec",pin["id"],"python3","-c",script,
                                       configuration["access_token_path"],*arguments],deadline=120,limit=8*1024*1024))
        check()
        observed = execute(_CATALOG)
        # A prior failed verification may already have disabled part of the
        # explicit selection. Resume from that observed state, preserving IDs,
        # schemas and every non-selected tool's activation state.
        comparable = deepcopy(observed)
        for kind in ("gateways", "tools"):
            originals = {item["id"]: item for item in expected[kind]}
            for item in comparable[kind]:
                identifier = item["id"] if kind == "gateways" else item["gateway_id"]
                if identifier in selected and item["id"] in originals and item.get("enabled") is False:
                    item["enabled"] = originals[item["id"]]["enabled"]
        if comparable != expected: raise ValueError("restored catalog differs before discovery")
        for gateway in observed["gateways"]:
            if gateway["id"] in selected and gateway["enabled"] is True:
                check()
                receipt["suspension_attempted"].append(gateway["id"])
                probe = execute(_SUSPEND, gateway["id"])
                if probe != {"gateway_id":gateway["id"], "disabled":True, "success":True}:
                    raise ValueError("gateway suspension not verified")
        check()
        if execute(_CATALOG) != suspended:
            raise ValueError("gateway suspension did not preserve catalog or disable its tools")
        expected = suspended
        disabled = {item["id"] for item in expected["gateways"] if item["enabled"] is False}
        if any(item["enabled"] is not False for item in expected["tools"] if item["gateway_id"] in disabled):
            raise ValueError("disabled gateway retains executable tools")
        receipt["suspended_gateway_ids"] = sorted(disabled)
        for gateway in expected["gateways"]:
            if gateway["id"] in disabled:
                continue
            check()
            identifier = gateway["id"]
            receipt["refresh_attempted"].append(identifier)
            receipt["catalog_metadata_may_change"] = True
            bound_tools = [item for item in expected["tools"] if item["gateway_id"] == identifier]
            reactivation = "allow_reactivation" if all(item["enabled"] is True for item in bound_tools) else "preserve_disabled"
            probe = execute(_REFRESH, identifier, reactivation)
            receipt["probes"].append(probe)
            counts = probe.get("counts", {})
            if (probe.get("gateway_id") != identifier or probe.get("success") is not True
                    or probe.get("fresh") is not True or probe.get("validation_clean") is not True
                    or set(counts) != {kind+"_"+action for kind in ("tools","resources","prompts") for action in ("added","updated","removed")}
                    or any(type(value) is not int or value < 0 for value in counts.values())
                    or any(value for key,value in counts.items() if key != "tools_updated")):
                raise ValueError("upstream discovery or catalog preservation not verified")
            check()
            if execute(_CATALOG) != expected: raise ValueError("bound tool identities or schemas changed during discovery")
        check()
        if execute(_CATALOG) != expected:
            raise ValueError("final restored gateway admission changed")
        receipt.update(all_verified=True, status="upstream_discovery_verified",
                       admitted_catalog_sha256=canonical_sha256(expected),
                       verification_scope="Fresh discovery of enabled providers; explicitly suspended providers and tools retained disabled with identical IDs and schemas. No tool execution or user-flow acceptance.")
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise GatewayProviderVerificationError(receipt) from error
