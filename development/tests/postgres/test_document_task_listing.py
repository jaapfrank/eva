"""Project discovery without a model or native document provider."""
import asyncio
from dataclasses import replace
import json
import pytest
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.application.orchestration import ToolRequest
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from tests.postgres.test_document_goal_actions import project
from tests.postgres.test_operational_timeline import timeline_engine


def test_listing_discovers_metadata_without_claiming_slots_and_rechecks_source(project, private_tmp_path):
    p = project
    pool = private_tmp_path / "listing-pool.json"
    pool.write_text(json.dumps({"contract": "EvaDocumentTaskPool/v1", "slots": [
        {"slot_id": "listing-one", "tool_configuration_sha256": "a" * 64},
        {"slot_id": "listing-two", "tool_configuration_sha256": "b" * 64}]}))
    pool.chmod(0o600)
    settings = replace(p["settings"], pool_file=str(pool), plan_file="", definition_file="")
    bundle = DocumentGoalActionsProviderFactory.build(settings, p["timeline_engine"])
    port = bundle.external_tools[0]
    adapter = port._provider
    route = PostgresConversationActions(p["timeline_engine"], external_tools=bundle.external_tools)
    def call(message, name, args, **changes):
        return route.execute(p["turn"](message, **changes), ConversationActionCall(message, name, args)).output["result"]
    assert call("empty", "list_document_projects", {}) == {"projects": []}
    assert all(p["store"].document_slot(slot) is None for slot in ("listing-one", "listing-two"))
    created = call("created", "start_document_project", {"title": "Family plan",
        "first_document": "Private first text", "second_document": "Private second text"})
    claim = p["store"].document_slot("listing-one")
    expected = {"projects": [{"title": "Family plan", "goal_id": created["goal_id"], "project_status": "waiting"}]}
    assert call("later", "list_document_projects", {}, conversation_id="another-private-channel") == expected
    assert call("other-project", "list_document_projects", {}, project_id="another") == {"projects": []}
    assert p["store"].document_slot("listing-one") == claim
    assert p["store"].document_slot("listing-two") is None
    call("stop", "stop_document_project", {"goal_id": created["goal_id"]})
    expected["projects"][0]["project_status"] = "cancelled"
    assert call("stopped", "list_document_projects", {}) == expected
    with pytest.raises(ConversationConflict):
        call("stranger", "list_document_projects", {}, principal_id="stranger")
    with pytest.raises(ConversationConflict):
        call("override", "list_document_projects", {"owner_id": "owner"})
    turn = p["turn"]("prepared-listing")
    prep = asyncio.run(adapter.prepare(turn, ConversationActionCall("list", "list_document_projects", {})))
    request = ToolRequest("test", "op", "prefix", prep.tool_name, prep.arguments, prep.expected_proof,
        observation_tool_name=prep.observation_tool_name, observation_arguments=prep.observation_arguments,
        tool_configuration_sha256=prep.tool_configuration_sha256)
    original_loader = adapter.load_source_turn
    def revoked_source(source):
        if source == claim["source"]:
            raise ConversationConflict("source binding revoked")
        return original_loader(source)
    adapter.load_source_turn = revoked_source
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.observe(request, None))
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.execute(request))
    assert p["store"].document_slot("listing-one") == claim
