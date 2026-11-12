"""A trusted Telegram decision binds only the exact, still-live speaker session."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
import re
import pytest
from eva.application.conversation import ConversationTurn
from eva.bootstrap.voice_identity import VoiceIdentity
from tests.postgres.test_household_public_work import setup, SAT, DEVICE


@pytest.fixture(scope="module")
def delegated_engine():
    import os
    import sqlalchemy as sa
    from sqlalchemy.engine import make_url
    from tests.postgres.test_delegated_work_store import install_schema, ROOT

    dsn = os.environ["EVA2_TEST_POSTGRES_DSN"]
    assert make_url(dsn).database.startswith("eva2_4b_disposable_")
    engine = sa.create_engine(dsn)
    install_schema(dsn, alembic_ini=ROOT / "deploy/alembic.ini")
    try:
        yield engine
    finally:
        engine.dispose()


def identity(engine, label):
    x = setup(engine, label)
    sent = []
    recipient = ["12345"]
    bindings = SimpleNamespace(
        resolve=lambda user, chat: str(x.server.authority.policy.owner_principal_id),
        resolve_binding_id=lambda user, chat: x.server.authority.policy.principal_binding_id,
    )
    runtime = SimpleNamespace(_telegram_principal_bindings_loader=lambda: bindings)
    delivery = SimpleNamespace(
        deliver_text=lambda text, **kwargs: sent.append((text, kwargs))
    )
    verifier = VoiceIdentity(x.server, runtime, delivery, lambda: recipient[0])
    initial = x.clock[0]
    real_now = datetime.now(UTC).replace(microsecond=0)
    verifier.now = lambda: real_now + (x.clock[0] - initial)
    return x, verifier, sent, recipient


def confirmation(v, sent, decision="JA", code=None):
    code = code or re.search(r"STEM JA ([A-F0-9]{10})", sent[0][0])[1]
    return ConversationTurn.private(
        "tenant:test",
        "installation:test",
        "telegram:12345",
        "telegram:12345",
        "telegram:message:" + str(uuid4()),
        "STEM " + decision + " " + code,
        v.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def test_one_question_private_only_after_exact_confirmation_and_no_secret_in_journal(
    delegated_engine,
):
    x, v, sent, recipient = identity(delegated_engine, "voice-verify-exact")
    binding = x.service.binding
    assert v.principal(binding) is None
    assert v.request(binding)["new_message_sent"]
    assert not v.request(binding)["new_message_sent"] and len(sent) == 1
    assert sent[0][1] == {"expected_chat_id": "12345"}
    turn = confirmation(v, sent)
    code = turn.text.split()[-1]
    assert code not in repr(v.timeline.events(v.thread(binding["session_id"])))
    assert v.principal(binding) is None
    wrong = replace(turn, text="STEM JA 0000000000")
    assert "niet meer geldig" in v.confirm_message(wrong, 12345, "12345").text
    assert v.principal(binding) is None
    with pytest.raises(ValueError):
        v.confirm_message(turn, 54321, "12345")
    with pytest.raises(ValueError):
        v.confirm_message(replace(turn, principal_id="other"), 12345, "12345")
    assert "Bevestigd" in v.confirm_message(turn, 12345, "12345").text
    assert v.principal(binding) == v.owner
    assert "al persoonlijk" in v.confirm_message(turn, 12345, "12345").text
    personal = v.private_turn(binding, uuid4(), "Wat heb je gedaan?")
    assert (
        personal.audience.kind == "private_person" and personal.principal_id == v.owner
    )
    assert personal.conversation_id == "voice:" + binding["session_id"]
    with pytest.raises(ValueError):
        v.principal({**binding, "device_id": "b" * 32})
    other = x.server.open_session(
        token="test-token", satellite_id="assist_satellite.office", device_id="b" * 32
    )
    assert v.principal(other) is None
    recipient[0] = "67890"
    with pytest.raises(ValueError):
        v.principal(binding)
    recipient[0] = "12345"
    x.service.current("close")
    with pytest.raises(ValueError):
        v.principal(binding)
    assert "niet meer geldig" in v.confirm_message(turn, 12345, "12345").text


@pytest.mark.parametrize(
    "ending", ["denied", "expired", "closed", "delivery_uncertain"]
)
def test_failed_verification_never_becomes_personal_or_resends(
    delegated_engine, ending
):
    x, v, sent, _ = identity(delegated_engine, "voice-verify-" + ending)
    if ending == "delivery_uncertain":

        def uncertain(text, **kwargs):
            sent.append((text, kwargs))
            raise OSError("delivery acknowledgement lost")

        v.delivery.deliver_text = uncertain
        with pytest.raises(OSError):
            v.request(x.service.binding)
    else:
        v.request(x.service.binding)
    turn = confirmation(v, sent, decision="NEE" if ending == "denied" else "JA")
    if ending == "expired":
        # Maintain the parent heartbeat while allowing the three-minute challenge to expire.
        for _ in range(46):
            x.clock[0] += timedelta(seconds=4)
            x.service.current("renew")
    elif ending == "closed":
        x.service.current("close")
    if ending != "delivery_uncertain":
        v.confirm_message(turn, 12345, "12345")
    if ending == "closed":
        with pytest.raises(ValueError):
            v.principal(x.service.binding)
    else:
        assert v.principal(x.service.binding) is None
        assert not v.request(x.service.binding)["new_message_sent"]
    assert len(sent) == 1


def test_verified_context_drops_shared_history_and_household_work(delegated_engine):
    from eva.adapters.api.voice_turns import VoiceRequest

    x, v, sent, _ = identity(delegated_engine, "voice-verify-history")
    v.request(x.service.binding)
    old_time = v.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    x.clock[0] += timedelta(seconds=1)
    v.confirm_message(confirmation(v, sent), 12345, "12345")
    payload = VoiceRequest(
        session_id=x.service.binding["session_id"],
        message_id=uuid4(),
        text="Hello",
        recent=[
            dict(role="user", text="Shared speaker statement", occurred_at=old_time)
        ],
        active_work=[dict(message_id=uuid4(), text="Shared work")],
        work_results=[dict(message_id=uuid4(), text="Shared result")],
    )
    filtered = v.personal_context(x.service.binding, payload)
    assert (
        not filtered.recent and not filtered.active_work and not filtered.work_results
    )


def test_http_confirmation_switches_only_following_turns_to_existing_private_runtime(
    delegated_engine,
):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.household_voice_session import install_household_voice_session
    from eva.application.conversation import GroundedReply
    from eva.application.voice_context import current_voice_context
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService

    x, v, sent, _ = identity(delegated_engine, "voice-verify-http")
    service = DelegatedWorkOwnerService(
        x.server.store,
        owner_principal_id=x.server.authority.policy.owner_principal_id,
        principal_binding_id=v.owner_binding,
        clock=lambda: x.clock[0],
    )
    turns = []
    admitted = []
    callbacks = []
    runtime = v.runtime
    runtime._voice_identity = v
    runtime.admit_turn_audience = lambda turn: None
    runtime.admit_voice_turn = lambda turn: admitted.append(turn) or turn
    runtime.private_work_return = service.private_return

    def respond(turn, recent=()):
        turns.append(turn)
        callbacks.append(current_voice_context().request_identity)
        return GroundedReply(
            turn.conversation_id,
            turn.message_id,
            "Fixture response",
            (),
            turn.occurred_at,
            "fixture",
        )

    runtime.respond = respond
    runtime.after_response = lambda turn, reply: None
    app = FastAPI()
    install_household_voice_session(
        app, runtime=runtime, authority=x.server.authority.voice, work_server=x.server
    )
    client = TestClient(app)
    path = "/internal/v1/ha-voice/session"
    headers = {"Authorization": "Bearer test-token"}
    body = {
        "session_id": x.service.binding["session_id"],
        "satellite_id": SAT,
        "device_id": DEVICE,
    }

    def ask(message_id=None):
        r = client.post(
            path + "/respond",
            headers=headers,
            json={
                **body,
                "message_id": str(message_id or uuid4()),
                "text": "Hello Eva",
            },
        )
        assert r.status_code == 200
        assert json.loads(r.text.splitlines()[-1])["type"] == "final", r.text

    original_message = uuid4()
    ask(original_message)
    assert turns[-1].audience.kind == "household" and callbacks[-1] is not None
    v.request(x.service.binding)
    v.confirm_message(confirmation(v, sent), 12345, "12345")
    x.clock[0] += timedelta(seconds=1)
    ask(original_message)
    assert turns[-1].audience.kind == "household"
    ask()
    assert (
        turns[-1].audience.kind == "private_person"
        and turns[-1].principal_id == v.owner
    )
    assert callbacks[-1] is None and len(admitted) == 1
    response = client.post(path + "/work/poll", headers=headers, json=body)
    assert (
        response.status_code == 200
        and response.json()["contract"] == "EvaPrivateWorkReturn/v1"
    )
    assert response.json()["result"] is None
    from tests.postgres.test_delegated_delivery_store import succeed

    work_turn = replace(
        turns[-1], occurred_at=x.clock[0].strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    accepted = service.execute(
        "start", work_turn, task="Find a public source", request_key="verified-work"
    )["work"]
    spec = service.store.load(accepted["work_id"], **service._owner).spec
    saved = succeed(service.store, spec)
    claimed = client.post(path + "/work/poll", headers=headers, json=body).json()[
        "result"
    ]
    assert claimed["output"] == saved.output
    claim = {key: claimed[key] for key in ("work_id", "operation_id")}
    authorized = client.post(
        path + "/work/authorize", headers=headers, json={**body, **claim}
    )
    assert (
        authorized.status_code == 200
        and authorized.json()["output_sha256"] == saved.output_sha256
    )
    assert (
        client.post(
            path + "/work/ack",
            headers=headers,
            json={**body, **claim, "disposition": "offered"},
        ).status_code
        == 200
    )
    assert (
        client.post(path + "/work/close", headers=headers, json=body).status_code == 200
    )
    assert (
        client.post(
            path + "/respond",
            headers=headers,
            json={**body, "message_id": str(uuid4()), "text": "Hello"},
        ).status_code
        == 403
    )


def test_concurrent_confirmation_commits_one_decision(delegated_engine):
    from concurrent.futures import ThreadPoolExecutor

    x, v, sent, _ = identity(delegated_engine, "voice-verify-race")
    v.request(x.service.binding)
    turn = confirmation(v, sent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda _: v.confirm_message(turn, 12345, "12345"), range(2))
        )
    assert all(
        "Bevestigd" in reply.text or "al persoonlijk" in reply.text for reply in replies
    )
    assert (
        len(
            [
                e
                for e in v.timeline.events(v.thread(x.service.binding["session_id"]))
                if e.event_type == "VOICE_IDENTITY_CONFIRMED"
            ]
        )
        == 1
    )


def test_database_deadline_conflict_is_bounded_and_does_not_confirm(
    delegated_engine, monkeypatch
):
    from eva.adapters.postgres.operational_timeline import CheckpointConflict

    x, v, sent, _ = identity(delegated_engine, "voice-verify-deadline-conflict")
    v.request(x.service.binding)

    def expired(**kwargs):
        raise CheckpointConflict("checkpoint deadline expired")

    monkeypatch.setattr(v.timeline, "transition", expired)
    assert (
        "niet meer geldig"
        in v.confirm_message(confirmation(v, sent), 12345, "12345").text
    )
    assert v.principal(x.service.binding) is None


def test_existing_telegram_command_entrypoint_commits_local_confirmation_without_model(
    delegated_engine,
):
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    x, v, sent, _ = identity(delegated_engine, "voice-verify-telegram-entry")
    v.request(x.service.binding)
    committed = []

    class Journal:
        def admit(self, turn):
            pass

        def committed_reply(self, turn):
            return committed[0] if committed else None

        def commit_reply(self, turn, reply):
            committed.append(reply)
            return reply

    runtime = SimpleNamespace(_voice_identity=v, _journal=lambda turn: Journal())
    turn = confirmation(v, sent)
    result = PostgresHindsightMemoryRuntime.respond_telegram_delegated_work(
        runtime, turn, telegram_user_id=12345, chat_id="12345"
    )
    assert "Bevestigd" in result.text and v.principal(x.service.binding) == v.owner
    assert (
        PostgresHindsightMemoryRuntime.respond_telegram_delegated_work(
            runtime, turn, telegram_user_id=12345, chat_id="12345"
        )
        == result
    )
    assert len(committed) == 1
