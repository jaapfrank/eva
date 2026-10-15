"""Actual model protocol retains optional speech and work for shared sources."""
from datetime import UTC, datetime
import json

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory


def test_shared_decision_can_offer_speech_and_native_work_without_private_history(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "never-read.json")
    captured = []
    proposal = {"title": "Clarify the observed situation", "motivation": "Shared evidence needs current context",
                "next_exploration": "Read the available current source before deciding what is useful."}
    def request(body, *args, **kwargs):
        captured.append(body)
        assert kwargs == {"autonomous": True, "include_usage": True}
        return (("", ConversationActionCall("choice", "submit_sensory_decision", {
            "disposition": "respond", "expression": "Ik kijk even wat er aan de hand is.",
            "reason": "A useful shared question", "urgency": 1, "initiative": proposal})),
            ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=20))
    monkeypatch.setattr(model, "_request_using", request)
    event = SensoryEvent("household-initiative", "fixture-bound-observation", "household_observation_review",
                        datetime(2026, 10, 15, 10, tzinfo=UTC), {"observation": {"payload": {
                            "entity_id": "binary_sensor.synthetic", "old_state": "off", "new_state": "on"}}})
    result = model.evaluate_sensory_event(event, IdentityProfile("Eva", "Huisgenoot", ("Honest",), 80),
                                         _memory(), initiative_capabilities=("read_home_assistant_inventory",))
    payload = json.loads(captured[0]["input"][0]["content"])
    assert payload["event"]["payload"] == event.payload
    assert payload["prior_private_initiatives"] == []
    assert payload["available_native_actions"] == ["read_home_assistant_inventory"]
    assert result.disposition == "respond" and result.initiative.title == proposal["title"]
    assert len(captured[0]["tools"]) == 1
    assert captured[0]["tools"][0]["parameters"]["properties"]["initiative"]["additionalProperties"] is False
    assert not (tmp_path / "never-read.json").exists()
