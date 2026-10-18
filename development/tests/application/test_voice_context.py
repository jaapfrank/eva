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



def test_meaningful_first_part_precedes_real_work_without_consuming_durable_step():
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.conversation import ConversationActionReceipt, ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation
    from eva.application.voice_context import SPEAK_AND_CONTINUE
    events, steps = [], []
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def continuation_action_names(self, turn): return self.action_names
        def execute(self, turn, call):
            assert events[0]["type"] == "spoken_update"
            steps.append(0)
            return ConversationActionReceipt(call, {"status": "verified", "value": "blue"})
        def execute_step(self, turn, call, *, step_index):
            steps.append(step_index)
            return ConversationActionReceipt(call, {"status": "verified", "value": "green"})
    class Model:
        count = 0
        def generate(self, *args, available_actions=(), **kwargs):
            self.count += 1
            if self.count == 1:
                assert SPEAK_AND_CONTINUE in available_actions
                call = ConversationActionCall("first", SPEAK_AND_CONTINUE, {"text": "Ik vergelijk de twee gevonden kleuren."})
            elif self.count < 4:
                assert SPEAK_AND_CONTINUE not in available_actions
                call = ConversationActionCall(str(self.count), "inspect_item", {"item": str(self.count)})
            else:
                return ConversationModelResult("De kleuren zijn blauw en groen.", "test")
            return ConversationModelResult(None, "test", call)
    ctx = VoiceContext((), (), lambda _: None, events.append)
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "one", "Vergelijk de kleuren", NOW)
    with voice_context(ctx):
        result = LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions()).respond(turn)
    assert steps == [0, 1]
    assert result.text == "De kleuren zijn blauw en groen."
    assert len([e for e in events if e["type"] == "spoken_update"]) == 1


def test_first_part_is_bounded_and_never_claims_playback_receipt():
    from eva.application.voice_context import SPEAK_AND_CONTINUE
    events = []
    ctx = VoiceContext((), (), lambda _: None, events.append)
    for text in ["", "woord " * 36, 42]:
        with pytest.raises(ValueError):
            ctx.speak_and_continue(ConversationActionCall("first", SPEAK_AND_CONTINUE, {"text": text}))
    assert not events
    result = ctx.speak_and_continue(ConversationActionCall("first", SPEAK_AND_CONTINUE, {"text": "Een regenboog ontstaat door gebroken zonlicht."}))
    assert result.output["heard"] is False
    assert result.output["status"] == "offered_to_voice"


def test_validated_commentary_precedes_action_without_extra_model_or_receipt():
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.conversation import ConversationActionReceipt, ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation
    from eva.application.voice_context import SPEAK_AND_CONTINUE
    events, steps, contexts = [], [], []
    first = "Ik bekijk eerst welke kleur beide voorwerpen hebben."

    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def continuation_action_names(self, turn): return self.action_names
        def execute(self, turn, call):
            assert events[0] == {"type": "spoken_update", "text": first}
            steps.append((0, call.call_id))
            return ConversationActionReceipt(call, {"status": "verified", "value": "blue"})
        def execute_step(self, turn, call, *, step_index):
            steps.append((step_index, call.call_id))
            return ConversationActionReceipt(call, {"status": "verified", "value": "green"})

    class Model:
        def generate(self, *args, **context):
            contexts.append(context)
            index = len(contexts)
            if index == 1:
                return ConversationModelResult(first, "test", ConversationActionCall("real-one", "inspect_item", {}), text_phase="commentary")
            assert SPEAK_AND_CONTINUE not in context["available_actions"]
            assert context["completed_action"].call.call_id == ("real-one" if index == 2 else "real-two")
            offered = [s for s in context["self_context"] if first in s]
            assert len(offered) == 1 and '"heard": false' in offered[0]
            assert '"status": "offered_to_voice"' in offered[0]
            if index == 2:
                assert context.get("prior_actions", ()) == ()
                return ConversationModelResult("Ik kijk nu ook naar het tweede voorwerp.", "test", ConversationActionCall("real-two", "inspect_item", {}), text_phase="commentary")
            assert [r.call.call_id for r in context["prior_actions"]] == ["real-one"]
            return ConversationModelResult("De kleuren zijn blauw en groen.", "test")

    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "commentary", "Vergelijk de kleuren", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, events.append)):
        reply = LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions()).respond(turn)
    assert reply.text == "De kleuren zijn blauw en groen."
    assert steps == [(0, "real-one"), (1, "real-two")]
    assert len(contexts) == 3
    assert [e for e in events if e["type"] == "spoken_update"] == [{"type": "spoken_update", "text": first}]


@pytest.mark.parametrize("text,phase,in_voice", [
    ("Ik kijk even.", None, True),
    ("Ik kijk even.", "commentary", False),
    ("woord " * 36, "commentary", True),
    ("x" * 1001, "commentary", True),
])
def test_commentary_requires_voice_explicit_phase_and_bounds(text, phase, in_voice):
    from contextlib import nullcontext
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.conversation import ConversationActionReceipt, ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation
    events, executed = [], []
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def execute(self, turn, call):
            executed.append(call.call_id)
            return ConversationActionReceipt(call, {"status": "verified"})
    class Model:
        def generate(self, *args, completed_action=None, **kwargs):
            if completed_action is not None:
                return ConversationModelResult("De kleur is blauw.", "test")
            return ConversationModelResult(text, "test", ConversationActionCall("real", "inspect_item", {}), text_phase=phase)
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "bounded", "Welke kleur?", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, events.append)) if in_voice else nullcontext():
        reply = LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions()).respond(turn)
    assert reply.text == "De kleur is blauw." and executed == ["real"]
    assert not any(e["type"] == "spoken_update" for e in events)


@pytest.mark.parametrize("failure", ["cancelled", "revoked", "unavailable_action"])
def test_commentary_and_action_wait_for_existing_turn_guards(failure):
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.cancellation import CancellationSignal, cancellation_scope
    from eva.application.conversation import ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation
    events = []; signal = CancellationSignal(); revoked = False
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def execute(self, turn, call): pytest.fail("Invalid turn admitted its action")
    class Model:
        def generate(self, *args, **kwargs):
            nonlocal revoked
            if failure == "cancelled": signal.cancel()
            if failure == "revoked": revoked = True
            name = "unavailable" if failure == "unavailable_action" else "inspect_item"
            return ConversationModelResult("Ik kijk even.", "test", ConversationActionCall("real", name, {}), text_phase="commentary")
    def checkpoint(*args):
        if revoked: raise PermissionError("Authority revoked")
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "blocked", "Welke kleur?", NOW)
    with cancellation_scope(signal), voice_context(VoiceContext((), (), lambda _: None, events.append)):
        with pytest.raises(PermissionError if failure == "revoked" else RuntimeError):
            LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions(), before_step=checkpoint).respond(turn)
    assert events == []


def test_commentary_rechecks_authority_after_delivery_callback():
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.conversation import ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation
    events = []; revoked = False
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def execute(self, turn, call): pytest.fail("Action crossed revoked delivery authority")
    class Model:
        def generate(self, *args, **kwargs):
            return ConversationModelResult("Ik kijk even.", "test", ConversationActionCall("real", "inspect_item", {}), text_phase="commentary")
    def event(value):
        nonlocal revoked
        events.append(value); revoked = True
    def checkpoint(*args):
        if revoked: raise PermissionError("Authority revoked")
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "revoke-after-offer", "Welke kleur?", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, event)):
        with pytest.raises(PermissionError):
            LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions(), before_step=checkpoint).respond(turn)
    assert events == [{"type": "spoken_update", "text": "Ik kijk even."}]


def test_existing_speak_blocks_another_action_commentary_bridge():
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.conversation import ConversationActionReceipt, ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation
    from eva.application.voice_context import SPEAK_AND_CONTINUE
    events, executed = [], []
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def execute(self, turn, call):
            executed.append(call.call_id)
            return ConversationActionReceipt(call, {"status": "verified"})
    class Model:
        count = 0
        def generate(self, *args, **kwargs):
            self.count += 1
            if self.count == 1:
                return ConversationModelResult(None, "test", ConversationActionCall("speak", SPEAK_AND_CONTINUE, {"text": "Ik kijk even."}))
            if self.count == 2:
                return ConversationModelResult("Ik ga de kleur bekijken.", "test", ConversationActionCall("real", "inspect_item", {}), text_phase="commentary")
            return ConversationModelResult("De kleur is blauw.", "test")
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "speak-first", "Welke kleur?", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, events.append)):
        LivingConversation(_Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions()).respond(turn)
    assert executed == ["real"]
    assert [e for e in events if e["type"] == "spoken_update"] == [{"type": "spoken_update", "text": "Ik kijk even."}]


@pytest.mark.parametrize("valid_search", [True, False])
def test_commentary_waits_for_memory_arguments_without_consuming_action_step(valid_search):
    from tests.application.test_conversation import _Memory, NOW
    from eva.application.conversation import ConversationActionReceipt, ConversationModelResult, ConversationTurn, IdentityProfile, LivingConversation, MEMORY_SEARCH_ACTION
    events, executed = [], []
    class Memory(_Memory):
        reads = 0
        def recall_for_presence(self, query):
            self.reads += 1
            return super().recall_for_presence(query)
    class Actions:
        action_names = ("inspect_item",)
        def pending(self, turn): return None
        def active_context(self, turn): return ()
        def execute(self, turn, call):
            executed.append(call.call_id)
            return ConversationActionReceipt(call, {"status": "verified"})
        def execute_step(self, *args, **kwargs): pytest.fail("Memory/commentary consumed a durable action step")
    class Model:
        count = 0
        def generate(self, *args, **kwargs):
            self.count += 1
            if self.count == 1:
                return ConversationModelResult("Ik denk even terug aan die kleur.", "test", ConversationActionCall("memory", MEMORY_SEARCH_ACTION, {"query_text": "kleur" if valid_search else ""}), text_phase="commentary")
            if self.count == 2:
                assert kwargs["completed_action"].call.name == MEMORY_SEARCH_ACTION
                return ConversationModelResult("Ik controleer de kleur.", "test", ConversationActionCall("real", "inspect_item", {}), text_phase="commentary")
            return ConversationModelResult("De kleur is blauw.", "test")
    memory = Memory()
    turn = ConversationTurn.private("tenant", "installation", "jaap", "voice", "memory-first", "Welke kleur was het?", NOW)
    with voice_context(VoiceContext((), (), lambda _: None, events.append)):
        conversation = LivingConversation(memory, Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions())
        if valid_search:
            assert conversation.respond(turn).text == "De kleur is blauw."
        else:
            with pytest.raises(RuntimeError, match="memory search request is invalid"):
                conversation.respond(turn)
    assert memory.reads == (2 if valid_search else 1)
    assert executed == (["real"] if valid_search else [])
    assert [e for e in events if e["type"] == "spoken_update"] == ([{"type": "spoken_update", "text": "Ik denk even terug aan die kleur."}] if valid_search else [])
