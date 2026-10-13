"""Corrected project dependencies on real PostgreSQL, without model calls."""
import pytest
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkResult
from tests.postgres.test_delegated_work_store import _usage
import hashlib
from pathlib import Path
from datetime import timedelta
from dataclasses import replace
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup


@pytest.mark.parametrize("via_schedule", [False, True])
@pytest.mark.parametrize("fresh_process", [False, True])
def test_corrected_dependency_waits_and_transfers_result_once(database, monkeypatch, via_schedule, fresh_process):
    service, turn, clock = setup(database, "l2-correction-dependency-"+str(via_schedule)+str(fresh_process), False)
    root = Path(__file__).resolve().parents[3] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    service.digital_profile = DelegatedDigitalProfile("model:controlled", "a"*64, "b"*64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root/source).read_bytes()).hexdigest()),))
    if via_schedule:
        first_schedule = ScheduledOwnerWork(service).schedule(turn, request_key="first", request={
            "task":"Prepare a document", "run_at":(clock[0]+timedelta(seconds=1)).isoformat().replace("+00:00","Z")})
        clock[0] += timedelta(seconds=2)
        assert ScheduledOwnerWork(service).run_once() == "dispatched"
        first = ScheduledOwnerWork(service).list()["schedules"][0]["work_id"]
        dependency = {"after_schedule_id":first_schedule["schedule_id"]}
    else:
        first = service.execute("digital", turn, task="Prepare a document", request_key="first")["work"]["work_id"]
        dependency = {"after_work_id":first}
    second = ScheduledOwnerWork(service).schedule(turn, request_key="second", request={
        "task":"Summarize the completed document", **dependency})
    clock[0] += timedelta(seconds=1)
    stop = service.store.request_stop

    def observe_between_stop_and_saved_correction(*args, **kwargs):
        result = stop(*args, **kwargs)
        # A concurrent scheduler sees the stop before correction is saved.
        # It must wait rather than persist a false terminal dependency failure.
        assert ScheduledOwnerWork(service).run_once() == "idle"
        return result

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "request_stop", observe_between_stop_and_saved_correction)
        correction = service.execute("revise_digital", replace(turn, message_id="correct"),
            request_key="correct", revision={"work_id":first, "instruction":"Use the corrected title"})
    assert correction["status"] == "correction_saved"
    if fresh_process:
        clock[0] += timedelta(days=1)
    dispatch = (lambda: _run_scheduler_process(service, clock[0])) if fresh_process else (lambda: ScheduledOwnerWork(service).run_once())
    assert dispatch() == "dispatched"
    rows = {row["schedule_id"]:row for row in ScheduledOwnerWork(service).list()["schedules"]}
    assert rows[second["schedule_id"]]["state"] == "scheduled"
    successor = rows[correction["schedule"]["schedule_id"]]["work_id"]
    assert successor != first
    from eva.adapters.postgres.work_corrections import work_corrections
    resumed = service.store.load(successor, **service._owner)
    assert [item["instruction"] for item in work_corrections(database, resumed.spec)] == ["Use the corrected title"]
    assert dispatch() == "idle"
    clock[0] += timedelta(seconds=1)
    claim = service.store.claim_next(worker_id="worker:corrected-dependency", release=service.digital_profile.release, now=clock[0])
    assert claim.spec.work_id == successor
    call = service.store.start_call(claim, now=clock[0])
    output = "Saved document with corrected title; controlled result fixture."
    result = DelegatedDigitalWorkResult(contract="DelegatedDigitalWorkResult/v4", schema_version=4,
        work_id=successor, call_id=call.call_id, spec_sha256=claim.spec.sha256,
        output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        usage=_usage().to_payload(), completed_at=clock[0].strftime("%Y-%m-%dT%H:%M:%SZ"))
    service.store.record_success(call, result, now=clock[0])
    assert dispatch() == "dispatched"
    rows = {row["schedule_id"]:row for row in ScheduledOwnerWork(service).list()["schedules"]}
    child_id = rows[second["schedule_id"]]["work_id"]
    child = service.store.load(child_id, **service._owner)
    assert output in child.spec.task and successor in child.spec.task
    assert dispatch() == "idle"
    assert len(service.store.list_owned(**service._owner, limit=10)) == 3


@pytest.mark.parametrize("stop_kind", ["plain_stop", "cancel_dependent", "cancel_correction"])
def test_explicit_stop_does_not_restart_project(database, stop_kind):
    service, turn, clock = setup(database, "l2-stop-"+stop_kind, False)
    root = Path(__file__).resolve().parents[3] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    service.digital_profile = DelegatedDigitalProfile("model:controlled", "a"*64, "b"*64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root/source).read_bytes()).hexdigest()),))
    first = service.execute("digital", turn, task="Prepare a document", request_key="first")["work"]["work_id"]
    second = ScheduledOwnerWork(service).schedule(turn, request_key="second", request={
        "task":"Summarize the completed document", "after_work_id":first})
    clock[0] += timedelta(seconds=1)
    if stop_kind == "plain_stop":
        service.execute("stop", turn, work_id=first, request_key="stop")
    else:
        correction = service.execute("revise_digital", replace(turn,message_id="correct"),
            request_key="correct", revision={"work_id":first,"instruction":"Use the corrected title"})
        target = second["schedule_id"] if stop_kind == "cancel_dependent" else correction["schedule"]["schedule_id"]
        ScheduledOwnerWork(service).cancel_chain(target)
    ScheduledOwnerWork(service).run_once()
    ScheduledOwnerWork(service).run_once()
    rows = {row["schedule_id"]:row for row in ScheduledOwnerWork(service).list()["schedules"]}
    child = rows[second["schedule_id"]]
    assert child["state"] == ("cancelled" if stop_kind == "cancel_dependent" else "blocked")
    assert child["work_id"] is None


def _run_scheduler_process(service, now):
    """Reconstruct only configuration in a fresh interpreter; all work comes from DB."""
    import json
    import os
    import subprocess
    import sys
    from dataclasses import asdict
    values = {"dsn": service.store._engine.url.render_as_string(hide_password=False),
        "tenant_id": str(service.store.scope.tenant_id),
        "installation_id": str(service.store.scope.installation_id),
        "owner": str(service.owner_principal_id), "binding": str(service.principal_binding_id),
        "now": now.isoformat(), "profile": asdict(service.digital_profile)}
    result = subprocess.run([sys.executable, "-c",
        "from tests.postgres.test_scheduled_work_corrections import _scheduler_process_main; _scheduler_process_main()"],
        input=json.dumps(values), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)
    assert result["pid"] != os.getpid()
    return result["state"]


def _scheduler_process_main():
    import json
    import os
    import sys
    from datetime import datetime
    from uuid import UUID
    import sqlalchemy as sa
    from sqlalchemy.pool import NullPool
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    from eva.adapters.postgres.operational_timeline import TimelineScope
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    values = json.load(sys.stdin)
    engine = sa.create_engine(values["dsn"], poolclass=NullPool)
    try:
        store = PostgresDelegatedWorkStore(engine,
            TimelineScope(UUID(values["tenant_id"]), UUID(values["installation_id"])))
        profile = values["profile"]
        profile["capability_ids"] = tuple(profile["capability_ids"])
        profile["execution_source_sha256"] = tuple(tuple(item) for item in profile["execution_source_sha256"])
        profile["historical_grants"] = tuple(profile["historical_grants"])
        service = DelegatedWorkOwnerService(store,
            owner_principal_id=UUID(values["owner"]), principal_binding_id=UUID(values["binding"]),
            clock=lambda: datetime.fromisoformat(values["now"]), digital_profile=DelegatedDigitalProfile(**profile))
        print(json.dumps({"pid": os.getpid(), "state": ScheduledOwnerWork(service).run_once()}))
    finally:
        engine.dispose()
