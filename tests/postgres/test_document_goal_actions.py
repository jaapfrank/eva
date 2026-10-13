"""Real ingress/authority/SQL/tool pipeline; document effect proof is separate."""

import asyncio
from dataclasses import replace
import json
from uuid import uuid4

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn, ConversationStopped
from eva.application.memory_audience import MemoryAudience, TelegramGroupConsentReference
from eva.application.durable_goals import GoalArtifact, GoalEvidence
from eva.application.orchestration import ToolRequest
from eva.providers.document_goal_actions import (
    DocumentGoalActionsProviderFactory, DocumentGoalActionSettings,
)
from eva.providers.document_goal_plan import DocumentGoalPlan, PreparedDocumentGoal
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from tests.postgres.test_durable_goal_store import spec, NOW
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


@pytest.fixture
def project(timeline_engine, private_tmp_path):
    mandate = {"contract": "OwnerConnectorMandate/v1", "mandate_id": "normal-documents",
        "tenant_id": "normal-documents-" + uuid4().hex, "installation_id": "isolated", "owner_principal_id": "owner",
        "issued_at": "2020-01-01T00:00:00Z", "authorization_source": "isolated test operator",
        "enabled": True, "public_https": False, "local_networks": []}
    def write(name, payload):
        path = private_tmp_path / name
        path.write_text(json.dumps(payload)); path.chmod(0o600)
        return path
    mandate_path = write("mandate.json", mandate)
    authority = McpOwnerAuthority(timeline_engine, str(mandate_path))
    goal = replace(spec("normal-document"), owner_id="owner", followup_instruction="Use draft",
                   pivot_instruction=None, suitability_field=None, unsuitable_value=None)
    plan = DocumentGoalPlan(goal.goal_id, goal.sha256, "owner", str(authority.scope.tenant_id),
        str(authority.scope.installation_id), authority.configuration_sha256, "b" * 64,
        "/proof-state/normal-draft.txt", "/proof-state/normal-final.txt", "Draft", "Final: ", "\n")
    definition = PreparedDocumentGoal(goal, plan.sha256, None)
    settings = DocumentGoalActionSettings(str(write("plan.json", plan.to_payload())),
        str(write("definition.json", definition.to_payload())), str(mandate_path),
        mandate["tenant_id"], mandate["installation_id"])
    bundle = DocumentGoalActionsProviderFactory.build(settings, timeline_engine)
    port = bundle.external_tools[0]
    store = PostgresDurableGoalStore(timeline_engine, tenant_id=mandate["tenant_id"],
                                     installation_id=mandate["installation_id"])
    journal = PostgresConversationJournal(timeline_engine, authority.scope)
    def turn(message="start", **changes):
        value = ConversationTurn.private(mandate["tenant_id"], mandate["installation_id"],
            "owner", "private-chat", message, "Maak het afgesproken documentproject af.",
            NOW.isoformat().replace("+00:00", "Z"))
        if "principal_id" in changes:
            changes["audience"] = MemoryAudience.private_person(principal_id=changes["principal_id"])
        value = replace(value, **changes)
        journal.admit(value)
        return value
    return locals()


def call(p, action, message, **changes):
    router = PostgresConversationActions(p["timeline_engine"], external_tools=(p["port"],))
    return router.execute(p["turn"](message, **changes),
        ConversationActionCall(message, action + "_prepared_document_project", {})).output


def test_normal_external_pipeline_start_replay_status_stop(project):
    p = project
    assert len(p["bundle"].conversation_tools) == 3
    assert call(p, "get", "before")["result"]["project_status"] == "not_started"
    started = call(p, "start", "start")
    assert started["status"] == "verified"
    assert started["result"]["project_status"] == "waiting"
    assert started["result"]["final_document"] is None
    assert call(p, "start", "start") == started
    assert call(p, "start", "repeat")["result"]["goal_id"] == p["goal"].goal_id
    events = p["store"]._events(p["goal"])
    assert sum(e.event_type == "GOAL_ADMITTED" for e in events) == 1
    assert sum(e.event_type == "GOAL_DOCUMENT_PLAN_BOUND" for e in events) == 1
    assert not any(e.event_type == "GOAL_EFFECT_PREPARED" for e in events)
    assert call(p, "stop", "stop")["result"]["project_status"] == "cancelled"
    assert call(p, "start", "after-stop")["result"]["project_status"] == "cancelled"
    assert call(p, "stop", "repeat-stop")["result"]["project_status"] == "cancelled"


@pytest.mark.parametrize("changes", [
    {"principal_id": "other"}, {"installation_id": "other"}, {"project_id": "other"},
    {"conversation_id": "initiative-task:fake"},
])
def test_other_identity_or_project_cannot_start(project, changes):
    p = project
    turn = p["turn"]("wrong", **changes)
    assert not p["port"].available_for(turn)
    with pytest.raises(ConversationConflict):
        p["port"].execute(turn, ConversationActionCall("wrong", "start_prepared_document_project", {}))
    assert p["store"].state(p["goal"].goal_id, "owner") is None


def test_group_owner_cannot_see_or_execute_project_tools(project):
    p = project
    audience = MemoryAudience.telegram_group(principal_id="owner", group_chat_id="-100",
        consent_ref=TelegramGroupConsentReference("owner", "-100", "telegram-group-consent:" + "a" * 64))
    turn = p["turn"]("telegram:-100:group", audience=audience, conversation_id="telegram:-100")
    router = PostgresConversationActions(p["timeline_engine"], external_tools=(p["port"],))
    assert not set(p["port"].action_names) & set(router.available_action_names(turn))
    for name in p["port"].action_names:
        with pytest.raises(ConversationConflict):
            router.execute(turn, ConversationActionCall("group", name, {}))
    assert p["store"].state(p["goal"].goal_id, "owner") is None


def test_partial_admission_is_recovered_without_second_goal(project):
    p = project
    p["store"].admit(p["goal"])
    assert call(p, "start", "recover")["result"]["project_status"] == "waiting"
    assert p["store"].document_plan_binding(p["goal"])[0] == p["plan"].sha256
    assert sum(e.event_type == "GOAL_ADMITTED" for e in p["store"]._events(p["goal"])) == 1


def test_factory_rejects_runtime_scope_mismatch(project):
    p = project
    with pytest.raises(ConversationConflict, match="runtime"):
        DocumentGoalActionsProviderFactory.build(replace(p["settings"], installation_id="other"),
                                                 p["timeline_engine"])


def test_closed_arguments_and_fresh_authority_between_prepare_and_dispatch(project):
    p = project
    turn = p["turn"]()
    adapter = p["port"]._provider
    for key in ("goal_id", "owner_id", "path", "content", "definition"):
        with pytest.raises(ConversationConflict):
            asyncio.run(adapter.prepare(turn, ConversationActionCall("override",
                "start_prepared_document_project", {key: "untrusted"})))
    prep = asyncio.run(adapter.prepare(turn, ConversationActionCall("start", "start_prepared_document_project", {})))
    request = ToolRequest("test", "op", "prefix", prep.tool_name, prep.arguments, prep.expected_proof,
        observation_tool_name=prep.observation_tool_name, observation_arguments=prep.observation_arguments,
        tool_configuration_sha256=prep.tool_configuration_sha256)
    p["write"]("mandate.json", {**p["mandate"], "enabled": False})
    with pytest.raises(ConversationConflict, match="stopped"):
        asyncio.run(adapter.execute(request))
    assert p["store"].state(p["goal"].goal_id, "owner") is None


def test_revocation_preserves_owner_status_and_stop(project):
    p = project
    call(p, "start", "start")
    p["write"]("mandate.json", {**p["mandate"], "enabled": False})
    assert call(p, "get", "status")["result"]["project_status"] == "waiting"
    assert call(p, "stop", "stop")["result"]["project_status"] == "cancelled"
    with pytest.raises(ConversationConflict):
        call(p, "start", "again")


def test_plan_change_and_missing_real_ingress_fail_closed(project):
    p = project
    adapter = p["port"]._provider
    turn = p["turn"]()
    prep = asyncio.run(adapter.prepare(turn, ConversationActionCall("start", "start_prepared_document_project", {})))
    args = {**prep.arguments, "source": {**prep.arguments["source"], "message_id": "absent"}}
    request = ToolRequest("test", "op", "prefix", prep.tool_name, args, prep.expected_proof,
        observation_tool_name=prep.observation_tool_name, observation_arguments=args,
        tool_configuration_sha256=prep.tool_configuration_sha256)
    with pytest.raises(ConversationConflict, match="absent"):
        asyncio.run(adapter.execute(request))
    p["write"]("plan.json", replace(p["plan"], first_content="Override").to_payload())
    with pytest.raises((ConversationConflict, ValueError)):
        call(p, "start", "changed")
    assert p["store"].state(p["goal"].goal_id, "owner") is None


def test_result_requires_complete_exact_artifacts_and_survives_factory_restart(project, timeline_engine):
    p = project
    call(p, "start", "start")
    store, goal, plan = p["store"], p["goal"], p["plan"]
    # Projection fixture only: actual native receipt proof has separate product evidence.
    first = GoalArtifact("document:" + plan.step(1).operation_id, plan.first_content, "text/plain", NOW)
    final = GoalArtifact("document:" + plan.step(2, first).operation_id, plan.final_content(first), "text/plain", NOW)
    store.record_wake(goal, GoalEvidence(goal.wake.provider, goal.wake.resource,
        {goal.wake.field: goal.wake.expected_value}, NOW, "projection-test"))
    store.prepare_effect(goal, goal.primary_instruction)
    store.record_effect(goal, first)
    assert call(p, "get", "draft")["result"]["final_document"] is None
    store.prepare_followup(goal)
    store.record_followup(goal, final)
    assert call(p, "get", "unverified-final")["result"]["final_document"] is None
    store.prepare_delivery(goal, final)
    store.record_delivery(goal, final, "accepted", None)
    p["port"] = DocumentGoalActionsProviderFactory.build(p["settings"], timeline_engine).external_tools[0]
    result = call(p, "get", "done")["result"]
    assert result["project_status"] == "completed"
    assert result["final_document"]["content"] == "Final: Draft\n"
    assert result["final_document"]["path"] == plan.final_path
    assert call(p, "start", "repeat-done")["result"] == result


def test_conversation_stop_fence_prevents_goal_admission(project):
    p = project
    turn = p["turn"]()
    p["journal"].request_turn_stop(turn)
    with pytest.raises(ConversationStopped):
        p["port"].execute(turn, ConversationActionCall("start", "start_prepared_document_project", {}))
    assert p["store"].state(p["goal"].goal_id, "owner") is None
