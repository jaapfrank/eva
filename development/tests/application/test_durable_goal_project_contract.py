from dataclasses import replace

import pytest

from eva.adapters.api.app import _durable_goal_payload, _durable_goal_from_wire
from eva.application.durable_goals import DurableGoalError, DurableGoalSpec, DurableGoalState
from tests.postgres.test_durable_goal_store import spec, store_with_timeline, NOW
from eva.application.durable_goals import GoalArtifact, GoalEvidence


def test_v1_contract_and_state_remain_exact():
    goal = spec()
    payload = goal.to_payload()
    assert payload["contract"] == "EvaDurableGoalSpec/v1"
    assert "followup_instruction" not in payload
    assert DurableGoalSpec.from_payload(payload) == goal
    state = _durable_goal_payload(DurableGoalState(goal, "waiting"))
    assert set(state) == {"artifact", "cancellation_reason", "contract", "delivery_reason",
                          "delivery_status", "evidence", "goal", "selected_instruction", "status"}
    assert state["contract"] == "EvaDurableGoalState/v1"


def test_v2_roundtrip_and_instruction_hash_binding():
    goal = replace(spec(), followup_instruction="Use the previous result")
    assert DurableGoalSpec.from_payload(goal.to_payload()) == goal
    assert goal.sha256 != spec().sha256
    assert goal.sha256 != replace(goal, followup_instruction="Different work").sha256


@pytest.mark.parametrize("value", [None, "", " ", 7, "x" * 2001])
def test_v2_requires_valid_followup(value):
    payload = {**spec().to_payload(), "contract": "EvaDurableGoalSpec/v2", "followup_instruction": value}
    with pytest.raises((ValueError, DurableGoalError)):
        DurableGoalSpec.from_payload(payload)


def test_store_requires_previous_checkpoint_and_final_artifact():
    store, _ = store_with_timeline()
    goal = replace(spec(), followup_instruction="Use previous result")
    store.admit(goal)
    with pytest.raises(DurableGoalError, match="artifact is missing"):
        store.prepare_followup(goal)
    store.schedule_wake(goal)
    store.record_wake(goal, GoalEvidence("goal-test", "release-1", {"ready": "yes"}, NOW, "1"))
    store.prepare_effect(goal, goal.primary_instruction)
    first = GoalArtifact("first", "First", "text/plain", NOW)
    store.record_effect(goal, first)
    with pytest.raises(DurableGoalError, match="artifact is missing"):
        store.prepare_delivery(goal, first)
    store.cancel(goal.goal_id, goal.owner_id, "Stop", NOW)
    with pytest.raises(DurableGoalError, match="terminal"):
        store.prepare_followup(goal)


def wire_goal():
    payload = spec().to_payload()
    del payload["contract"]
    del payload["wake"]["contract"]
    return payload


def test_operator_wire_supports_existing_and_two_step_admission():
    payload = wire_goal()
    assert _durable_goal_from_wire(payload) == spec()
    assert _durable_goal_from_wire({**payload, "followup_instruction": "Use result"}) == (
        replace(spec(), followup_instruction="Use result")
    )


@pytest.mark.parametrize("extra", [
    {"unknown": "value"}, {"followup_instruction": None},
    {"followup_instruction": " "}, {"followup_instruction": 4},
    {"followup_instruction": "x" * 2001},
    {"followup_instruction": "Good", "unknown": True},
])
def test_operator_wire_rejects_invalid_extension(extra):
    with pytest.raises(ValueError):
        _durable_goal_from_wire({**wire_goal(), **extra})


def test_existing_operator_route_admits_and_exposes_two_step_contract():
    from fastapi.testclient import TestClient
    from eva.adapters.api.app import create_app
    from tests.api.test_memory_api import _Runtime, _goal_payload, TENANT, INSTALLATION

    with TestClient(create_app(_Runtime())) as client:
        response = client.post("/internal/v1/goals/admit", json={
            "tenant_id": TENANT, "installation_id": INSTALLATION,
            "goal": {**_goal_payload(), "followup_instruction": "Use previous result"},
        })
    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "EvaDurableGoalState/v2"
    assert payload["goal"]["contract"] == "EvaDurableGoalSpec/v2"
    assert payload["completed_steps"] == 0
    assert payload["total_steps"] == 2
