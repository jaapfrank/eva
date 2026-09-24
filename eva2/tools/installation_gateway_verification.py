"""Discover restored upstream tools through ContextForge's maintained API.

Refresh may update catalog metadata and timestamps. No tools/call is issued.
Failed or ambiguous refreshes remain for inspection and are never retried here.
"""
from contextlib import closing
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
            rows = database.execute("SELECT id,gateway_id,original_name,input_schema,output_schema FROM tools ORDER BY id LIMIT 10001").fetchall()
    if len(rows) > 10000:
        raise ValueError("restored tool catalog exceeds bound")
    result["tools"] = [{"id": row[0], "gateway_id": row[1], "original_name": row[2],
                        "input_schema": json.loads(row[3]), "output_schema": json.loads(row[4]) if row[4] else None}
                       for row in rows]
    return result


# Reuse the existing private-token reader, loopback-only HTTP client and bounded
# inventory pagination. Extend only the projected tool fields for comparison.
_OLD_TOOL = "rows.append({'id':identifier,'gateway_id':item.get('gatewayId',item.get('gateway_id'))})"
_NEW_TOOL = "rows.append({'id':identifier,'gateway_id':item.get('gatewayId',item.get('gateway_id')),'original_name':item.get('originalName',item.get('original_name')),'input_schema':item.get('inputSchema',item.get('input_schema')),'output_schema':item.get('outputSchema',item.get('output_schema'))})"
if _READBACK.count(_OLD_TOOL) != 1:
    raise RuntimeError("maintained gateway inventory projection changed")
_BASE = _READBACK.replace(_OLD_TOOL, _NEW_TOOL).split("print(json.dumps({'gateways':inventory('gateways')")[0]
_CATALOG = _BASE + "\nprint(json.dumps({'gateways':inventory('gateways'),'tools':inventory('tools')},ensure_ascii=False))"
_REFRESH = _BASE + r"""
from datetime import datetime,timezone
identifier=sys.argv[2]
route='/gateways/'+urllib.parse.quote(identifier,safe='')
def stamp(item):
    value=item.get('lastRefreshAt',item.get('last_refresh_at'))
    if not value:return None
    return datetime.fromisoformat(value.replace('Z','+00:00')).replace(tzinfo=timezone.utc) if datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None else datetime.fromisoformat(value.replace('Z','+00:00'))
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
print(json.dumps({'gateway_id':identifier,'success':result.get('success') is True,'fresh':after is not None and after>=started and (before is None or after>before),'validation_clean':valid==[],'counts':counts,'refreshed_at':after.isoformat() if after else None}))
"""


def verify_gateway_providers(package_root, *, configuration, external_configuration, binding_configuration, guard):
    receipt = {"contract":"EvaRestoredGatewayProviders/v1", "all_verified":False,
               "tool_calls_executed":False, "refresh_attempted":[], "probes":[],
               "catalog_metadata_may_change":False}
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
        if execute(_CATALOG) != expected: raise ValueError("restored catalog differs before discovery")
        for gateway in expected["gateways"]:
            check()
            identifier = gateway["id"]
            receipt["refresh_attempted"].append(identifier)
            receipt["catalog_metadata_may_change"] = True
            probe = execute(_REFRESH, identifier)
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
        receipt.update(all_verified=True, status="upstream_discovery_verified",
                       verification_scope="Fresh authenticated upstream initialization and tool discovery; preserved catalog identities and schemas. No tool execution or user-flow acceptance.")
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise GatewayProviderVerificationError(receipt) from error
