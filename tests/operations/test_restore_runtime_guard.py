"""Isolated operator inventory and actual-connection lineage boundaries."""
from copy import deepcopy

import pytest

from tools.restore_runtime_guard import RestoreRuntimeGuardError, verify_restore_runtime


def fixture():
    groups, documents = {}, []
    for index, side in enumerate(("source", "target"), 1):
        name = side + "-core"
        pin = {"name": name, "id": str(index)*64, "image": "sha256:"+"a"*64}
        groups[side] = {"core_name": name, "workers": [pin]}
        documents.append({"Name": "/"+name, "Id": pin["id"], "Image": pin["image"],
            "State": {"Running": False, "Paused": False, "Restarting": False, "Pid": 0, "Status": "exited"},
            "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "Mounts": [{"Destination": "/run/secrets/database_dsn", "Source": "/private/"+side}]})
    groups["target_database"] = {"name": "eva", "oid": 12345, "system_identifier": "123456789"}
    return groups, documents


class Connection:
    def __init__(self, database):
        self.database = database
        self.calls = 0

    def exec_driver_sql(self, sql):
        assert "pg_control_system()" in sql and "current_database()" in sql and "pg_database" in sql
        self.calls += 1
        return self

    def mappings(self):
        return self

    def one(self):
        return self.database


def test_checks_each_side_and_reads_actual_target_connection():
    expected, documents = fixture()
    connection = Connection(expected["target_database"])
    calls = []
    def inspect():
        calls.append(1)
        return documents
    verify_restore_runtime(connection, expected=expected, inspect_all=inspect)
    verify_restore_runtime(connection, expected=expected, inspect_all=inspect)
    assert len(calls) == connection.calls == 2


@pytest.mark.parametrize("mutation", ["missing", "id", "image", "running", "paused", "restarting", "pid", "state", "restart_policy"])
def test_rejects_missing_replaced_or_active_pinned_workers_before_database_read(mutation):
    expected, documents = fixture()
    if mutation == "missing":
        documents.pop()
    elif mutation in {"id", "image"}:
        documents[-1]["Id" if mutation == "id" else "Image"] = "different"
    elif mutation == "restart_policy":
        documents[-1]["HostConfig"]["RestartPolicy"]["Name"] = "always"
    else:
        key, value = {"running": ("Running", True), "paused": ("Paused", True),
                      "restarting": ("Restarting", True), "pid": ("Pid", 44),
                      "state": ("Status", "dead")}[mutation]
        documents[-1]["State"][key] = value
    connection = Connection(expected["target_database"])
    with pytest.raises(RestoreRuntimeGuardError):
        verify_restore_runtime(connection, expected=expected, inspect_all=lambda: documents)
    assert connection.calls == 0


@pytest.mark.parametrize("side", [0, 1])
def test_additional_shared_database_writer_is_refused(side):
    expected, documents = fixture()
    sibling = deepcopy(documents[side])
    sibling.update(Name="/extra-document-worker", Id="3"*64)
    sibling["State"]["Running"] = True
    documents.append(sibling)
    with pytest.raises(RestoreRuntimeGuardError, match="additional running"):
        verify_restore_runtime(Connection(expected["target_database"]), expected=expected, inspect_all=lambda: documents)


def test_unrelated_running_database_consumer_is_allowed():
    expected, documents = fixture()
    sibling = deepcopy(documents[0])
    sibling.update(Name="/unrelated", Id="3"*64)
    sibling["State"]["Running"] = True
    sibling["Mounts"][0]["Source"] = "/other-database"
    documents.append(sibling)
    verify_restore_runtime(Connection(expected["target_database"]), expected=expected, inspect_all=lambda: documents)


@pytest.mark.parametrize("key,value", [("name", "other"), ("oid", 54321), ("system_identifier", "999")])
def test_database_name_oid_and_cluster_identifier_must_all_match(key, value):
    expected, documents = fixture()
    actual = dict(expected["target_database"], **{key: value})
    with pytest.raises(RestoreRuntimeGuardError, match="lineage"):
        verify_restore_runtime(Connection(actual), expected=expected, inspect_all=lambda: documents)


@pytest.mark.parametrize("mutation", ["empty", "unpinned_core", "duplicate", "boolean_oid"])
def test_invalid_private_configuration_fails_before_inspection(mutation):
    expected, _ = fixture()
    if mutation == "empty":
        expected["source"]["workers"] = []
    elif mutation == "unpinned_core":
        expected["source"]["core_name"] = "not-pinned"
    elif mutation == "duplicate":
        expected["target"] = deepcopy(expected["source"])
    else:
        expected["target_database"]["oid"] = True
    def inspect():
        pytest.fail("invalid config must not inspect Docker")
    with pytest.raises(RestoreRuntimeGuardError):
        verify_restore_runtime(Connection({}), expected=expected, inspect_all=inspect)


def test_inspection_and_database_read_fail_closed():
    expected, documents = fixture()
    def unavailable():
        raise RuntimeError("Docker unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        verify_restore_runtime(Connection({}), expected=expected, inspect_all=unavailable)
    class Unavailable(Connection):
        def exec_driver_sql(self, sql):
            raise RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        verify_restore_runtime(Unavailable({}), expected=expected, inspect_all=lambda: documents)


def test_lineage_on_real_postgresql_connection():
    import os
    import sqlalchemy as sa
    dsn = os.environ.get("EVA2_IN002_TEST_ADMIN_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL DSN required")
    engine = sa.create_engine(dsn)
    expected, documents = fixture()
    try:
        with engine.connect() as connection:
            expected["target_database"] = dict(connection.exec_driver_sql("""
                SELECT current_database() AS name,
                (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid,
                (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier
            """).mappings().one())
            verify_restore_runtime(connection, expected=expected, inspect_all=lambda: documents)
            changed = deepcopy(expected)
            changed["target_database"]["system_identifier"] = str(int(changed["target_database"]["system_identifier"])+1)
            with pytest.raises(RestoreRuntimeGuardError, match="lineage"):
                verify_restore_runtime(connection, expected=changed, inspect_all=lambda: documents)
    finally:
        engine.dispose()


def prepared_fixture():
    expected, documents = fixture()
    preparation = {"contract": "EvaRestoreTargetPreparation/v1", "status": "prepared",
        "schema_completed": True,
        "containers": [{**expected["target"]["workers"][0], "service": "core"}]}
    return expected, documents, preparation


def test_capture_binds_actual_prepared_container_and_connection_without_source_mutation():
    from tools.restore_runtime_guard import capture_prepared_restore_runtime
    expected, documents, preparation = prepared_fixture()
    source = deepcopy(expected["source"])
    captured = capture_prepared_restore_runtime(Connection(expected["target_database"]),
        approved_source=source, preparation=preparation, core_service="core",
        writer_services=("core",), inspect_all=lambda: documents)
    assert captured == expected
    captured["source"]["workers"].clear()
    assert source == expected["source"]


@pytest.mark.parametrize("change", ["incomplete", "duplicate", "missing", "replaced", "running"])
def test_capture_rejects_incomplete_or_changed_target(change):
    from tools.restore_runtime_guard import capture_prepared_restore_runtime
    expected, documents, preparation = prepared_fixture()
    if change == "incomplete":
        preparation["status"] = "preparing"
    elif change == "duplicate":
        preparation["containers"] *= 2
    elif change == "missing":
        preparation["containers"] = []
    elif change == "replaced":
        documents[-1]["Id"] = "f" * 64
    else:
        documents[-1]["State"]["Running"] = True
    with pytest.raises(RestoreRuntimeGuardError):
        capture_prepared_restore_runtime(Connection(expected["target_database"]),
            approved_source=expected["source"], preparation=preparation, core_service="core",
            writer_services=("core",), inspect_all=lambda: documents)
