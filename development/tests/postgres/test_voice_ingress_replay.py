"""Real PostgreSQL voice receive-time binding, without provider calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, UTC
from threading import Barrier, Event
from uuid import uuid4
import json

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationConflict, ConversationTurn, GroundedReply
from eva.application.memory_audience import MemoryAudience
from tests.postgres.test_people_memory_overview import people_memory_engine


def turn():
    session = str(uuid4())
    return ConversationTurn.private("family", "home", "person:owner", f"voice:{session}",
        f"voice:{session}:{uuid4()}", "Dezelfde vraag", "2026-09-12T12:00:00Z")


def journal(engine):
    return PostgresConversationJournal(engine, external_timeline_scope("family", "home", "test"))


def test_first_receive_time_survives_new_database_connection(people_memory_engine):
    original = turn()
    first = journal(people_memory_engine)
    assert first.admit_voice_ingress(original) == original
    reply = GroundedReply(original.conversation_id, original.message_id, "Bewaard", (), original.occurred_at, "test")
    first.commit_reply(original, reply)
    recovered_engine = sa.create_engine(people_memory_engine.url)
    try:
        recovered = journal(recovered_engine)
        admitted = recovered.admit_voice_ingress(replace(original, occurred_at="2026-09-12T12:05:00Z"))
        assert admitted == original
        assert recovered.committed_reply(admitted) == reply
    finally:
        recovered_engine.dispose()


def test_simultaneous_first_admissions_share_one_durable_stamp(people_memory_engine):
    original = turn()
    barrier = Barrier(2)
    def receive(stamp):
        barrier.wait(timeout=5)
        return journal(people_memory_engine).admit_voice_ingress(replace(original, occurred_at=stamp))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(receive, [original.occurred_at, "2026-09-12T12:00:02Z"]))
    assert replies[0] == replies[1]
    events = journal(people_memory_engine)._timeline.events("conversation:" + original.message_id)
    assert len(events) == 1 and events[0].event_type == "INGRESS_COMMITTED"


def test_changed_identity_content_and_general_timestamps_stay_strict(people_memory_engine):
    original = turn()
    store = journal(people_memory_engine)
    store.admit_voice_ingress(original)
    with pytest.raises(ConversationConflict):
        store.admit(replace(original, occurred_at="2026-09-12T12:00:02Z"))
    for changed in [replace(original, text="Andere vraag"),
                    replace(original, principal_id="person:other", audience=MemoryAudience.private_person(principal_id="person:other")),
                    replace(original, conversation_id=f"voice:{uuid4()}"),
                    replace(original, project_id="different-context")]:
        with pytest.raises(ConversationConflict):
            store.admit_voice_ingress(changed)
    assert store.admit_voice_ingress(original) == original


def test_erased_ingress_cannot_be_restored_by_voice_retry(people_memory_engine):
    original = turn()
    store = journal(people_memory_engine)
    store.admit_voice_ingress(original)
    receipt = store.erase_message_content(principal_id=original.principal_id,
        message_id=original.message_id, operation_sha256="cd" * 32,
        erased_at=datetime(2026, 9, 12, 12, 1, tzinfo=UTC))
    assert receipt.redacted_event_count == 1
    with pytest.raises(ConversationConflict):
        store.admit_voice_ingress(replace(original, occurred_at="2026-09-12T12:00:02Z"))


def test_parallel_voice_execution_and_reconstructed_runtime_do_not_repeat_model(people_memory_engine, monkeypatch):
    from types import SimpleNamespace
    from datetime import timedelta
    from eva.adapters.api import voice_turns
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    from eva.application.memory_background import SemanticMemoryColdPathWorker
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from tests.application.test_conversation import _Memory
    class Clock(datetime):
        current = datetime(2026, 9, 12, 12, tzinfo=UTC)
        @classmethod
        def now(cls, tz=None): return cls.current
    monkeypatch.setattr(voice_turns, "datetime", Clock)
    from eva.application.conversation import ConversationModelResult
    from tests.api.test_telegram_helper_stop_race import helper_race_runtime, OWNER
    waiting, release = Event(), Event()
    calls = []
    class Model:
        def generate(self, received, *_args, **_kwargs):
            calls.append(received)
            waiting.set()
            assert release.wait(5)
            return ConversationModelResult("Eén uitvoering", "controlled")
    def application(engine):
        runtime, _, _, helper_release = helper_race_runtime(monkeypatch)
        helper_release.set()
        runtime._journal = lambda received: journal(engine)
        runtime._conversation._model = Model()
        runtime._cold_path = SemanticMemoryColdPathWorker(_Memory(),
            outbox=PostgresMemoryOutbox(engine, tenant_id="family", installation_id="home"))
        retained = Event()
        def after(received, reply):
            PostgresHindsightMemoryRuntime.after_response(runtime, received, reply)
            retained.set()
        runtime.after_response = after
        class Config:
            valid = True
            def issue_session(self):
                if not self.valid: raise ValueError("revoked")
                return SimpleNamespace(tenant_id="family", installation_id="home", principal_id=str(OWNER))
            def check(self, grant):
                if not self.valid: raise ValueError("revoked")
        config = Config()
        return TestClient(create_app(runtime, dashboard_access_token="v" * 40, dashboard_voice=config)), config, retained
    client, config, first_retained = application(people_memory_engine)
    data = {"session_id": str(uuid4()), "message_id": str(uuid4()), "text": "Eén vraag"}
    url, headers = "/internal/v1/voice-session/respond", {"authorization": "Bearer " + "v" * 40}
    with ThreadPoolExecutor() as pool:
        first = pool.submit(client.post, url, headers=headers, json=data)
        try:
            assert waiting.wait(5)
            second = client.post(url, headers=headers, json=data)
            assert json.loads(second.text.splitlines()[-1]) == {"type": "error", "code": "voice_turn_failed"}
            assert len(calls) == 1
        finally:
            release.set()
        expected = json.loads(first.result(timeout=5).text.splitlines()[-1])
        assert expected["type"] == "final"
    assert first_retained.wait(3)
    Clock.current += timedelta(seconds=2)
    new_engine = sa.create_engine(people_memory_engine.url)
    try:
        restarted, authority, replay_retained = application(new_engine)
        replay = restarted.post(url, headers=headers, json=data)
        assert json.loads(replay.text.splitlines()[-1]) == expected
        assert len(calls) == 1
        assert replay_retained.wait(3)
        pending = PostgresMemoryOutbox(new_engine, tenant_id="family", installation_id="home").pending()
        matching = [job for job in pending if job.document.source_ref == calls[0].message_id]
        assert len(matching) == 1
        assert matching[0].document.occurred_at == calls[0].occurred_at
        events = journal(new_engine)._timeline.events("conversation:" + calls[0].message_id)
        assert sum(event.event_type == "MEMORY_RETAIN_QUEUED" for event in events) == 1
        authority.valid = False
        assert restarted.post(url, headers=headers, json=data).status_code == 403
        assert len(calls) == 1
    finally:
        new_engine.dispose()


def test_distinct_telegram_messages_overlap_in_runtime_and_postgres(people_memory_engine, monkeypatch):
    """A waiting model turn must not lock a different question in the same chat."""
    from eva.application.conversation import ConversationModelResult
    from tests.api.test_telegram_helper_stop_race import helper_race_runtime

    entered, release = Event(), Event()
    calls = []

    class Model:
        def generate(self, received, *_args, **_kwargs):
            calls.append(received.message_id)
            if received.message_id == "telegram:7:101":
                entered.set()
                assert release.wait(5)
            return ConversationModelResult("Answer to " + received.text, "controlled")

    runtime, _, _, unused_release = helper_race_runtime(monkeypatch)
    unused_release.set()
    runtime._journal = lambda received: journal(people_memory_engine)
    runtime._conversation._model = Model()
    first_turn = ConversationTurn.private("family", "home", "telegram:7", "telegram:7",
        "telegram:7:101", "Research question", "2026-09-12T12:00:00Z")
    second_turn = replace(first_turn, message_id="telegram:7:102", text="How are you?")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runtime.respond, first_turn)
        try:
            assert entered.wait(5)
            second = pool.submit(runtime.respond, second_turn)
            second_reply = second.result(timeout=5)
            assert not first.done()
            assert second_reply.message_id == second_turn.message_id
            assert second_reply.text == "Answer to How are you?"
            assert journal(people_memory_engine).committed_reply(second_turn) == second_reply
            assert journal(people_memory_engine).committed_reply(first_turn) is None
        finally:
            release.set()
        first_reply = first.result(timeout=5)
    assert first_reply.message_id == first_turn.message_id
    assert first_reply.text == "Answer to Research question"
    assert journal(people_memory_engine).committed_reply(first_turn) == first_reply
    assert journal(people_memory_engine).committed_reply(second_turn) == second_reply
    assert calls == [first_turn.message_id, second_turn.message_id]
