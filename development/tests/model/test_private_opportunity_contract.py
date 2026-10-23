"""Private orientation has phase-appropriate metadata and the same host schema."""
from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest

from eva.adapters.model.pydantic_conversation import (
    PydanticConversationModel, _FREE_WILL_TOOL, _private_opportunity_tool,
)
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_json_bytes
from tests.model.test_conversation_model import _memory

NOW = datetime(2026, 10, 19, 1, tzinfo=UTC)
IDENTITY = IdentityProfile("Eva", "Housemate", ("Honest",), 80)
USAGE = ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=20)
DECISION = {"disposition": "silence", "expression": None, "reason": "A considered choice",
            "urgency": 0, "initiative": None, "next_attention": None}


def without_descriptions(value):
    if isinstance(value, dict):
        return {key: without_descriptions(item) for key, item in value.items() if key != "description"}
    if isinstance(value, list):
        return [without_descriptions(item) for item in value]
    return value


def event(source="initiative-review"):
    return SensoryEvent(source, "synthetic:orientation-contract", "private_opportunity_review"
        if source == "initiative-review" else "household_opportunity_review", NOW, {})


def final():
    return ("", ConversationActionCall("final", "submit_sensory_decision", deepcopy(DECISION)))


def assert_private(tool):
    assert tool["name"] == "submit_sensory_decision"
    extended = deepcopy(tool)
    initiative = extended["parameters"]["properties"]["initiative"]
    for key in ("parent_intent_id", "parent_result_sha256"):
        assert initiative["properties"][key]["type"] == ["string", "null"]
        assert key in initiative["required"]
        initiative["properties"].pop(key)
        initiative["required"].remove(key)
    attention = extended["parameters"]["properties"]["next_attention"]
    assert attention["properties"]["source_event_id"]["type"] == ["string", "null"]
    attention["properties"].pop("source_event_id")
    attention["required"].remove("source_event_id")
    feedback = extended["parameters"]["properties"].pop("feedback_application")
    assert feedback["type"] == ["object", "null"] and feedback["additionalProperties"] is False
    assert set(feedback["required"]) == {"intent_id", "feedback_sha256", "adjustment",
        "usefulness", "timing", "nuisance", "usefulness_quote", "timing_quote", "nuisance_quote"}
    for dimension in ("usefulness", "timing", "nuisance"):
        assert set(feedback["properties"][dimension]["enum"]) == {"unknown", "positive", "negative", "mixed"}
    extended["parameters"]["required"].remove("feedback_application")
    assert without_descriptions(extended) == without_descriptions(_FREE_WILL_TOOL)
    assert "private oriëntatie" in tool["description"]
    assert "passieve huisprikkel" not in tool["description"]
    fields = tool["parameters"]["properties"]
    assert "berichtkeuze" in fields["disposition"]["description"]
    assert "eigen onderzoek" in fields["disposition"]["description"]
    assert "informatie" in fields["reason"]["description"]
    assert "verifieerbare" in fields["initiative"]["properties"]["next_exploration"]["description"]
    assert "anders null" in fields["initiative"]["description"]


def test_private_descriptor_extends_feedback_and_continuation_without_mutating_household():
    original = canonical_json_bytes(_FREE_WILL_TOOL)
    private = _private_opportunity_tool()
    assert_private(private)
    private["parameters"]["properties"]["initiative"]["properties"]["title"]["description"] = "Changed local copy"
    private["parameters"]["required"].append("not_a_real_field")
    assert canonical_json_bytes(_FREE_WILL_TOOL) == original
    assert_private(_private_opportunity_tool())
    assert "passieve huisprikkel" in _FREE_WILL_TOOL["description"]


@pytest.mark.parametrize("search_first", [False, True])
def test_private_initial_and_postsearch_submit_use_the_same_private_contract(monkeypatch, tmp_path, search_first):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    assert model.private_opportunity_profile == "private-person-context-orientation-contract-v12"
    original = canonical_json_bytes(_FREE_WILL_TOOL)
    bodies, reads = [], []
    def request(body, *args, **kwargs):
        bodies.append(deepcopy(body))
        assert_private(body["tools"][0])
        if len(bodies) == 1 and search_first:
            return (("", ConversationActionCall("search", "search_personal_memory",
                {"query_text": "self-chosen source question"})), USAGE)
        return final(), USAGE
    monkeypatch.setattr(model, "_request_using", request)
    def search(query):
        reads.append(query)
        return {"result_count": 0, "recalled_context": [], "is_execution_permission": False}
    result = model.evaluate_sensory_event(event(), IDENTITY, _memory(), private_context_search=search)
    assert result.initiative is None and result.next_attention is None and result.disposition == "silence"
    assert len(bodies) == (2 if search_first else 1)
    assert len(reads) == (1 if search_first else 0)
    assert bodies[0]["tool_choice"] == "required" and len(bodies[0]["tools"]) == 2
    if search_first:
        assert bodies[1]["tools"] == [bodies[0]["tools"][0]]
        assert bodies[1]["tool_choice"] == {"name": "submit_sensory_decision", "type": "function"}
    assert canonical_json_bytes(_FREE_WILL_TOOL) == original


def test_private_without_search_keeps_the_private_descriptor_and_household_bytes_unchanged(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    original = canonical_json_bytes(_FREE_WILL_TOOL)
    bodies = []
    monkeypatch.setattr(model, "_request_using", lambda body, *args, **kwargs:
        (bodies.append(deepcopy(body)) or final(), USAGE))
    model.evaluate_sensory_event(event(), IDENTITY, _memory())
    assert_private(bodies[-1]["tools"][0])
    model.evaluate_sensory_event(event("household-initiative"), IDENTITY, _memory())
    assert canonical_json_bytes(bodies[-1]["tools"][0]) == original
    assert "passieve huisprikkel" in bodies[-1]["tools"][0]["description"]
    assert canonical_json_bytes(_FREE_WILL_TOOL) == original


def test_each_private_request_has_a_fresh_descriptor(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    original = canonical_json_bytes(_FREE_WILL_TOOL)
    called = []
    def request(body, *args, **kwargs):
        assert_private(body["tools"][0])
        body["tools"][0]["description"] = "Only this request changed"
        called.append(True)
        return final(), USAGE
    monkeypatch.setattr(model, "_request_using", request)
    model.evaluate_sensory_event(event(), IDENTITY, _memory())
    model.evaluate_sensory_event(event(), IDENTITY, _memory())
    assert called == [True, True]
    assert canonical_json_bytes(_FREE_WILL_TOOL) == original


def test_private_orientation_history_passes_unchanged_and_never_becomes_household_instruction(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    history = [{"decided_at": "2026-10-18T20:00:00Z",
        "reason": "Synthetic earlier considered silence", "reason_sha256": "c" * 64,
        "reason_truncated": False, "disposition": "silence", "initiative_status": None,
        "next_attention": None, "source_event_id": "initiative-review:synthetic:1",
        "source_sha256": "a" * 64, "decision_sha256": "b" * 64, "decision_sequence": 4,
        "classification": "historical_model_decision_not_owner_preference_or_execution_permission"}]
    original = deepcopy(history)
    bodies = []
    def request(body, *args, **kwargs):
        bodies.append(deepcopy(body))
        return final(), USAGE
    monkeypatch.setattr(model, "_request_using", request)
    private = event()
    private = SensoryEvent(private.source, private.source_event_id, private.event_type,
        private.occurred_at, {"life_context": {"own_orientation_history": history}})
    result = model.evaluate_sensory_event(private, IDENTITY, _memory())
    payload = json.loads(bodies[-1]["input"][0]["content"])
    assert payload["event"]["payload"]["life_context"]["own_orientation_history"] == original
    assert history == original
    assert history[0]["classification"] == "historical_model_decision_not_owner_preference_or_execution_permission"
    instructions = bodies[-1]["instructions"]
    assert "eigen eerdere historische afwegingen" in instructions
    assert "geen bewonersuitspraken" in instructions and "of uitvoertoestemming" in instructions
    assert "eerdere stilte is geen blijvend besluit" in instructions
    assert "stilte of null geldig blijft" in instructions and "geen taakquotum" in instructions
    assert result.initiative is None and result.next_attention is None
    model.evaluate_sensory_event(event("household-initiative"), IDENTITY, _memory())
    assert "own_orientation_history" not in bodies[-1]["instructions"]
    assert "eigen eerdere historische afwegingen" not in bodies[-1]["instructions"]


def test_private_necessary_question_boundary_keeps_optional_social_curiosity_private(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    identity = IdentityProfile("Eva", "Housemate", ("Stel alleen vragen als noodzakelijke informatie ontbreekt.",), 80)
    bodies = []
    def request(body, *args, **kwargs):
        bodies.append(deepcopy(body))
        return final(), USAGE
    monkeypatch.setattr(model, "_request_using", request)
    result = model.evaluate_sensory_event(event(), identity, _memory(), private_contact_allowed=True)
    private = bodies[-1]["instructions"]
    identity_text = model._identity_instructions(identity)
    clarification = (
        "De identiteitsgrens over alleen noodzakelijke vragen betreft onnodige "
        "taakverheldering; zij sluit passend sociaal of nieuwsgierig contact zonder "
        "lopende taak niet uit. Zo'n contact blijft een eigen passende keuze, geen "
        "verplichte vraag, en verruimt geen privacy, persoonsgrenzen of uitvoerbevoegdheid. "
    )
    assert identity_text + clarification + "Dit is je rustige private oriëntatie:" in private
    assert "Stel alleen vragen als noodzakelijke informatie ontbreekt." in private
    assert "Geen verplichte vraag of begroeting." in private
    assert "stop, privacy en persoonsgrenzen gelden" in private
    assert result.disposition == "silence" and result.initiative is None and result.next_attention is None
    assert model.private_opportunity_profile == "private-person-context-orientation-contract-v12"
    model.evaluate_sensory_event(event("household-initiative"), identity, _memory())
    assert clarification not in bodies[-1]["instructions"]
    assert "Stel alleen vragen als noodzakelijke informatie ontbreekt." in bodies[-1]["instructions"]


@pytest.mark.parametrize("boundary", ["valid", "incomplete", "shared"])
def test_continuation_fields_parse_only_as_a_valid_private_pair(monkeypatch, tmp_path, boundary):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    decision = deepcopy(DECISION)
    decision["initiative"] = {"title": "Continue my interest", "motivation": "An actual earlier finding",
        "next_exploration": "Explore a different next question", "parent_intent_id": "personal-interest:" + "a" * 64,
        "parent_result_sha256": "b" * 64 if boundary != "incomplete" else None}
    monkeypatch.setattr(model, "_request_using", lambda *args, **kwargs:
        (("", ConversationActionCall("final", "submit_sensory_decision", decision)), USAGE))
    if boundary == "valid":
        result = model.evaluate_sensory_event(event(), IDENTITY, _memory())
        assert result.initiative.parent_intent_id == "personal-interest:" + "a" * 64
        assert result.initiative.parent_result_sha256 == "b" * 64
    else:
        with pytest.raises(Exception):
            model.evaluate_sensory_event(event("household-initiative") if boundary == "shared" else event(), IDENTITY, _memory())


def test_private_missing_context_uses_existing_search_without_forcing_activity(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    bodies = []
    monkeypatch.setattr(model, "_request_using", lambda body, *args, **kwargs:
        (bodies.append(deepcopy(body)) or final(), USAGE))
    decision = model.evaluate_sensory_event(event(), IDENTITY, _memory(), private_context_search=lambda _: {})
    instructions = bodies[-1]["instructions"]
    assert "De eerste geheugenselectie is onvolledig." in instructions
    assert "controleer dat informatiegat eerst" in instructions
    assert "Kies zelf de inhoudelijke zoektermen" in instructions
    assert "Zoek niet als je afweging al op voldoende relevante context berust." in instructions
    assert "Ook na zoeken blijft een onderbouwde pauze geldig" in instructions
    assert decision.initiative is None
    model.evaluate_sensory_event(event("household-initiative"), IDENTITY, _memory())
    assert "De eerste geheugenselectie is onvolledig." not in bodies[-1]["instructions"]
