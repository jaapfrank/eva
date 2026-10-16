"""Current owner counts and schedule outcome projection, with no task execution."""
from datetime import timedelta
import hashlib

import pytest

from tests.postgres.test_dashboard_work_context import context_engine, digital
from tests.postgres.test_private_work_return import setup, start
from tests.postgres.test_delegated_work_store import principal_bindings, _release
from eva.bootstrap.dashboard_work import dashboard_delegated_work
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.contracts.delegated_work import DelegatedWorkUsage


def listing(service, turn):
    return dashboard_delegated_work(service, tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, owner_principal_id=service.owner_principal_id,
        principal_binding_id=service.principal_binding_id, operation="list")


def test_counts_cover_owner_history_and_open_selection_before_limit(context_engine):
    service, turn, clock = setup(context_engine, "overview-counts", voice=False)
    active = start(service, turn, "open")
    for index in range(25):
        spec = start(service, turn, "closed-" + str(index))
        service.store.request_stop(spec.work_id, **service._owner, now=clock[0])
    other, other_turn, _ = setup(context_engine, "overview-other", voice=False)
    start(other, other_turn)
    result = listing(service, turn)
    assert result["overview"]["open_count"] == 1
    assert result["overview"]["closed_count"] == 25
    assert result["overview"]["total_count"] == 26
    assert result["overview"]["state_counts"] == {"admitted": 1, "stopped": 25}
    assert [job["work_id"] for job in result["open_jobs"]] == [active.work_id]
    assert result["truncated"] and not result["open_truncated"]
    assert len(result["jobs"]) == 20
    service.store.request_stop(active.work_id, **service._owner, now=clock[0])
    result = listing(service, turn)
    assert result["overview"]["open_count"] == 0
    assert result["overview"]["closed_count"] == 26
    assert result["open_jobs"] == []
    assert service.store.load(active.work_id, **service._owner).state == "stopped"


def test_dispatched_plan_keeps_phase_and_reads_exact_uncertain_outcome(context_engine):
    service, turn, clock = setup(context_engine, "overview-planning", voice=False)
    seed, _ = digital(service, turn)
    service.store.request_stop(seed, **service._owner, now=clock[0])
    planner = ScheduledOwnerWork(service)
    planned = planner.schedule(turn, request_key="overview-planned",
        request={"task": "Synthetic delayed work", "run_at": (clock[0] + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    clock[0] += timedelta(seconds=2)
    assert planner.run_once() == "dispatched"
    original = planner.list()["schedules"][0]
    work = service.store.load(original["work_id"], **service._owner)
    claim = service.store.claim_next(worker_id="isolated-overview", release=_release(work.spec), now=clock[0])
    assert claim.work_id == work.spec.work_id
    call = service.store.start_call(claim, now=clock[0])
    # The isolated call fence is exercised without contacting any provider.
    service.store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(),
        error_code="synthetic_unknown", error_fingerprint=hashlib.sha256(b"isolated").hexdigest(),
        ambiguous=True, now=clock[0])
    before = service.store.load(work.spec.work_id, **service._owner)
    result = listing(service, turn)
    schedule = result["planning"]["schedules"][0]
    assert schedule["state"] == "dispatched"
    assert schedule["work_id"] == original["work_id"]
    assert schedule["linked_work"] == {"state": "ambiguous", "closed": True, "result_available": False}
    assert result["overview"]["open_count"] == 0
    assert result["overview"]["state_counts"] == {"ambiguous": 1, "stopped": 1}
    assert service.store.load(work.spec.work_id, **service._owner) == before
    assert planner.list()["schedules"][0] == original
    assert planned["schedule_id"] == schedule["schedule_id"]


def test_counts_and_history_reject_revoked_current_binding(context_engine):
    service, turn, _ = setup(context_engine, "overview-revocation", voice=False)
    start(service, turn)
    with context_engine.begin() as conn:
        conn.execute(principal_bindings.update().where(
            principal_bindings.c.principal_binding_id == service.principal_binding_id).values(status="revoked"))
    with pytest.raises(Exception):
        listing(service, turn)


def test_pending_start_remains_visible_beyond_blocked_history(context_engine):
    service, turn, clock = setup(context_engine, "overview-pending", voice=False)
    seed, _ = digital(service, turn)
    planner = ScheduledOwnerWork(service)
    future = planner.schedule(turn, request_key="future", request={"task": "Still pending",
        "run_at": (clock[0] + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    for index in range(21):
        planner.schedule(turn, request_key="blocked-" + str(index), request={"task": "Blocked predecessor",
            "run_at": (clock[0] + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "after_work_id": seed})
    service.store.request_stop(seed, **service._owner, now=clock[0])
    clock[0] += timedelta(seconds=2)
    for index in range(21):
        assert planner.run_once() == "blocked"
    assert all(item["schedule_id"] != future["schedule_id"] for item in planner.list()["schedules"])
    result = listing(service, turn)
    pending = [item for item in result["planning"]["schedules"] if item["state"] == "scheduled"]
    assert [item["schedule_id"] for item in pending] == [future["schedule_id"]]
    assert not result["planning"]["current_truncated"]
