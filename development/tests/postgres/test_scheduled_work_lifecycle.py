"""Durable scheduling across coordinator interruption; no model or transport calls."""
from datetime import timedelta
import hashlib
from pathlib import Path

import pytest

from eva.adapters.postgres import scheduled_work as module
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkResult
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup
from tests.postgres.test_delegated_work_store import _usage


class CoordinatorInterrupted(BaseException):
    pass


def test_restart_replays_admission_once_then_waits_advances_and_stops_chain(database, monkeypatch):
    service, turn, clock = setup(database, "scheduled-durable-lifecycle", False)
    root = Path(__file__).resolve().parents[3] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root / source).read_bytes()).hexdigest()),))
    service.digital_profile = profile
    scheduler = ScheduledOwnerWork(service)
    first = scheduler.schedule(turn, request_key="first", request={"task": "Create a report",
        "run_at": (clock[0] + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    assert ScheduledOwnerWork(service).run_once() == "idle"
    clock[0] += timedelta(seconds=2)
    transition = module.transition_timeline_thread

    def interrupt_after_admission(*args, **kwargs):
        if kwargs.get("event_type") == "SCHEDULED_WORK_ADMITTED":
            raise CoordinatorInterrupted()
        return transition(*args, **kwargs)

    with monkeypatch.context() as crash:
        crash.setattr(module, "transition_timeline_thread", interrupt_after_admission)
        with pytest.raises(CoordinatorInterrupted):
            ScheduledOwnerWork(service).run_once()
    assert ScheduledOwnerWork(service).list()["schedules"][0]["state"] == "launching"
    admitted = service.store.list_owned(**service._owner, limit=10)
    assert len(admitted) == 1
    first_work = admitted[0]
    clock[0] += timedelta(seconds=1)
    assert ScheduledOwnerWork(service).run_once() == "dispatched"
    assert ScheduledOwnerWork(service).list()["schedules"][0]["work_id"] == first_work.spec.work_id
    assert len(service.store.list_owned(**service._owner, limit=10)) == 1
    assert ScheduledOwnerWork(service).run_once() == "idle"

    second = ScheduledOwnerWork(service).schedule(turn, request_key="second", request={
        "task": "Summarize the completed report", "after_schedule_id": first["schedule_id"]})
    assert ScheduledOwnerWork(service).run_once() == "idle"
    assert len(service.store.list_owned(**service._owner, limit=10)) == 1
    # Record a controlled predecessor result through the real work lifecycle.
    # No model, tool, or report-writing execution is represented by this fixture.
    clock[0] += timedelta(seconds=1)
    claim = service.store.claim_next(worker_id="worker:schedule-fixture", release=profile.release, now=clock[0])
    assert claim.spec.work_id == first_work.spec.work_id
    clock[0] += timedelta(seconds=1)
    call = service.store.start_call(claim, now=clock[0])
    clock[0] += timedelta(seconds=1)
    output = "Synthetic predecessor result for scheduling proof."
    result = DelegatedDigitalWorkResult(contract="DelegatedDigitalWorkResult/v4", schema_version=4,
        work_id=first_work.spec.work_id, call_id=call.call_id, spec_sha256=first_work.spec.sha256,
        output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        usage=_usage().to_payload(), completed_at=clock[0].strftime("%Y-%m-%dT%H:%M:%SZ"))
    service.store.record_success(call, result, now=clock[0])
    assert ScheduledOwnerWork(service).run_once() == "dispatched"
    planned = {row["schedule_id"]: row for row in ScheduledOwnerWork(service).list()["schedules"]}
    child_id = planned[second["schedule_id"]]["work_id"]
    child = service.store.load(child_id, **service._owner)
    assert output in child.spec.task
    third = ScheduledOwnerWork(service).schedule(turn, request_key="third", request={
        "task": "Archive the summary", "after_schedule_id": second["schedule_id"]})
    stopped = ScheduledOwnerWork(service).cancel_chain(second["schedule_id"])
    assert stopped["status"] == "chain_stop_recorded" and stopped["affected_schedules"] == 2
    assert ScheduledOwnerWork(service).run_once() == "idle"
    final = {row["schedule_id"]: row for row in ScheduledOwnerWork(service).list()["schedules"]}
    assert final[second["schedule_id"]]["state"] == "chain_stopped"
    assert final[third["schedule_id"]]["state"] == "cancelled"
    stopped_child = service.store.load(child_id, **service._owner)
    assert stopped_child.state == "stopped" and stopped_child.call_id is None
    assert service.store.load(first_work.spec.work_id, **service._owner).state == "succeeded"
    assert len(service.store.list_owned(**service._owner, limit=10)) == 2


@pytest.mark.parametrize("resume", [False, True])
def test_restart_before_admission_starts_with_remaining_execution_time(database, monkeypatch, resume):
    service, turn, clock = setup(database, "scheduled-before-admission-restart-" + str(resume), False)
    root = Path(__file__).resolve().parents[3] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    service.digital_profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root / source).read_bytes()).hexdigest()),))
    if resume:
        previous = service.execute("digital", turn, task="Continue the saved project", request_key="original")["work"]["work_id"]
        service.store.request_stop(previous, **service._owner, now=clock[0])
    request = {"work_id": previous} if resume else {"task": "Continue the saved project"}
    request["run_at"] = (clock[0] + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    scheduled = ScheduledOwnerWork(service).schedule(turn, request_key="first", request=request, resume=resume)
    clock[0] += timedelta(seconds=2)

    def interrupt_before_admission(*args, **kwargs):
        raise CoordinatorInterrupted()

    with monkeypatch.context() as crash:
        crash.setattr(service, "_execute", interrupt_before_admission)
        with pytest.raises(CoordinatorInterrupted):
            ScheduledOwnerWork(service).run_once()
    assert len(service.store.list_owned(**service._owner, limit=10)) == int(resume)
    assert ScheduledOwnerWork(service).list()["schedules"][0]["state"] == "launching"
    clock[0] += timedelta(days=1)
    assert ScheduledOwnerWork(service).run_once() == "dispatched"
    row = ScheduledOwnerWork(service).list()["schedules"][0]
    assert row["schedule_id"] == scheduled["schedule_id"]
    claim = service.store.claim_next(worker_id="worker:restart", release=service.digital_profile.release, now=clock[0])
    assert claim is not None, "A step never started before shutdown must still be executable after restart"
    assert claim.spec.work_id == row["work_id"]


def test_scheduled_replay_does_not_renew_already_admitted_attempt(database, monkeypatch):
    service, turn, clock = setup(database, "scheduled-preserve-admitted-deadline", False)
    root = Path(__file__).resolve().parents[3] / "src/eva"
    source = "bootstrap/delegated_digital_runner.py"
    service.digital_profile = DelegatedDigitalProfile("model:controlled", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        ((source, hashlib.sha256((root / source).read_bytes()).hexdigest()),))
    ScheduledOwnerWork(service).schedule(turn, request_key="first", request={
        "task": "Continue the saved project", "run_at": (clock[0] + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")})
    clock[0] += timedelta(seconds=2)
    transition = module.transition_timeline_thread
    def interrupt_after_admission(*args, **kwargs):
        if kwargs.get("event_type") == "SCHEDULED_WORK_ADMITTED":
            raise CoordinatorInterrupted()
        return transition(*args, **kwargs)
    with monkeypatch.context() as crash:
        crash.setattr(module, "transition_timeline_thread", interrupt_after_admission)
        with pytest.raises(CoordinatorInterrupted):
            ScheduledOwnerWork(service).run_once()
    original = service.store.list_owned(**service._owner, limit=10)[0].spec
    clock[0] += timedelta(days=1)
    assert ScheduledOwnerWork(service).run_once() == "dispatched"
    saved = service.store.list_owned(**service._owner, limit=10)
    assert len(saved) == 1 and saved[0].spec == original
    assert service.store.claim_next(worker_id="worker:restart", release=service.digital_profile.release, now=clock[0]) is None
