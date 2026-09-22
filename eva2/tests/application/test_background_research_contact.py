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
            assert any("publieke bronnen" in line for line in context["self_context"])
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
