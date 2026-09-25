"""Native device continuity preserves original task and delivery authority."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.application.conversation import ConversationActionCall
from eva.bootstrap.household_work import HouseholdWorkActions
from tests.postgres.test_household_public_work import setup, start, PUBLIC, SAT, DEVICE
from tests.postgres.test_scheduled_failure_return import database


def native(x, key, *, satellite=SAT, device=DEVICE):
    binding = x.server.sessions.native(satellite, device, key)
    service = x.server.session(token="test-token", satellite_id=satellite,
                               device_id=device, session_id=binding["session_id"])
    return SimpleNamespace(service=service, turn=service.turn(str(uuid4()), PUBLIC["question"]))


def ids(service, turn):
    return {row["work_id"] for row in service.execute("list", turn, request_key="list")["jobs"]}


@pytest.mark.parametrize("kind", ["research", "digital"])
def test_native_controls_preserve_origin_and_leave_other_task_running(database, kind):
    if kind == "digital":
        from tests.postgres.test_household_digital_work import digital_setup
        x = digital_setup(database, "native-digital-controls")
        def start_task(session):
            return session.service.execute("digital", session.turn,
                request_key="start", task="Bekijk de HA-automatiseringen")["work"]["work_id"]
    else:
        x = setup(database, "native-controls")
        start_task = start
    old = native(x, "first")
    first = start_task(old)
    current = native(x, "second")
    second = start_task(current)
    assert ids(current.service, current.turn) == {first, second}
    assert {row["work_id"] for row in current.service.active_work_metadata(current.turn)["jobs"]} == {first, second}
    original = old.service._load(first, old.service.origin(old.turn)).spec.origin
    assert current.service.execute("get", current.turn, request_key="get", work_id=first)["work"]["work_id"] == first
    # Control continuity must never transfer automatic delivery ownership.
    with pytest.raises(DelegatedWorkNotFound):
        current.service.result_delivery(current.turn, "claim", work_id=first)
    action_turn = current.service.turn(str(uuid4()), "Stop the first task")
    actions = HouseholdWorkActions(database, current.service)
    receipt = actions.execute(action_turn, ConversationActionCall("stop:first", "stop_delegated_work", {"work_id": first}))
    assert actions.pending(action_turn) == receipt
    assert old.service._load(first, original).state == "stopped"
    assert current.service._load(second, current.service.origin(current.turn)).state == "admitted"
    resumed = current.service.execute("resume", action_turn, request_key="resume", work_id=first)["work"]["work_id"]
    assert current.service._load(resumed, current.service.origin(action_turn)).spec.origin == original
    assert current.service._load(second, current.service.origin(current.turn)).state == "admitted"
    old.service.current("close")
    with pytest.raises(DelegatedWorkNotFound):
        actions.pending(action_turn)


def test_native_scope_excludes_other_devices_and_closed_or_expired_sessions(database):
    x = setup(database, "native-scope")
    nonnative = start(x)
    old = native(x, "old")
    expired = start(old)
    x.clock[0] += timedelta(minutes=29)
    closed = native(x, "closed")
    closed_id = start(closed)
    closed.service.current("close")
    other = native(x, "office", satellite="assist_satellite.office", device="b" * 32)
    other_id = start(other)
    current = native(x, "current")
    own = start(current)
    assert ids(current.service, current.turn) == {expired, own}
    x.clock[0] += timedelta(minutes=2)
    assert ids(current.service, current.turn) == {own}
    for identifier in (nonnative, expired, closed_id, other_id):
        with pytest.raises(DelegatedWorkNotFound):
            current.service.execute("get", current.turn, request_key="get", work_id=identifier)
    # Ordinary audio sessions retain exact-session control even on this device.
    binding = x.server.open_session(**x.args)
    exact = x.server.session(**x.args, session_id=binding["session_id"])
    turn = exact.turn(str(uuid4()), "List work")
    assert ids(exact, turn) == set()
    with pytest.raises(DelegatedWorkNotFound):
        exact.execute("get", turn, request_key="get", work_id=own)
    x.policies[0] = x.policies[0].model_copy(update={"state": "revoked"})
    with pytest.raises(ValueError):
        ids(current.service, current.turn)
