"""Providerless complete model-phase handoff for explicitly resumed public reads."""
from copy import deepcopy

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.model.pydantic_conversation import _ACTION_TOOLS
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationTurn, IdentityProfile,
)
from tests.model.test_conversation_model import _memory, _script_pydantic_phases, NOW


@pytest.mark.parametrize("explicit", [False, True])
def test_failed_read_handoff_keeps_next_tool_and_truthful_provenance(monkeypatch, tmp_path, explicit):
    descriptor = _ACTION_TOOLS["research_current_options"]
    model = PydanticConversationModel(auth_file=tmp_path / "never-read.json")
    next_call = ConversationActionCall("next-index-read", "research_current_options", {
        "question": "Read the implementation of the indicator condition.",
        "title": "Indicator implementation", "options": ["Public implementation"],
        "research_mode": "topic", "public_review": "public_unlinked"})
    phases = _script_pydantic_phases(monkeypatch, model, ("", next_call))
    output = {"status": "failed", "error_fingerprint": "b" * 64}
    if explicit:
        output.update({"recoverable_read_error": True, "proof": {"evidence": {
            "source": "native_research_explicit_owner_resume", "operation_kind": "read",
            "provider_execution": "unknown", "prior_usage": "unknown_or_partial",
            "artifact_status": "no_recorded_artifact", "resume_binding": {
                "work_id": "work:successor", "parent_work_id": "work:original"}}}})
    retained = deepcopy(output)
    old = ConversationActionReceipt(ConversationActionCall(
        "old-failed-read", "research_current_options", next_call.arguments), output)
    result = model.generate(
        ConversationTurn.private("tenant", "installation", "jaap", "conversation",
                                 "same-native-task", "Continue the existing research.", NOW),
        IdentityProfile("Eva", "Digital housemate.", ("Be honest.",), 80), _memory(), (),
        available_actions=(descriptor["name"],), completed_action=old,
        self_context=("Bounded existing task; no new authority.",))
    assert len(phases) == 1
    phase = phases[0]
    tools = [tool for tool in phase["tools"] if tool["name"] == descriptor["name"]]
    assert tools == [descriptor]
    assert result.action_call == next_call
    assert phase["completed_action"] == old
    assert output == retained
    instructions = phase["instructions"]
    if explicit:
        assert "De host bevestigt expliciete eigenaarshervatting" in instructions
        assert "Een aangeboden nieuwe bronaanvraag" in instructions
        assert "oud providergebruik blijft onbekend" in instructions
        assert "claim geen voltooiing zonder nieuw gecontroleerd resultaat" in instructions
        assert "Herhaal een mislukte of onzekere onderzoeksaanvraag niet automatisch" not in instructions
    else:
        assert "De host bevestigt expliciete eigenaarshervatting" not in instructions
        assert "Herhaal een mislukte of onzekere onderzoeksaanvraag niet automatisch" in instructions
    assert "Bounded existing task; no new authority." in instructions
