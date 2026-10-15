"""Private planning must remain possible without a household notification trigger."""
import json
from datetime import UTC, datetime
import pytest
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory

@pytest.mark.parametrize("proposal", [None, {
    "title": "A self-chosen useful question", "motivation": "A supported information gap",
    "next_exploration": "Verify the available evidence before making a useful contribution."}])
def test_private_orientation_keeps_silence_distinct_from_work(monkeypatch, tmp_path, proposal):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    bodies = []
    history = ({"title": "An earlier stopped task", "phase": "personal_stopped"},)
    source = ({"source_question": "An original question", "verified_reads": []},)
    def request(body, *args, **kwargs):
        bodies.append(body)
        assert kwargs == {"autonomous": True, "include_usage": True}
        return (("", ConversationActionCall("decision", "submit_sensory_decision", {
            "disposition": "silence", "expression": None, "urgency": 0,
            "reason": "A considered choice", "initiative": proposal})),
            ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=20))
    monkeypatch.setattr(model, "_request_using", request)
    event = SensoryEvent("initiative-review", "synthetic-private-orientation", "private_opportunity_review",
                         datetime(2026, 10, 15, 14, tzinfo=UTC), {"origin": "synthetic"})
    result = model.evaluate_sensory_event(event, IdentityProfile("Eva", "Housemate", ("Honest",), 80),
        _memory(), initiative_history=history, initiative_capabilities=("read_available_sources",),
        initiative_source_outcomes=source)
    instructions = bodies[0]["instructions"]
    assert "rustige private oriëntatie" in instructions
    assert "dringende behoefte is daarvoor geen voorwaarde" in instructions
    assert "geen idee" in instructions and "initiative null geldig" in instructions
    assert "Het verruimt geen rechten" in instructions
    assert "Roep exact de functie aan" in instructions
    assert "Gewone lampbediening" not in instructions
    payload = json.loads(bodies[0]["input"][0]["content"])
    assert payload["prior_private_initiatives"] == list(history)
    assert payload["native_source_outcomes"] == list(source)
    assert payload["event"]["payload"] == event.payload
    assert result.disposition == "silence" and result.expression is None
    assert (result.initiative is None) == (proposal is None)
    assert len(bodies[0]["tools"]) == 1
    assert result.usage.model_attempts == 1
