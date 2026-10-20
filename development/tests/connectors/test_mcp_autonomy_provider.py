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
        if name.startswith(("EVA_MCP_", "EVA_BROWSER_")):
            monkeypatch.delenv(name)


def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(module.McpGatewayProviderFactory, "load_settings", lambda: None)
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

    def __init__(self, *args, private_owner_resolver=None):
        assert private_owner_resolver is None
        self.seen = []

    def authorize_turn(self, turn):
        if turn.principal_id != self.owner_principal_id or turn.audience.kind != "private_person":
            raise ConversationConflict("not private owner")
        self.seen.append(turn)

    def canonical_owner(self, turn):
        self.authorize_turn(turn)
        return self.owner_principal_id

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
    timeline = SimpleNamespace(load_with_events=lambda key: (None, (event,)))
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
    timeline.load_with_events = absent
    with pytest.raises(ConversationConflict, match="absent"):
        loader(source)
    def corrupt(key):
        raise RuntimeError("corrupt projection")
    timeline.load_with_events = corrupt
    with pytest.raises(RuntimeError, match="corrupt projection"):
        loader(source)


@pytest.mark.parametrize("with_browser", [False, True])
def test_factory_reuses_two_durable_ports_without_registration_io(monkeypatch, with_browser):
    monkeypatch.setitem(sys.modules, "eva.providers.mcp_owner_authority",
        SimpleNamespace(McpOwnerAuthority=Authority))
    records = []
    def registry(*args, canonical_owner, configured_catalog):
        assert callable(canonical_owner)
        assert configured_catalog is None
        records.append(args)
        return SimpleNamespace()
    monkeypatch.setattr(module, "PostgresMcpConnectorRegistry", registry)
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")
    settings = module.McpAutonomySettings("/private/mandate.json",
        McpGatewayConfig("https://gateway.example", "gateway-token"),
        McpRegistrationConfig("https://gateway.example", "registration-token"),
        browser_autostart=(module.BrowserAutostartConfig("https://browser.example/mcp",
            module.BrowserSessionClient("https://controller.example", "control-token"))
            if with_browser else None))
    contribution = module.McpAutonomyProviderFactory.build(settings, engine)
    names = {tool.name for tool in contribution.conversation_tools}
    assert {"connect_service", "discover_connector_tools", "use_connector_tool"} <= names
    assert len(contribution.external_tools) == 2
    browser = contribution.external_tools[1]._private._provider._browser_autostart
    if with_browser:
        assert browser.connector_id == module.connector_id_for(
            Authority.owner_key, "https://browser.example/mcp")
        assert browser.config is settings.browser_autostart
    else:
        assert browser is None
    assert records[0][0] is engine and records[0][1] == Authority.scope
    assert records[0][2] == "owner"
    assert all(port.available_for(source_turn()) for port in contribution.external_tools)
    from dataclasses import replace
    from tests.connectors.test_scoped_connector_results import household
    shared = replace(source_turn(), audience=household("owner"), project_id=None,
        conversation_id="household:home:conversation:test",
        message_id="household:home:conversation:test:message")
    offered = {name for port in contribution.external_tools if port.available_for(shared)
               for name in port.action_names}
    assert offered == {"use_connector_tool"}
    assert not contribution.external_tools[1].available_for(replace(shared, installation_id="other"))

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
        assert (method, path, payload) == ("GET", "/tools?include_pagination=true&limit=50", None)
        return {"tools": [row], "nextCursor": None}
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


def test_browser_autostart_requires_explicit_complete_trusted_configuration(monkeypatch, tmp_path):
    configured(monkeypatch, tmp_path)
    assert module.McpAutonomyProviderFactory.load_settings().browser_autostart is None
    monkeypatch.setenv("EVA_BROWSER_MCP_ENDPOINT", "https://browser.example/mcp")
    with pytest.raises(RuntimeError, match="autostart configuration is incomplete"):
        module.McpAutonomyProviderFactory.load_settings()
    token_file = tmp_path / "controller-token"
    token_file.write_text("synthetic-browser-control")
    token_file.chmod(0o600)
    monkeypatch.setenv("EVA_BROWSER_CONTROL_URL", "https://controller.example")
    monkeypatch.setenv("EVA_BROWSER_CONTROL_TOKEN_FILE", str(token_file))
    settings = module.McpAutonomyProviderFactory.load_settings()
    assert settings.browser_autostart.endpoint == "https://browser.example/mcp"
    assert settings.browser_autostart.client.origin == "https://controller.example"
    assert settings.browser_autostart.client.token == "synthetic-browser-control"
    assert "synthetic-browser-control" not in repr(settings)


def test_browserless_autonomy_uses_real_settings_without_dummy_catalog(monkeypatch,tmp_path):
    for name in ('EVA_MCP_GATEWAY_TOKEN_FILE','EVA_MCP_REGISTRATION_TOKEN_FILE'):
        path=tmp_path/name;path.write_text('private-'+name);path.chmod(0o600);monkeypatch.setenv(name,str(path))
    monkeypatch.setenv('EVA_MCP_GATEWAY_URL','http://10.254.80.1:44500')
    monkeypatch.setenv('EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP','true')
    monkeypatch.setenv('EVA_MCP_AUTONOMY_MANDATE_FILE',str(tmp_path/'mandate.json'))
    assert module.McpGatewayProviderFactory.load_settings() is None
    actual=module.McpAutonomyProviderFactory.load_settings()
    assert actual.configured_catalog is None and actual.gateway.base_url=='http://10.254.80.1:44500'
    monkeypatch.delenv('EVA_MCP_GATEWAY_URL')
    with pytest.raises(RuntimeError):module.McpAutonomyProviderFactory.load_settings()
    monkeypatch.delenv('EVA_MCP_AUTONOMY_MANDATE_FILE')
    with pytest.raises(RuntimeError):module.McpGatewayProviderFactory.load_settings()


def test_configured_browser_catalog_shares_direct_owner_session(monkeypatch):
    from eva.adapters.connectors.browser_autostart import direct_browser_owner_binding,managed_browser_session_id
    monkeypatch.setitem(sys.modules,'eva.providers.mcp_owner_authority',SimpleNamespace(McpOwnerAuthority=Authority))
    monkeypatch.setattr(module,'PostgresMcpConnectorRegistry',lambda *a,**k:SimpleNamespace(_configured_id=lambda:'configured-browser'))
    client=module.BrowserSessionClient('https://controller.example','private-token')
    catalog=SimpleNamespace(principal_sha256_allowlist=('a'*64,),base_url='https://gateway.example',
        specs=(SimpleNamespace(operation_kind='invoke',descriptor=SimpleNamespace(name='browser_snapshot')),),browser_client=client)
    settings=module.McpAutonomySettings('/private/mandate.json',McpGatewayConfig(catalog.base_url,'gateway-token'),
        McpRegistrationConfig(catalog.base_url,'registration-token'),configured_catalog=catalog,
        browser_autostart=module.BrowserAutostartConfig('https://browser.example/mcp',client))
    contribution=module.McpAutonomyProviderFactory.build(settings,sa.create_engine('postgresql+psycopg://unused:unused@localhost/unused'))
    browser=contribution.external_tools[1]._private._provider._browser_autostart
    direct=direct_browser_owner_binding('a'*64,catalog.base_url,client.origin)
    assert browser.matches('configured-browser') and browser.session_binding('configured-browser')==direct
    assert managed_browser_session_id('gateway-token',browser.session_binding('configured-browser'),7)==managed_browser_session_id('gateway-token',direct,7)
    assert browser.session_binding(browser.connector_id)==browser.connector_id
    assert not browser.matches('unrelated-connector')
