from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
from threading import Thread

import pytest
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_gateway import (
    McpGatewayProviderFactory,
    McpGatewayProviderSettings,
    parse_mcp_gateway_catalog,
)


SCHEMA = {
    "additionalProperties": False,
    "properties": {"message": {"maxLength": 80, "type": "string"}},
    "required": ["message"],
    "type": "object",
}
CATALOG_ROW = {
    "annotations": {"readOnlyHint": True},
    "deprecated": False,
    "enabled": True,
    "input_schema": SCHEMA,
    "integration_type": "MCP",
    "name": "proof-echo",
    "reachable": True,
    "request_type": "STREAMABLEHTTP",
    "visibility": "private",
}


def _provider_catalog(*, extra: dict[str, object] | None = None) -> str:
    tool: dict[str, object] = {
        "completion_guidance": "Use only the observed echo text.",
        "description": "Echo one short proof message.",
        "eva_name": "echo_proof",
        "execute_catalog_name": "proof-echo",
        "execute_catalog_sha256": canonical_sha256(
            mcp_gateway_catalog_binding(CATALOG_ROW)
        ),
        "execute_tool_name": "proof-echo",
        "input_schema": SCHEMA,
        "observe_catalog_name": "proof-echo",
        "observe_catalog_sha256": canonical_sha256(
            mcp_gateway_catalog_binding(CATALOG_ROW)
        ),
        "observe_tool_name": "proof-echo",
        "proof_bindings": [{"argument_name": "message", "observed_name": "text"}],
    }
    if extra:
        tool.update(extra)
    return json.dumps(
        {"contract": "EvaMcpGatewayProvider/v1", "tools": [tool]},
        sort_keys=True,
    )


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/tools?include_pagination=true&limit=50":
            self.send_error(404)
            return
        raw = json.dumps({"tools": [CATALOG_ROW], "nextCursor": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: object) -> None:
        return


@contextmanager
def _gateway() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _private_file(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o600)


def test_file_configured_gateway_contributes_tools_without_core_branch(
    monkeypatch: pytest.MonkeyPatch, private_tmp_path: Path
) -> None:
    token_file = private_tmp_path / "gateway-token"
    catalog_file = private_tmp_path / "gateway-catalog"
    _private_file(token_file, "private-test-token\n")
    _private_file(catalog_file, _provider_catalog())
    with _gateway() as base_url:
        monkeypatch.setenv("EVA_MCP_GATEWAY_URL", base_url)
        monkeypatch.setenv("EVA_MCP_GATEWAY_TOKEN_FILE", str(token_file))
        monkeypatch.setenv("EVA_MCP_GATEWAY_CATALOG_FILE", str(catalog_file))
        monkeypatch.setenv("EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP", "true")

        monkeypatch.setenv("EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST", hashlib.sha256(b"owner").hexdigest())
        settings = McpGatewayProviderFactory.load_settings()
        contribution = McpGatewayProviderFactory.build(
            settings, sa.create_engine("sqlite://")
        )

    assert isinstance(settings, McpGatewayProviderSettings)
    assert "private-test-token" not in repr(settings)
    assert [item.name for item in contribution.conversation_tools] == ["echo_proof"]
    assert contribution.external_tools[0].action_names == ("echo_proof",)
    assert settings.specs[0].configuration_sha256


def test_gateway_provider_rejects_inline_or_incomplete_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP",
        "EVA_MCP_GATEWAY_CATALOG_FILE",
        "EVA_MCP_GATEWAY_TOKEN",
        "EVA_MCP_GATEWAY_TOKEN_FILE",
        "EVA_MCP_GATEWAY_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert McpGatewayProviderFactory.load_settings() is None
    monkeypatch.setenv("EVA_MCP_GATEWAY_URL", "https://gateway.example.invalid")
    with pytest.raises(RuntimeError, match="incomplete"):
        McpGatewayProviderFactory.load_settings()
    monkeypatch.setenv("EVA_MCP_GATEWAY_TOKEN", "forbidden")
    with pytest.raises(RuntimeError, match="forbidden"):
        McpGatewayProviderFactory.load_settings()


def test_gateway_catalog_is_closed_and_proof_inputs_must_be_required() -> None:
    assert parse_mcp_gateway_catalog(_provider_catalog())[0].descriptor.name == (
        "echo_proof"
    )
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(_provider_catalog(extra={"unknown": True}))
    optional_schema = {
        "additionalProperties": False,
        "properties": {"message": {"type": "string"}},
        "required": [],
        "type": "object",
    }
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(
            _provider_catalog(extra={"input_schema": optional_schema})
        )


def test_v2_catalog_requires_and_hashes_explicit_argument_projections() -> None:
    payload = json.loads(_provider_catalog())
    payload["contract"] = "EvaMcpGatewayProvider/v2"
    tool = payload["tools"][0]
    tool["execute_argument_bindings"] = [
        {"argument_name": "message", "provider_name": "input"}
    ]
    tool["observe_argument_bindings"] = [
        {"argument_name": "message", "provider_name": "lookup"}
    ]

    spec = parse_mcp_gateway_catalog(json.dumps(payload))[0]

    assert spec.execute_argument_bindings[0].provider_name == "input"
    assert spec.observe_argument_bindings[0].provider_name == "lookup"
    v1 = parse_mcp_gateway_catalog(_provider_catalog())[0]
    assert v1.execute_argument_bindings[0].provider_name == "message"
    assert v1.configuration_sha256 == (
        "f71f3643ec67f9f8623484e7924058624e3dd9a6a6a9171970c92f7721f79f3b"
    )
    assert spec.configuration_sha256 != v1.configuration_sha256


def test_v1_catalog_binding_ignores_new_output_schema_but_v2_binds_it() -> None:
    row = {
        **CATALOG_ROW,
        "output_schema": {
            "additionalProperties": False,
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "type": "object",
        },
    }

    historical = mcp_gateway_catalog_binding(CATALOG_ROW)
    v1_with_new_wire_field = mcp_gateway_catalog_binding(
        row, contract="EvaMcpGatewayProvider/v1"
    )
    v2_with_new_wire_field = mcp_gateway_catalog_binding(
        row, contract="EvaMcpGatewayProvider/v2"
    )

    assert canonical_sha256(v1_with_new_wire_field) == canonical_sha256(historical)
    assert canonical_sha256(v2_with_new_wire_field) != canonical_sha256(historical)
    assert "output_schema" in v2_with_new_wire_field


def test_v2_catalog_rejects_missing_or_unknown_argument_projections() -> None:
    payload = json.loads(_provider_catalog())
    payload["contract"] = "EvaMcpGatewayProvider/v2"
    payload["tools"][0]["execute_argument_bindings"] = [
        {"argument_name": "missing", "provider_name": "message"}
    ]
    payload["tools"][0]["observe_argument_bindings"] = []
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(json.dumps(payload))

    payload = json.loads(_provider_catalog())
    payload["contract"] = "EvaMcpGatewayProvider/v2"
    payload["tools"][0]["execute_argument_bindings"] = []
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(json.dumps(payload))


def test_gateway_requires_explicit_private_principal_before_loading_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVA_MCP_GATEWAY_URL", "https://gateway.example.invalid")
    monkeypatch.setenv("EVA_MCP_GATEWAY_TOKEN_FILE", "/not-opened-token")
    monkeypatch.setenv("EVA_MCP_GATEWAY_CATALOG_FILE", "/not-opened-catalog")
    monkeypatch.setenv("EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP", "false")
    monkeypatch.delenv("EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError, match="principal allowlist"):
        McpGatewayProviderFactory.load_settings()


def test_gateway_private_workspace_refuses_other_person_before_storage_or_io() -> None:
    from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn
    settings = McpGatewayProviderSettings(
        base_url="https://gateway.example.invalid", bearer_token="test-token",
        allow_plain_http=False, specs=parse_mcp_gateway_catalog(_provider_catalog()),
        principal_sha256_allowlist=(hashlib.sha256(b"owner").hexdigest(),),
    )
    turn = ConversationTurn.private("tenant", "installation", "other", "chat", "message", "Read file", "2026-09-08T12:00:00Z")
    with pytest.raises(ConversationConflict, match="audience"):
        settings.authorize_turn(turn)
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    class Planner:
        action_names = ("echo_proof",)
        async def prepare(self, turn, call):
            pytest.fail("Unauthorized turn reached planning")
    tools = PostgresExternalConversationTools(
        sa.create_engine("sqlite://"), Planner(), object(), authorize_turn=settings.authorize_turn,
    )
    from eva.adapters.postgres.conversation_actions import PostgresConversationActions
    actions = PostgresConversationActions(sa.create_engine("sqlite://"), external_tools=(tools,))
    assert "echo_proof" not in actions.available_action_names(turn)
    assert "echo_proof" not in actions.continuation_action_names(turn)
    owner = ConversationTurn.private("tenant", "installation", "owner", "chat", "owner-message", "Read file", "2026-09-08T12:00:00Z")
    assert "echo_proof" in actions.available_action_names(owner)
    assert "echo_proof" in actions.continuation_action_names(owner)
    for index in (0, 1):
        with pytest.raises(ConversationConflict, match="audience"):
            tools.execute(turn, ConversationActionCall("call", "echo_proof", {"message": "private"}), step_index=index)
    settings.authorize_turn(ConversationTurn.private("tenant", "installation", "owner", "chat", "owner-message", "Read file", "2026-09-08T12:00:00Z"))


def test_gateway_workspace_rejects_owner_request_in_consented_group() -> None:
    from eva.application.conversation import ConversationConflict, ConversationTurn
    from eva.application.memory_audience import MemoryAudience, TelegramGroupConsentReference
    settings = McpGatewayProviderSettings(
        base_url="https://gateway.example.invalid", bearer_token="test-token",
        allow_plain_http=False, specs=parse_mcp_gateway_catalog(_provider_catalog()),
        principal_sha256_allowlist=(hashlib.sha256(b"owner").hexdigest(),),
    )
    audience = MemoryAudience.telegram_group(
        principal_id="owner", group_chat_id="-100",
        consent_ref=TelegramGroupConsentReference("owner", "-100", "telegram-group-consent:proof"),
    )
    turn = ConversationTurn(
        "tenant", "installation", "owner", "telegram:-100", "telegram:-100:1",
        "Read my private file", "2026-09-08T12:00:00Z", audience,
    )
    with pytest.raises(ConversationConflict, match="audience"):
        settings.authorize_turn(turn)


@pytest.mark.parametrize("valid_catalog", [True, False])
def test_uvicorn_factory_admits_gateway_inside_running_server_loop(valid_catalog):
    import asyncio
    from fastapi import FastAPI
    import uvicorn

    with _gateway() as base_url:
        settings = McpGatewayProviderSettings(
            base_url=base_url, bearer_token="test-token", allow_plain_http=True,
            specs=parse_mcp_gateway_catalog(_provider_catalog(
                extra=None if valid_catalog else {"execute_catalog_sha256": "0" * 64},
            )),
            principal_sha256_allowlist=(hashlib.sha256(b"owner").hexdigest(),),
        )
        admitted = []
        engine = sa.create_engine("sqlite://")

        def app_factory():
            # Uvicorn's real factory loader runs here within Server.serve's loop.
            asyncio.get_running_loop()
            contribution = McpGatewayProviderFactory.build(settings, engine)
            admitted.extend(tool.name for tool in contribution.conversation_tools)
            return FastAPI()

        async def load_in_server_loop():
            config = uvicorn.Config(app_factory, factory=True, log_config=None, ws="none")
            config.load()
            assert config.loaded

        try:
            if valid_catalog:
                asyncio.run(load_in_server_loop())
                assert admitted == ["echo_proof"]
            else:
                with pytest.raises(RuntimeError, match="tool differs from the sealed catalog"):
                    asyncio.run(load_in_server_loop())
                assert admitted == []
        finally:
            engine.dispose()


def _v3_catalog(operation_kind: str = "read") -> dict[str, object]:
    payload = json.loads(_provider_catalog())
    payload["contract"] = "EvaMcpGatewayProvider/v3"
    tool = payload["tools"][0]
    tool["operation_kind"] = operation_kind
    tool["execute_argument_bindings"] = [{"argument_name": "message", "provider_name": "message"}]
    tool["observe_argument_bindings"] = tool["execute_argument_bindings"]
    if operation_kind == "read":
        tool["proof_bindings"] = []
    return payload


def test_v3_catalog_requires_explicit_host_read_or_effect_authority() -> None:
    read = parse_mcp_gateway_catalog(json.dumps(_v3_catalog()))[0]
    effect = parse_mcp_gateway_catalog(json.dumps(_v3_catalog("effect")))[0]
    assert read.operation_kind == "read"
    assert read.proof_bindings == ()
    assert effect.operation_kind == "effect"
    assert effect.proof_bindings
    assert effect.configuration_sha256 != read.configuration_sha256
    # A provider readOnlyHint alone has never authorized the read route.
    assert parse_mcp_gateway_catalog(_provider_catalog())[0].operation_kind == "effect"
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(_provider_catalog(extra={"operation_kind": "read"}))


@pytest.mark.parametrize("value", [None, "automatic", "READ", 0, {}, []])
def test_v3_catalog_rejects_malformed_operation_kind(value: object) -> None:
    payload = _v3_catalog()
    payload["tools"][0]["operation_kind"] = value
    with pytest.raises(RuntimeError, match="operation kind"):
        parse_mcp_gateway_catalog(json.dumps(payload))


def test_v3_catalog_rejects_missing_operation_and_empty_effect_proof() -> None:
    payload = _v3_catalog()
    del payload["tools"][0]["operation_kind"]
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(json.dumps(payload))
    payload = _v3_catalog("effect")
    payload["tools"][0]["proof_bindings"] = []
    with pytest.raises(RuntimeError, match="proof bindings"):
        parse_mcp_gateway_catalog(json.dumps(payload))
    payload = _v3_catalog()
    payload["tools"][0]["observe_tool_name"] = "another-tool"
    with pytest.raises(RuntimeError, match="configuration"):
        parse_mcp_gateway_catalog(json.dumps(payload))


def test_v3_binds_output_schema_like_v2_without_changing_legacy_configuration_hash() -> None:
    payload = _v3_catalog("effect")
    v3 = parse_mcp_gateway_catalog(json.dumps(payload))[0]
    payload["contract"] = "EvaMcpGatewayProvider/v2"
    del payload["tools"][0]["operation_kind"]
    v2 = parse_mcp_gateway_catalog(json.dumps(payload))[0]
    assert v3.configuration_sha256 != v2.configuration_sha256
    assert v2.operation_kind == "effect"
    row = {**CATALOG_ROW, "output_schema": {"type": "object", "properties": {"content": {"type": "string"}}}}
    assert mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v3") == mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v2")
