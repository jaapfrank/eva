#!/usr/bin/env python3
"""Exercise one exact MCP tool server through the ContextForge candidate."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from typing import Any
from urllib import error, request as urllib_request

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayConfig,
    McpGatewayProofBinding,
    McpGatewayToolAdapter,
    McpGatewayToolSpec,
    mcp_gateway_catalog_binding,
)
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationToolDescriptor,
    ConversationTurn,
)
from eva.application.orchestration import ToolRequest
from eva.contracts.canonical_json import canonical_sha256


ECHO_SCHEMA = {
    "additionalProperties": False,
    "properties": {
        "message": {"type": "string"},
    },
    "required": ["message"],
    "type": "object",
}


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib_request.Request(
        base_url.rstrip("/") + path,
        data=body,
        method=method,
        headers=headers,
    )
    with urllib_request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _wait_healthy(base_url: str) -> None:
    for _ in range(90):
        try:
            if _request_json(base_url, "GET", "/health").get("status") == "healthy":
                return
        except (OSError, ValueError, error.HTTPError):
            pass
        time.sleep(1)
    raise RuntimeError("ContextForge did not become healthy")


def _login(base_url: str, password_file: Path) -> str:
    password = password_file.read_text()
    response = _request_json(
        base_url,
        "POST",
        "/auth/login",
        {"email": "admin@example.com", "password": password},
    )
    token = response.get("access_token") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("ContextForge login returned no token")
    return token


def _register_echo_server(base_url: str, token: str) -> None:
    gateways = _request_json(base_url, "GET", "/gateways", token=token)
    existing = next(
        (row for row in gateways if row.get("name") == "eva-proof-echo"), None
    )
    if existing is not None:
        if existing.get("visibility") != "public":
            gateway_id = existing.get("id")
            if not isinstance(gateway_id, str) or not gateway_id:
                raise RuntimeError("ContextForge gateway omitted its ID")
            _request_json(
                base_url,
                "PUT",
                f"/gateways/{gateway_id}",
                {"visibility": "public"},
                token,
            )
    else:
        _request_json(
            base_url,
            "POST",
            "/gateways",
            {
                "name": "eva-proof-echo",
                "transport": "STREAMABLEHTTP",
                "url": "http://proof-tool:9080/mcp",
                # "public" is registry visibility inside this authenticated,
                # sealed network. There is no host port or public route.
                "visibility": "public",
            },
            token,
        )

    # ContextForge stores discovered tools as separately scoped records. Expose
    # this synthetic tool to the gateway's model-facing RPC, still solely behind
    # the bearer token and internal Docker network.
    for _ in range(60):
        tools = _request_json(base_url, "GET", "/tools", token=token)
        matches = [row for row in tools if row.get("name") == "eva-proof-echo-echo"]
        if len(matches) == 1:
            row = matches[0]
            if row.get("visibility") != "public":
                tool_id = row.get("id")
                if not isinstance(tool_id, str) or not tool_id:
                    raise RuntimeError("ContextForge tool omitted its ID")
                _request_json(
                    base_url,
                    "PUT",
                    f"/tools/{tool_id}",
                    {"visibility": "public"},
                    token,
                )
            return
        time.sleep(1)
    raise RuntimeError("ContextForge did not scope the discovered echo tool")


def _echo_catalog(base_url: str, token: str) -> tuple[str, str, dict[str, Any]]:
    for _ in range(60):
        rows = _request_json(base_url, "GET", "/tools", token=token)
        matches = [
            row
            for row in rows
            if row.get("original_name", row.get("originalName")) == "echo"
            or row.get("name") == "eva-proof-echo-echo"
        ]
        if len(matches) == 1:
            row = matches[0]
            try:
                binding = mcp_gateway_catalog_binding(row)
            except RuntimeError as exc:
                safe_state = {
                    key: row.get(key)
                    for key in (
                        "deprecated",
                        "enabled",
                        "integration_type",
                        "name",
                        "reachable",
                        "request_type",
                    )
                }
                raise RuntimeError(
                    f"discovered echo tool is not executable: {safe_state}"
                ) from exc
            return str(row["name"]), canonical_sha256(binding), row
        time.sleep(1)
    raise RuntimeError("ContextForge did not discover exactly one echo tool")


def _raw_tool_output(
    base_url: str, token: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    envelope = _request_json(
        base_url,
        "POST",
        "/rpc",
        {
            "id": "contextforge-proof-inspection",
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"arguments": arguments, "name": tool_name},
        },
        token,
    )
    result = envelope.get("result") if isinstance(envelope, dict) else None
    content = result.get("content") if isinstance(result, dict) else None
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or not isinstance(content[0].get("text"), str)
    ):
        raise RuntimeError("ContextForge inspection tool returned invalid output")
    decoded = json.loads(content[0]["text"])
    if not isinstance(decoded, dict):
        raise RuntimeError("ContextForge inspection output is not an object")
    return decoded


def _server_request_count(base_url: str, token: str) -> int:
    output = _raw_tool_output(base_url, token, "eva-proof-echo-get-stats", {})
    value = output.get("requests_handled")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("MCP proof server omitted its request count")
    return value


def _migrate_empty_postgres(engine: sa.Engine, dsn: str) -> None:
    with engine.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) != 160013:
            raise RuntimeError("durable proof requires PostgreSQL 16.13")
        relations = int(
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname NOT LIKE 'pg_%' "
                    "AND n.nspname NOT IN ('information_schema') "
                    "AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                )
            )
        )
        if relations:
            raise RuntimeError("durable proof database is not empty")
    root = Path(__file__).resolve().parents[2]
    config = AlembicConfig(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    alembic_command.upgrade(config, "4b_0000_core_namespace")
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        config.attributes["connection"] = connection
        try:
            alembic_command.upgrade(config, "4b_0002_episode_atomicity")
        finally:
            config.attributes.pop("connection", None)
    alembic_command.upgrade(config, "head")


def _durable_proof(
    *,
    adapter: McpGatewayToolAdapter,
    config: McpGatewayConfig,
    spec: McpGatewayToolSpec,
    base_url: str,
    token: str,
    postgres_host: str,
    postgres_password: str,
) -> dict[str, Any]:
    if not postgres_password or len(postgres_password) > 1_024:
        raise RuntimeError("PostgreSQL proof password is invalid")
    database = "eva2_living_disposable_contextforge"
    url = sa.URL.create(
        "postgresql+psycopg",
        username="postgres",
        password=postgres_password,
        host=postgres_host,
        port=5432,
        database=database,
    )
    dsn = url.render_as_string(hide_password=False)
    engine = sa.create_engine(url, pool_pre_ping=False)
    _migrate_empty_postgres(engine, dsn)
    turn = ConversationTurn.private(
        "contextforge-proof-tenant",
        "contextforge-proof-installation",
        "contextforge-proof-person",
        "contextforge-proof-conversation",
        "contextforge-proof-message",
        "Echo de vaste bewijszin",
        "2026-08-30T19:45:00Z",
    )
    call = ConversationActionCall(
        "contextforge-proof-call", "echo_proof", {"message": "koperen kompas"}
    )
    before = _server_request_count(base_url, token)
    tools = PostgresExternalConversationTools(engine, adapter.planner(), adapter)
    receipt = tools.execute(turn, call)
    after_execution = _server_request_count(base_url, token)
    engine.dispose()

    restarted_engine = sa.create_engine(url, pool_pre_ping=False)
    restarted_adapter = McpGatewayToolAdapter(config, (spec,))
    restarted_tools = PostgresExternalConversationTools(
        restarted_engine, restarted_adapter.planner(), restarted_adapter
    )
    replay = restarted_tools.execute(turn, call)
    after_replay = _server_request_count(base_url, token)
    restarted_engine.dispose()
    if receipt.output.get("status") != "verified" or replay != receipt:
        raise RuntimeError("durable MCP receipt did not survive restart")
    provider_calls = after_execution - before
    replay_calls = after_replay - after_execution
    if provider_calls != 2 or replay_calls != 0:
        raise RuntimeError(
            "durable MCP request counts differ: "
            f"effect_readback={provider_calls}, replay={replay_calls}"
        )
    return {
        "gateway_calls_for_effect_and_readback": provider_calls,
        "gateway_calls_after_restart_replay": replay_calls,
        "postgres_version_num": 160013,
        "receipt_status": receipt.output["status"],
        "restart_receipt_equal": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--expected-catalog-sha256")
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--postgres-host")
    parser.add_argument("--postgres-password-file", type=Path)
    parser.add_argument("--postgres-proof-password")
    arguments = parser.parse_args()

    _wait_healthy(arguments.base_url)
    token = _login(arguments.base_url, arguments.password_file)
    _register_echo_server(arguments.base_url, token)
    tool_name, catalog_sha256, catalog_row = _echo_catalog(arguments.base_url, token)
    if arguments.catalog_only:
        all_tools = _request_json(arguments.base_url, "GET", "/tools", token=token)
        print(
            json.dumps(
                {
                    "catalog": {
                        key: catalog_row.get(key)
                        for key in (
                            "customName",
                            "custom_name",
                            "gatewaySlug",
                            "gateway_slug",
                            "name",
                            "originalName",
                            "original_name",
                            "executionCount",
                            "execution_count",
                            "visibility",
                        )
                    },
                    "catalog_sha256": catalog_sha256,
                    "tool_names": sorted(row.get("name") for row in all_tools),
                    "tool_stats": _raw_tool_output(
                        arguments.base_url,
                        token,
                        "eva-proof-echo-get-stats",
                        {},
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    if (
        arguments.expected_catalog_sha256 is not None
        and catalog_sha256 != arguments.expected_catalog_sha256
    ):
        raise RuntimeError("discovered MCP tool differs from the sealed catalog")
    invocation_name = tool_name

    spec = McpGatewayToolSpec(
        descriptor=ConversationToolDescriptor(
            name="echo_proof",
            description="Echo one fixed proof phrase through the generic MCP route.",
            input_schema=ECHO_SCHEMA,
        ),
        execute_tool_name=invocation_name,
        observe_tool_name=invocation_name,
        execute_catalog_name=tool_name,
        observe_catalog_name=tool_name,
        execute_catalog_sha256=catalog_sha256,
        observe_catalog_sha256=catalog_sha256,
        proof_bindings=(McpGatewayProofBinding("message", "text"),),
    )
    gateway_config = McpGatewayConfig(arguments.base_url, token, allow_plain_http=True)
    adapter = McpGatewayToolAdapter(gateway_config, (spec,))
    verified = asyncio.run(adapter.verify_catalog())
    request = ToolRequest(
        thread_id="contextforge-proof-thread",
        operation_id="contextforge-proof-operation",
        checkpoint_prefix="contextforge-proof-checkpoint",
        tool_name=invocation_name,
        arguments={"message": "koperen kompas"},
        expected_proof={"text": "koperen kompas"},
    )
    dispatch = asyncio.run(adapter.execute(request))
    observation = asyncio.run(adapter.observe(request, dispatch))
    durable = None
    if (
        arguments.postgres_host
        or arguments.postgres_password_file
        or arguments.postgres_proof_password
    ):
        passwords = [
            value
            for value in (
                arguments.postgres_password_file,
                arguments.postgres_proof_password,
            )
            if value is not None
        ]
        if not arguments.postgres_host or len(passwords) != 1:
            raise RuntimeError("PostgreSQL host and exactly one password are required")
        postgres_password = (
            arguments.postgres_password_file.read_text()
            if arguments.postgres_password_file is not None
            else arguments.postgres_proof_password
        )
        durable = _durable_proof(
            adapter=adapter,
            config=gateway_config,
            spec=spec,
            base_url=arguments.base_url,
            token=token,
            postgres_host=arguments.postgres_host,
            postgres_password=postgres_password,
        )
    print(
        json.dumps(
            {
                "catalog_sha256": catalog_sha256,
                "dispatch_accepted": dispatch.accepted,
                "durable": durable,
                "gateway_tool_count": len(verified),
                "observed": observation.observed,
                "observation_source": observation.evidence["source"],
                "tool_name": tool_name,
                "invocation_name": invocation_name,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
