"""Fresh operator-side restore checks; never stop, start or release services.

Configuration must come from the private operator configuration, not an archive
or request. The caller pins every intended writer. Shared-DSN discovery detects
additional Docker writers using the existing exact mount-source rule; it cannot
detect another DSN file for the same database or non-Docker clients. This check
is a point-in-time observation, not a lock against another host operator.
"""
from __future__ import annotations

import re
from copy import deepcopy

from tools.eva2_quiescent_cutover import SystemBackend, _shared_database_consumers


class RestoreRuntimeGuardError(ValueError):
    """The trusted restore target is not currently isolated as configured."""


def _configuration(expected):
    if not isinstance(expected, dict) or set(expected) != {"source", "target", "target_database"}:
        raise RestoreRuntimeGuardError("restore runtime guard configuration is invalid")
    names, identifiers = set(), set()
    for side in ("source", "target"):
        group = expected[side]
        if not isinstance(group, dict) or set(group) != {"core_name", "workers"}:
            raise RestoreRuntimeGuardError("restore worker group is invalid")
        workers = group["workers"]
        if not isinstance(workers, list) or not workers:
            raise RestoreRuntimeGuardError("restore worker pins are required")
        group_names = set()
        for worker in workers:
            if not isinstance(worker, dict) or set(worker) != {"name", "id", "image"}:
                raise RestoreRuntimeGuardError("restore worker pin is invalid")
            name, identifier, image = worker["name"], worker["id"], worker["image"]
            if (not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None
                    or not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{64}", identifier) is None
                    or not isinstance(image, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None
                    or name in names or identifier in identifiers):
                raise RestoreRuntimeGuardError("restore worker identity is invalid or duplicated")
            names.add(name)
            identifiers.add(identifier)
            group_names.add(name)
        if not isinstance(group["core_name"], str) or group["core_name"] not in group_names:
            raise RestoreRuntimeGuardError("restore core must be explicitly pinned")
    database = expected["target_database"]
    if (not isinstance(database, dict) or set(database) != {"name", "oid", "system_identifier"}
            or not isinstance(database["name"], str) or not database["name"]
            or type(database["oid"]) is not int or database["oid"] <= 0
            or not isinstance(database["system_identifier"], str)
            or re.fullmatch(r"[1-9][0-9]*", database["system_identifier"]) is None):
        raise RestoreRuntimeGuardError("restore database identity is invalid")


def verify_restore_runtime(connection, *, expected, inspect_all=None):
    """Reinspect pinned writers and read lineage on the actual restore connection.

    Return only after all checks pass. Reinvoke at each mutation/commit boundary.
    Inspection injection exists for isolated tests; production uses Docker.
    """
    _configuration(expected)
    documents = (SystemBackend._inspect_all if inspect_all is None else inspect_all)()
    if not isinstance(documents, list) or any(not isinstance(item, dict) for item in documents):
        raise RestoreRuntimeGuardError("restore Docker inventory is invalid")
    for side in ("source", "target"):
        for pin in expected[side]["workers"]:
            matches = [item for item in documents if item.get("Name") == "/" + pin["name"]]
            if len(matches) != 1:
                raise RestoreRuntimeGuardError("pinned restore worker is missing or duplicated")
            actual = matches[0]
            state = actual.get("State", {})
            if actual.get("Id") != pin["id"] or actual.get("Image") != pin["image"]:
                raise RestoreRuntimeGuardError("pinned restore worker was replaced")
            if (state.get("Running") is not False or state.get("Paused") is not False
                    or state.get("Restarting") is not False or type(state.get("Pid")) is not int
                    or state["Pid"] != 0 or state.get("Status") not in {"created", "exited"}):
                raise RestoreRuntimeGuardError("pinned restore worker is not stopped")
            if actual.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") != "no":
                raise RestoreRuntimeGuardError("pinned restore worker has automatic restart enabled")
        if _shared_database_consumers(retained_core=expected[side]["core_name"], inspected=documents):
            raise RestoreRuntimeGuardError("restore database has an additional running consumer")
    actual_database = connection.exec_driver_sql("""
        SELECT current_database() AS name,
               (SELECT oid::bigint FROM pg_database
                WHERE datname=current_database()) AS oid,
               (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier
    """).mappings().one()
    if dict(actual_database) != expected["target_database"]:
        raise RestoreRuntimeGuardError("restore target database lineage differs")


def capture_prepared_restore_runtime(connection, *, approved_source,
                                     preparation, core_service, writer_services,
                                     inspect_all=None):
    """Bind actual prepared target identities to the existing restore guard.

    Inputs come from the trusted installer, not a backup or browser. The source
    group remains an explicit approved selection; this function never discovers,
    stops or adopts source writers. The connection must use the prepared target's
    private DSN. The caller persists the returned pins with its restore settings.
    """
    preparation = deepcopy(preparation)
    services = tuple(writer_services)
    if (not isinstance(preparation, dict)
            or preparation.get("contract") != "EvaRestoreTargetPreparation/v1"
            or preparation.get("status") != "prepared"
            or preparation.get("schema_completed") is not True
            or not services or len(set(services)) != len(services)
            or core_service not in services):
        raise RestoreRuntimeGuardError("completed target preparation and explicit writers required")
    containers = preparation.get("containers")
    if not isinstance(containers, list):
        raise RestoreRuntimeGuardError("prepared target container identities missing")
    pins = []
    for service in services:
        selected = [item for item in containers if isinstance(item, dict) and item.get("service") == service]
        if len(selected) != 1:
            raise RestoreRuntimeGuardError("prepared writer missing or duplicated")
        item = selected[0]
        if not {"name", "id", "image"} <= set(item):
            raise RestoreRuntimeGuardError("prepared writer identity missing")
        pins.append({key: item[key] for key in ("name", "id", "image")})
    database = dict(connection.exec_driver_sql("""
        SELECT current_database() AS name,
               (SELECT oid::bigint FROM pg_database
                WHERE datname=current_database()) AS oid,
               (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier
    """).mappings().one())
    expected = {
        "source": deepcopy(approved_source),
        "target": {"core_name": pins[services.index(core_service)]["name"], "workers": pins},
        "target_database": database,
    }
    # Reuse the same actual stopped-writer and lineage checks as every restore.
    verify_restore_runtime(connection, expected=expected, inspect_all=inspect_all)
    return expected
