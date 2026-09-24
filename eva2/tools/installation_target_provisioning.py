"""Prepare persistent restore resources; only databases and schema setup may run."""
from copy import deepcopy
import json
from pathlib import Path
from sqlalchemy.engine import make_url
from eva.operations.installer_safety import require_owner_only_directory
import re
import time
import uuid

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.installer_identity_storage import atomic_write_private
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs
from tools.installation_document_connection import _command

_LABEL = "io.eva2.restore-preparation"


class TargetPreparationError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = deepcopy(receipt)
        super().__init__("target preparation incomplete; retain recorded resources, do not retry")


def _write_private(path, value):
    atomic_write_private(path, canonical_json_bytes(value))


def _validate(inputs, graph, namespace, roles, databases, schema, materialized_bind_root):
    if materialized_bind_root is not None:
        materialized_bind_root = Path(materialized_bind_root)
        if not materialized_bind_root.is_absolute() or materialized_bind_root.resolve() != materialized_bind_root:
            raise ValueError("materialized target root must be absolute and unlinked")
        require_owner_only_directory(materialized_bind_root, error=ValueError,
            unavailable_message="target materialization root unavailable",
            invalid_message="target materialization root must be private")
    if not isinstance(inputs, InstallerRuntimeInputs):
        raise ValueError("bound installer inputs required")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", namespace):
        raise ValueError("invalid target namespace")
    services = graph.get("services", {})
    if (not services or set(roles) != set(services) or not databases
            or len(set(databases)) != len(databases) or schema in databases
            or not set((*databases, schema)) <= set(services)):
        raise ValueError("explicit service roles required")
    if set(graph) - {"name", "services", "volumes", "networks"}:
        raise ValueError("unsupported target graph sections")
    approved = dict(inputs.receipt.images)
    volumes, networks = graph.get("volumes", {}), graph.get("networks", {})
    if not volumes or len(networks) != 1:
        raise ValueError("persistent storage and one isolated network required")
    names = set()
    for kind, resources in (("volume", volumes), ("network", networks)):
        for spec in resources.values():
            name = spec.get("name", "")
            allowed = {"name", "labels"} | ({"internal", "ipam"} if kind == "network" else set())
            if (set(spec) - allowed or not name.startswith(namespace + "-")
                    or name in names or not re.fullmatch(r"[a-z0-9-]+", name)):
                raise ValueError("target resources must be new local named resources")
            if kind == "network" and spec.get("internal") is not True:
                raise ValueError("target network must be internal")
            names.add(name)
    for name, service in services.items():
        if (service.get("image") != approved.get(roles[name]) or roles[name] not in approved
                or service.get("restart") != "no" or service.get("ports")
                or service.get("network_mode") or service.get("privileged")
                or any(key in service for key in ("build", "extends", "env_file", "secrets", "configs"))):
            raise ValueError("target service image or isolation differs")
        container = service.get("container_name", "")
        if not container.startswith(namespace + "-") or container in names:
            raise ValueError("distinct explicit target container names required")
        names.add(container)
        if set(service.get("networks", {})) != set(networks):
            raise ValueError("service must use only the target network")
        persistent_targets = []
        for mount in service.get("volumes", []):
            if not isinstance(mount, dict):
                raise ValueError("rendered long mount syntax required")
            if mount.get("type") == "volume":
                if mount.get("source") not in volumes:
                    raise ValueError("unknown persistent volume")
                persistent_targets.append(mount.get("target", ""))
            elif mount.get("type") == "bind":
                source = Path(mount.get("source", ""))
                if (not source.is_absolute() or source.resolve() != source or not source.exists()
                        or mount.get("bind", {}).get("create_host_path") is not False):
                    raise ValueError("materialized bind source required")
                if mount.get("read_only") is not True and (
                        materialized_bind_root is None or not source.is_relative_to(materialized_bind_root)):
                    raise ValueError("writable bind must belong to fresh target materialization")
            else:
                raise ValueError("unsupported mount type")
        if name in databases:
            environment = service.get("environment", {})
            if any(not isinstance(environment.get(key), str) or not environment[key]
                   for key in ("POSTGRES_USER", "POSTGRES_DB")):
                raise ValueError("explicit PostgreSQL readiness identity required")
            data_path = environment.get("PGDATA", "/var/lib/postgresql/data")
            if (not any(data_path == target or data_path.startswith(target.rstrip("/") + "/")
                        for target in persistent_targets if target.startswith("/"))
                    or service.get("tmpfs") or not service.get("healthcheck", {}).get("test")):
                raise ValueError("database requires persistent storage and readiness check")
    setup = services[schema]
    if (setup.get("extra_hosts")
            or setup.get("entrypoint") != ["/usr/local/bin/python"]
            or setup.get("command") != ["-m", "eva.operations.install_schema"]):
        raise ValueError("maintained schema installer required")
    dsn_target = setup.get("environment", {}).get("EVA_DATABASE_DSN_FILE")
    candidates = [m for m in setup.get("volumes", [])
                  if m.get("type") == "bind" and m.get("target") == dsn_target
                  and m.get("read_only") is True]
    if len(candidates) != 1:
        raise ValueError("schema installer requires an exact readonly DSN file mount")
    dsn_source = Path(candidates[0]["source"])
    if not dsn_source.is_file() or dsn_source.stat().st_size > 65536:
        raise ValueError("schema DSN file is invalid")
    try:
        dsn = make_url(dsn_source.read_text().strip())
    except Exception:
        raise ValueError("schema DSN is invalid") from None
    matched = []
    for name in databases:
        service = services[name]
        aliases = {name, service["container_name"]}
        for endpoint in service["networks"].values():
            aliases.update((endpoint or {}).get("aliases", []))
        environment = service["environment"]
        if (dsn.get_backend_name() == "postgresql" and dsn.host in aliases
                and dsn.port in {None, 5432} and dsn.database == environment["POSTGRES_DB"]
                and dsn.username == environment["POSTGRES_USER"] and not dsn.query):
            matched.append(name)
    if len(matched) != 1:
        raise ValueError("schema DSN must select one declared target database")


def prepare_restore_target(*, runtime_inputs, launch_graph, namespace, image_roles,
                           database_services, schema_service, workspace,
                           materialized_bind_root=None, command=_command, timeout_seconds=120):
    """Consume trusted installer render output, never browser-provided graph data.

    Failed resources remain recorded and are never automatically reused or removed.
    This prepares prerequisites; it does not restore archives or release runtime.
    """
    graph = deepcopy(launch_graph)
    validation_graph = deepcopy(graph)
    _validate(runtime_inputs, graph, namespace, image_roles, database_services, schema_service,
              materialized_bind_root)
    if not 0 < timeout_seconds <= 300:
        raise ValueError("bounded timeout required")
    workspace = Path(workspace)
    if not workspace.is_absolute() or workspace.resolve() != workspace:
        raise ValueError("absolute unlinked workspace required")
    workspace.mkdir(mode=0o700, exist_ok=False)
    receipt = {"contract": "EvaRestoreTargetPreparation/v1", "status": "preparing",
               "namespace": namespace, "plan_sha256": runtime_inputs.receipt.plan_sha256,
               "graph_sha256": canonical_sha256(graph), "volumes": [], "networks": [],
               "containers": [], "started": [], "schema_completed": False,
               "planned_containers": [s["container_name"] for s in graph["services"].values()]}
    nonce = uuid.uuid4().hex
    receipt_path = workspace / "preparation-receipt.json"
    def save():
        _write_private(receipt_path, receipt)
    def run(*args):
        return command(["docker", *args], deadline=timeout_seconds)
    def row(kind, identifier):
        return json.loads(run(*([kind] if kind else []), "inspect", identifier))[0]
    save()
    try:
        # A project collision must not become implicit Compose adoption.
        for kind, args in (("containers", ("ps", "-aq")),
                           ("volumes", ("volume", "ls", "-q")),
                           ("networks", ("network", "ls", "-q"))):
            if run(*args, "--filter", "label=com.docker.compose.project=" + namespace).strip():
                raise ValueError("target project already exists")
        existing_containers = set(run("ps", "-a", "--format", "{{.Names}}").splitlines())
        existing_volumes = set(run("volume", "ls", "--format", "{{.Name}}").splitlines())
        existing_networks = set(run("network", "ls", "--format", "{{.Name}}").splitlines())
        if (any(s["container_name"] in existing_containers for s in graph["services"].values())
                or any(s["name"] in existing_volumes for s in graph["volumes"].values())
                or any(s["name"] in existing_networks for s in graph["networks"].values())):
            raise ValueError("target resource name already exists")
        image_ids = {service["image"]: row("image", service["image"])["Id"]
                     for service in graph["services"].values()}
        for kind in ("volumes", "networks"):
            for key, spec in list(graph[kind].items()):
                singular = kind[:-1]
                args = [singular, "create", "--label", _LABEL + "=" + nonce]
                for label, value in spec.get("labels", {}).items():
                    args += ["--label", label + "=" + str(value)]
                if singular == "network":
                    args += ["--internal"]
                    for entry in spec.get("ipam", {}).get("config", []):
                        if set(entry) != {"subnet"}:
                            raise ValueError("only explicit subnet IPAM supported")
                        args += ["--subnet", entry["subnet"]]
                args.append(spec["name"])
                identifier = run(*args).strip()
                receipt[kind].append({"name": spec["name"], "id": identifier})
                save()
                actual = row(singular, identifier)
                if (actual.get("Labels", {}).get(_LABEL) != nonce
                        or actual.get("Name") != spec["name"]
                        or (singular == "network" and (actual.get("Id") != identifier
                                                       or actual.get("Internal") is not True))):
                    raise ValueError("created resource ownership or isolation differs")
                graph[kind][key] = {"external": True, "name": spec["name"]}
        graph["name"] = namespace
        for service in graph["services"].values():
            service.setdefault("labels", {})[_LABEL] = nonce
        manifest = workspace / "compose.json"
        _write_private(manifest, graph)
        run("compose", "--project-name", namespace, "--file", str(manifest),
            "--profile", "*", "create", "--no-build", "--pull", "never", "--no-recreate",
            *graph["services"])
        by_service = {}
        for name, service in graph["services"].items():
            actual = row("", service["container_name"])
            pin = {"service": name, "name": service["container_name"],
                   "id": actual["Id"], "image": actual["Image"]}
            receipt["containers"].append(pin)
            save()
            if (actual.get("Config", {}).get("Labels", {}).get(_LABEL) != nonce
                    or actual["Image"] != image_ids[service["image"]]
                    or actual["State"]["Running"] or actual["State"]["Status"] != "created"):
                raise ValueError("target container identity or stopped state differs")
            network_name = next(iter(graph["networks"].values()))["name"]
            network_id = receipt["networks"][0]["id"]
            attached = actual.get("NetworkSettings", {}).get("Networks", {})
            if (set(attached) != {network_name}
                    or actual.get("HostConfig", {}).get("NetworkMode") not in {network_name, network_id}
                    or attached[network_name].get("NetworkID") not in {"", network_id}):
                raise ValueError("created target network differs")
            if name == schema_service and actual.get("HostConfig", {}).get("ExtraHosts"):
                raise ValueError("schema target host override is forbidden")
            actual_mounts = actual.get("Mounts", [])
            for mount in service.get("volumes", []):
                if mount["type"] == "volume":
                    volume_name = graph["volumes"][mount["source"]]["name"]
                    if not any(item.get("Type") == "volume" and item.get("Name") == volume_name
                               and item.get("Destination") == mount["target"] for item in actual_mounts):
                        raise ValueError("created persistent storage differs")
                elif mount["type"] == "bind":
                    if not any(item.get("Type") == "bind" and item.get("Source") == mount["source"]
                               and item.get("Destination") == mount["target"]
                               and item.get("RW") is (mount.get("read_only") is not True)
                               for item in actual_mounts):
                        raise ValueError("created materialized bind differs")
            by_service[name] = pin
        def start_and_wait(name, schema=False):
            identifier = by_service[name]["id"]
            receipt["started"].append({"service": name, "id": identifier, "attempted": True})
            save()
            run("start", identifier)
            deadline = time.monotonic() + timeout_seconds
            while True:
                state = row("", identifier)["State"]
                if schema and state.get("Status") == "exited":
                    if state.get("ExitCode") != 0:
                        raise ValueError("schema installation failed")
                    return
                if not schema and state.get("Health", {}).get("Status") == "healthy":
                    environment = graph["services"][name]["environment"]
                    try:
                        run("exec", identifier, "pg_isready", "-h", "127.0.0.1",
                            "-U", environment["POSTGRES_USER"], "-d", environment["POSTGRES_DB"])
                        return
                    except RuntimeError:
                        # Image health can briefly pass against its init socket.
                        pass
                if state.get("Status") in {"dead", "exited"} or time.monotonic() >= deadline:
                    raise ValueError("target prerequisite did not become ready")
                time.sleep(.25)
        for name in database_services:
            start_and_wait(name)
        # Re-read target DSN and private materialization after database startup.
        _validate(runtime_inputs, validation_graph, namespace, image_roles, database_services,
                  schema_service, materialized_bind_root)
        start_and_wait(schema_service, schema=True)
        receipt["schema_completed"] = True
        for name, pin in by_service.items():
            if name not in {*database_services, schema_service} and row("", pin["id"])["State"]["Running"]:
                raise ValueError("restore runtime started prematurely")
        receipt["status"] = "prepared"
        save()
        return deepcopy(receipt)
    except Exception as error:
        # Compose may create a subset before returning an error. Recover only
        # identities carrying this invocation's unique ownership label.
        known = {pin["id"] for pin in receipt["containers"]}
        for name, service in graph["services"].items():
            try:
                actual = row("", service["container_name"])
                if (actual.get("Config", {}).get("Labels", {}).get(_LABEL) == nonce
                        and actual["Id"] not in known):
                    receipt["containers"].append({"service": name,
                        "name": service["container_name"], "id": actual["Id"],
                        "image": actual["Image"]})
            except Exception:
                pass
        receipt["status"] = "incomplete"
        receipt["failure_type"] = type(error).__name__
        save()
        raise TargetPreparationError(receipt) from error
