"""Connect a real prepared target to the existing trusted restore configuration."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from sqlalchemy.engine import make_url

from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
from eva.operations.installer_identity_storage import read_private_artifact
from tools.installation_document_connection import _command
from tools.installation_gateway_start import _host_file
from tools.installation_external_restore import _DATABASE_QUERY
from tools.restore_runtime_guard import capture_prepared_restore_runtime

_COMPONENT_KEYS = {"recovery_materialization", "external_recovery", "document_recovery",
    "document_connection", "document_gateway_binding", "gateway_start", "document_write_access", "activation"}


@dataclass(frozen=True, slots=True)
class PreparedRestoreConfiguration:
    config: dict = field(repr=False)
    receipt: dict
    pending: tuple[str, ...]


def _read(path):
    return json.loads(read_private_artifact(path,label="prepared target receipt"))


def _absolute(value):
    path=Path(value)
    if not path.is_absolute() or path.resolve()!=path:
        raise ValueError("restore path must be absolute without links")
    return path


def _actual(pin):
    rows=json.loads(_command(["docker","inspect",pin["id"]]))
    if len(rows)!=1:
        raise ValueError("prepared container is unavailable")
    row=rows[0]
    if (row.get("Id")!=pin["id"] or row.get("Name")!="/"+pin["name"]
            or row.get("Image")!=pin["image"]):
        raise ValueError("prepared container identity changed")
    return row


def _database(pin, service):
    actual=_actual(pin)
    if actual.get("State",{}).get("Running") is not True:
        raise ValueError("prepared database is not running")
    expected=service["environment"]
    env=dict(item.split("=",1) for item in actual["Config"]["Env"] if "=" in item)
    if any(env.get(k)!=expected.get(k) for k in ("POSTGRES_DB","POSTGRES_USER")):
        raise ValueError("prepared database configuration changed")
    raw=_command(["docker","exec",pin["id"],"psql","-U",env["POSTGRES_USER"],
                  "-d",env["POSTGRES_DB"],"-Atc",_DATABASE_QUERY])
    observed=json.loads(raw)
    return {k:observed[k] for k in ("name","oid","system_identifier")},env["POSTGRES_USER"]


def build_prepared_restore_config(*, preparation_workspace, connection, target_dsn,
                                  approved_source, authority, workspace, objects, keyring,
                                  runtime_identity=None, component_configuration=None,
                                  core_service="core",
                                  writer_services=("core","durable-goal-worker","telegram-ingress")):
    """Use explicit source approval and actual target identities, never archive pins.

    The target DSN is private installer input and must be the URL of the supplied
    connection. Core import configuration may be returned while external component
    joins remain pending; that is not full-installation restore/activation readiness.
    """
    root=_absolute(preparation_workspace)
    workflow=_read(root/"target-workflow.json")
    preparation_path=root/"resources/preparation-receipt.json"
    if (workflow.get("contract")!="EvaInstallerTargetWorkflow/v1" or workflow.get("status")!="prepared"
            or workflow.get("preparation_receipt")!=str(preparation_path)):
        raise ValueError("completed target workflow required")
    prepared=_read(preparation_path)
    graph=_read(root/"resources/compose.json")
    if (prepared.get("namespace")!=workflow.get("namespace") or graph.get("name")!=workflow.get("namespace")):
        raise ValueError("prepared target namespace differs")
    if (not isinstance(workflow.get("plan_sha256"), str)
            or prepared.get("plan_sha256") != workflow["plan_sha256"]):
        raise ValueError("prepared target plan differs from workflow")
    def pin(service):
        selected=[p for p in prepared["containers"] if p["service"]==service]
        if len(selected)!=1:raise ValueError("prepared service is missing or duplicated")
        return selected[0]
    if make_url(target_dsn)!=connection.engine.url:
        raise ValueError("private target DSN differs from supplied connection")
    core_pin=pin(core_service);actual_core=_actual(core_pin)
    env=dict(item.split("=",1) for item in actual_core["Config"]["Env"] if "=" in item)
    dsn_path=env.get("EVA_DATABASE_DSN_FILE")
    if dsn_path!="/run/secrets/database_dsn":
        raise ValueError("maintained core database file route required")
    host_file,_=_host_file(dsn_path,actual_core["Mounts"],readonly=True)
    owner=host_file.stat().st_uid
    dsn=make_url(read_private_runtime_file_bytes(str(host_file),setting_name="prepared core database DSN",effective_uid=owner).decode())
    db_pin=pin("postgres");db_service=graph["services"]["postgres"]
    names={"postgres",db_pin["name"]}
    for endpoint in db_service["networks"].values():names.update((endpoint or {}).get("aliases",[]))
    database,user=_database(db_pin,db_service)
    if (dsn.get_backend_name()!="postgresql" or dsn.host not in names or dsn.port not in {None,5432}
            or dsn.database!=database["name"] or dsn.username!=user or dsn.query):
        raise ValueError("core DSN does not select the prepared database")
    guard=capture_prepared_restore_runtime(connection,approved_source=approved_source,
        preparation=prepared,core_service=core_service,writer_services=writer_services)
    if guard["target_database"]!=database:
        raise ValueError("supplied connection is not the prepared database lineage")
    memory_pin=pin("hindsight-postgres")
    memory_database,memory_user=_database(memory_pin,graph["services"]["hindsight-postgres"])
    memory={"container":{k:memory_pin[k] for k in ("name","id","image")},
            "database":memory_database,"user":memory_user}
    components=deepcopy(component_configuration or {})
    if set(components)-_COMPONENT_KEYS:
        raise ValueError("unsupported trusted component configuration")
    if "external_recovery" in components:
        external=components["external_recovery"]
        if "memory" in external and external["memory"]!=memory:
            raise ValueError("external recovery contains stale memory target identities")
        external["memory"]=memory
    if "activation" in components:
        expected_api={k:pin("hindsight")[k] for k in ("name","id","image")}
        if components["activation"].get("memory_api",{}).get("container")!=expected_api:
            raise ValueError("activation does not select the prepared memory API")
    config={"dsn":target_dsn,"authority":str(_absolute(authority)),"workspace":str(_absolute(workspace)),
            "objects":str(_absolute(objects)),"keyring":str(_absolute(keyring)) if keyring else None,
            "traffic_closed":True,"runtime_guard":guard,**components}
    if runtime_identity is not None:config["runtime_identity"]=deepcopy(runtime_identity)
    pending=tuple(sorted(_COMPONENT_KEYS-set(components)))
    return PreparedRestoreConfiguration(config,{
        "contract":"EvaPreparedRestoreConfiguration/v1","namespace":workflow["namespace"],
        "target_database":database,"memory_target":memory,"runtime_guard":deepcopy(guard),
        "scope":"core_restore_configuration","activation_configured":"activation" in components,
        "restore_executed":False},pending)
