"""Native public task returns across wakeword sessions, without real announcements."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest

from eva.bootstrap.native_work_delivery import NativeWorkDelivery


def fixture(monkeypatch, idle_states, satellite="assist_satellite.room"):
    now = datetime.now(timezone.utc)
    def row(session, phase="open", seconds=60):
        return N(payload={"data": {"binding": {"session_id": session,
            "device_id": "device", "satellite_id": satellite}}},
            phase=phase, deadline=now + timedelta(seconds=seconds))
    rows = [row("new"), row("old", "closed")]
    connection = Mock(); connection.execute.return_value.all.return_value = rows
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.pending_household_results",
        lambda connection, store, now: sa.select(runs.c.work_id, runs.c.spec_payload, runs.c.completed_at))
    @contextmanager
    def begin(): yield connection
    store = N(_engine=N(begin=begin), _scope=lambda c: None,
        scope=N(tenant_id="00000000-0000-0000-0000-000000000001", installation_id="00000000-0000-0000-0000-000000000002"))
    acknowledgments, announcements, polls, gets = [], [], [], []
    offered = set()
    class Service:
        def __init__(self, session): self.session = session
        def control_turn(self): return self.session
        def poll(self, turn):
            polls.append(turn)
            if turn != "old" or turn in offered: return None
            offered.add(turn)
            return {"work_id": "research", "result_sha256": "hash", "result": {"text": "Stored public result"}}
        def origin(self, turn): return turn
        def _load(self, work_id, origin):
            assert origin == "old" and work_id == "research"
            return N(state="completed", title="earlier research")
        def result_delivery(self, turn, action, **kwargs):
            acknowledgments.append((turn, action, kwargs))
            if action == "defer": offered.remove(turn)
            return {"authorized": True}
        def current(self): pass
    def session(**kwargs):
        assert kwargs["device_id"] == "device"
        assert kwargs["satellite_id"] == satellite
        return Service(kwargs["session_id"])
    server = N(store=store, delivery_session=session,
        sessions=N(native_delivery=lambda binding: {"mode": "speaker"}),
        authority=N(clock=lambda: now, validate_current=lambda: None, voice=N(token="fixture"),
            sha256="research-policy", policy=N(tenant_id="tenant", installation_id="installation",
                household_id="household", consent_operation_id="consent", household_policy_sha256="policy",
                devices={satellite: "device"}, owner_principal_id="owner",
                principal_binding_id="binding")))
    states = iter(idle_states)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            gets.append(path)
            return N(raise_for_status=lambda: None,
                json=lambda: {"entity_id": satellite, "state": next(states)})
        def post(self, path, json):
            announcements.append((path, json))
            return N(raise_for_status=lambda: None)
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.httpx.Client", Client)
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.readable_work_result", lambda r: r["text"])
    delivery = NativeWorkDelivery(server, N(endpoint_url="http://fixture", credential="fixture"))
    return delivery, announcements, acknowledgments, polls, gets


def test_closed_original_session_does_not_hide_earlier_public_result(monkeypatch):
    delivery, announcements, acknowledgments, polls, gets = fixture(monkeypatch, ["idle", "idle", "idle"])
    assert [b["session_id"] for b in delivery._sessions()] == ["new", "old"]
    delivery.run_once()
    assert polls == ["new", "old"]
    assert len(gets) == 2  # one initial per device, one fresh before dispatch
    assert len(announcements) == 1
    assert announcements[0][1]["entity_id"] == "assist_satellite.room"
    assert "earlier research" in announcements[0][1]["message"]
    assert acknowledgments[-1][2]["disposition"] == "offered"
    delivery.run_once()
    assert len(announcements) == 1


@pytest.mark.parametrize("states, expected", [(["listening"], None), (["idle", "listening"], "defer")])
def test_active_speech_still_blocks_announcements(monkeypatch, states, expected):
    delivery, announcements, acknowledgments, polls, gets = fixture(monkeypatch, states)
    delivery.run_once()
    assert not announcements
    if expected:
        assert acknowledgments[-1][1] == expected
    else:
        assert not polls and not acknowledgments
        assert len(gets) == 1


def test_busy_after_claim_retries_only_when_idle_without_losing_result(monkeypatch):
    delivery, announcements, acknowledgments, polls, gets = fixture(
        monkeypatch, ["idle", "listening", "idle", "idle", "idle"])
    delivery.run_once()
    assert not announcements and acknowledgments[-1][1] == "defer"
    delivery.run_once()
    assert len(announcements) == 1
    assert acknowledgments[-1][2]["disposition"] == "offered"
    delivery.run_once()
    assert len(announcements) == 1


def test_announce_transport_failure_is_ambiguous_and_never_deferred(monkeypatch):
    delivery, announcements, acknowledgments, polls, gets = fixture(
        monkeypatch, ["idle", "idle", "idle"])
    def failed_post(self, path, json):
        announcements.append((path, json))
        raise TimeoutError("response unknown")
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.httpx.Client.post", failed_post)
    delivery.run_once()
    assert len(announcements) == 1
    assert acknowledgments[-1][2]["disposition"] == "ambiguous"
    assert all(action != "defer" for _, action, _ in acknowledgments)
    delivery.run_once()
    assert len(announcements) == 1


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("satellite", ["assist_satellite.room", "assist_satellite.office"])
def test_automation_returns_full_result_as_event_without_audio_or_idle_wait(monkeypatch, failed, satellite):
    delivery, sent, acknowledgments, polls, gets = fixture(monkeypatch, [], satellite)
    delivery.server.sessions.native_delivery = lambda binding: {
        "mode": "automation", "conversation_id": "ha-observation-17"}
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.readable_work_result", lambda r: "x" * 5000)
    if failed:
        def failed_post(self, path, json):
            sent.append((path, json))
            raise TimeoutError("event acknowledgement unavailable")
        monkeypatch.setattr("eva.bootstrap.native_work_delivery.httpx.Client.post", failed_post)
    delivery.run_once()
    assert not gets
    assert len(sent) == 1
    assert sent[0] == ("/api/events/eva2_work_result", {
        "conversation_id": "ha-observation-17", "satellite_id": satellite, "work_id": "research",
        "title": "earlier research", "state": "completed", "text": "x" * 5000,
        "result_sha256": "hash"})
    assert acknowledgments[-1][2]["disposition"] == ("ambiguous" if failed else "offered")
    delivery.run_once()
    assert len(sent) == 1


def test_changed_automation_destination_does_not_publish(monkeypatch):
    delivery, sent, acknowledgments, polls, gets = fixture(monkeypatch, [])
    calls = []
    def destination(binding):
        calls.append(binding["session_id"])
        return {"mode": "automation", "conversation_id": "original" if len(calls) < 3 else "changed"}
    delivery.server.sessions.native_delivery = destination
    delivery.run_once()
    assert not sent and not gets
    assert acknowledgments[-1][2]["disposition"] == "ambiguous"
