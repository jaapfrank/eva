from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn
from eva.application.orchestration import ToolRequest
from eva.providers.mcp_registration import McpRegistrationToolAdapter


TURN = ConversationTurn.private("tenant", "installation", "owner", "private-chat", "message-1",
                                "Sluit de documentatiedienst aan en gebruik de zoekfunctie.", "2026-09-09T20:00:00Z")


class Authority:
    configuration_sha256 = "a" * 64
    active = True
    revoked = False

    def authorize_turn(self, turn):
        if turn != TURN:
            raise ConversationConflict("another owner")

    def managed_endpoints(self, turn):
        self.authorize_turn(turn)
        return ["https://configured.example/mcp"]

    def status(self, turn):
        self.authorize_turn(turn)
        return {"active": self.active, "revoked": self.revoked}

    def stop(self, turn):
        self.authorize_turn(turn)
        self.active = False
        self.revoked = True
        return self.status(turn)

    def authorize_endpoint(self, turn, endpoint):
        if turn != TURN or not self.active:
            raise ConversationConflict("owner mandate revoked")

    def admit_catalog(self, turn, endpoint, rows, provenance):
        self.authorize_endpoint(turn, endpoint)
        # Only the host authority supplies executable configuration; provider
        # annotations are not projected into authorization by the adapter.
        assert rows[0]["annotations"] == {"readOnlyHint": True}
        return {"host_admitted": "catalog"}


class Client:
    registrations = 0
    reads = 0

    def register(self, *, owner_key, endpoint):
        self.registrations += 1
        return {"id": "gateway-1"}

    def discover(self, *, owner_key, endpoint):
        self.reads += 1
        return {"id": "gateway-1"}, [{"name": "search", "annotations": {"readOnlyHint": True}}]


class Registry:
    recorded = None

    def inventory(self, turn):
        assert turn == TURN
        return [{"connector_id": "documents", "endpoint": "https://documents.example/mcp",
                 "tool_names": ["read_document", "save_document"],
                 "connection_status": "registered", "next_action": "discover_connector_tools"}]

    def register(self, turn, cid, endpoint, gateway_id, catalog, provenance):
        assert turn == TURN and catalog == {"host_admitted": "catalog"}
        self.recorded = {"connector_id": cid, "tools": [{"tool_id": "sealed-search"}]}
        return self.recorded

    def discover(self, turn, cid):
        assert turn == TURN and self.recorded["connector_id"] == cid
        return self.recorded


def command(preparation):
    accepted = {f.name for f in fields(ToolRequest)}
    kwargs = {f.name: getattr(preparation, f.name) for f in fields(preparation) if f.name in accepted}
    return ToolRequest(thread_id="thread", operation_id="operation", checkpoint_prefix="checkpoint", **kwargs)


def test_registration_crosses_default_page_boundary_then_fresh_provider_composes():
    import hashlib
    import sqlalchemy as sa
    from eva.providers.mcp_gateway import McpGatewayProviderFactory, McpGatewayProviderSettings
    from tests.connectors.test_mcp_gateway import _gateway, _catalog_row, _spec

    with _gateway() as (base_url, handler):
        handler.catalog = [_catalog_row(f"existing-{i}") for i in range(48)] + [_catalog_row()]
        settings = McpGatewayProviderSettings(
            base_url=base_url, bearer_token="private-test-token", allow_plain_http=True,
            specs=(_spec(),), principal_sha256_allowlist=(hashlib.sha256(b"owner").hexdigest(),),
        )
        engine = sa.create_engine("sqlite://")
        before = McpGatewayProviderFactory.build(settings, engine)

        class RegistrationClient(Client):
            def register(self, *, owner_key, endpoint):
                result = super().register(owner_key=owner_key, endpoint=endpoint)
                handler.catalog[:0] = [_catalog_row(f"new-{i}") for i in range(4)]
                return result

        client, registry = RegistrationClient(), Registry()
        adapter = McpRegistrationToolAdapter(client, registry, Authority(), "owner", lambda source: TURN)
        preparation = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
            "new-registration", "connect_service", {
                "endpoint": "https://docs.example/mcp", "documentation_url": "https://docs.example/about",
                "provenance": "Synthetic public documentation service in an offline integration fixture.",
            },
        )))
        request = command(preparation)
        dispatch = asyncio.run(adapter.execute(request))
        assert dispatch.accepted
        assert asyncio.run(adapter.observe(request, dispatch)).disposition == "verified"
        assert client.registrations == 1 and len(handler.catalog) == 53
        after = McpGatewayProviderFactory.build(settings, engine)
        assert after is not before
        assert after.conversation_tools == before.conversation_tools
        assert after.external_tools[0].action_names == ("echo_proof",)
        engine.dispose()


def test_connect_readback_then_discover_uses_actual_source_and_host_catalog():
    client, registry, authority = Client(), Registry(), Authority()
    adapter = McpRegistrationToolAdapter(client, registry, authority, "owner", lambda source: TURN)
    preparation = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("call1", "connect_service", {
        "endpoint": "https://docs.example/mcp", "documentation_url": "https://docs.example/about",
        "provenance": "Maintainer documentation inspected in this development fixture.",
    })))
    request = command(preparation)
    assert asyncio.run(adapter.execute(request)).accepted
    assert registry.recorded is None
    proof = asyncio.run(adapter.observe(request, None))
    assert proof.disposition == "verified" and request.expected_proof == proof.observed
    assert proof.evidence["observation"]["tool_count"] == 1
    assert "tools" not in proof.evidence["observation"]
    follow = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("call2", "discover_connector_tools", {
        "connector_id": registry.recorded["connector_id"],
    })))
    assert asyncio.run(adapter.execute(command(follow))).accepted
    assert asyncio.run(adapter.observe(command(follow), None)).evidence["observation"] == registry.recorded
    assert client.registrations == client.reads == 1
    authority.active = False
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.execute(request))
    assert client.registrations == 1


def test_missing_registration_proves_safe_initial_dispatch_but_not_success():
    from eva.adapters.connectors.mcp_registration import McpRegistrationAbsent
    from eva.application.orchestration import ToolDispatchResult
    client, registry, authority = Client(), Registry(), Authority()
    def absent(**kwargs):
        raise McpRegistrationAbsent("not registered")
    client.discover = absent
    adapter = McpRegistrationToolAdapter(client, registry, authority, "owner", lambda source: TURN)
    prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("call3", "connect_service", {
        "endpoint": "https://docs.example/mcp", "documentation_url": "https://docs.example/about", "provenance": "fixture",
    })))
    req = command(prepared)
    assert asyncio.run(adapter.observe(req, None)).disposition == "safe_to_execute"
    assert asyncio.run(adapter.observe(req, ToolDispatchResult(True, "sent", {}))).disposition == "pending"
    assert registry.recorded is None


def test_owner_can_stop_and_inspect_mandate_without_a_new_provider_call():
    client, authority = Client(), Authority()
    # A host-level pause must not be mistaken for the owner's durable stop.
    authority.active = False
    adapter = McpRegistrationToolAdapter(client, Registry(), authority, "owner", lambda source: TURN)
    prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("stop1", "manage_connected_services", {"action": "stop"})))
    req = command(prepared)
    assert asyncio.run(adapter.observe(req, None)).disposition == "safe_to_execute"
    dispatch = asyncio.run(adapter.execute(req))
    assert asyncio.run(adapter.observe(req, dispatch)).observed == {"revoked": True}
    status = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("status1", "manage_connected_services", {"action": "status"})))
    assert asyncio.run(adapter.observe(command(status), None)).evidence["observation"] == {"active": False, "revoked": True}
    assert client.registrations == client.reads == 0


def test_endpoint_discovery_absence_allows_connection_without_hiding_denials(monkeypatch):
    import sqlalchemy as sa
    from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
    from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.providers.mcp_registration import connector_id_for

    class ExistingClient(Client):
        find_calls = 0
        def register(self, *, owner_key, endpoint):
            self.find_calls += 1
            return {"id": "already-registered-gateway"}

    client, registry, authority = ExistingClient(), Registry(), Authority()
    def current(**kwargs):
        if not authority.active:
            raise ConversationConflict("owner mandate revoked")
    engine = sa.create_engine("postgresql+psycopg://never-connected.invalid/unused")
    durable = PostgresMcpConnectorRegistry(engine,
        external_timeline_scope(TURN.tenant_id, TURN.installation_id, "required"),
        TURN.principal_id, McpGatewayConfig("https://gateway.example", "fixture"),
        authority.authorize_turn, current)
    monkeypatch.setattr(durable, "_snapshot", lambda: (None, {}))
    registry.discover = durable.discover
    adapter = McpRegistrationToolAdapter(client, registry, authority, "owner", lambda source: TURN)
    endpoint = "https://docs.example/mcp"
    prepared = asyncio.run(adapter.planner().prepare(TURN,
        ConversationActionCall("lookup", "discover_connector_tools", {"connector_id": endpoint})))
    cid = connector_id_for("owner", endpoint)
    assert cid != connector_id_for("another-owner", endpoint)
    assert prepared.arguments["connector_id"] == cid
    assert prepared.arguments["endpoint"] == endpoint
    req = command(prepared)
    dispatch = asyncio.run(adapter.execute(req))
    proof = asyncio.run(adapter.observe(req, dispatch))
    result = proof.evidence["observation"]
    assert proof.disposition == "verified" and proof.observed == req.expected_proof
    assert proof.evidence["source"] == "owner_connector_catalog_lookup"
    assert result["connection_status"] == "not_connected" and result["tools"] == []
    assert result["next_action"] == "connect_service" and result["endpoint"] == endpoint
    assert client.registrations == client.find_calls == client.reads == 0

    authority.active = False
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.observe(req, dispatch))
    authority.active = True
    other = ConversationTurn.private("tenant", "installation", "other-owner", "chat", "message", "hello", TURN.occurred_at)
    with pytest.raises(ConversationConflict, match="another owner"):
        asyncio.run(adapter.planner().prepare(other,
            ConversationActionCall("wrong-owner", "discover_connector_tools", {"connector_id": endpoint})))
    monkeypatch.setattr(durable, "_record", lambda cid: {"revoked": False})
    def drift(record):
        raise ConversationConflict("gateway binding differs")
    monkeypatch.setattr(durable, "_require_gateway", drift)
    with pytest.raises(ConversationConflict, match="gateway binding differs"):
        asyncio.run(adapter.observe(req, dispatch))

    # The verified absence gives the next ordinary action usable arguments;
    # connecting reuses the provider's existing registration and seals its catalog.
    registry.discover = Registry.discover.__get__(registry)
    follow = asyncio.run(adapter.planner().prepare(TURN,
        ConversationActionCall("complete-connection", result["next_action"], {
            "endpoint": result["endpoint"], "documentation_url": "https://docs.example/about",
            "provenance": "Previously researched maintainer documentation.",
        })))
    connected = command(follow)
    done = asyncio.run(adapter.observe(connected, asyncio.run(adapter.execute(connected))))
    assert done.disposition == "verified" and done.observed == connected.expected_proof
    assert asyncio.run(adapter.observe(req, dispatch)).evidence["observation"]["tools"]
    assert client.find_calls == client.reads == 1 and client.registrations == 0


def test_status_exposes_only_owner_inventory_and_distinguishes_configuration():
    client, authority = Client(), Authority()
    adapter = McpRegistrationToolAdapter(client, Registry(), authority, "owner", lambda source: TURN)
    prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall(
        "inventory", "manage_connected_services", {"action": "status"})))
    result = asyncio.run(adapter.observe(command(prepared), None)).evidence["observation"]
    assert result["connectors"][0]["connector_id"] == "documents"
    assert result["connectors"][0]["tool_names"] == ["read_document", "save_document"]
    assert result["configured_endpoints"][0]["connection_status"] == "registration_not_recorded"
    assert result["configured_endpoints"][0]["endpoint"] == "https://configured.example/mcp"
    assert result["reachability_verified"] is False
    assert client.registrations == client.reads == 0


def test_inventory_rejects_other_owner_paused_mandate_and_gateway_drift(monkeypatch):
    from dataclasses import replace
    import sqlalchemy as sa
    from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
    from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from tests.postgres.test_mcp_connector_registry import _catalog

    authority = Authority()
    def current(**kwargs):
        if not authority.active:
            raise ConversationConflict("owner mandate revoked")
    engine = sa.create_engine("postgresql+psycopg://never-connected.invalid/unused")
    registry = PostgresMcpConnectorRegistry(engine,
        external_timeline_scope(TURN.tenant_id, TURN.installation_id, "required"),
        TURN.principal_id, McpGatewayConfig("https://gateway.example", "private-token"),
        authority.authorize_turn, current)
    record = {"endpoint": "https://documents.example/mcp", "revoked": False,
              "gateway": registry._gateway_identity(), "catalog": _catalog()}
    monkeypatch.setattr(registry, "_snapshot", lambda: (None, {
        "documents": record, "revoked": {**record, "revoked": True}}))
    assert registry.inventory(TURN) == [{
        "connector_id": "documents", "endpoint": "https://documents.example/mcp",
        "connection_status": "registered", "tool_names": ["fixture_read", "fixture_save"],
        "next_action": "discover_connector_tools"}]
    with pytest.raises(ConversationConflict, match="owner or installation"):
        registry.inventory(replace(TURN, principal_id="other",
                                   audience=replace(TURN.audience, principal_id="other", audience_id="person:other")))
    with pytest.raises(ConversationConflict, match="owner or installation"):
        registry.inventory(replace(TURN, installation_id="other"))
    authority.active = False
    with pytest.raises(ConversationConflict, match="revoked"):
        registry.inventory(TURN)
    authority.active = True
    record["gateway"] = {"base_url": "https://other.example", "allow_plain_http": False}
    with pytest.raises(ConversationConflict, match="gateway differs"):
        registry.inventory(TURN)
    engine.dispose()


def test_managed_endpoint_inventory_checks_owner_mandate_and_configuration(monkeypatch):
    from eva.providers.mcp_owner_authority import McpOwnerAuthority
    authority = object.__new__(McpOwnerAuthority)
    calls = []
    authority._managed_endpoints = {"https://configured.example/mcp": frozenset({"192.0.2.1"})}
    monkeypatch.setattr(authority, "authorize_turn", lambda turn: calls.append(turn))
    monkeypatch.setattr(authority, "authorize_current", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(authority, "_read_managed_endpoints", lambda: authority._managed_endpoints)
    assert authority.managed_endpoints(TURN) == ["https://configured.example/mcp"]
    assert calls == [TURN, {"phase": "execute", "operation_kind": "read"}]
    monkeypatch.setattr(authority, "_read_managed_endpoints", lambda: {})
    with pytest.raises(ConversationConflict, match="changed after admission"):
        authority.managed_endpoints(TURN)


def test_configured_catalog_reuses_pinned_tools_and_original_principal_policy(monkeypatch):
    import hashlib
    from dataclasses import replace
    import sqlalchemy as sa
    from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
    from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.providers.mcp_gateway import McpGatewayProviderSettings, parse_mcp_gateway_catalog
    from tests.postgres.test_mcp_connector_registry import _catalog
    import json

    settings = McpGatewayProviderSettings("https://gateway.example", "private-token", False,
        parse_mcp_gateway_catalog(json.dumps(_catalog())),
        (hashlib.sha256(TURN.principal_id.encode()).hexdigest(),))
    engine = sa.create_engine("postgresql+psycopg://never-connected.invalid/unused")
    enabled = [True]
    def current(**kwargs):
        if not enabled[0]:
            raise ConversationConflict("mandate paused")
    registry = PostgresMcpConnectorRegistry(engine,
        external_timeline_scope(TURN.tenant_id, TURN.installation_id, "required"),
        TURN.principal_id, McpGatewayConfig(settings.base_url, settings.bearer_token),
        lambda turn: None, current, configured_catalog=settings)
    monkeypatch.setattr(registry, "_snapshot", lambda: (None, {}))
    inventory = registry.inventory(TURN)
    assert inventory[0]["tool_names"] == ["fixture_read", "fixture_save"]
    assert inventory[0]["connection_status"] == "configured_catalog"
    catalog = registry.discover(TURN, inventory[0]["connector_id"])
    tool = catalog["tools"][0]
    assert tool["name"] == "read_document"
    assert "path" in tool["input_schema"]["properties"]
    binding = registry.resolve_for_turn(TURN, tool["tool_id"])
    assert binding.spec is settings.specs[0]
    assert registry.resolve_configuration(binding.spec.configuration_sha256, phase="execute") == binding
    assert "private-token" not in json.dumps(inventory + [catalog])
    enabled[0] = False
    with pytest.raises(ConversationConflict, match="paused"):
        registry.resolve_for_turn(TURN, tool["tool_id"])
    enabled[0] = True
    registry._configured_catalog = replace(settings,
        principal_sha256_allowlist=(hashlib.sha256(b"someone-else").hexdigest(),))
    assert registry.inventory(TURN) == []
    with pytest.raises(ConversationConflict, match="unknown"):
        registry.resolve_for_turn(TURN, tool["tool_id"])
    dynamic = {"connector_id": "dynamic", "endpoint": "https://docs.example/mcp",
               "gateway": registry._gateway_identity(), "revoked": False, "catalog": _catalog()}
    monkeypatch.setattr(registry, "_snapshot", lambda: (None, {"dynamic": dynamic}))
    assert registry.inventory(TURN)[0]["connector_id"] == "dynamic"
    dynamic_tool = registry.discover(TURN, "dynamic")["tools"][0]
    assert registry.resolve_for_turn(TURN, dynamic_tool["tool_id"]).connector_id == "dynamic"
    with pytest.raises(ConversationConflict, match="no unique immutable binding"):
        registry.resolve_configuration(binding.spec.configuration_sha256, phase="execute")
    engine.dispose()
