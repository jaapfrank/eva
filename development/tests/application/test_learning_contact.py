"""Saving an adjustment can be acknowledged without a second memory search."""
import pytest
from tests.application.test_contact_model import Actions, Model, IDENTITY, turn
from tests.application.test_conversation import _Memory
from eva.application.conversation import LivingConversation, ConversationActionCall, ConversationActionReceipt
from eva.providers.human_understanding_actions import ACTION, HumanUnderstandingActions


@pytest.mark.parametrize("status", ["queued", "storage_refused"])
def test_learning_storage_receipt_stays_on_contact_route(status):
    class LearningActions(Actions):
        action_names = (ACTION,)
        contact_reply_allowed = staticmethod(HumanUnderstandingActions.contact_reply_allowed)

        def execute(self, source, call):
            return ConversationActionReceipt(call, {"contract": "EvaHumanUnderstandingAction/v1",
                "source_ref": source.message_id, "status": status, "semantic_retention_completed": False})

    contact = Model(lambda t, k: "Begrepen." if k.get("completed_action") else
        ConversationActionCall("save", ACTION, {}))
    work = Model(lambda t, k: pytest.fail("storage acknowledgement is not substantive work"))
    memory = _Memory()
    reply = LivingConversation(memory, work, IDENTITY, contact_model=contact,
        actions=LearningActions()).respond(turn())
    assert reply.text == "Begrepen."
    assert len(contact.calls) == 2 and memory.query is None


@pytest.mark.parametrize("name,changes", [
    ("other_action", {}), (ACTION, {"contract": "other"}),
    (ACTION, {"source_ref": "another-turn"}), (ACTION, {"status": "unknown"}),
])
def test_other_receipts_do_not_enable_learning_contact(name, changes):
    source = turn()
    receipt = ConversationActionReceipt(ConversationActionCall("call", name, {}), {
        "contract": "EvaHumanUnderstandingAction/v1", "source_ref": source.message_id,
        "status": "queued", **changes})
    assert HumanUnderstandingActions.contact_reply_allowed(source, receipt) is False
