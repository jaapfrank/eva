"""Real API/runtime orchestration; persistence, memory provider and model are ports.

This does NOT prove Hindsight correction/recall, physical playback interruption,
or durable restart. It proves correction admission cannot steal a concurrent
voice turn's identity/context or cancel/re-admit that turn's helper work.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import MethodType, SimpleNamespace
from uuid import uuid4
import json

from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app
from eva.application.conversation import ConversationTurn
from eva.application.voice_context import current_voice_context
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from tests.api.test_telegram_helper_stop_race import helper_race_runtime
from tests.bootstrap.test_delegated_work_commands import OWNER


def test_voice_helper_and_owner_correction_keep_independent_context_and_results(monkeypatch):
    runtime, store, waiting, release = helper_race_runtime(monkeypatch)
    token = "x" * 40
    source_id = str(uuid4())
    session_id, message_id = str(uuid4()), str(uuid4())
    source_turn = ConversationTurn.private("family", "home", str(OWNER),
        "telegram:7", "source:one", "Mijn favoriete kleur is blauw.", "2026-09-12T10:00:00Z")
    doc = SimpleNamespace(**{key: getattr(source_turn, key) for key in
        ("tenant_id", "installation_id", "principal_id", "conversation_id", "audience", "project_id")})
    source_reads, queued, admitted, observed_contexts = [], [], [], []
    voice_finished = Event()

    def document(identifier, **_):
        source_reads.append(identifier)
        if identifier != source_id:
            raise LookupError("outside owner source selection")
        return doc, "retained"
    runtime._owner_memory_sources = lambda: SimpleNamespace(document=document)
    runtime.dashboard_correct_memory_source = MethodType(
        PostgresHindsightMemoryRuntime.dashboard_correct_memory_source, runtime)
    original_journal = runtime._journal
    def journal(turn):
        value = original_journal(turn)
        value.admit = lambda target: admitted.append(target)
        value.load = lambda target: SimpleNamespace(phase=(
            "memory_queued" if any(t.message_id == target.message_id for t in queued) else "reply_committed"))
        return value
    runtime._journal = journal
    def after_response(turn, reply):
        # Replace only the persistence/retention boundary, not correction admission.
        assert runtime.saved[turn.message_id] == reply
        assert current_voice_context() is None
        queued.append(turn)
        if turn.message_id.startswith("voice:"):
            voice_finished.set()
    runtime.after_response = after_response
    model = runtime._conversation._model
    generate = model.generate
    def observe(*args, **kwargs):
        ctx = current_voice_context()
        assert ctx is not None
        observed_contexts.append(ctx)
        return generate(*args, **kwargs)
    model.generate = observe

    class Config:
        def issue_session(self):
            return SimpleNamespace(tenant_id="family", installation_id="home", principal_id=str(OWNER))
        def check(self, grant):
            assert grant.principal_id == str(OWNER)
    client = TestClient(create_app(runtime, dashboard_access_token=token, dashboard_voice=Config()))
    headers = {"authorization": "Bearer " + token}
    payload = {"session_id": session_id, "message_id": message_id,
        "text": "Laat een hulpagent een gezinsvoorstel schrijven",
        "recent": [{"role": "assistant", "text": "Alleen dit hoorde ik.",
                    "occurred_at": "2026-09-12T10:00:00Z", "interrupted": True}]}
    voice_url = "/internal/v1/voice-session/respond"
    correction_url = f"/internal/v1/dashboard/memory-sources/{source_id}/correct"
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, voice_url, headers=headers, json=payload)
        try:
            assert waiting.wait(5), "real helper start must precede correction"
            assert len(store.admitted) == 1 and store.owned_work[0].state == "call_started"
            assert not future.done()
            # Client cannot redirect either route to another person's scope.
            assert client.post(voice_url, headers=headers,
                json={**payload, "principal_id": "person:other"}).status_code == 422
            assert client.post(correction_url, headers=headers,
                json={"text": "groen", "principal_id": "person:other"}).status_code == 422
            correction = client.post(correction_url, headers=headers,
                json={"text": "Mijn favoriete kleur is groen."})
            assert correction.status_code == 202
            assert correction.json()["state"] == "correction_queued"
            assert len(queued) == 1 and source_reads == [source_id]
            changed = queued[0]
            assert changed.principal_id == str(OWNER)
            assert changed.audience == source_turn.audience
            assert changed.conversation_id == "telegram:7"
            assert changed.message_id.startswith("dashboard-memory:")
            assert "groen" in changed.text
            assert store.stopped == [] and len(store.admitted) == 1
        finally:
            release.set()
        response = future.result(timeout=5)
    assert response.status_code == 200
    rows = [json.loads(line) for line in response.iter_lines()]
    assert rows[-1]["type"] == "final", rows
    voice_mid = f"voice:{session_id}:{message_id}"
    final = rows[-1]["reply"]
    assert final["text"] == runtime.saved[voice_mid].text
    assert voice_finished.wait(5)
    assert len({t.message_id for t in queued}) == 2
    assert all(t.principal_id == str(OWNER) for t in queued)
    assert observed_contexts and all(c is observed_contexts[0] for c in observed_contexts)
    assert observed_contexts[0].recent[0].playback_status == "interrupted"
    assert store.stopped == [] and len(store.admitted) == 1
    assert current_voice_context() is None
    # Same request replay returns committed result, not another helper or model call.
    calls_before = len(observed_contexts)
    replay = client.post(voice_url, headers=headers, json=payload)
    assert [json.loads(line) for line in replay.iter_lines()][-1]["reply"] == final
    assert len(store.admitted) == 1 and len(observed_contexts) == calls_before
    assert runtime.saved[changed.message_id].provider == "owner-memory-controls"
