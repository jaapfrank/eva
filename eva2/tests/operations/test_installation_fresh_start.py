"""Mocked fresh startup checks; these tests never invoke Docker or a model."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.initialize_dashboard_authority import DashboardAuthorityReceipt
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs, RuntimeInputReceipt
from tools import installation_fresh_start as subject


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one(self):
        return self.value

    def scalar_one(self):
        return self.value


@pytest.fixture
def target(tmp_path, monkeypatch):
    authority = dict(tenant_id="00000000-0000-0000-0000-000000000001",
                     installation_id="00000000-0000-0000-0000-000000000002",
                     principal_id="00000000-0000-0000-0000-000000000003",
                     binding_id="00000000-0000-0000-0000-000000000004",
                     release_sha256="1" * 64, access_token="private-token-" * 4)
    inputs = InstallerRuntimeInputs(RuntimeInputReceipt("a"*64, "b"*64, "c"*64, "d"*64,
        (("core", "sha256:"+"1"*64), ("pg", "sha256:"+"2"*64)), ()), ())
    dsn = tmp_path / "dsn"
    dsn.write_text("postgresql://postgres:private@db/eva")
    token = tmp_path / "token"
    token.write_text(authority["access_token"])
    def mount(source, destination):
        return dict(type="bind", source=str(source), target=destination,
                    read_only=True, bind={"create_host_path": False})
    services = {}
    for name in ("db", "schema", "core", "worker", "unused"):
        services[name] = dict(container_name="new-target-"+name,
            image=dict(inputs.receipt.images)["pg" if name == "db" else "core"],
            restart="no", networks={"local": {}}, volumes=[], environment={})
    services["db"].update(environment={"POSTGRES_USER": "postgres", "POSTGRES_DB": "eva"},
        volumes=[dict(type="volume", source="data", target="/var/lib/postgresql/data")],
        healthcheck={"test": ["CMD", "pg_isready"]})
    services["schema"].update(entrypoint=["/usr/local/bin/python"],
        command=["-m", "eva.operations.install_schema"],
        environment={"EVA_DATABASE_DSN_FILE": "/run/secrets/database_dsn"},
        volumes=[mount(dsn, "/run/secrets/database_dsn")])
    services["core"].update(environment={
        "EVA_DATABASE_DSN_FILE": "/run/secrets/database_dsn",
        "EVA_DASHBOARD_ACCESS_TOKEN_FILE": "/run/secrets/token",
        **{key: authority[field] for key, field in (
            ("EVA_DASHBOARD_TENANT_ID", "tenant_id"),
            ("EVA_DASHBOARD_INSTALLATION_ID", "installation_id"),
            ("EVA_RUNTIME_PRINCIPAL_ID", "principal_id"),
            ("EVA_DASHBOARD_PRINCIPAL_BINDING_ID", "binding_id"),
            ("EVA_DASHBOARD_RELEASE_SHA256", "release_sha256"))}},
        volumes=[mount(dsn, "/run/secrets/database_dsn"), mount(token, "/run/secrets/token")],
        depends_on={"db": {"condition": "service_healthy"}})
    services["worker"]["depends_on"] = {"core": {"condition": "service_healthy"}}
    graph = dict(services=services, volumes={"data": {"name": "new-target-data"}},
        networks={"local": {"name": "new-target-network", "internal": True}})
    pins = [dict(service=name, name=spec["container_name"], id=str(i)*64, image=spec["image"])
            for i, (name, spec) in enumerate(services.items(), 1)]
    prepared = dict(contract="EvaRestoreTargetPreparation/v1", status="prepared", schema_completed=True,
        namespace="new-target", plan_sha256=inputs.receipt.plan_sha256,
        graph_sha256=canonical_sha256(graph), containers=pins,
        networks=[{"name": "new-target-network", "id": "e"*64}])
    rows = []
    for pin in pins:
        name = pin["service"]
        spec = services[name]
        rows.append(dict(Id=pin["id"], Name="/"+pin["name"], Image=pin["image"],
            Config={"Env": [key+"="+value for key, value in spec["environment"].items()]},
            HostConfig={"RestartPolicy": {"Name": "no"}},
            State=dict(Running=name == "db", Status="running" if name == "db" else "exited" if name == "schema" else "created",
                Pid=10 if name == "db" else 0, Paused=False, Restarting=False, ExitCode=0, Health={"Status": "healthy"}),
            NetworkSettings={"Networks": {"new-target-network": {"NetworkID": "e"*64, "IPAddress": "172.30.0.2"}}},
            Mounts=[dict(Type=m["type"], Source=m["source"], Name="new-target-data",
                Destination=m["target"], RW=m.get("read_only") is not True) for m in spec["volumes"]]))
    state = SimpleNamespace(lineage={"name": "eva", "oid": 12, "system_identifier": "1234"},
        host_lineage=None, occupied=None, commands=[], initialized=[], rows=rows, disposed=False,
        fail_start=False, fail_connect=False, drift=False, dsn=None)
    class Engine:
        def connect(self):
            if state.fail_connect:
                raise RuntimeError("target route unreachable")
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def exec_driver_sql(self, sql):
            if "system_identifier" in sql:
                return Result(state.host_lineage or deepcopy(state.lineage))
            return Result(state.occupied is not None and state.occupied in sql)

        def dispose(self):
            state.disposed = True

    def engine(dsn, **kwargs):
        state.dsn = dsn
        return Engine()

    def initialize(dsn, **kwargs):
        state.initialized.append((dsn, kwargs))
        if state.drift:
            state.lineage["oid"] = 13
        return DashboardAuthorityReceipt(authority["binding_id"], "evidence", authority["installation_id"],
            authority["release_sha256"], "initialized", "f"*64, authority["tenant_id"])

    def command(args, **kwargs):
        state.commands.append(args)
        if args[1:3] == ["image", "inspect"]:
            return json.dumps([{"Id": args[3]}])
        if args[1:3] == ["network", "inspect"]:
            return json.dumps([dict(Id="e"*64, Name="new-target-network", Internal=True,
                                    Containers={pin["id"]: {} for pin in pins})])
        if args[1] == "exec":
            assert args[2] == pins[0]["id"]
            return json.dumps(state.lineage)
        if args[1] == "start":
            row = next(row for row in rows if row["Id"] == args[2])
            row["State"].update(Running=True, Status="running", Pid=20)
            if state.fail_start:
                raise RuntimeError("start response lost")
            return args[2]
        raise AssertionError(args)
    monkeypatch.setattr(subject.sa, "create_engine", engine)
    monkeypatch.setattr(subject, "initialize_dashboard_authority", initialize)
    state.kwargs = dict(runtime_inputs=inputs, launch_graph=graph, preparation=prepared,
        image_roles={name: "pg" if name == "db" else "core" for name in services},
        database_services=("db",), database_service="db", schema_service="schema",
        core_service="core", writer_services=("worker", "core"), authority=authority,
        command=command, inspect_all=lambda: deepcopy(rows))
    return state


def test_initializes_empty_schema_and_starts_only_selected_dependencies(target):
    receipt = subject.start_fresh_runtime(**target.kwargs)
    assert receipt["status"] == "runtime_started_verified"
    assert [pin["service"] for pin in receipt["ready"]] == ["core", "worker"]
    assert len(target.initialized) == 1
    assert "@172.30.0.2:5432/eva" in target.initialized[0][0]
    assert subject.sqlalchemy_postgres_dsn(target.initialized[0][0]) == target.dsn
    assert target.disposed
    assert "private" not in json.dumps(receipt)
    assert not any(command[1] in {"stop", "rm", "restart"} for command in target.commands)


@pytest.mark.parametrize("table", ["tenants", "installations", "principals", "principal_bindings", "restore_activation"])
def test_existing_scope_or_restore_rows_prevent_initialization(target, table):
    target.occupied = table
    with pytest.raises(subject.FreshRuntimeStartError):
        subject.start_fresh_runtime(**target.kwargs)
    assert target.initialized == []
    assert not any(command[1] == "start" for command in target.commands)


@pytest.mark.parametrize("failure", ["lineage", "network", "image", "environment", "receipt", "route", "token"])
def test_changed_or_unreachable_target_never_initializes(target, failure):
    if failure == "lineage":
        target.host_lineage = {**target.lineage, "oid": 99}
    elif failure == "network":
        target.rows[2]["NetworkSettings"]["Networks"]["foreign"] = {}
    elif failure == "image":
        target.rows[2]["Image"] = "sha256:" + "9"*64
    elif failure == "environment":
        target.rows[2]["Config"]["Env"] = []
    elif failure == "receipt":
        target.kwargs["preparation"]["schema_completed"] = False
    elif failure == "route":
        target.fail_connect = True
    else:
        target.kwargs["authority"]["access_token"] = "different-token" * 4
    with pytest.raises(subject.FreshRuntimeStartError):
        subject.start_fresh_runtime(**target.kwargs)
    assert target.initialized == []
    assert not any(command[1] == "start" for command in target.commands)


def test_database_lineage_drift_after_initialization_blocks_start(target):
    target.drift = True
    with pytest.raises(subject.FreshRuntimeStartError) as caught:
        subject.start_fresh_runtime(**target.kwargs)
    assert caught.value.receipt["authority_initialized"] is True
    assert not any(command[1] == "start" for command in target.commands)


def test_uncertain_start_is_retained_and_never_retried(target):
    target.fail_start = True
    with pytest.raises(subject.FreshRuntimeStartError) as caught:
        subject.start_fresh_runtime(**target.kwargs)
    receipt = caught.value.receipt
    assert receipt["status"] == "inspection_required_no_retry"
    assert receipt["runtime_started"] is None
    assert len(receipt["attempted"]) == 1
    assert len([command for command in target.commands if command[1] == "start"]) == 1


def test_unselected_dependency_prevents_mutation(target):
    target.kwargs["writer_services"] = ("worker",)
    with pytest.raises(subject.FreshRuntimeStartError):
        subject.start_fresh_runtime(**target.kwargs)
    assert target.initialized == []


@pytest.mark.parametrize("database", ["eva", "wrong"])
def test_distinct_runtime_database_credentials_still_require_same_target(target, tmp_path, database):
    path = tmp_path / "runtime-dsn"
    path.write_text("postgresql://eva_runtime:runtime-private@db/" + database)
    graph = target.kwargs["launch_graph"]
    graph["services"]["core"]["volumes"][0]["source"] = str(path)
    target.rows[2]["Mounts"][0]["Source"] = str(path)
    target.kwargs["preparation"]["graph_sha256"] = canonical_sha256(graph)
    if database == "eva":
        assert subject.start_fresh_runtime(**target.kwargs)["runtime_started"] is True
    else:
        with pytest.raises(subject.FreshRuntimeStartError):
            subject.start_fresh_runtime(**target.kwargs)
        assert target.initialized == []
