"""Start pinned restored writers only after a separately committed activation.

Failures retain all targets for operator inspection. Never retry a start command,
release a database, stop a service, or change traffic/automatic restart policies.
"""
from __future__ import annotations

from copy import deepcopy
import time

import sqlalchemy as sa

from eva.operations.restore_activation import require_restore_activation
from tools.eva2_quiescent_cutover import SystemBackend, _shared_database_consumers
from tools.installation_document_connection import _command
from tools.restore_runtime_guard import verify_restore_runtime


class RestoredRuntimeStartError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("restored runtime start not confirmed; inspect retained targets, do not retry")


def _released(engine, expected, restore_id, archive_sha256, release_receipt, runtime_identity):
    require_restore_activation(engine)
    with engine.connect() as connection:
        row = connection.execute(sa.text("""
            SELECT *, target_database_oid = (SELECT oid FROM pg_database
                WHERE datname=current_database()) AS lineage_matches
            FROM eva_core.restore_activation
        """)).mappings().one()
        lineage = dict(connection.exec_driver_sql("""
            SELECT current_database() AS name,
            (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid,
            (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier
        """).mappings().one())
    if (not isinstance(runtime_identity, dict) or set(runtime_identity) != {"tenant_id", "installation_id"}
            or lineage != expected["target_database"]
            or row["held"] is not False or row["lineage_matches"] is not True
            or str(row["restore_id"]) != str(restore_id) or row["archive_sha256"] != archive_sha256
            or any(str(row["runtime_" + key]) != value for key, value in runtime_identity.items())
            or release_receipt.get("contract") != "EvaRestoreActivationRelease/v1"
            or release_receipt.get("status") != "released_pending_commit"
            or release_receipt.get("runtime_started") is not False
            or release_receipt.get("restore_id") != str(restore_id)
            or release_receipt.get("archive_sha256") != archive_sha256
            or type(release_receipt.get("target_database_oid")) is not int
            or release_receipt["target_database_oid"] != lineage["oid"]
            or not row["release_authority_sha256"]
            or release_receipt.get("release_authority_sha256") != row["release_authority_sha256"]):
        raise ValueError("committed restore release identity differs")


def _inventory(expected, documents, allowed_running):
    if not isinstance(documents, list) or any(not isinstance(row, dict) for row in documents):
        raise ValueError("runtime inventory is invalid")
    result = {}
    for side in ("source", "target"):
        for pin in expected[side]["workers"]:
            rows = [row for row in documents if row.get("Name") == "/" + pin["name"]]
            if len(rows) != 1:
                raise ValueError("runtime pin missing or duplicated")
            row = rows[0]; state = row.get("State", {})
            if (row.get("Id") != pin["id"] or row.get("Image") != pin["image"]
                    or row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") != "no"
                    or state.get("Paused") is not False or state.get("Restarting") is not False):
                raise ValueError("runtime pin or fence changed")
            if pin["id"] in allowed_running:
                if (state.get("Running") is not True or state.get("Status") != "running"
                        or type(state.get("Pid")) is not int or state["Pid"] <= 0):
                    raise ValueError("started runtime is not running")
                result[pin["id"]] = state.get("Health", {}).get("Status")
            elif (state.get("Running") is not False or state.get("Pid") != 0
                  or state.get("Status") not in {"created", "exited"}):
                raise ValueError("unstarted runtime or source is not fenced")
        consumers = set(_shared_database_consumers(retained_core=expected[side]["core_name"], inspected=documents))
        permitted = {pin["name"] for pin in expected["target"]["workers"] if pin["id"] in allowed_running} if side == "target" else set()
        if consumers - permitted:
            raise ValueError("additional database writer appeared")
    return result


def start_restored_runtime(engine, *, expected, restore_id, archive_sha256, release_receipt,
                           runtime_identity, timeout_seconds=60, inspect_all=None, start_container=None):
    """Trusted operator callable; engine must open fresh committed target connections.

    Docker healthchecks provide readiness, not an end-to-end restored product
    guarantee. Injection hooks are for controlled tests, never request data.
    """
    receipt = {"contract": "EvaRestoredRuntimeStart/v1", "status": "not_started",
               "restore_id": str(restore_id), "archive_sha256": archive_sha256,
               "attempted": [], "started": [], "ready": [], "runtime_started": False}
    inspect = SystemBackend._inspect_all if inspect_all is None else inspect_all
    start = (lambda identifier: _command(["docker", "start", identifier], deadline=30, limit=4096)) if start_container is None else start_container
    try:
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120:
            raise ValueError("runtime start readiness deadline is invalid")
        expected = deepcopy(expected)
        with engine.connect() as connection:
            verify_restore_runtime(connection, expected=expected, inspect_all=inspect)
        _released(engine, expected, restore_id, archive_sha256, release_receipt, runtime_identity)
        order = sorted(expected["target"]["workers"], key=lambda pin: pin["name"] != expected["target"]["core_name"])
        started_ids = set()
        for pin in order:
            _released(engine, expected, restore_id, archive_sha256, release_receipt, runtime_identity)
            _inventory(expected, inspect(), started_ids)
            receipt["attempted"].append(dict(pin))
            receipt["runtime_started"] = None
            start(pin["id"])
            started_ids.add(pin["id"])
            deadline = time.monotonic() + timeout_seconds
            while True:
                health = _inventory(expected, inspect(), started_ids)
                if pin not in receipt["started"]:
                    receipt["started"].append(dict(pin))
                _released(engine, expected, restore_id, archive_sha256, release_receipt, runtime_identity)
                if health[pin["id"]] == "healthy":
                    receipt["ready"].append(dict(pin))
                    break
                if health[pin["id"]] != "starting" or time.monotonic() >= deadline:
                    raise ValueError("restored runtime health is not ready")
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
        if any(value != "healthy" for value in _inventory(expected, inspect(), started_ids).values()):
            raise ValueError("restored runtime readiness changed")
        _released(engine, expected, restore_id, archive_sha256, release_receipt, runtime_identity)
        receipt.update(status="runtime_started_verified", runtime_started=True,
                       verification_scope="Pinned writer Docker healthchecks only; no restored user-flow or channel acceptance")
        return receipt
    except Exception as error:
        # A command error can follow a successful Docker start. Observe once;
        # never infer failure means the target stayed stopped or retry it.
        try:
            rows = inspect()
            for pin in receipt["attempted"]:
                if pin not in receipt["started"] and any(
                    row.get("Id") == pin["id"] and row.get("Image") == pin["image"]
                    and row.get("Name") == "/" + pin["name"]
                    and row.get("State", {}).get("Running") is True for row in rows
                ):
                    receipt["started"].append(dict(pin))
        except Exception:
            pass
        receipt["status"] = "inspection_required_no_retry"
        raise RestoredRuntimeStartError(receipt) from error
