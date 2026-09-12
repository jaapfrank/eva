"""PostgreSQL correction/outbox recovery during an admitted voice/helper turn.

Semantic retain/recall is deterministic, NOT Hindsight/model quality evidence.
Journal, source authority, outbox, recovery worker and API/runtime are real.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from threading import Event
from types import MethodType, SimpleNamespace
from uuid import uuid4
import hashlib
import json
import time

from fastapi.testclient import TestClient
import sqlalchemy as sa
import pytest
from eva.adapters.api.app import create_app
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
from eva.application.conversation import ConversationModelResult, ConversationTurn
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from eva.application.memory_background import SemanticMemoryColdPathWorker
from eva.bootstrap import runtime as runtime_module
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from tests.api import test_telegram_helper_stop_race as helpers
from tests.application.test_conversation import _Memory
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_people_memory_overview import people_memory_engine, NOW


def test_durable_correction_recovery_during_voice_helper_then_new_recall(monkeypatch, people_memory_engine):
    engine = people_memory_engine
    label = "voice-memory-integration"
    authority, _, binding = _seed_authority(engine, label)
    owner = _id(label, "principal")
    monkeypatch.setattr(helpers, "OWNER", owner)
    monkeypatch.setattr(helpers, "BINDING", binding)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(runtime_module, "datetime", Clock)
    runtime, store, waiting, release = helpers.helper_race_runtime(monkeypatch)
    scope = external_timeline_scope("family", "home", "test")
    runtime._journal = lambda turn: PostgresConversationJournal(engine, scope)
    runtime.after_response = MethodType(PostgresHindsightMemoryRuntime.after_response, runtime)
    runtime.dashboard_correct_memory_source = MethodType(PostgresHindsightMemoryRuntime.dashboard_correct_memory_source, runtime)
    runtime._owner_memory_sources = lambda: PostgresOwnerMemorySources(engine,
        tenant_id="family", installation_id="home", owner=owner, binding=binding, authority_scope=authority)
    outbox = PostgresMemoryOutbox(engine, tenant_id="family", installation_id="home")

    class SemanticPort(_Memory):
        # An explicit port double: newest submitted controlled colour, no NLP.
        def __init__(self):
            super().__init__(); self.documents = {}; self.recalls = []
        def retain_conversation(self, document):
            self.documents[document.document_id] = document
            return MemoryRetentionReceipt("test-bank", document.document_id,
                hashlib.sha256(document.content.encode()).hexdigest())
        def recall_for_presence(self, query):
            self.recalls.append(query)
            result = super().recall_for_presence(query)
            docs = [d for d in self.documents.values() if d.principal_id == query.principal_id]
            corrections = [d for d in docs if "groen" in d.content]
            text = corrections[-1].content if corrections else "geen kleur bekend"
            return replace(result, confirmed_facts=(result.confirmed_facts[0].model_copy(update={"value": {"text": text}}),))
    semantic = SemanticPort()
    runtime._conversation._memory = semantic
    runtime._cold_path = SemanticMemoryColdPathWorker(semantic, outbox=outbox, idle_seconds=3600)
    original = ConversationMemoryDocument(tenant_id="family", installation_id="home",
        principal_id=str(owner), conversation_id="telegram:7", document_id="telegram:7:exchange:old",
        source_ref="old", evidence_ref="conversation-reply:old", content="Mijn kleur is blauw.",
        occurred_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), privacy_class="internal", trust="direct_user",
        audience=MemoryAudience.private_person(principal_id=str(owner)))
    outbox.enqueue(original)
    foreign = replace(original, principal_id="person:other", document_id="other:exchange:old",
        source_ref="foreign", audience=MemoryAudience.private_person(principal_id="person:other"), content="geel")
    outbox.enqueue(foreign)
    page = runtime._owner_memory_sources().page(now=NOW)
    assert len(page.sources) == 1
    source_id = page.sources[0].source_id
    class Config:
        def issue_session(self): return SimpleNamespace(tenant_id="family", installation_id="home", principal_id=str(owner))
        def check(self, grant): assert grant.principal_id == str(owner)
    errors = []
    real_respond = runtime.respond
    def debug_respond(*args, **kwargs):
        try: return real_respond(*args, **kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    runtime.respond = debug_respond
    token = "x" * 40
    client = TestClient(create_app(runtime, dashboard_access_token=token, dashboard_voice=Config()))
    headers = {"authorization": "Bearer " + token}
    payload = {"session_id": str(uuid4()), "message_id": str(uuid4()), "text": "Start een helper"}
    url = "/internal/v1/voice-session/respond"
    worker = None
    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(client.post, url, headers=headers, json=payload)
            try:
                assert waiting.wait(5), errors
                correction = client.post(f"/internal/v1/dashboard/memory-sources/{source_id}/correct",
                    headers=headers, json={"text": "Mijn kleur is groen."})
                assert correction.status_code == 202, correction.text
                assert store.stopped == [] and len(store.admitted) == 1
                # Reconstruct DB connection and outbox before any retain processing.
                recovered_engine = sa.create_engine(engine.url)
                recovered = PostgresMemoryOutbox(recovered_engine, tenant_id="family", installation_id="home")
                pending = recovered.pending()
                corrections = [j.document for j in pending if "groen" in j.document.content]
                assert len(corrections) == 1
                changed = corrections[0]
                assert changed.principal_id == str(owner) and changed.conversation_id == "telegram:7"
                correction_turn = ConversationTurn.private("family", "home", str(owner), changed.conversation_id,
                    changed.source_ref, "Correctie op eerdere informatie. Voortaan geldt: Mijn kleur is groen.", changed.occurred_at)
                assert runtime._journal(correction_turn).load(correction_turn).phase == "memory_queued"
                worker = SemanticMemoryColdPathWorker(semantic, outbox=recovered, idle_seconds=3600)
                worker.start()
                deadline = time.monotonic() + 5
                while recovered.pending() and time.monotonic() < deadline: time.sleep(.02)
                assert recovered.pending() == () and worker.last_error is None
                assert changed.document_id in semantic.documents
            finally:
                release.set()
            response = future.result(timeout=5)
        rows = [json.loads(line) for line in response.iter_lines()]
        assert rows[-1]["type"] == "final", rows
        final = rows[-1]["reply"]
        # Exact admitted-turn replay, alongside the separate HTTP clock-advance regression.
        admitted_turn = ConversationTurn.private("family", "home", str(owner),
            final["conversation_id"], final["message_id"], payload["text"], final["generated_at"])
        assert runtime.respond(admitted_turn).text == final["text"]
        assert len(store.admitted) == 1 and store.stopped == []
        assert len([j for j in recovered.pending() if j.document.document_id == changed.document_id]) == 0
        # Fresh normal conversation uses the retained port result, not prior context.
        class ReadModel:
            def generate(self, turn, identity, memory, *_args, **_kwargs):
                return ConversationModelResult(memory.confirmed_facts[0].value["text"], "controlled-read")
        runtime._conversation._model = ReadModel()
        fresh = {"session_id": str(uuid4()), "message_id": str(uuid4()), "text": "Welke kleur?"}
        answered = client.post(url, headers=headers, json=fresh)
        assert "groen" in [json.loads(line) for line in answered.iter_lines()][-1]["reply"]["text"]
        assert semantic.recalls[-1].principal_id == str(owner)
        assert all(d.principal_id == str(owner) for d in corrections)
        assert len(runtime._owner_memory_sources().page(now=NOW).sources) >= 2
        assert all(s.excerpt != "geel" for s in runtime._owner_memory_sources().page(now=NOW).sources)
    finally:
        release.set()
        runtime._cold_path.stop()
        if worker is not None: worker.stop()
        if "recovered_engine" in locals(): recovered_engine.dispose()


def test_voice_http_replay_after_clock_advance_keeps_committed_result(monkeypatch, people_memory_engine):
    from datetime import timedelta
    from eva.adapters.api import voice_turns
    runtime, store, _, release = helpers.helper_race_runtime(monkeypatch)
    release.set()
    scope = external_timeline_scope("family", "home", "test")
    runtime._journal = lambda turn: PostgresConversationJournal(people_memory_engine, scope)
    calls = []
    class Model:
        def generate(self, turn, *_args, **_kwargs):
            calls.append(turn)
            return ConversationModelResult("Bewaard antwoord.", "controlled")
    runtime._conversation._model = Model()
    class Clock(datetime):
        current = NOW
        @classmethod
        def now(cls, tz=None): return cls.current
    monkeypatch.setattr(voice_turns, "datetime", Clock)
    class Config:
        def issue_session(self): return SimpleNamespace(tenant_id="family", installation_id="home", principal_id=str(helpers.OWNER))
        def check(self, grant): pass
    token = "r" * 40
    client = TestClient(create_app(runtime, dashboard_access_token=token, dashboard_voice=Config()))
    payload = {"session_id": str(uuid4()), "message_id": str(uuid4()), "text": "Antwoord kort"}
    headers = {"authorization": "Bearer " + token}
    first = client.post("/internal/v1/voice-session/respond", headers=headers, json=payload)
    expected = [json.loads(line) for line in first.iter_lines()][-1]
    assert expected["type"] == "final"
    Clock.current += timedelta(seconds=2)
    second = client.post("/internal/v1/voice-session/respond", headers=headers, json=payload)
    actual = [json.loads(line) for line in second.iter_lines()][-1]
    assert actual == expected
    assert len(calls) == 1 and store.admitted == []
