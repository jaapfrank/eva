"""Private orientation preserves history without turning it into a selected goal."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest

from eva.application.free_will import private_orientation_context
from eva.application.sensory import SensoryEvent
from eva.application.conversation import ConversationActionCall, ConversationModelUsage, IdentityProfile
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from tests.model.test_conversation_model import _memory

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)
EVENT = SensoryEvent("initiative-review", "synthetic:orientation", "private_opportunity_review", NOW, {})


def test_closed_history_and_contacts_do_not_implicitly_select_current_work():
    history = tuple({"intent_id": "retained:" + phase, "title": "Original title", "phase": phase}
        for phase in ("initiative_completed", "initiative_blocked", "personal_stopped", "release_ready"))
    history += ({"kind": "private_contact", "phase": "expression_delivered", "question": "Earlier question"},)
    before = deepcopy(history)
    view = private_orientation_context(EVENT, history)
    assert view["mode"] == "open_orientation"
    assert view["visible_pending_projects"] == []
    assert view["phase_counts_do_not_prove_goal_completion"] is True
    assert sum(view["visible_project_phase_counts"].values()) == 4
    assert view["history_selects_no_current_goal"] is True and view["is_execution_permission"] is False
    assert history == before


@pytest.mark.parametrize("key,mode", [("attention_origin", "chosen_attention"),
    ("native_completion_source", "source_followup"), ("pending_attention", "pending_attention")])
def test_actual_host_followup_keeps_its_mode(key, mode):
    event = replace(EVENT, payload={key: {"reason": "Retained host binding"}})
    assert private_orientation_context(event, ())["mode"] == mode


def test_unfinished_or_unknown_phase_is_visible_and_never_guessed_complete():
    history = ({"intent_id": "retained:active", "phase": "exploration_pending", "title": "Ongoing"},
               {"intent_id": "retained:future", "phase": "future_host_phase", "title": "Unknown"})
    view = private_orientation_context(EVENT, history)
    assert view["visible_pending_projects"] == list(history)
    assert "not_all_goals" in view["coverage"]


@pytest.mark.parametrize("source,event_type", [("home_assistant", "state_changed"),
    ("household-initiative", "household_observation_review")])
def test_other_audiences_get_no_private_orientation(source, event_type):
    assert private_orientation_context(replace(EVENT, source=source, event_type=event_type), ()) is None


def test_private_model_input_retains_full_history_and_keeps_silence_valid(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "not-used")
    history = ({"intent_id": "original", "title": "Completed", "phase": "initiative_completed",
                "question": "Exact historical task", "owner_feedback": {"feedback": "Not useful"},
                "recorded_failure": {"effects": "unknown", "retry": "not_authorized"}},)
    bodies = []
    def request(body, *args, **kwargs):
        bodies.append(body)
        return (("", ConversationActionCall("decision", "submit_sensory_decision", {
            "disposition": "silence", "expression": None, "urgency": 0,
            "reason": "No useful next step from these sources", "initiative": None, "next_attention": None})),
            ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=20))
    monkeypatch.setattr(model, "_request_using", request)
    decision = model.evaluate_sensory_event(EVENT, IdentityProfile("Eva", "Digital housemate", ("honesty",)),
        _memory(), initiative_history=history)
    data = json.loads(bodies[0]["input"][0]["content"])
    assert data["prior_private_initiatives"] == list(history)
    assert data["private_orientation_context"]["mode"] == "open_orientation"
    assert decision.disposition == "silence" and decision.initiative is None
    assert len(bodies) == 1
