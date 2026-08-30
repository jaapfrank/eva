from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        if self.path != "/tools":
            self.send_error(404)
            return
        raw = json.dumps([CATALOG_ROW]).encode()
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
