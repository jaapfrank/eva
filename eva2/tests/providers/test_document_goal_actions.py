from dataclasses import replace
import json

import pytest

from eva.application.durable_goals import DurableGoalError
from eva.providers.document_goal_plan import PreparedDocumentGoal
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from tests.providers.test_document_goal_plan import plan


def prepared():
    goal, value = plan()
    goal = replace(goal, pivot_instruction=None, suitability_field=None, unsuitable_value=None)
    value = replace(value, goal_sha256=goal.sha256)
    return value, PreparedDocumentGoal(goal, value.sha256, None)


def test_prepared_definition_roundtrip():
    value, definition = prepared()
    assert PreparedDocumentGoal.from_json(json.dumps(definition.to_payload()), value) == definition


@pytest.mark.parametrize("field,value", [
    ("owner_id", "other"), ("goal_id", "other"), ("primary_instruction", "Override"),
    ("followup_instruction", "Override"), ("delivery_recipient", "other"),
])
def test_host_definition_rejects_goal_substitution(field, value):
    plan, definition = prepared()
    payload = definition.to_payload()
    payload["goal"][field] = value
    with pytest.raises(DurableGoalError):
        PreparedDocumentGoal.from_json(json.dumps(payload), plan)


@pytest.mark.parametrize("change", [
    {"unknown": True}, {"plan_sha256": "b" * 64}, {"conversation_project_id": []},
    {"conversation_project_id": " "}, {"contract": "EvaPreparedDocumentGoal/v2"},
])
def test_host_definition_is_closed(change):
    plan, definition = prepared()
    with pytest.raises(DurableGoalError):
        PreparedDocumentGoal.from_json(json.dumps({**definition.to_payload(), **change}), plan)


def test_duplicate_nested_goal_field_rejected():
    plan, definition = prepared()
    raw = json.dumps(definition.to_payload()).replace('"owner_id":', '"owner_id":"other", "owner_id":')
    with pytest.raises(DurableGoalError):
        PreparedDocumentGoal.from_json(raw, plan)


def test_factory_is_opt_in_and_requires_host_dependencies(monkeypatch):
    monkeypatch.delenv("EVA_DOCUMENT_GOAL_DEFINITION_FILE", raising=False)
    assert DocumentGoalActionsProviderFactory.load_settings() is None
    monkeypatch.setenv("EVA_DOCUMENT_GOAL_DEFINITION_FILE", "/host/definition")
    monkeypatch.delenv("EVA_DOCUMENT_GOAL_PLAN_FILE", raising=False)
    with pytest.raises(RuntimeError):
        DocumentGoalActionsProviderFactory.load_settings()
    monkeypatch.setenv("EVA_DOCUMENT_GOAL_PLAN_FILE", "/host/plan")
    monkeypatch.setenv("EVA_MCP_AUTONOMY_MANDATE_FILE", "/host/mandate")
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", "tenant")
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", "installation")
    assert DocumentGoalActionsProviderFactory.load_settings().definition_file == "/host/definition"
