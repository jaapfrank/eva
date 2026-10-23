"""Private choice feedback is source checked; missing reaction stays unknown."""
from datetime import UTC, datetime
import pytest
from eva.application.human_understanding import validate_feedback_application
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.model.contracts import ConversationModelError
from tests.model.test_conversation_model import _memory


def assessment():
    return {"intent_id": "personal-interest:" + "a"*64, "feedback_sha256": "b"*64,
        "adjustment": "Investigate an actual need instead of another generic list.",
        "usefulness": "negative", "usefulness_quote": "This list did not help",
        "timing": "unknown", "timing_quote": "", "nuisance": "unknown", "nuisance_quote": ""}


@pytest.mark.parametrize("change", [{}, {"feedback_sha256": "c"*64},
    {"timing": "positive", "timing_quote": "well timed"}])
def test_model_choice_preserves_or_rejects_exact_feedback(monkeypatch, tmp_path, change):
    value = {**assessment(), **change}
    model = PydanticConversationModel(auth_file=tmp_path/'unused')
    usage = ConversationModelUsage.measured(input_tokens=12, output_tokens=8, model_attempts=1, tool_calls=1)
    def request(body, *args, **kwargs):
        assert "feedback_application" in body["tools"][0]["parameters"]["properties"]
        return (("", ConversationActionCall("decision", "submit_sensory_decision", {
            "disposition": "silence", "expression": None, "reason": "Apply actual criticism", "urgency": 0,
            "initiative": None, "feedback_application": value})), usage)
    monkeypatch.setattr(model, '_request_using', request)
    history = ({"intent_id": assessment()["intent_id"], "owner_feedback_sha256": "b"*64,
                "owner_feedback": "This list did not help"},)
    event = SensoryEvent("initiative-review", "synthetic:feedback", "private_opportunity_review",
                         datetime(2026,10,23,tzinfo=UTC), {})
    def run():
        return model.evaluate_sensory_event(event, IdentityProfile("Eva", "Housemate", ("Be honest",), 80),
            _memory(), initiative_history=history)
    if change:
        with pytest.raises(ConversationModelError, match="sensory decision is invalid"):
            run()
    else:
        assert run().feedback_application == assessment()


def test_unknown_has_no_invented_evidence_and_known_needs_a_quote():
    for change in ({"timing": "positive"}, {"timing_quote": "They never replied"}, {"nuisance": []}):
        with pytest.raises(ValueError):
            validate_feedback_application({**assessment(), **change})
