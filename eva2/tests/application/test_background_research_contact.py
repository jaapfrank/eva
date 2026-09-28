"""Research admission ends the foreground turn without running or polling research."""
import pytest

from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, LivingConversation
from tests.application.test_contact_model import IDENTITY, Model, Actions, turn
from tests.application.test_conversation import _Memory


@pytest.mark.parametrize("use_contact", [False, True])
def test_background_research_admission_keeps_conversation_available(use_contact):
    class ResearchActions(Actions):
        action_names = ("start_background_research", "get_delegated_work")

        def execute(self, t, call):
            assert call.name == "start_background_research", "foreground must not poll"
            self.jobs[t.message_id] = "admitted"
            return ConversationActionReceipt(call, {"status": "verified", "work_id": t.message_id})

    def choose(t, context):
        if context["completed_action"] is not None:
            assert context["available_actions"] == ()
            return "Het onderzoek is ingepland."
        if t.text == "onderzoek":
            assert any("start_background_research" in line and "geen privégegevens" in line
                       for line in context["self_context"])
            return ConversationActionCall("research-call", "start_background_research", {})
        return "Vier."

    foreground = Model(choose)
    work = Model(lambda *_: pytest.fail("admitted research must not run in foreground")) if use_contact else foreground
    actions = ResearchActions()
    conversation = LivingConversation(_Memory(), work, IDENTITY, actions,
                                      contact_model=foreground if use_contact else None)
    conversation.respond(turn("research", "onderzoek"))
    reply = conversation.respond(turn("follow-up", "twee plus twee"))
    assert reply.text == "Vier."
    assert actions.jobs == {"research": "admitted"}
    assert len(foreground.calls) == 3


def test_unavailable_research_does_not_add_research_instructions():
    model = Model(lambda *_: "Hallo.")
    LivingConversation(_Memory(), model, IDENTITY, Actions()).respond(turn())
    assert not any("start_background_research" in line for line in model.calls[0][1]["self_context"])


@pytest.mark.parametrize("repeated_invalid", [False, True])
def test_research_input_is_corrected_once_before_any_admission(repeated_invalid):
    from eva.application.conversation import ConversationActionInputRejected

    class ResearchActions(Actions):
        action_names = ("start_background_research",)

        def execute(self, t, call):
            if call.arguments["invalid"]:
                raise ConversationActionInputRejected(("question",))
            self.jobs[t.message_id] = "admitted"
            return ConversationActionReceipt(call, {"status": "verified", "work_id": t.message_id})

    seen = []
    def choose(t, context):
        receipt = context["completed_action"]
        seen.append(receipt)
        if receipt is None:
            return ConversationActionCall("invalid-first", "start_background_research", {"invalid": True})
        if receipt.output["status"] == "invalid_input":
            assert not actions.jobs
            assert receipt.output["action_admitted"] is False
            assert receipt.output["invalid_fields"] == ["question"]
            assert "start_background_research" in context["available_actions"]
            return ConversationActionCall("corrected-second", "start_background_research", {"invalid": repeated_invalid})
        assert context["available_actions"] == ()
        return "Het onderzoek is ingepland."

    actions = ResearchActions()
    foreground = Model(choose)
    work = Model(lambda *_: pytest.fail("Input repair must stay on the contact model"))
    reply = LivingConversation(_Memory(), work, IDENTITY, actions,
                               contact_model=foreground).respond(turn("repair", "onderzoek"))
    if repeated_invalid:
        assert not actions.jobs and len(seen) == 2
        assert "nog niet gestart" in reply.text
    else:
        assert actions.jobs == {"repair": "admitted"} and len(seen) == 3
        assert reply.text == "Het onderzoek is ingepland."


def test_research_permission_failure_is_not_retried_as_bad_input():
    class ResearchActions(Actions):
        action_names = ("start_background_research",)
        def execute(self, t, call):
            raise PermissionError("not admitted")
    foreground = Model(lambda *_: ConversationActionCall("denied", "start_background_research", {}))
    with pytest.raises(PermissionError):
        LivingConversation(_Memory(), foreground, IDENTITY, ResearchActions()).respond(turn())
    assert len(foreground.calls) == 1


def test_research_repair_cannot_stream_or_claim_admission_without_a_tool():
    from eva.application.conversation import (
        ConversationActionInputRejected, publish_conversation_text, stream_conversation_text,
    )
    class ResearchActions(Actions):
        action_names = ("start_background_research",)
        def execute(self, t, call):
            raise ConversationActionInputRejected(("question",))
    def choose(t, context):
        if context["completed_action"] is None:
            return ConversationActionCall("invalid", "start_background_research", {})
        publish_conversation_text("Het onderzoek is gestart.", tools_disabled=False, final_answer=True)
        return "Het onderzoek is gestart."
    emitted = []
    with stream_conversation_text(emitted.append):
        reply = LivingConversation(_Memory(), Model(choose), IDENTITY, ResearchActions()).respond(turn())
    assert emitted == []
    assert "nog niet gestart" in reply.text


def test_research_repair_preserves_the_last_real_lookup_in_context():
    from eva.application.conversation import ConversationActionInputRejected
    class ResearchActions(Actions):
        action_names = ("list_delegated_work", "start_background_research")
        def execute(self, t, call):
            if call.name == "start_background_research":
                raise ConversationActionInputRejected(("question",))
            return ConversationActionReceipt(call, {"status": "verified", "jobs": []})
        def execute_step(self, t, call, *, step_index):
            assert step_index == 1
            return self.execute(t, call)
        def continuation_action_names(self, t):
            return self.action_names
    def choose(t, context):
        receipt = context["completed_action"]
        if receipt is None:
            return ConversationActionCall("lookup", "list_delegated_work", {})
        if receipt.call.name == "list_delegated_work":
            return ConversationActionCall("invalid", "start_background_research", {})
        assert [item.call.call_id for item in context["prior_actions"]] == ["lookup"]
        return "Kan nog niet starten."
    reply = LivingConversation(_Memory(), Model(choose), IDENTITY, ResearchActions()).respond(turn())
    assert "nog niet gestart" in reply.text
