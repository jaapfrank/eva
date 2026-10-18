"""Existing private recall provenance and learning reach initiative decisions."""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import pytest
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory

@pytest.mark.parametrize("source", ["initiative-review", "household-initiative", "home_assistant"])
@pytest.mark.parametrize("mirror", [None, "Een eerdere uitleg hielp niet; vraag gericht naar het ontbrekende gegeven."])
def test_initiative_receives_recalled_evidence_without_widening_its_audience(monkeypatch, tmp_path, source, mirror):
    model = PydanticConversationModel(auth_file=tmp_path / "not-used")
    memory = replace(_memory(), style_mirror=mirror)
    before = memory.confirmed_facts[0].model_dump(mode="json")
    bodies = []
    usage = ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=90, output_tokens=12)
    def request(body, *args, **kwargs):
        bodies.append(body)
        assert kwargs == {"autonomous": True, "include_usage": True}
        return (("", ConversationActionCall("choice", "submit_sensory_decision", {
            "disposition": "silence", "expression": None, "urgency": 0,
            "reason": "A considered choice", "initiative": None})), usage)
    monkeypatch.setattr(model, "_request_using", request)
    event = SensoryEvent(source, "synthetic:learning-context", "private_opportunity_review",
                         datetime(2026, 10, 18, tzinfo=UTC), {"synthetic": True})
    decision = model.evaluate_sensory_event(event, IdentityProfile("Eva", "Housemate", ("Honest",), 80), memory)
    body = bodies[0]
    payload = json.loads(body["input"][0]["content"])
    assert payload["recalled_context"] == [before]
    assert memory.confirmed_facts[0].model_dump(mode="json") == before
    assert payload["event"]["payload"] == event.payload
    if source == "initiative-review" and mirror is not None:
        assert payload["private_learning_context"] == {
            "style_mirror": mirror, "source": "current_private_presence_recall",
            "sha256": hashlib.sha256(mirror.encode()).hexdigest(), "is_execution_permission": False}
        assert "geen nieuwe opdracht" in body["instructions"]
    else:
        assert "private_learning_context" not in payload
        assert mirror is None or mirror not in body["input"][0]["content"]
    assert decision.usage == usage
    assert len(bodies) == 1


@pytest.mark.parametrize("allowed", [False, True])
def test_private_contact_is_host_optional_and_not_a_forced_question(monkeypatch, tmp_path, allowed):
    model = PydanticConversationModel(auth_file=tmp_path / "not-used")
    bodies = []
    def request(body, *args, **kwargs):
        bodies.append(body)
        return (("", ConversationActionCall("choice", "submit_sensory_decision", {
            "disposition": "respond" if allowed else "silence", "expression": "What would help?" if allowed else None,
            "urgency": 1 if allowed else 0, "reason": "Considered", "initiative": None})),
            ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=50, output_tokens=12))
    monkeypatch.setattr(model, "_request_using", request)
    event = SensoryEvent("initiative-review", "synthetic:private-contact", "private_opportunity_review",
                         datetime(2026, 10, 18, tzinfo=UTC), {})
    decision = model.evaluate_sensory_event(event, IdentityProfile("Eva", "Housemate", ("Honest",), 80), _memory(),
                                          private_contact_allowed=allowed)
    instructions = bodies[0]["instructions"]
    if allowed:
        assert "Je mag respond kiezen" in instructions and "Geen verplichte vraag" in instructions
        assert "kies hier altijd disposition silence" not in instructions
        assert decision.expression == "What would help?"
    else:
        assert "altijd disposition silence" in instructions and decision.expression is None
    assert len(bodies) == 1


def test_household_cannot_enable_private_question(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "not-used")
    monkeypatch.setattr(model, "_request_using", lambda *args, **kwargs: pytest.fail("must not dispatch"))
    event = SensoryEvent("household-initiative", "synthetic:other", "private_opportunity_review",
                         datetime(2026, 10, 18, tzinfo=UTC), {})
    with pytest.raises(ValueError, match="host-authorized"):
        model.evaluate_sensory_event(event, IdentityProfile("Eva", "Housemate", ("Honest",), 80), _memory(),
                                    private_contact_allowed=True)
