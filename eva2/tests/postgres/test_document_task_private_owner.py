"""Private cross-channel document authority with real ingress and PostgreSQL."""
from dataclasses import replace
import json
import pytest
import sqlalchemy as sa

from eva.adapters.postgres.private_owner import PostgresPrivateOwnerResolver
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from tests.postgres.test_document_goal_actions import project
from tests.postgres.test_operational_timeline import timeline_engine
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_people_memory_overview import NOW


def test_private_owner_start_status_stop_and_current_revocation(project, private_tmp_path, monkeypatch):
    p = project
    engine = p["timeline_engine"]
    label = "document-private-owner"
    scope, _, binding = _seed_authority(engine, label)
    owner = str(_id(label, "principal"))
    alias = "telegram:101"
    bindings = [TelegramPrincipalBindings((TelegramPrincipalBinding(101, owner, ("101",), str(binding)),))]
    resolver = PostgresPrivateOwnerResolver(
        engine, tenant_id=p["settings"].tenant_id, installation_id=p["settings"].installation_id,
        owner=_id(label, "principal"), binding=binding, authority_scope=scope,
        bindings_loader=lambda: bindings[0], clock=lambda: NOW)
    monkeypatch.setattr("eva.providers.private_owner_authority.configured_private_owner_resolver",
                        lambda *args, **kwargs: resolver)
    mandate_path = private_tmp_path / "private-mandate.json"
    mandate_path.write_text(json.dumps({**p["mandate"], "owner_principal_id": owner}))
    mandate_path.chmod(0o600)
    pool = private_tmp_path / "private-pool.json"
    pool.write_text(json.dumps({"contract": "EvaDocumentTaskPool/v1", "slots": [
        {"slot_id": "private-one", "tool_configuration_sha256": "a" * 64},
        {"slot_id": "private-two", "tool_configuration_sha256": "b" * 64}]}))
    pool.chmod(0o600)
    settings = replace(p["settings"], mandate_file=str(mandate_path), pool_file=str(pool),
                       plan_file="", definition_file="")
    bundle = DocumentGoalActionsProviderFactory.build(settings, engine)
    port = bundle.external_tools[0]
    route = PostgresConversationActions(engine, external_tools=bundle.external_tools)
    def turn(message, principal=alias, **changes):
        return p["turn"](message, principal_id=principal,
                         conversation_id=alias if principal == alias else "owner-local", **changes)
    def call(message, name, args, principal=alias):
        return route.execute(turn(message, principal), ConversationActionCall(message, name, args)).output
    args = {"title": "Private project", "first_document": "First", "second_document": "Second"}
    website = call("website-start", "start_document_project", args, owner)
    website_id = website["result"]["goal_id"]
    assert call("telegram-status", "get_document_project", {"goal_id": website_id})["result"]["project_status"] == "waiting"
    assert call("telegram-stop", "stop_document_project", {"goal_id": website_id})["result"]["project_status"] == "cancelled"
    created = call("telegram-start", "start_document_project", args)
    assert call("telegram-start", "start_document_project", args) == created
    claim = p["store"].document_slot("private-two")
    assert claim["definition"]["goal"]["owner_id"] == owner
    assert claim["plan"]["owner_principal_id"] == owner
    assert claim["source"]["principal_id"] == alias
    assert claim["source"]["conversation_id"] == alias
    assert call("website-status", "get_document_project", {"goal_id": created["result"]["goal_id"]}, owner)["result"]["project_status"] == "waiting"
    # The worker reloads the immutable Telegram ingress under fresh owner authority.
    import eva.bootstrap.document_goal_worker as bootstrap
    monkeypatch.setenv("EVA_DOCUMENT_TASK_POOL_FILE", str(pool))
    monkeypatch.setenv("EVA_DOCUMENT_GOAL_PLAN_FILE", "")
    monkeypatch.setenv("EVA_MCP_AUTONOMY_MANDATE_FILE", str(mandate_path))
    monkeypatch.delenv("EVA_MCP_MANAGED_ENDPOINTS_FILE", raising=False)
    callbacks = {}
    def capture_loop(**kwargs):
        callbacks.update(kwargs)
        return 0
    monkeypatch.setattr(bootstrap, "run_loop", capture_loop)
    def capture_ports(**kwargs):
        assert json.loads(kwargs["read_bound_plan"]())["owner_principal_id"] == owner
        return object()
    monkeypatch.setattr(bootstrap, "build_document_goal_ports", capture_ports)
    bootstrap.main()
    pending = callbacks["select_goals"](p["store"], NOW)
    assert len(pending) == 1
    callbacks["worker_factory"](engine, p["store"], pending[0], settings.tenant_id, settings.installation_id)
    request = ConversationActionCall("denied", "get_document_project", {"goal_id": website_id})
    for denied in (turn("stranger", "someone-else"),
                   replace(turn("group"), conversation_id="telegram:-101"),
                   replace(turn("installation"), installation_id="other")):
        with pytest.raises(ConversationConflict):
            port.execute(denied, request)
    bindings[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(101, owner, ("-101",), str(binding)),))
    with pytest.raises(ConversationConflict):
        call("removed-binding", "get_document_project", {"goal_id": website_id})
    assert call("website-without-telegram", "get_document_project", {"goal_id": website_id}, owner)["result"]["project_status"] == "cancelled"
    bindings[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(101, owner, ("101",), str(binding)),))
    import asyncio
    from eva.application.orchestration import ToolRequest
    adapter = port._provider
    prep = asyncio.run(adapter.prepare(turn("before-revocation"),
        ConversationActionCall("before-revocation", "get_document_project", {"goal_id": website_id})))
    prepared = ToolRequest("test", "op", "prefix", prep.tool_name, prep.arguments, prep.expected_proof,
        observation_tool_name=prep.observation_tool_name, observation_arguments=prep.observation_arguments,
        tool_configuration_sha256=prep.tool_configuration_sha256)
    table = metadata.tables["eva_core.principal_bindings"]
    with engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == scope.tenant_id,
            table.c.principal_binding_id == binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="not current"):
        call("revoked-binding", "get_document_project", {"goal_id": website_id})
    for operation in (adapter.execute(prepared), adapter.observe(prepared, None)):
        with pytest.raises(RuntimeError, match="not current"):
            asyncio.run(operation)
    with pytest.raises(RuntimeError, match="not current"):
        callbacks["worker_factory"](engine, p["store"], pending[0], settings.tenant_id, settings.installation_id)
    assert p["store"].document_slot("private-two") == claim
