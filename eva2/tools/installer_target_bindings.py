"""Bind installer-owned files into the maintained isolated service graph."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
from sqlalchemy.engine import make_url

from eva.operations.installer_identity_storage import read_private_artifact, write_private_once
from eva.operations.installer_safety import require_owner_only_directory
from tools.managed_b_render import isolated_restore_graph


@dataclass(frozen=True, slots=True)
class TargetGraphBinding:
    graph: dict | None
    pending: tuple[str, ...]
    files: tuple[tuple[str, str, str], ...]
    pending_runtime_checks: tuple[str, ...] = ("runtime_reader_ownership", "runtime_readiness")
    runtime_readiness_verified: bool = False


_FILE_ROLES = {
    "/run/secrets/database_dsn": "core_database_dsn",
    "/run/secrets/hindsight_database_dsn": "memory_database_dsn",
    "/run/eva/runtime-budget/current.json": "runtime_budget",
    "/run/secrets/home_assistant_token": "provider:home_assistant",
    "/run/secrets/telegram_bot_token": "provider:telegram",
}
_DIRECTORY_ROLES = {
    "/run/eva/model-lease": ("model_access_lease", "model-lease"),
    "/run/eva/hindsight-access": ("hindsight_consumer_auth", "hindsight-access"),
    "/home/hindsight/.codex": ("hindsight_consumer_auth", "hindsight-access"),
}


def _private_root(root):
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("target binding root must be absolute without links")
    require_owner_only_directory(root,error=ValueError,
        unavailable_message="target binding root unavailable", invalid_message="target binding root is not private")


def bind_installer_target_graph(*, render_receipt, rendered_graph, namespace,
                                materialization_receipt, expected_plan_sha256,
                                materialized_bind_root, additional_bindings=None, network_subnet=None, current_plan=None,
                                fresh_installation=False, local_port=None):
    """Return no candidate graph until every existing bind has an explicit target.

    A non-None graph proves file selection only. It is not a runtime-readable or
    ready installation; ownership and actual runtime checks remain explicit.

    Additional bindings are trusted in-process mappings keyed by
    (service_name, container_destination). They are never accepted from a browser.
    Read-only approved authorities may be reused; writable binds must be inside
    the installer-owned target root. This does not set runtime UID/GID ownership.
    """
    material = deepcopy(materialization_receipt)
    if (material.get("contract") != "EvaInstallerRuntimeMaterialization/v1"
            or material.get("status") != "materialized"
            or material.get("plan_sha256") != expected_plan_sha256):
        raise ValueError("materialization receipt differs from confirmed plan")
    root = Path(materialized_bind_root)
    _private_root(root)
    graph = isolated_restore_graph(render_receipt, rendered_graph, namespace=namespace, network_subnet=network_subnet,
                                   fresh_installation=fresh_installation, local_port=local_port)
    omitted_bindings = set()
    if current_plan is not None:
        from tools.installer_optional_capabilities import project_optional_capabilities
        graph, omitted_bindings = project_optional_capabilities(graph, current_plan, expected_plan_sha256)
    roles, evidence = {}, []
    for item in material["files"]:
        path = Path(item["path"])
        if not path.is_relative_to(root) or path.resolve() != path or item["role"] in roles:
            raise ValueError("materialized role path differs")
        raw = read_private_artifact(path,label="materialized runtime input")
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("materialized runtime input changed")
        roles[item["role"]] = path
        evidence.append((item["role"],str(path),item["sha256"]))
    pending = []
    databases = {}
    for descriptor in material["databases"]:
        host = descriptor["service_hostname"]
        matches = []
        for name, service in graph["services"].items():
            env = service.get("environment", {})
            aliases = {name, service["container_name"]}
            for endpoint in service.get("networks", {}).values():
                aliases.update((endpoint or {}).get("aliases", []))
            if host in aliases and env.get("POSTGRES_DB") == descriptor["database"] and env.get("POSTGRES_USER") == descriptor["username"]:
                matches.append(name)
        if len(matches) != 1 or matches[0] in databases:
            raise ValueError("materialized database does not select one maintained service")
        dsn_path = roles.get("database_dsn:"+host)
        password_path = roles.get("database_password:"+host)
        if str(dsn_path) != descriptor["dsn_path"] or str(password_path) != descriptor["password_path"]:
            raise ValueError("database materialization paths differ")
        dsn = make_url(read_private_artifact(dsn_path,label="target database DSN").decode())
        password = read_private_artifact(password_path,label="target database password").decode()
        if (dsn.get_backend_name() != "postgresql" or dsn.host != host or dsn.port != 5432
                or dsn.database != descriptor["database"] or dsn.username != descriptor["username"]
                or dsn.password != password or dsn.query):
            raise ValueError("target database DSN differs from maintained service")
        databases[matches[0]] = (password_path, dsn_path)
    for name, role in (("postgres","core_database_dsn"),("hindsight-postgres","memory_database_dsn")):
        if name in databases:
            roles[role] = databases[name][1]
    directories = {}
    for role, directory in set(_DIRECTORY_ROLES.values()):
        if role not in roles:
            continue
        target = root/directory
        if not target.exists():
            target.mkdir(mode=0o700)
        _private_root(target)
        raw = read_private_artifact(roles[role],label="materialized model consumer")
        write_private_once(target/"auth.json",raw)
        directories[role] = target
        evidence.append(("layout:"+role,str(target/"auth.json"),hashlib.sha256(raw).hexdigest()))
    extras = {key: value for key, value in (additional_bindings or {}).items()
              if key not in omitted_bindings}
    used = set()
    for name, service in graph["services"].items():
        for mount in service.get("volumes", []):
            if mount.get("type") != "bind":
                continue
            destination = mount["target"]
            selected = None
            if name in databases and destination == service.get("environment", {}).get("POSTGRES_PASSWORD_FILE"):
                selected = databases[name][0]
            elif destination in _FILE_ROLES:
                selected = roles.get(_FILE_ROLES[destination])
            elif destination in _DIRECTORY_ROLES:
                selected = directories.get(_DIRECTORY_ROLES[destination][0])
            if selected is None and (name,destination) in extras:
                selected = Path(extras[(name,destination)])
                used.add((name,destination))
            if selected is None and destination == "/var/lib/eva/restore-authority":
                # New empty target storage, not a fabricated restore authority.
                selected = root / "restore-authority"
                if not selected.exists():
                    selected.mkdir(mode=0o700)
                _private_root(selected)
                if any(selected.iterdir()):
                    raise ValueError("new restore authority storage must be empty")
            if selected is None:
                pending.append(name+":"+destination)
                continue
            selected = Path(selected)
            if not selected.is_absolute() or selected.resolve() != selected or not selected.exists():
                raise ValueError("explicit target bind is unavailable or linked")
            directory_target = destination in {*_DIRECTORY_ROLES, "/run/eva/codex-owner", "/var/lib/eva/restore-authority"}
            if directory_target and not selected.is_dir():
                raise ValueError("maintained directory bind requires a directory")
            if not directory_target and not selected.is_file():
                raise ValueError("maintained file bind requires a regular file")
            if mount.get("read_only") is not True and not selected.is_relative_to(root):
                raise ValueError("writable bind would reuse source storage")
            mount["source"] = str(selected)
            mount.setdefault("bind", {})["create_host_path"] = False
    if set(extras) != used:
        raise ValueError("additional bindings contain unused or overridden targets")
    return TargetGraphBinding(None if pending else graph,tuple(sorted(pending)),tuple(evidence))
