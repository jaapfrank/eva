"""Native public task returns across wakeword sessions, without real announcements."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest

from eva.bootstrap.native_work_delivery import NativeWorkDelivery


def fixture(monkeypatch, idle_states):
    now = datetime.now(timezone.utc)
    def row(session, phase="open", seconds=60):
        return N(payload={"data": {"binding": {"session_id": session,
            "device_id": "device", "satellite_id": "assist_satellite.room"}}},
            phase=phase, deadline=now + timedelta(seconds=seconds))
    rows = [row("closed", "closed"), row("new"), row("old"), row("expired", seconds=-1)]
    connection = Mock(); connection.execute.return_value.all.return_value = rows
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
            return {"authorized": True}
        def current(self): pass
    def session(**kwargs):
        assert kwargs["device_id"] == "device"
        assert kwargs["satellite_id"] == "assist_satellite.room"
        return Service(kwargs["session_id"])
    server = N(store=store, session=session,
        authority=N(clock=lambda: now, validate_current=lambda: None, voice=N(token="fixture")))
    states = iter(idle_states)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            gets.append(path)
            return N(raise_for_status=lambda: None,
                json=lambda: {"entity_id": "assist_satellite.room", "state": next(states)})
        def post(self, path, json):
            announcements.append((path, json))
            return N(raise_for_status=lambda: None)
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.httpx.Client", Client)
    monkeypatch.setattr("eva.bootstrap.native_work_delivery.readable_work_result", lambda r: r["text"])
    delivery = NativeWorkDelivery(server, N(endpoint_url="http://fixture", credential="fixture"))
    return delivery, announcements, acknowledgments, polls, gets


def test_new_session_does_not_hide_earlier_public_result(monkeypatch):
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


@pytest.mark.parametrize("states, expected", [(["listening"], None), (["idle", "listening"], "interrupted")])
def test_active_speech_still_blocks_announcements(monkeypatch, states, expected):
    delivery, announcements, acknowledgments, polls, gets = fixture(monkeypatch, states)
    delivery.run_once()
    assert not announcements
    if expected:
        assert acknowledgments[-1][2]["disposition"] == expected
    else:
        assert not polls and not acknowledgments
        assert len(gets) == 1
