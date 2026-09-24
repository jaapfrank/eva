"""Correction errors preserve confirmed lifecycle effects without retrying uncertain writes."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eva.adapters.postgres import scheduled_work, work_corrections
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec


@pytest.mark.parametrize("failure,committed", [
    (None, False), ("correction", False), ("correction", True),
    ("schedule", False), ("schedule", True),
])
def test_correction_reports_confirmed_stop_and_uncertain_later_effect(monkeypatch, failure, committed):
    owner, binding = uuid4(), uuid4()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    scope = external_timeline_scope("family", "home", "test")
    calls, corrections, appointments = [], [], []
    # Only lifecycle identity fields are used; no model or worker executes this fixture.
    spec = DelegatedDigitalWorkSpec.model_construct(work_id="work:original", generation=1)
    previous = SimpleNamespace(spec=spec, state="call_started", stop_requested_at=None)

    def load(work_id, **kwargs):
        if work_id != spec.work_id:
            raise DelegatedWorkNotFound("no successor")
        return previous

    def stop(work_id, **kwargs):
        calls.append("stop")
        previous.stop_requested_at = now
        return previous

    store = SimpleNamespace(scope=scope, _engine=SimpleNamespace(url=SimpleNamespace(get_backend_name=lambda: "postgresql")), load=load, request_stop=stop,
        validate_admission_authority=lambda **kwargs: None)
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
        principal_binding_id=binding, clock=lambda: now)
    turn = ConversationTurn.private("family", "home", str(owner), "private:owner",
        "message:correction", "Revise this task", "2026-09-24T12:00:00Z")

    def record(engine, received, **kwargs):
        calls.append("correction")
        assert previous.stop_requested_at == now and received is spec
        if failure != "correction" or committed:
            corrections.append(kwargs["instruction"])
        if failure == "correction":
            raise RuntimeError("private persistence detail")
        return now

    def schedule(self, received, **kwargs):
        calls.append("schedule")
        assert corrections == ["Use the corrected requirement"]
        assert kwargs["resume"] is True and kwargs["wait_for_stop"] is True
        assert kwargs["request"] == {"work_id": spec.work_id, "run_at": "2026-09-24T12:00:00Z"}
        if failure != "schedule" or committed:
            appointments.append("saved appointment")
        if failure == "schedule":
            raise ValueError("private scheduling detail")
        return {"state": "scheduled", "schedule_id": "schedule:one"}

    monkeypatch.setattr(work_corrections, "record_work_correction", record)
    monkeypatch.setattr(scheduled_work.ScheduledOwnerWork, "schedule", schedule)
    result = service.execute("revise_digital", turn, request_key="correction-request",
        revision={"work_id": spec.work_id, "instruction": "Use the corrected requirement"})
    expected = {None: "correction_saved", "correction": "stop_recorded_correction_unconfirmed",
                "schedule": "correction_saved_resume_unconfirmed"}[failure]
    assert result["status"] == expected
    assert result["work_id"] == spec.work_id
    assert previous.stop_requested_at == now
    assert result["in_flight_effects_may_complete"] is True
    assert calls == (["stop", "correction"] if failure == "correction" else ["stop", "correction", "schedule"])
    assert "private persistence detail" not in str(result) and "private scheduling detail" not in str(result)
    if failure:
        assert "Controleer de taak en planning" in result["guidance"]
        assert "schedule" not in result
    else:
        assert result["schedule"] == {"state": "scheduled", "schedule_id": "schedule:one"}
    assert len(appointments) == int(failure != "correction" and (failure is None or committed))
