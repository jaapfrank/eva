"""Initialize and start a prepared fresh target through its inspected Docker IP.

Trusted operator inputs only. The host must reach the isolated target database;
failure never falls back to another DSN or removes/retries retained resources.
Docker health is readiness evidence, not a full installation acceptance.
"""
from copy import deepcopy
from dataclasses import asdict
import ipaddress
import json
from pathlib import Path
import time

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.initialize_dashboard_authority import initialize_dashboard_authority
from tools.eva2_quiescent_cutover import SystemBackend, _shared_database_consumers
from tools.installation_document_connection import _command
from tools.installation_target_provisioning import _validate


_LINEAGE = """SELECT current_database() AS name,
    (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid,
    (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier"""


class FreshRuntimeStartError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = deepcopy(receipt)
        super().__init__("fresh target start unconfirmed; inspect retained resources, do not retry")


def _mount(service, setting):
    target = service.get("environment", {}).get(setting)
    matches = [m for m in service.get("volumes", []) if m.get("type") == "bind"
               and m.get("target") == target and m.get("read_only") is True]
    if len(matches) != 1:
        raise ValueError("exact readonly credential mount required")
    return Path(matches[0]["source"])


def _order(graph, selected, prerequisites):
    pending, result = list(selected), []
    if not pending or len(set(pending)) != len(pending) or set(pending) & prerequisites:
        raise ValueError("distinct explicit runtime service selection required")
    while pending:
        for name in pending:
            dependencies = set(graph["services"][name].get("depends_on", {}))
            if not dependencies <= set(selected) | prerequisites:
                raise ValueError("runtime dependency was not selected")
            if dependencies <= set(result) | prerequisites:
                result.append(name)
                pending.remove(name)
                break
        else:
            raise ValueError("runtime dependency cycle")
    return result


def start_fresh_runtime(*, runtime_inputs, launch_graph, preparation, image_roles,
                        database_services, database_service, schema_service,
                        core_service, writer_services, authority,
                        materialized_bind_root=None, timeout_seconds=60,
                        command=_command, inspect_all=None):
    """Reuse schema preparation and existing owner initialization, without restore.

    Authority contains tenant_id, installation_id, principal_id, binding_id,
    release_sha256 and access_token from the trusted operator. No receipt stores
    the token or DSN. Injection hooks are for controlled tests only. This callable
    requires Linux Docker IPv4 reachability from the operator host; it adds no
    published port, extra network or alternate credential route.
    """
    receipt = {"contract": "EvaFreshRuntimeStart/v1", "status": "not_started",
               "authority_initialized": False, "attempted": [], "started": [],
               "ready": [], "runtime_started": False}
    inspect = SystemBackend._inspect_all if inspect_all is None else inspect_all
    engine = None
    try:
        graph, prepared, authority = deepcopy(launch_graph), deepcopy(preparation), deepcopy(authority)
        if (prepared.get("contract") != "EvaRestoreTargetPreparation/v1"
                or prepared.get("status") != "prepared" or prepared.get("schema_completed") is not True
                or prepared.get("graph_sha256") != canonical_sha256(graph)
                or prepared.get("plan_sha256") != runtime_inputs.receipt.plan_sha256):
            raise ValueError("completed preparation must match trusted graph and inputs")
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120:
            raise ValueError("bounded readiness deadline required")
        _validate(runtime_inputs, graph, prepared["namespace"], image_roles,
                  database_services, schema_service, materialized_bind_root)
        services = graph["services"]
        order = _order(graph, tuple(writer_services), {*database_services, schema_service})
        if database_service not in database_services or core_service not in order:
            raise ValueError("explicit prepared database and core required")
        pins = {pin["service"]: pin for pin in prepared["containers"]}
        if len(pins) != len(prepared["containers"]) or set(pins) != set(services):
            raise ValueError("prepared service identities differ")
        if len({pin["id"] for pin in pins.values()}) != len(pins):
            raise ValueError("prepared identities overlap")
        if len(prepared["networks"]) != 1:
            raise ValueError("one prepared network required")
        network = prepared["networks"][0]
        if network["name"] != next(iter(graph["networks"].values()))["name"]:
            raise ValueError("prepared network name differs")
        for name, spec in services.items():
            images = json.loads(command(["docker", "image", "inspect", spec["image"]]))
            if (len(images) != 1 or pins[name]["image"] != images[0]["Id"]
                    or pins[name]["name"] != spec["container_name"]):
                raise ValueError("prepared image binding differs")
        keys = {"tenant_id", "installation_id", "principal_id", "binding_id", "release_sha256", "access_token"}
        if set(authority) != keys or authority["release_sha256"] != pins[core_service]["image"].removeprefix("sha256:"):
            raise ValueError("fixed owner authority must match verified release")
        core = services[core_service]
        for setting, key in (("EVA_DASHBOARD_TENANT_ID", "tenant_id"),
                             ("EVA_DASHBOARD_INSTALLATION_ID", "installation_id"),
                             ("EVA_RUNTIME_PRINCIPAL_ID", "principal_id"),
                             ("EVA_DASHBOARD_PRINCIPAL_BINDING_ID", "binding_id"),
                             ("EVA_DASHBOARD_RELEASE_SHA256", "release_sha256")):
            if core.get("environment", {}).get(setting) != authority[key]:
                raise ValueError("runtime owner authority differs")
        if _mount(core, "EVA_DASHBOARD_ACCESS_TOKEN_FILE").read_text().strip() != authority["access_token"]:
            raise ValueError("runtime owner token differs")
        dsn_file = _mount(services[schema_service], "EVA_DATABASE_DSN_FILE")
        core_dsn_file = _mount(core, "EVA_DATABASE_DSN_FILE")
        core_dsn = make_url(core_dsn_file.read_text().strip())
        dsn = make_url(dsn_file.read_text().strip())
        db_environment = services[database_service]["environment"]
        if dsn.database != db_environment["POSTGRES_DB"] or dsn.username != db_environment["POSTGRES_USER"]:
            raise ValueError("selected database identity differs")
        aliases = {database_service, pins[database_service]["name"]}
        for endpoint in services[database_service]["networks"].values():
            aliases.update((endpoint or {}).get("aliases", []))
        if dsn.host not in aliases:
            raise ValueError("schema DSN selects another prepared database")
        if (core_dsn.get_backend_name() != "postgresql" or core_dsn.host not in aliases
                or core_dsn.database != dsn.database or core_dsn.port not in {None, 5432}
                or core_dsn.query or not core_dsn.username):
            raise ValueError("core DSN selects another database")
        started = set()

        def inventory():
            documents = inspect()
            networks = json.loads(command(["docker", "network", "inspect", network["id"]]))
            if (len(networks) != 1 or networks[0].get("Id") != network["id"]
                    or networks[0].get("Name") != network["name"] or networks[0].get("Internal") is not True
                    or set(networks[0].get("Containers", {})) - {pin["id"] for pin in pins.values()}):
                raise ValueError("prepared isolated network differs")
            rows = {}
            for name, pin in pins.items():
                matches = [row for row in documents if row.get("Name") == "/" + pin["name"]]
                if len(matches) != 1:
                    raise ValueError("prepared container missing or duplicated")
                row = matches[0]
                state, host = row["State"], row["HostConfig"]
                environment = dict(item.split("=", 1) for item in row.get("Config", {}).get("Env", []))
                if any(environment.get(key) != str(value) for key, value in services[name].get("environment", {}).items()):
                    raise ValueError("prepared runtime environment differs")
                attached = row.get("NetworkSettings", {}).get("Networks", {})
                if (row.get("Id") != pin["id"] or row.get("Image") != pin["image"]
                        or host.get("RestartPolicy", {}).get("Name") != "no"
                        or host.get("PortBindings") or host.get("Privileged") or host.get("ExtraHosts")
                        or state.get("Paused") is not False or state.get("Restarting") is not False
                        or set(attached) != {network["name"]}
                        or attached[network["name"]].get("NetworkID") not in {"", network["id"]}):
                    raise ValueError("prepared container identity or isolation changed")
                for mount in services[name].get("volumes", []):
                    source = mount["source"] if mount["type"] == "bind" else graph["volumes"][mount["source"]]["name"]
                    source_key = "Source" if mount["type"] == "bind" else "Name"
                    if not any(m.get("Type") == mount["type"] and m.get(source_key) == source
                               and m.get("Destination") == mount["target"]
                               and m.get("RW") is (mount.get("read_only") is not True) for m in row.get("Mounts", [])):
                        raise ValueError("prepared credential or data mount differs")
                running = name in started or name in database_services
                if running:
                    if (state.get("Running") is not True or state.get("Status") != "running"
                            or type(state.get("Pid")) is not int or state["Pid"] <= 0
                            or attached[network["name"]].get("NetworkID") != network["id"]):
                        raise ValueError("prepared running service differs")
                    if name in database_services and state.get("Health", {}).get("Status") != "healthy":
                        raise ValueError("prepared database is unhealthy")
                elif (state.get("Running") is not False or state.get("Pid") != 0
                      or state.get("Status") not in {"created", "exited"}):
                    raise ValueError("unselected service is not fenced")
                if name == schema_service and (state.get("Status") != "exited" or state.get("ExitCode") != 0):
                    raise ValueError("schema execution is not complete")
                rows[name] = row
            consumers = set(_shared_database_consumers(retained_core=pins[core_service]["name"], inspected=documents))
            if consumers - {pins[name]["name"] for name in started}:
                raise ValueError("additional target database writer appeared")
            return rows

        rows = inventory()
        address = str(ipaddress.IPv4Address(rows[database_service]["NetworkSettings"]["Networks"][network["name"]]["IPAddress"]))
        derived_dsn = dsn.set(host=address, port=5432).render_as_string(hide_password=False)
        engine = sa.create_engine(sqlalchemy_postgres_dsn(derived_dsn),
                                  connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"})

        def guard(*, fresh=False):
            if (make_url(dsn_file.read_text().strip()) != dsn
                    or make_url(core_dsn_file.read_text().strip()) != core_dsn
                    or _mount(core, "EVA_DASHBOARD_ACCESS_TOKEN_FILE").read_text().strip() != authority["access_token"]):
                raise ValueError("prepared runtime credentials changed")
            current = inventory()
            if current[database_service]["NetworkSettings"]["Networks"][network["name"]].get("IPAddress") != address:
                raise ValueError("prepared database address changed")
            query = "SELECT row_to_json(identity) FROM (" + _LINEAGE + ") identity"
            docker_lineage = json.loads(command(["docker", "exec", pins[database_service]["id"],
                "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", dsn.username,
                "-d", dsn.database, "-c", query]))
            with engine.connect() as connection:
                lineage = dict(connection.exec_driver_sql(_LINEAGE).mappings().one())
                if lineage != docker_lineage or lineage["name"] != dsn.database:
                    raise ValueError("connection does not reach prepared database lineage")
                if receipt.get("target_database", lineage) != lineage:
                    raise ValueError("prepared database lineage changed")
                if fresh:
                    for table in ("tenants", "installations", "principals", "principal_bindings"):
                        if connection.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM eva_core." + table + ")").scalar_one():
                            raise ValueError("fresh target already contains owner scope")
                    if connection.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM eva_core.restore_activation)").scalar_one():
                        raise ValueError("fresh target contains restore activation state")
            return lineage

        receipt["target_database"] = guard(fresh=True)
        receipt["authority_initialized"] = None
        initialized = initialize_dashboard_authority(derived_dsn, **authority)
        if initialized.status != "initialized":
            raise ValueError("fresh owner initialization did not create a new scope")
        receipt["authority"] = asdict(initialized)
        receipt["authority_initialized"] = True
        guard()
        for name in order:
            guard()
            pin = dict(pins[name])
            receipt["attempted"].append(pin)
            receipt["runtime_started"] = None
            command(["docker", "start", pin["id"]], deadline=30)
            started.add(name)
            deadline = time.monotonic() + timeout_seconds
            while True:
                guard()
                health = inventory()[name]["State"].get("Health", {}).get("Status")
                if pin not in receipt["started"]:
                    receipt["started"].append(pin)
                if health == "healthy":
                    receipt["ready"].append(pin)
                    break
                if health != "starting" or time.monotonic() >= deadline:
                    raise ValueError("fresh runtime readiness not confirmed")
                time.sleep(.25)
        guard()
        if any(inventory()[name]["State"].get("Health", {}).get("Status") != "healthy" for name in started):
            raise ValueError("fresh runtime readiness changed")
        receipt.update(status="runtime_started_verified", runtime_started=True,
                       verification_scope="Pinned Docker health only; no channel, model or owner acceptance")
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise FreshRuntimeStartError(receipt) from error
    finally:
        if engine is not None:
            engine.dispose()
