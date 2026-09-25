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
    assert actions.pending(action_turn) == receipt


def test_native_scope_keeps_retained_tasks_and_excludes_other_devices_and_nonnative_sessions(database):
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
    assert ids(current.service, current.turn) == {expired, closed_id, own}
    x.clock[0] += timedelta(minutes=2)
    assert ids(current.service, current.turn) == {expired, closed_id, own}
    for identifier in (nonnative, other_id):
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


@pytest.mark.parametrize("kind", ["research", "digital"])
@pytest.mark.parametrize("lifetime", ["closed", "expired"])
def test_retained_native_work_controls_and_original_output_once(database, monkeypatch, kind, lifetime):
    import hashlib
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import operational_events, operational_checkpoints
    from eva.bootstrap import native_work_delivery as delivery_module
    from eva.contracts.delegated_work import DelegatedWorkResult
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkResult
    from tests.postgres.test_delegated_work_store import _usage

    label = "native-retained-" + kind + "-" + lifetime
    if kind == "digital":
        from tests.postgres.test_household_digital_work import digital_setup
        x = digital_setup(database, label)
    else:
        x = setup(database, label)
    original = native(x, "original")
    if kind == "digital":
        first_id = original.service.execute("digital", original.turn, request_key="start",
            task="Controleer de woonkamer")["work"]["work_id"]
    else:
        first_id = start(original)
    first = original.service._load(first_id, original.service.origin(original.turn))
    if lifetime == "closed":
        original.service.current("close")
    else:
        x.clock[0] += timedelta(minutes=31)
    with pytest.raises(ValueError):
        x.server.session(**x.args, session_id=first.spec.origin.session_id)
    assert x.server.sessions.read_retained_native(first.spec.origin.session_id, SAT, DEVICE) == original.service.binding

    # Empty native history cannot displace a retained task or trip a global session limit.
    if kind == "research" and lifetime == "closed":
        for index in range(130):
            x.server.sessions.native(SAT, DEVICE, "empty-history-" + str(index))
    current = native(x, "fresh-wakeword")
    assert current.service.execute("get", current.turn, request_key="get", work_id=first_id)["work"]["work_id"] == first_id
    assert current.service.execute("stop", current.turn, request_key="stop", work_id=first_id)["work"]["state"] == "stopped"
    second_id = current.service.execute("resume", current.turn, request_key="resume", work_id=first_id)["work"]["work_id"]
    assert current.service.execute("resume", current.turn, request_key="resume-retry", work_id=first_id)["work"]["work_id"] == second_id
    second = current.service._load(second_id, current.service.origin(current.turn))
    assert second.spec.origin == first.spec.origin and second.spec.grant == first.spec.grant
    assert second.spec.generation == first.spec.generation + 1
    with pytest.raises(DelegatedWorkNotFound):
        x.server.store.load(second_id, **current.service._owner)
    other = native(x, "other-device", satellite="assist_satellite.office", device="b" * 32)
    with pytest.raises(DelegatedWorkNotFound):
        other.service.execute("get", other.turn, request_key="get", work_id=second_id)
    with pytest.raises(ValueError):
        x.server.delivery_session(token="test-token", satellite_id="assist_satellite.office",
            device_id="b" * 32, session_id=first.spec.origin.session_id)

    release = x.profile.release if kind == "digital" else x.server.authority.profile.release
    claim = x.server.store.claim_next(worker_id="worker:retained", release=release, now=x.clock[0])
    call = x.server.store.start_call(claim, now=x.clock[0])
    text = "Het bewaarde huishoudresultaat is gecontroleerd."
    result_type = DelegatedDigitalWorkResult if kind == "digital" else DelegatedWorkResult
    result = result_type.model_validate(dict(
        contract="DelegatedDigitalWorkResult/v4" if kind == "digital" else "DelegatedWorkResult/v1",
        schema_version=4 if kind == "digital" else 1, work_id=second_id, call_id=call.call_id,
        output=text, output_sha256=hashlib.sha256(text.encode()).hexdigest(), spec_sha256=second.spec.sha256,
        usage=_usage().model_dump(mode="json"), completed_at=x.clock[0].strftime("%Y-%m-%dT%H:%M:%SZ")))
    x.server.store.record_success(call, result, now=x.clock[0])
    assert current.service.execute("get", current.turn, request_key="result", work_id=second_id)["work"]["result"] == text
    with pytest.raises(DelegatedWorkNotFound):
        current.service.result_delivery(current.turn, "claim", work_id=second_id)
    output = x.server.delivery_session(**x.args, session_id=first.spec.origin.session_id)
    control = output.control_turn()
    with pytest.raises(ValueError):
        output.turn(str(uuid4()), "Start other work")
    with pytest.raises(ValueError):
        output.execute("resume", control, request_key="forbidden", work_id=first_id)
    with pytest.raises(ValueError):
        output.active_work_metadata(control)
    with pytest.raises(DelegatedWorkNotFound):
        output._load(first_id, output.origin(control))

    sent = []
    class Response:
        def __init__(self, data=None):
            self.data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self.data
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://ha.invalid"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, path):
            assert path == "/api/states/" + SAT
            return Response({"entity_id": SAT, "state": "idle"})
        def post(self, path, *, json):
            assert path == "/api/services/assist_satellite/announce"
            sent.append(json)
            return Response()
    monkeypatch.setattr(delivery_module.httpx, "Client", Client)
    delivery = delivery_module.NativeWorkDelivery(x.server,
        SimpleNamespace(endpoint_url="http://ha.invalid", credential="fixture"))
    assert list(delivery._sessions()) == [original.service.binding]
    delivery.run_once()
    delivery.run_once()
    assert len(sent) == 1 and sent[0]["entity_id"] == SAT and text in sent[0]["message"]
    assert list(delivery._sessions()) == [] and output.poll(control) is None
    with database.connect() as connection:
        x.server.store._scope(connection)
        acknowledgements = connection.execute(sa.select(operational_events.c.payload).where(
            operational_events.c.tenant_id == x.server.store.scope.tenant_id,
            operational_events.c.installation_id == x.server.store.scope.installation_id,
            operational_events.c.event_type == "HOUSEHOLD_WORK_RESULT_ACKNOWLEDGED",
        )).scalars().all()
        state = connection.execute(sa.select(operational_checkpoints.c.phase, operational_checkpoints.c.deadline).where(
            operational_checkpoints.c.tenant_id == x.server.store.scope.tenant_id,
            operational_checkpoints.c.installation_id == x.server.store.scope.installation_id,
            operational_checkpoints.c.thread_id == "household-work-session:" + first.spec.origin.session_id,
        )).one()
    assert len(acknowledgements) == 1
    assert acknowledgements[0]["data"]["origin_message_id"] == first.spec.origin.message_id
    assert acknowledgements[0]["data"]["destination_sha256"] == second.spec.destination_sha256
    assert (state.phase == "closed") if lifetime == "closed" else (state.deadline < x.clock[0])
    x.clock[0] += timedelta(days=8)
    later = native(x, "after-retention")
    with pytest.raises(DelegatedWorkNotFound):
        later.service.execute("get", later.turn, request_key="expired", work_id=second_id)
    with pytest.raises(DelegatedWorkNotFound):
        x.server.store.load(second_id, **output._owner, household_origin=output.origin(control),
            household_delivery_binding=original.service.binding)
    x.policies[0] = x.policies[0].model_copy(update={"state": "revoked"})
    with pytest.raises(ValueError):
        output.current()
    with pytest.raises(ValueError):
        later.service.execute("get", later.turn, request_key="revoked", work_id=second_id)
