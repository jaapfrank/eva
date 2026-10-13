"""Atomic private budget reports retain text without a successful result."""
import asyncio
import hashlib
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace

import pytest

from eva.adapters.postgres import delegated_work as module
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict
from eva.adapters.subagents.tool_free_child import ToolFreeChildOutcome
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.operations.delegated_work_worker import DelegatedWorkWorker
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_delegated_delivery_store import NOW, _usage


def setup(engine, label):
    scope, _, binding = _seed_authority(engine, label)
    clock = [NOW]
    store = PostgresDelegatedWorkStore(engine, scope, failure_report_clock=lambda: clock[0])
    profile = DelegatedDigitalProfile("model:controlled", "a"*64, "b"*64,
        ("use_connector_tool",), (("bootstrap/delegated_digital_runner.py", hashlib.sha256((Path(__file__).resolve().parents[2] / "src/eva/bootstrap/delegated_digital_runner.py").read_bytes()).hexdigest()),))
    service = DelegatedWorkOwnerService(store, owner_principal_id=_id(label, "principal"),
        principal_binding_id=binding, digital_profile=profile, clock=lambda: clock[0])
    turn = ConversationTurn.private("tenant:"+label, "installation:"+label,
        str(service.owner_principal_id), "dashboard:"+str(binding), "message", "Prepare report",
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    work_id = service.execute("digital", turn, task=turn.text, request_key="start")["work"]["work_id"]
    return store, profile, service, work_id, clock


@pytest.mark.parametrize("error_code", ["budget_exceeded", "incomplete_work"])
def test_atomic_report_replay_retention_and_old_failure_path(database, monkeypatch, error_code):
    store, profile, service, identifier, clock = setup(database, "budget-report-"+error_code)
    claim = store.claim_next(worker_id="worker:report", release=profile.release, now=NOW+timedelta(seconds=1))
    call = store.start_call(claim, now=NOW+timedelta(seconds=2))
    report = "Already returned report. " * 400
    kwargs = dict(usage=_usage(), error_code=error_code, error_fingerprint="d"*64,
                  ambiguous=False, now=NOW+timedelta(seconds=3), failure_report=report)
    original = module.insert_initial_timeline_thread
    def failed_insert(*args, **kwargs): raise RuntimeError("report persistence failure")
    monkeypatch.setattr(module, "insert_initial_timeline_thread", failed_insert)
    with pytest.raises(RuntimeError, match="persistence"):
        store.record_failure(call, **kwargs)
    assert store.load(identifier, **service._owner).state == "call_started"
    monkeypatch.setattr(module, "insert_initial_timeline_thread", original)
    saved = store.record_failure(call, **kwargs)
    assert saved.state == "failed" and saved.result is None and saved.failure_report == report
    detail = service._view(saved)
    summary = service._view(saved, include_result=False)
    assert detail["result"] is None and detail["result_available"] is False
    assert detail["failure_report_available"] is True
    assert detail["failure_description"].endswith(report)
    assert report not in summary["failure_description"]
    from eva.application.work_failure_notice import delivery_outcome
    notice = delivery_outcome(saved)
    assert notice.state == "failed" and notice.output.endswith(report)
    assert store.record_failure(call, **kwargs).failure_report == report
    with pytest.raises(DelegatedWorkConflict):
        store.record_failure(call, **{**kwargs, "failure_report": "Changed report"})
    with pytest.raises(DelegatedWorkConflict):
        store.record_failure(call, **{**kwargs, "failure_report": None})
    with pytest.raises((ValueError, RuntimeError)):
        store.load(identifier, owner_principal_id=_id("other", "principal"),
                   principal_binding_id=service.principal_binding_id)
    clock[0] += timedelta(days=8)
    assert store.load(identifier, **service._owner).failure_report is None
    other_store, other_profile, other_service, other_id, _ = setup(database, "budget-no-report-"+error_code)
    other_claim = other_store.claim_next(worker_id="worker:old", release=other_profile.release, now=NOW+timedelta(seconds=1))
    other_call = other_store.start_call(other_claim, now=NOW+timedelta(seconds=2))
    old = other_store.record_failure(other_call, **{k:v for k,v in kwargs.items() if k != "failure_report"})
    assert old.failure_report is None and old.result is None


@pytest.mark.parametrize("error_code", ["budget_exceeded", "incomplete_work"])
def test_worker_passes_budget_report_and_stop_wins(database, error_code):
    for stop in (False, True):
        store, profile, service, identifier, clock = setup(database, "budget-worker-"+error_code+str(stop))
        class Runner:
            async def run(self, spec, **kwargs):
                if stop:
                    store.request_stop(identifier, **service._owner, now=NOW+timedelta(seconds=2))
                clock[0] = NOW+timedelta(seconds=3)
                return ToolFreeChildOutcome(error_code, None, _usage(), False, "d"*64,
                                            failure_report="Returned report")
        worker = DelegatedWorkWorker(store, Runner(), worker_id="worker:report",
                                     release=profile.release, clock=lambda: clock[0])
        assert asyncio.run(worker.run_once()) == ("stopped" if stop else "failed")
        saved = store.load(identifier, **service._owner)
        assert saved.result is None
        assert saved.failure_report == (None if stop else "Returned report")


@pytest.mark.parametrize("status,text", [("internal_error", "report"), ("budget_exceeded", ""),
    ("budget_exceeded", "x"*65537), ("budget_exceeded", "é"*32769)])
def test_outcome_rejects_other_failures_or_unbounded_reports(status, text):
    with pytest.raises(ValueError, match="failure report"):
        ToolFreeChildOutcome(status, None, _usage(), False, "d"*64, failure_report=text)
