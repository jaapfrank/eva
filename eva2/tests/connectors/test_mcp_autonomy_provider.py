from datetime import UTC, datetime
from types import SimpleNamespace
import sys

import pytest
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
from eva.adapters.connectors.mcp_registration import McpRegistrationConfig
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import TimelineNotFound, external_timeline_scope
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.providers import mcp_autonomy as module


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in tuple(module.os.environ):
        if name.startswith("EVA_MCP_"):
            monkeypatch.delenv(name)


def configured(monkeypatch, tmp_path):
    for name in ("EVA_MCP_GATEWAY_TOKEN_FILE", "EVA_MCP_REGISTRATION_TOKEN_FILE"):
        path = tmp_path / name
        path.write_text("synthetic-" + name)
        path.chmod(0o600)
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("EVA_MCP_GATEWAY_URL", "http://127.0.0.1:4444")
    monkeypatch.setenv("EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP", "true")
    monkeypatch.setenv("EVA_MCP_AUTONOMY_MANDATE_FILE", str(tmp_path / "mandate.json"))


def test_disabled_autonomy_does_not_adopt_existing_gateway_configuration(monkeypatch):
    monkeypatch.setenv("EVA_MCP_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("EVA_MCP_GATEWAY_TOKEN_FILE", "/not/read")
    assert module.McpAutonomyProviderFactory.load_settings() is None


@pytest.mark.parametrize("setting", [
    "EVA_MCP_AUTONOMY_MANDATE_FILE", "EVA_MCP_REGISTRATION_TOKEN_FILE",
    "EVA_MCP_REGISTRATION_VISIBILITY", "EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY",
])
def test_partial_opt_in_is_rejected(monkeypatch, setting):
    monkeypatch.setenv(setting, "configured")
    with pytest.raises(RuntimeError):
        module.McpAutonomyProviderFactory.load_settings()


def test_settings_use_separate_private_registration_credential(monkeypatch, tmp_path):
    configured(monkeypatch, tmp_path)
    settings = module.McpAutonomyProviderFactory.load_settings()
    assert settings.registration.visibility == "private"
    assert settings.registration.registration_token != settings.gateway.bearer_token
    assert settings.registration.registration_token not in repr(settings)
    monkeypatch.setenv("EVA_MCP_REGISTRATION_VISIBILITY", "public")
    with pytest.raises(ValueError):
        module.McpAutonomyProviderFactory.load_settings()
    monkeypatch.setenv("EVA_MCP_REGISTRATION_EXCLUSIVE_OWNER_GATEWAY", "true")
    assert module.McpAutonomyProviderFactory.load_settings().registration.visibility == "public"


@pytest.mark.parametrize("setting", ["EVA_MCP_GATEWAY_TOKEN", "EVA_MCP_REGISTRATION_TOKEN"])
def test_inline_secrets_are_rejected(monkeypatch, tmp_path, setting):
    configured(monkeypatch, tmp_path)
    monkeypatch.setenv(setting, "forbidden")
    with pytest.raises(RuntimeError, match="private runtime files"):
        module.McpAutonomyProviderFactory.load_settings()


class Authority:
    scope = external_timeline_scope("tenant", "installation", "required")
    owner_principal_id = "owner"
    owner_key = "tenant:installation:owner"
    configuration_sha256 = "a" * 64

    def __init__(self, *args):
        self.seen = []

    def authorize_turn(self, turn):
        if turn.principal_id != self.owner_principal_id or turn.audience.kind != "private_person":
            raise ConversationConflict("not private owner")
        self.seen.append(turn)

    def authorize_current(self, *, phase, operation_kind):
        pass


def source_turn():
    return ConversationTurn.private("tenant", "installation", "owner", "real-conversation",
        "real-message", "The original admitted owner instruction", "2026-09-09T12:00:00Z",
        project_id="original-project")


def source_reference(turn):
    return {key: getattr(turn, key) for key in (
        "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id"
    )} | {"audience": {"kind": "private_person", "principal_id": turn.principal_id}}


def source_loader(monkeypatch):
    turn = source_turn()
    data = PostgresConversationJournal._ingress_data(turn)
    event = SimpleNamespace(event_type="INGRESS_COMMITTED", payload={"data": data},
        occurred_at=datetime(2026, 9, 9, 12, tzinfo=UTC))
    timeline = SimpleNamespace(load=lambda key: None, events=lambda key: (event,))
    monkeypatch.setattr(module, "PostgresOperationalTimeline", lambda *args: timeline)
    authority = Authority()
    return module._SourceTurnLoader(None, authority), source_reference(turn), event, authority, timeline


def test_loader_reconstructs_actual_stored_text_time_and_project(monkeypatch):
    loader, source, event, authority, _ = source_loader(monkeypatch)
    assert loader(source) == source_turn()
    event.payload["data"]["text"] = "Actual stored revised fixture, not source payload text"
    assert loader(source).text == event.payload["data"]["text"]
    assert authority.seen[-1].project_id == "original-project"
    event.occurred_at = event.occurred_at.replace(microsecond=123456)
    with pytest.raises(ConversationConflict, match="stored ingress is invalid"):
        loader(source)


@pytest.mark.parametrize("change", ["principal", "tenant", "audience", "message", "invented-text"])
def test_loader_rejects_wrong_scope_audience_or_unstored_source(monkeypatch, change):
    loader, source, _, _, _ = source_loader(monkeypatch)
    if change == "principal":
        source["principal_id"] = "someone-else"
    elif change == "tenant":
        source["tenant_id"] = "another-tenant"
    elif change == "audience":
        source["audience"]["kind"] = "telegram_group"
    elif change == "message":
        source["message_id"] = "another-message"
    else:
        source["text"] = "Invented authorization"
    with pytest.raises(ConversationConflict):
        loader(source)


def test_loader_refuses_missing_or_erased_ingress_and_propagates_corruption(monkeypatch):
    loader, source, event, _, timeline = source_loader(monkeypatch)
    event.payload["data"].pop("text")
    with pytest.raises(ConversationConflict, match="stored ingress is invalid"):
        loader(source)
    def absent(key):
        raise TimelineNotFound("absent")
    timeline.load = absent
    with pytest.raises(ConversationConflict, match="absent"):
        loader(source)
    def corrupt(key):
        raise RuntimeError("corrupt projection")
    timeline.load = corrupt
    with pytest.raises(RuntimeError, match="corrupt projection"):
        loader(source)


def test_factory_reuses_two_durable_ports_without_registration_io(monkeypatch):
    monkeypatch.setitem(sys.modules, "eva.providers.mcp_owner_authority",
        SimpleNamespace(McpOwnerAuthority=Authority))
    records = []
    def registry(*args):
        records.append(args)
        return SimpleNamespace()
    monkeypatch.setattr(module, "PostgresMcpConnectorRegistry", registry)
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")
    settings = module.McpAutonomySettings("/private/mandate.json",
        McpGatewayConfig("https://gateway.example", "gateway-token"),
        McpRegistrationConfig("https://gateway.example", "registration-token"))
    contribution = module.McpAutonomyProviderFactory.build(settings, engine)
    names = {tool.name for tool in contribution.conversation_tools}
    assert {"connect_service", "discover_connector_tools", "use_connector_tool"} <= names
    assert len(contribution.external_tools) == 2
    assert records[0][0] is engine and records[0][1] == Authority.scope
    assert records[0][2] == "owner"
    assert all(port.available_for(source_turn()) for port in contribution.external_tools)
    assert all(not port.available_for(ConversationTurn.private("tenant", "installation", "other",
        "chat", "message", "hello", "2026-09-09T12:00:00Z")) for port in contribution.external_tools)


def test_parameterless_mcp_schema_keeps_original_catalog_pin(monkeypatch):
    import asyncio
    import copy
    import json
    from eva.adapters.connectors.mcp_gateway import (
        McpGatewayToolAdapter, mcp_gateway_catalog_binding,
    )
    from eva.application.conversation import ConversationActionCall
    from eva.contracts.canonical_json import canonical_sha256
    from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
    from eva.providers.mcp_owner_authority import McpOwnerAuthority

    row = {"name": "provider-fetch-docs", "inputSchema": {"type": "object"},
        "description": "Fetch documentation", "integrationType": "MCP",
        "requestType": "STREAMABLEHTTP", "enabled": True, "reachable": True,
        "deprecated": False, "visibility": "public", "annotations": {}}
    original = copy.deepcopy(row)
    original_hash = canonical_sha256(mcp_gateway_catalog_binding(
        row, contract="EvaMcpGatewayProvider/v4"))
    authority = McpOwnerAuthority.__new__(McpOwnerAuthority)
    monkeypatch.setattr(authority, "authorize_endpoint", lambda *args: None)
    catalog = authority.admit_catalog(source_turn(), "https://service.example/mcp",
        [row], {"documentation_url": "https://service.example/docs"})
    spec, = parse_mcp_gateway_catalog(json.dumps(catalog))
    assert row == original
    assert spec.descriptor.input_schema == {
        "type": "object", "properties": {}, "required": [], "additionalProperties": False,
    }
    assert spec.execute_catalog_sha256 == spec.observe_catalog_sha256 == original_hash
    adapter = McpGatewayToolAdapter(McpGatewayConfig("https://gateway.example", "fixture"), (spec,))
    def catalog_read(method, path, payload):
        assert (method, path, payload) == ("GET", "/tools", None)
        return [row]
    monkeypatch.setattr(adapter, "_request_json", catalog_read)
    asyncio.run(adapter.verify_catalog())
    prepared = asyncio.run(adapter.planner().prepare(source_turn(),
        ConversationActionCall("empty-args", spec.descriptor.name, {})))
    assert prepared.arguments == prepared.observation_arguments == {}
    with pytest.raises(ConversationConflict, match="fields are invalid"):
        asyncio.run(adapter.planner().prepare(source_turn(),
            ConversationActionCall("extra-args", spec.descriptor.name, {"invented": True})))
    row["inputSchema"]["properties"] = {}
    with pytest.raises(RuntimeError, match="differs from the sealed catalog"):
        asyncio.run(adapter.verify_catalog())
