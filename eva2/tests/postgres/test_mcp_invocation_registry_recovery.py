"""Durable registry + invocation recovery; mocked transport, real PostgreSQL."""
import asyncio

import json

import pytest
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import McpGatewayToolAdapter
from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.application.orchestration import VerifiedToolOrchestrator
from eva.providers.mcp_discovery import McpDiscoveryToolAdapter
from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
from tests.connectors.test_mcp_invocation import CONFIG, OUTPUT, DIGEST, _command, _provider
from tests.connectors.test_mcp_gateway import _turn
from tests.connectors.test_mcp_gateway_provider import _provider_catalog
from tests.postgres.test_mcp_connector_registry import _catalog
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401
from tests.application.test_orchestration import NOW


def test_real_registry_revoked_invocation_recovers_durable_receipt_offline(monkeypatch, timeline_engine):
    calls, catalog_calls = _provider(monkeypatch)
    turn = _turn()
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "integration fixture")
    paused = [False]
    phases = []
    def owner(actual):
        if (actual.tenant_id, actual.installation_id, actual.principal_id) != (turn.tenant_id, turn.installation_id, turn.principal_id):
            raise ConversationConflict("different owner")
    def current(*, phase, operation_kind):
        phases.append((phase, operation_kind))
        if paused[0] and (phase, operation_kind) != ("observe", "invoke"):
            raise ConversationConflict("mandate paused")
    def registry(engine):
        return PostgresMcpConnectorRegistry(engine, scope, turn.principal_id, CONFIG, owner, current)
    catalog = json.loads(_provider_catalog())
    catalog["contract"] = "EvaMcpGatewayProvider/v4"
    tool = catalog["tools"][0]
    tool.update(operation_kind="invoke", proof_bindings=[])
    for phase in ("execute", "observe"):
        tool[phase + "_catalog_sha256"] = DIGEST
        tool[phase + "_argument_bindings"] = [{"argument_name":"message", "provider_name":"message"}]
    catalog["tools"].append(_catalog()["tools"][0])
    spec, read_spec = parse_mcp_gateway_catalog(json.dumps(catalog))
    store = registry(timeline_engine)
    store.register(turn, "fixture", "https://fixture.invalid/mcp", "fixture-id", catalog, {"scope":"synthetic"})
    tools = store.discover(turn, "fixture")["tools"]
    invoke_id = tools[0]["tool_id"]
    adapter = McpDiscoveryToolAdapter(store)
    command = _command(spec)
    timeline = PostgresOperationalTimeline(timeline_engine, scope)
    timeline.create_thread(thread_id=command.thread_id, checkpoint_id="fixture-admission", occurred_at=NOW, data={})
    class InterruptedAfterDispatch:
        execute = adapter.execute
        async def observe(self, request, dispatch):
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(VerifiedToolOrchestrator(timeline, InterruptedAfterDispatch(), clock=lambda: NOW).run(command))
    assert timeline.load(command.thread_id).phase == "tool_executed"
    before = timeline.events(command.thread_id)
    assert [e.event_type for e in before].count("TOOL_EXECUTED") == 1
    assert len(calls) == len(catalog_calls) == 1
    # Close old pool, reconstruct every persistence/provider object. No process or DB kill.
    timeline_engine.dispose()
    restarted_engine = sa.create_engine(timeline_engine.url, pool_pre_ping=False)
    try:
        restored_store = registry(restarted_engine)
        restored_store.revoke(turn, "fixture")
        paused[0] = True
        def offline(*args, **kwargs):
            pytest.fail("Recovery or denied work contacted the gateway")
        monkeypatch.setattr(McpGatewayToolAdapter, "_request_json", offline)
        restored = McpDiscoveryToolAdapter(restored_store)
        durable = PostgresOperationalTimeline(restarted_engine, scope)
        assert durable.events(command.thread_id) == before
        result = asyncio.run(VerifiedToolOrchestrator(durable, restored, clock=lambda: NOW).run(command))
        assert result.status == "verified"
        evidence = result.proof["evidence"]
        assert evidence["observation"] == OUTPUT
        assert evidence["source"] == "mcp_gateway_invocation_response"
        assert evidence["external_state_verified"] is False
        assert result.proof["observed"] == {}
        events = durable.events(command.thread_id)
        assert [e.event_type for e in events].count("TOOL_EXECUTED") == 1
        assert [e.event_type for e in events].count("STATE_VERIFIED") == 1
        assert ("observe", "invoke") in phases
        # New work and read observations remain forbidden; cached invocation is narrow.
        for action in (lambda: restored_store.resolve_for_turn(turn, invoke_id),
                       lambda: restored_store.resolve_configuration(spec.configuration_sha256, phase="execute"),
                       lambda: restored_store.resolve_configuration(read_spec.configuration_sha256, phase="observe")):
            with pytest.raises(ConversationConflict): action()
        with pytest.raises(ConversationConflict):
            asyncio.run(restored.execute(command))
        with pytest.raises(ConversationConflict):
            restored_store.discover(ConversationTurn.private(turn.tenant_id, turn.installation_id, "person:other", "other", "other", "read", turn.occurred_at), "fixture")
        replay = asyncio.run(VerifiedToolOrchestrator(durable, restored, clock=lambda: NOW).run(command))
        assert replay.proof == result.proof and durable.events(command.thread_id) == events
        assert len(calls) == len(catalog_calls) == 1
    finally:
        restarted_engine.dispose()