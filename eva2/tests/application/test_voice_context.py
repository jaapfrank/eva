from concurrent.futures import ThreadPoolExecutor
import pytest
from eva.application.voice_context import VoiceContext, voice_context, current_voice_context, STOP_VOICE_WORK
from eva.application.conversation import ConversationActionCall

def test_stop_is_exact_and_context_does_not_leak():
    stopped, events = [], []
    ctx = VoiceContext((), (("running", "onderzoek"),), stopped.append, events.append)
    with voice_context(ctx):
        assert current_voice_context() is ctx
        with ThreadPoolExecutor() as pool:
            assert pool.submit(current_voice_context).result() is None
        with pytest.raises(ValueError):
            ctx.stop_work(ConversationActionCall("c1", STOP_VOICE_WORK, {"message_id": "other"}))
        assert not stopped
        receipt = ctx.stop_work(ConversationActionCall("c2", STOP_VOICE_WORK, {"message_id": "running"}))
        assert receipt.output["stop_requested"] is True
        assert receipt.output["already_dispatched_steps_may_finish"] is True
    assert current_voice_context() is None
    assert stopped == ["running"]
    assert events == [{"type": "task_stop_requested", "message_id": "running"}]


@pytest.mark.parametrize("text", ["stop deze opdracht", "/stop"])
def test_runtime_shortcut_does_not_bypass_voice_suppression(text):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from eva.application.conversation import ConversationTurn, GroundedReply
    turn = ConversationTurn.private("tenant:001", "installation:001", "person:owner", "voice:session", "voice:new", text, "2026-09-10T10:00:00Z")
    stopped, events = [], []
    context = VoiceContext((), (("voice:old", "onderzoek"),), stopped.append, events.append)
    class Journal:
        def execution(self, turn): return nullcontext()
        def admit(self, turn): pass
        def committed_reply(self, turn): return None
        def recent(self, turn, **kwargs): return ()
        def request_conversation_stop(self, **kwargs): pytest.fail("voice skipped its suppression callback")
        def ensure_running(self, turn): pass
        def commit_reply(self, turn, reply): return reply
    def respond(turn, recent, **kwargs):
        current_voice_context().stop_work(ConversationActionCall("c", STOP_VOICE_WORK, {"message_id": "voice:old"}))
        return GroundedReply(turn.conversation_id, turn.message_id, "Gestopt.", (), turn.occurred_at, "test")
    runtime = SimpleNamespace(admit_turn_audience=lambda turn: None, _journal=lambda turn: Journal(),
                              _conversation=SimpleNamespace(respond=respond))
    with voice_context(context):
        PostgresHindsightMemoryRuntime.respond(runtime, turn)
    assert stopped == ["voice:old"]
    assert events[0]["type"] == "task_stop_requested"


@pytest.mark.parametrize("with_budget", [False, True])
def test_voice_control_is_offered_to_existing_model_and_removed_outside_context(tmp_path, with_budget):
    from tests.model.test_pydantic_conversation_model import _adapter, _turn, _identity, _memory, _runtime_budget
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase, ScriptedCall
    scripted = ScriptedPydanticConversation((ScriptedPhase.deferred(
        ScriptedCall("voice-stop", STOP_VOICE_WORK, {"message_id": "old"})),))
    model = _adapter(tmp_path, scripted, resource_budget=_runtime_budget() if with_budget else None)
    with voice_context(VoiceContext((), (("old", "onderzoek"),), lambda key: None, lambda event: None)):
        result = model.generate(_turn(), _identity(), _memory(), (), available_actions=(STOP_VOICE_WORK,))
    assert result.action_call.name == STOP_VOICE_WORK
    with pytest.raises(ValueError, match="unknown conversation actions"):
        model.generate(_turn(), _identity(), _memory(), (), available_actions=(STOP_VOICE_WORK,))


def test_voice_control_keeps_existing_catalog_allowance_and_token_budget(tmp_path):
    from tests.model.test_pydantic_conversation_model import _adapter, _runtime_budget
    from tests.support.scripted_pydantic_conversation import ScriptedPydanticConversation, ScriptedPhase
    from eva.adapters.model.pydantic_conversation import _descriptor_tool
    from eva.application.voice_context import STOP_DESCRIPTOR
    from eva.contracts.canonical_json import canonical_json_bytes
    model = _adapter(tmp_path, ScriptedPydanticConversation((ScriptedPhase.text("unused"),)), resource_budget=_runtime_budget())
    search = {"execution": "server", "type": "tool_search"}
    tool = {"type": "function", "name": "boundary_probe", "description": "", "parameters": {"type": "object", "properties": {}}}
    baseline = [search, {**tool, "defer_loading": True}]
    tool["description"] = "x" * (20_000 - len(canonical_json_bytes(baseline)))
    model._conversation_tools[tool["name"]] = tool
    catalog = [search, {**tool, "defer_loading": True}]
    assert len(canonical_json_bytes(catalog)) == 20_000
    model._admit_request({"tools": catalog}, autonomous=False)
    control = {**_descriptor_tool(STOP_DESCRIPTOR), "defer_loading": True}
    with voice_context(VoiceContext((), (("old", "onderzoek"),), lambda key: None, lambda event: None)):
        model._admit_request({"tools": [*catalog, control]}, autonomous=False)
        with pytest.raises(Exception, match="runtime input budget"):
            model._admit_request({"tools": [*catalog, control], "input": "words " * 100_000}, autonomous=False)
    with pytest.raises(Exception, match="deferred tool catalog"):
        model._admit_request({"tools": [*catalog, control]}, autonomous=False)

