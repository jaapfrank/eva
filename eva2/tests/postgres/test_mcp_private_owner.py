"""One connector registry across verified private channels; no network calls."""
import asyncio
from dataclasses import replace
import json
import pytest
import sqlalchemy as sa
from eva.adapters.connectors.mcp_gateway import McpGatewayConfig, McpGatewayToolAdapter
from eva.adapters.connectors.mcp_registration import McpRegistrationConfig
from eva.adapters.postgres.private_owner import PostgresPrivateOwnerResolver
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.application.memory_audience import MemoryAudience, TelegramGroupConsentReference
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.providers.mcp_autonomy import McpAutonomyProviderFactory, McpAutonomySettings
from tests.postgres.test_document_goal_actions import project
from tests.postgres.test_operational_timeline import timeline_engine
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_people_memory_overview import NOW
from tests.connectors.test_mcp_discovery import ROWS


def test_factory_registry_invocation_replay_and_fresh_private_authority(project, private_tmp_path, monkeypatch):
    p = project
    engine = p["timeline_engine"]
    label = "mcp-private-owner"
    scope, _, binding = _seed_authority(engine, label)
    owner = str(_id(label, "principal"))
    alias = "telegram:101"
    bindings = [TelegramPrincipalBindings((TelegramPrincipalBinding(101, owner, ("101",), str(binding)),))]
    resolver = PostgresPrivateOwnerResolver(engine, tenant_id=p["settings"].tenant_id,
        installation_id=p["settings"].installation_id, owner=_id(label, "principal"),
        binding=binding, authority_scope=scope, bindings_loader=lambda: bindings[0], clock=lambda: NOW)
    monkeypatch.setattr("eva.providers.private_owner_authority.configured_private_owner_resolver",
                        lambda *args, **kwargs: resolver)
    path = private_tmp_path / "mcp-private-mandate.json"
    path.write_text(json.dumps({**p["mandate"], "owner_principal_id": owner, "public_https": True}))
    path.chmod(0o600)
    settings = McpAutonomySettings(str(path), McpGatewayConfig("https://gateway.invalid", "gateway-secret"),
        McpRegistrationConfig("https://gateway.invalid", "registration-secret"))
    bundle = McpAutonomyProviderFactory.build(settings, engine)
    registration_port, discovery_port = bundle.external_tools
    registration = registration_port._provider
    authority, registry = registration.authority, registration.registry
    def turn(message, principal=alias, **changes):
        return p["turn"](message, principal_id=principal,
            conversation_id=alias if principal == alias else "owner-local", **changes)
    website, telegram = turn("website-registration", owner), turn("telegram-registration")
    endpoint = "https://8.8.8.8/mcp"
    connect_call = ConversationActionCall("connect", "connect_service",
        {"endpoint": endpoint, "documentation_url": "https://docs.example.test/mcp", "provenance": "test"})
    web_prep = asyncio.run(registration.planner().prepare(website, connect_call))
    tg_prep = asyncio.run(registration.planner().prepare(telegram, connect_call))
    assert web_prep.arguments["connector_id"] == tg_prep.arguments["connector_id"]
    assert tg_prep.arguments["source"]["principal_id"] == alias
    catalog = authority.admit_catalog(telegram, endpoint, ROWS, {"documentation_url": "https://docs.example.test"})
    connector_id = tg_prep.arguments["connector_id"]
    first = registry.register(telegram, connector_id, endpoint, "gateway-id", catalog, {"test": True})
    assert registry.register(website, connector_id, endpoint, "gateway-id", catalog, {"test": True}) == first
    source = registry._timeline.events(registry._thread_id)[0].payload["data"]["registration"]["source"]
    assert source["principal_id"] == alias and source["canonical_owner_principal_id"] == owner
    restarted = McpAutonomyProviderFactory.build(settings, engine)
    assert restarted.external_tools[0]._provider.registry.discover(website, connector_id) == registry.discover(telegram, connector_id)
    calls = []
    def request_json(self, method, path, payload):
        if method == "GET":
            return {"tools": ROWS, "nextCursor": None}
        calls.append(payload["params"])
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {"structuredContent": {"value": "result"}}}
    monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", request_json)
    route = PostgresConversationActions(engine, external_tools=bundle.external_tools)
    use = ConversationActionCall("use", "use_connector_tool", {"tool_id": first["tools"][0]["tool_id"],
        "arguments_json": '{"key":"entry"}'})
    active_turn = turn("telegram-use")
    result = route.execute(active_turn, use).output
    assert result["status"] == "verified"
    assert route.execute(active_turn, use).output == result
    assert len(calls) == 1
    assert route.execute(turn("website-use", owner), use).output["status"] == "verified"
    assert len(calls) == 2
    invalid = [turn("other", "someone-else"),
        replace(turn("wrong-chat"), conversation_id="telegram:-101"),
        replace(turn("wrong-installation"), installation_id="elsewhere"),
        replace(turn("group"), message_id="telegram:-101:group", conversation_id="telegram:-101", audience=MemoryAudience.telegram_group(principal_id=alias, group_chat_id="-101",
            consent_ref=TelegramGroupConsentReference(alias, "-101", "telegram-group-consent:test")))]
    fresh_turn = turn("prepared-before-revocation")
    prep = asyncio.run(discovery_port._planner.prepare(fresh_turn, use))
    request = discovery_port._request(fresh_turn, use.name, prep)
    for denied in invalid:
        with pytest.raises(ConversationConflict):
            asyncio.run(discovery_port._planner.prepare(denied, use))
        with pytest.raises(ConversationConflict):
            asyncio.run(registration.planner().prepare(denied, connect_call))
        guarded = discovery_port._orchestrator(denied, PostgresOperationalTimeline(engine, authority.scope))._tool
        for coroutine in (guarded.execute(request), guarded.observe(request, None)):
            with pytest.raises(ConversationConflict):
                asyncio.run(coroutine)
    checked = discovery_port._orchestrator(fresh_turn, PostgresOperationalTimeline(engine, authority.scope))._tool
    registration_request = registration_port._request(telegram, connect_call.name, tg_prep)
    table = metadata.tables["eva_core.principal_bindings"]
    with engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == scope.tenant_id,
            table.c.principal_binding_id == binding).values(status="revoked"))
    for coroutine in (checked.execute(request), checked.observe(request, None),
                      registration.execute(registration_request), registration.observe(registration_request, None)):
        with pytest.raises(RuntimeError, match="not current"):
            asyncio.run(coroutine)
    assert len(calls) == 2
