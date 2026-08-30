#!/usr/bin/env python3
"""Prove one real non-home MCP provider through Eva's installed plugin route."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib import request as urllib_request

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
from eva.adapters.model.conversation import CodexConversationModel
from eva.application.conversation import ConversationTurn, IdentityProfile
from eva.application.memory import PresenceMemory
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall
from eva.providers.runtime import (
    build_external_provider_bundle,
    load_external_provider_settings,
)


TOOL_NAME = "eva-proof-echo-convert-time"
EVA_TOOL_NAME = "convert_time_zone"
FIXED_ARGUMENTS = {
    "source_timezone": "Europe/Amsterdam",
    "target_timezone": "Europe/Amsterdam",
    "time": "2026-08-30T12:34:56+02:00",
}


def _json_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
) -> object:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
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


def _login(base_url: str, password_file: Path) -> str:
    response = _json_request(
        base_url,
        "POST",
        "/auth/login",
        {
            "email": "admin@example.com",
            "password": password_file.read_text(encoding="utf-8"),
        },
    )
    if not isinstance(response, dict) or not isinstance(
        response.get("access_token"), str
    ):
        raise RuntimeError("ContextForge login returned no access token")
    return response["access_token"]


def _tool_row(base_url: str, token: str) -> dict[str, object]:
    rows = _json_request(base_url, "GET", "/tools", token=token)
    if not isinstance(rows, list):
        raise RuntimeError("ContextForge tool catalog is invalid")
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == TOOL_NAME]
    if len(matches) != 1:
        raise RuntimeError("real time tool is absent or duplicated")
    return matches[0]


def _raw_tool_output(
    base_url: str, token: str, name: str, arguments: dict[str, object]
) -> str:
    envelope = _json_request(
        base_url,
        "POST",
        "/rpc",
        {
            "id": "real-time-proof-inspection",
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"arguments": arguments, "name": name},
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
        or result.get("isError") is True
    ):
        raise RuntimeError("real time MCP output is invalid")
    return content[0]["text"]


def _request_count(base_url: str, token: str) -> int:
    raw = _raw_tool_output(base_url, token, "eva-proof-echo-get-stats", {})
    decoded = json.loads(raw)
    value = decoded.get("requests_handled") if isinstance(decoded, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("real time MCP server omitted its request count")
    return value


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(0o400)


def _configure_provider(base_url: str, token: str, row: dict[str, object]) -> str:
    catalog_sha256 = canonical_sha256(mcp_gateway_catalog_binding(row))
    schema = {
        "additionalProperties": False,
        "properties": {
            "source_timezone": {"const": "Europe/Amsterdam", "type": "string"},
            "target_timezone": {"const": "Europe/Amsterdam", "type": "string"},
            "time": {
                "const": FIXED_ARGUMENTS["time"],
                "type": "string",
            },
        },
        "required": ["source_timezone", "target_timezone", "time"],
        "type": "object",
    }
    catalog = {
        "contract": "EvaMcpGatewayProvider/v1",
        "tools": [
            {
                "completion_guidance": (
                    "Noem alleen de tijd die uit het gecontroleerde gereedschapsresultaat blijkt."
                ),
                "description": (
                    "Zet de vastgelegde ISO-datumtijd om via de echte externe tijdprovider."
                ),
                "eva_name": EVA_TOOL_NAME,
                "execute_catalog_name": TOOL_NAME,
                "execute_catalog_sha256": catalog_sha256,
                "execute_tool_name": TOOL_NAME,
                "input_schema": schema,
                "observe_catalog_name": TOOL_NAME,
                "observe_catalog_sha256": catalog_sha256,
                "observe_tool_name": TOOL_NAME,
                "proof_bindings": [
                    {"argument_name": "time", "observed_name": "text"}
                ],
            }
        ],
    }
    token_path = Path("/tmp/mcp-gateway-token")
    catalog_path = Path("/tmp/mcp-gateway-catalog.json")
    _write_private(token_path, token)
    _write_private(
        catalog_path,
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )
    os.environ.update(
        {
            "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true",
            "EVA_MCP_GATEWAY_CATALOG_FILE": str(catalog_path),
            "EVA_MCP_GATEWAY_TOKEN_FILE": str(token_path),
            "EVA_MCP_GATEWAY_URL": base_url,
        }
    )
    return catalog_sha256


def _database_url(password_file: Path) -> sa.URL:
    return sa.URL.create(
        "postgresql+psycopg",
        username="postgres",
        password=password_file.read_text(encoding="utf-8"),
        host="proof-postgres",
        port=5432,
        database="eva2_living_disposable_contextforge",
    )


def _migrate(engine: sa.Engine, url: sa.URL) -> None:
    with engine.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) != 160013:
            raise RuntimeError("real provider proof requires PostgreSQL 16.13")
        relations = int(
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
                    "AND c.relkind IN ('r','p','v','m','S','f')"
                )
            )
        )
        if relations:
            raise RuntimeError("real provider proof database is not empty")
    config = AlembicConfig("/opt/eva/alembic.ini")
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    alembic_command.upgrade(config, "4b_0000_core_namespace")
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        config.attributes["connection"] = connection
        try:
            alembic_command.upgrade(config, "4b_0002_episode_atomicity")
        finally:
            config.attributes.pop("connection", None)
    alembic_command.upgrade(config, "head")


def _memory() -> PresenceMemory:
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="real-time-proof-recall",
        query_purpose="conversation_reply",
        tenant_id="real-time-proof-tenant",
        principal_id="real-time-proof-person",
        conversation_id="real-time-proof-conversation",
        project_id=None,
        requested_valid_at="2026-08-30T20:20:00Z",
        admitted_fact_ids_sha256=canonical_sha256([]),
        selected=[],
        excluded_reasons={},
        projection_version="real-time-proof-v1",
        scope_sha256=canonical_sha256({"scope": "real-time-proof"}),
        created_at="2026-08-30T20:20:00Z",
    )
    return PresenceMemory(recall, ())


def main() -> int:
    base_url = "http://contextforge:4444"
    token = _login(base_url, Path("/run/secrets/gateway-password"))
    row = _tool_row(base_url, token)
    catalog_sha256 = _configure_provider(base_url, token, row)
    url = _database_url(Path("/run/secrets/postgres-password"))
    engine = sa.create_engine(url, pool_pre_ping=False)
    _migrate(engine, url)
    settings = load_external_provider_settings()
    bundle = build_external_provider_bundle(settings, engine)
    if bundle is None or [item.name for item in bundle.conversation_tools] != [EVA_TOOL_NAME]:
        raise RuntimeError("installed MCP provider did not contribute the sealed tool")
    model = CodexConversationModel(
        auth_file=Path("/run/eva/model-lease/auth.json"),
        transport_kind="openai_sdk",
        conversation_tools=bundle.conversation_tools,
    )
    turn = ConversationTurn.private(
        "real-time-proof-tenant",
        "real-time-proof-installation",
        "real-time-proof-person",
        "real-time-proof-conversation",
        "real-time-proof-message",
        (
            "Gebruik het beschikbare tijdgereedschap. Zet exact "
            "2026-08-30T12:34:56+02:00 om van Europe/Amsterdam naar "
            "Europe/Amsterdam en geef daarna alleen de gecontroleerde uitkomst."
        ),
        "2026-08-30T20:20:00Z",
    )
    identity = IdentityProfile(
        "Eva",
        "Een eerlijke digitale assistent die extern bewijs gebruikt.",
        ("Gebruik gereedschap alleen wanneer het resultaat echt nodig is.",),
        80,
    )
    selected = model.generate(
        turn,
        identity,
        _memory(),
        (),
        available_actions=(EVA_TOOL_NAME,),
    )
    call = selected.action_call
    if call is None or call.name != EVA_TOOL_NAME or call.arguments != FIXED_ARGUMENTS:
        raise RuntimeError("model did not select the exact real time tool call")
    route = next(item for item in bundle.external_tools if EVA_TOOL_NAME in item.action_names)
    before = _request_count(base_url, token)
    receipt = route.execute(turn, call)
    after_execution = _request_count(base_url, token)
    completed = model.generate(
        turn,
        identity,
        _memory(),
        (),
        completed_action=receipt,
    )
    engine.dispose()

    restarted_engine = sa.create_engine(url, pool_pre_ping=False)
    restarted_settings = load_external_provider_settings()
    restarted_bundle = build_external_provider_bundle(
        restarted_settings, restarted_engine
    )
    if restarted_bundle is None:
        raise RuntimeError("restarted installed MCP provider is absent")
    restarted_route = next(
        item
        for item in restarted_bundle.external_tools
        if EVA_TOOL_NAME in item.action_names
    )
    replay = restarted_route.execute(turn, call)
    after_replay = _request_count(base_url, token)
    restarted_engine.dispose()
    if receipt != replay or receipt.output.get("status") != "verified":
        raise RuntimeError("real provider receipt did not survive restart")
    if after_execution - before != 2 or after_replay - after_execution != 0:
        raise RuntimeError("real provider execution or replay call count differs")
    if completed.text is None or FIXED_ARGUMENTS["time"] not in completed.text:
        raise RuntimeError("model final answer omitted the verified time")
    print(
        json.dumps(
            {
                "candidate_image_id": os.environ["EVA_PROOF_IMAGE_ID"],
                "catalog_sha256": catalog_sha256,
                "contract": "EvaRealNonHomeMcpProviderProof/v1",
                "final_answer": completed.text,
                "gateway_calls_after_restart_replay": 0,
                "gateway_calls_for_execution_and_readback": 2,
                "runtime_provider_id": "mcp_gateway",
                "model_action": call.name,
                "model_provider": completed.provider,
                "postgres_version_num": 160013,
                "receipt_sha256": canonical_sha256(receipt.output),
                "receipt_status": receipt.output["status"],
                "source_commit": os.environ["EVA_PROOF_SOURCE_COMMIT"],
                "status": "passed",
                "tool_name": TOOL_NAME,
                "usage": completed.usage.to_wire(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
