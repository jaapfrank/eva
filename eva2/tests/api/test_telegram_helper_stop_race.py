from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
import json

import pytest
from fastapi.testclient import TestClient
from eva.adapters.api.app import create_app
from eva.adapters.postgres import delegated_work_actions as delegated_module
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationActionCall, ConversationModelResult, ConversationModelUsage, IdentityProfile, LivingConversation, publish_conversation_text
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from tests.api.test_telegram_canary_api import _Runtime, _payload
from tests.application.test_conversation import _Memory
from tests.bootstrap.test_delegated_work_commands import FakeStore, OWNER, BINDING, NOW


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("helper_count", [1, 2])
def test_late_helper_start_reply_cannot_override_an_already_confirmed_stop(monkeypatch, streaming, helper_count):
    events = {}
    model_waiting = Event()
    release_model = Event()
    class Timeline:
        def __init__(self, *_args): pass
        def events(self, key): return tuple(events.get(key, ()))
        def create_thread(self, *, thread_id, data, event_type, **_):
            events[thread_id] = [SimpleNamespace(event_type=event_type, payload={"data": data})]
        def transition(self, *, thread_id, data, event_type, **_):
            events[thread_id].append(SimpleNamespace(event_type=event_type, payload={"data": data}))
    monkeypatch.setattr(delegated_module, "PostgresOperationalTimeline", Timeline)
    class Store(FakeStore):
        def admit(self, **values):
            result = super().admit(**values)
            work = result.work
            work.state = "call_started"
            work.call_id = "helper-call"
            self.owned_work.insert(0, work)
            self.active_work_id = work.spec.work_id
            return result
        def request_stop(self, work_id, **values):
            self.stopped.append(work_id)
            work = self.load(work_id)
            work.state = "stopped"
            work.stop_requested_at = NOW
            return work
    store = Store()
    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=7, principal_id=str(OWNER), allowed_chat_ids=("7",), principal_binding_id=str(BINDING)),))
    service = DelegatedWorkOwnerService(store, owner_principal_id=OWNER, principal_binding_id=BINDING,
        clock=lambda: NOW, conversation_scope=external_timeline_scope("family", "home", "test"),
        private_bridge_principal_loader=lambda: bindings.private_bridge_principal(principal_id=str(OWNER), principal_binding_id=BINDING))
    provider = delegated_module.PostgresDelegatedWorkActions(object(), service)
    class Actions(PostgresConversationActions):
        def __init__(self): self._external_tool_by_action = {n: provider for n in provider.action_names}
        def pending(self, turn): return provider.pending(turn)
        def pending_step(self, turn, *, step_index): return provider.pending(turn, step_index=step_index)
        def available_action_names(self, turn): return provider.action_names if provider.available_for(turn) else ()
        def continuation_action_names(self, turn): return self.available_action_names(turn)
        def active_context(self, turn): return ()
        def memory_query_hints(self, turn): return ()
        def execute(self, turn, call): return provider.execute(turn, call)
        def execute_step(self, turn, call, *, step_index): return provider.execute(turn, call, step_index=step_index)
    class Model:
        def generate(self, turn, *_args, completed_action=None, **_kwargs):
            if completed_action is None:
                return ConversationModelResult(None, "test-model", action_call=ConversationActionCall("start-call", "start_delegated_work", {"task": "Schrijf een gezinsvoorstel"}))
            if len(store.admitted) < helper_count:
                return ConversationModelResult(None, "test-model", action_call=ConversationActionCall("start-call-two", "start_delegated_work", {"task": "Schrijf een tweede voorstel"}))
            model_waiting.set()
            assert release_model.wait(5)
            text = "De hulpagent is nu bezig. Ik pas je losse stopopdracht straks toe."
            publish_conversation_text(text, tools_disabled=True, final_answer=True)
            return ConversationModelResult(text, "test-model")
    class Runtime(_Runtime):
        respond = PostgresHindsightMemoryRuntime.respond
        respond_telegram_delegated_work = PostgresHindsightMemoryRuntime.respond_telegram_delegated_work
        def __init__(self):
            super().__init__()
            self._telegram_principal_bindings = bindings
            self._telegram_principal_bindings_loader = lambda: bindings
            self._delegated_work_commands = DelegatedWorkConversationCommands(service=service)
            self._conversation = LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Direct", ("Eerlijk",)), Actions())
            self.saved = {}
        def _journal(self, turn):
            return SimpleNamespace(execution=lambda _: nullcontext(), admit=lambda _: None,
                committed_reply=lambda t: self.saved.get(t.message_id), recent=lambda *_a, **_k: (),
                begin_model_request=lambda _: "request", complete_model_request=lambda *_: None,
                model_usage=lambda _: ConversationModelUsage.unmeasured(), ensure_running=lambda _: None,
                commit_reply=self.commit)
        def commit(self, turn, reply):
            self.saved[turn.message_id] = reply
            return reply
    runtime = Runtime()
    client = TestClient(create_app(runtime))
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, "/internal/v1/telegram/canary/respond", json=_payload(text="Laat een hulpagent een gezinsdag bedenken"),
            headers={"accept": "application/x-ndjson"} if streaming else {})
        try:
            assert model_waiting.wait(5)
            stop = client.post("/internal/v1/telegram/canary/respond", json=_payload(message_id=2, text="Stop mijn onderzoek"))
            assert stop.status_code == 200
            assert "stopopdracht is bewaard" in stop.json()["text"]
        finally:
            release_model.set()
        start = future.result(timeout=5)
    assert start.status_code == 200
    rows = [json.loads(line) for line in start.iter_lines()] if streaming else [start.json()]
    final = rows[-1]
    assert "stopopdracht" in final["text"]
    assert "straks" not in start.text
    assert "nu bezig" not in start.text
    assert final["text"] == runtime.saved["telegram:7:1"].text
    assert store.owned_work[0].state == "stopped"
    assert len(store.admitted) == helper_count and len(store.stopped) == 1
    replay = client.post("/internal/v1/telegram/canary/respond", json=_payload(text="Laat een hulpagent een gezinsdag bedenken"))
    assert replay.status_code == 200 and replay.json()["text"] == final["text"]
    assert len(store.admitted) == helper_count and len(store.stopped) == 1

    if helper_count == 2:
        other = store.owned_work[1]
        assert other.state == "call_started" and other.stop_requested_at is None
        assert f"Andere hulpopdrachten in dit verzoek: {other.spec.work_id}." in final["text"]
