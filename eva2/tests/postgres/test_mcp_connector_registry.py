"""Durable connector authority, restart and exact revocation regression."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationConflict, ConversationTurn
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


def _catalog() -> dict:
    schema = {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
    }
    binding = [{"argument_name": "path", "provider_name": "path"}]
    read = {
        "eva_name": "read_document", "description": "Read an admitted document.",
        "completion_guidance": "Report the returned content.",
        "input_schema": schema,
        "execute_tool_name": "fixture-read", "observe_tool_name": "fixture-read",
        "execute_catalog_name": "fixture_read", "observe_catalog_name": "fixture_read",
        "execute_catalog_sha256": "a" * 64, "observe_catalog_sha256": "a" * 64,
        "execute_argument_bindings": binding, "observe_argument_bindings": binding,
        "proof_bindings": [], "operation_kind": "read",
    }
    effect = {
        **read, "eva_name": "save_document", "description": "Save an admitted document.",
        "execute_tool_name": "fixture-save", "execute_catalog_name": "fixture_save",
        "execute_catalog_sha256": "b" * 64, "operation_kind": "effect",
        "proof_bindings": [{"argument_name": "path", "observed_name": "path"}],
    }
    return {"contract": "EvaMcpGatewayProvider/v3", "tools": [read, effect]}


def test_registration_restart_owner_scope_and_revoke_preserve_exact_readback(
    timeline_engine: Engine,
) -> None:
    turn = ConversationTurn.private(
        tenant_id="connector-registry-test", installation_id="home",
        principal_id="person:owner", conversation_id="private:owner",
        message_id="registration-source", text="Connect this service for my work.",
        occurred_at="2026-09-09T12:00:00Z",
    )
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope required")
    config = McpGatewayConfig("https://gateway.example.test", "original-private-token")
    enabled = [True]
    seen_sources: list[str] = []

    def authorize_turn(actual: ConversationTurn) -> None:
        seen_sources.append(actual.message_id)

    def authorize_current(*, phase: str, operation_kind: str) -> None:
        if not enabled[0] and not (phase == "observe" and operation_kind == "effect"):
            raise ConversationConflict("owner mandate paused")

    def registry(engine: Engine, **overrides) -> PostgresMcpConnectorRegistry:
        arguments = dict(
            engine=engine, scope=scope, owner_principal_id=turn.principal_id,
            gateway_config=config, authorize_turn=authorize_turn,
            authorize_current=authorize_current,
        )
        return PostgresMcpConnectorRegistry(**{**arguments, **overrides})

    store = registry(timeline_engine)
    catalog = _catalog()
    first = store.register(
        turn, "documents", "https://documents.example.test/mcp", "gateway-record-1",
        catalog, {"admission": "host-approved-scope", "source": "test-fixture"},
    )
    # A later chat retry keeps both the original source and immutable IDs.
    assert store.register(
        replace(turn, message_id="registration-retry"),
        "documents", "https://documents.example.test/mcp", "gateway-record-1",
        catalog, {"admission": "host-approved-scope", "source": "test-fixture"},
    ) == first
    events = store._timeline.events(store._thread_id)
    assert len(events) == 1
    assert events[0].payload["data"]["registration"]["source"]["source_message_id"] == turn.message_id
    assert config.bearer_token not in json.dumps(events[0].payload)
    assert turn.text not in json.dumps(events[0].payload)
    discovered = store.discover(turn, "documents")
    assert discovered["connector_id"] == "documents"
    tools = discovered["tools"]
    assert [item["operation_kind"] for item in tools] == ["read", "effect"]
    assert tools[0]["input_schema"] == catalog["tools"][0]["input_schema"]
    read_id, effect_id = [item["tool_id"] for item in tools]
    read_hash, effect_hash = [item["configuration_sha256"] for item in tools]
    assert store.resolve_for_turn(turn, read_id).spec.configuration_sha256 == read_hash

    # Recreate both process-local engine and registry; token renewal preserves
    # binding identity, while silently switching the gateway host is rejected.
    restarted_engine = sa.create_engine(timeline_engine.url, pool_pre_ping=False)
    try:
        restarted = registry(restarted_engine, gateway_config=replace(
            config, bearer_token="rotated-private-token",
        ))
        binding = restarted.resolve_configuration(read_hash, phase="observe")
        assert binding.tool_id == read_id
        assert binding.config.bearer_token == "rotated-private-token"
        with pytest.raises(ConversationConflict, match="gateway differs"):
            registry(restarted_engine, gateway_config=replace(
                config, base_url="https://other-gateway.example.test",
            )).resolve_configuration(read_hash, phase="execute")
        with pytest.raises(ConversationConflict, match="owner or installation"):
            restarted.discover(replace(turn, installation_id="other-home"), "documents")
        other_owner = ConversationTurn.private(
            tenant_id=turn.tenant_id, installation_id=turn.installation_id,
            principal_id="person:other", conversation_id="private:other",
            message_id="other-source", text="Read the documents.", occurred_at=turn.occurred_at,
        )
        with pytest.raises(ConversationConflict, match="owner or installation"):
            restarted.resolve_for_turn(other_owner, read_id)
        with pytest.raises(ConversationConflict, match="no unique immutable binding"):
            registry(restarted_engine, owner_principal_id=other_owner.principal_id).resolve_configuration(
                read_hash, phase="execute",
            )
        with pytest.raises(ConversationConflict, match="immutable"):
            restarted.register(turn, "documents", "https://changed.example.test/mcp",
                               "gateway-record-1", catalog, {"admission": "changed"})
        with pytest.raises(ConversationConflict, match="already belongs"):
            restarted.register(turn, "duplicate", "https://documents.example.test/mcp",
                               "gateway-record-1", catalog, {})
        with pytest.raises(ConversationConflict, match="no unique immutable binding"):
            restarted.resolve_configuration("f" * 64, phase="execute")

        enabled[0] = False
        with pytest.raises(ConversationConflict, match="mandate paused"):
            restarted.resolve_configuration(read_hash, phase="observe")
        assert restarted.resolve_configuration(effect_hash, phase="observe").tool_id == effect_id
        assert restarted.revoke(turn, "documents")["state"] == "revoked"
        enabled[0] = True
        with pytest.raises(ConversationConflict, match="revoked"):
            restarted.discover(turn, "documents")
        with pytest.raises(ConversationConflict, match="revoked"):
            restarted.resolve_for_turn(turn, effect_id)
        with pytest.raises(ConversationConflict, match="revoked"):
            restarted.resolve_configuration(effect_hash, phase="execute")
        with pytest.raises(ConversationConflict, match="revoked"):
            restarted.resolve_configuration(read_hash, phase="observe")
        assert restarted.resolve_configuration(effect_hash, phase="observe").tool_id == effect_id
        assert restarted.revoke(turn, "documents")["state"] == "revoked"
        history = restarted._timeline.events(restarted._thread_id)
        assert len(history) == 2
        assert history[0] == events[0]
        assert history[1].event_type == "MCP_CONNECTOR_REVOKED"
        assert "registration-source" in seen_sources
        assert "registration-retry" in seen_sources
    finally:
        restarted_engine.dispose()
