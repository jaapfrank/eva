#!/usr/bin/env python3
"""Produce the container-internal TOOL01-v2 filesystem crash artifacts.

The host remains responsible for starting three fresh candidate containers,
issuing SIGKILL to p1, inspecting those containers and assembling the final
acceptance bundle.  This program only records authorities that the candidate
can observe itself: framework output, PostgreSQL timeline state and
ContextForge's per-tool counters.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable
from urllib import request as urllib_request

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
import sqlalchemy as sa

from eva.adapters.connectors.mcp_gateway import (
    McpGatewayToolAdapter,
    mcp_gateway_catalog_binding,
)
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.application.conversation import ConversationTurn, IdentityProfile
from eva.application.memory import PresenceMemory
from eva.application.orchestration import (
    AsyncToolPort,
    ToolDispatchResult,
    ToolProofObservation,
    ToolRequest,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.runtime_budget import RuntimeResourceBudget
from eva.contracts.memory import MemoryRecall
from eva.providers.runtime import (
    build_external_provider_bundle,
    load_external_provider_settings,
)


BASE_URL = "http://contextforge:4444"
STATE_ROOT = Path("/run/proof-state")
DATABASE_NAME = "eva2_living_disposable_tool01"
EXPECTED_MIGRATION = "4e_0007_delegated_private"
PROMPT = (
    'Schrijf exact "warme kopermaan" naar "tool01-proof.txt" in de beschikbare '
    "proefwerkmap en controleer daarna of het bestand exact zo is opgeslagen."
)
MODEL = "gpt-5.4-mini"
REASONING = "medium"
BUDGET = {
    "modelRequests": 8,
    "outputTokens": 1_500,
    "toolCalls": 8,
    "totalTokens": 8_000,
    "wallSeconds": 120,
}

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,159}$")
_NONCE = re.compile(r"^[0-9a-f]{12}$")


def _json_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
) -> object:
    body = None if payload is None else _canonical(payload)
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
        raw = response.read(2_097_153)
    if len(raw) > 2_097_152:
        raise RuntimeError("ContextForge response is too large")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("ContextForge returned invalid JSON") from error


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
    token = response.get("access_token") if isinstance(response, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or token != token.strip()
        or len(token.encode("utf-8")) > 8_192
    ):
        raise RuntimeError("ContextForge login returned no valid access token")
    return token


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(0o400)


def _migrate(engine: sa.Engine, url: sa.URL) -> None:
    with engine.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) != 160013:
            raise RuntimeError("filesystem crash proof requires PostgreSQL 16.13")
        relations = int(
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname NOT LIKE 'pg_%' "
                    "AND n.nspname <> 'information_schema' "
                    "AND c.relkind IN ('r','p','v','m','S','f')"
                )
            )
        )
        if relations:
            raise RuntimeError("filesystem crash proof database is not empty")
    config = AlembicConfig("/opt/eva/alembic.ini")
    rendered_url = url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", rendered_url)
    alembic_command.upgrade(config, "4b_0000_core_namespace")
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        config.attributes["connection"] = connection
        try:
            alembic_command.upgrade(config, "4b_0002_episode_atomicity")
        finally:
            config.attributes.pop("connection", None)
    alembic_command.upgrade(config, "head")


def _environment(name: str, pattern: re.Pattern[str], *, limit: int) -> str:
    value = os.environ.get(name, "")
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > limit
        or pattern.fullmatch(value) is None
    ):
        raise RuntimeError(f"{name} is invalid")
    return value


def _run_id() -> str:
    return _environment("EVA_PROOF_RUN_ID", _IDENTIFIER, limit=160)


def _nonce() -> str:
    return _environment("EVA_PROOF_NONCE", _NONCE, limit=12)


def _container_id() -> str:
    return _environment("EVA_PROOF_CONTAINER_ID", _CONTAINER_ID, limit=64)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write_once(name: str, value: dict[str, Any]) -> None:
    raw = _canonical(value) + b"\n"
    target = STATE_ROOT / name
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(STATE_ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_state(name: str) -> dict[str, Any]:
    raw = (STATE_ROOT / name).read_bytes()
    if not raw or len(raw) > 1_000_000:
        raise RuntimeError(f"{name} size is invalid")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} is not a JSON object")
    return value


def _state_root(*, require_empty: bool) -> None:
    STATE_ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
    status = STATE_ROOT.stat()
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) & 0o077
    ):
        raise RuntimeError("proof state directory is not private")
    if require_empty and any(STATE_ROOT.iterdir()):
        raise RuntimeError("filesystem crash proof state is not empty")


def _database_url(password_file: Path) -> sa.URL:
    return sa.URL.create(
        "postgresql+psycopg",
        username="postgres",
        password=password_file.read_text(encoding="utf-8"),
        host="proof-postgres",
        port=5432,
        database=DATABASE_NAME,
    )


def _assert_existing_database(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) != 160013:
            raise RuntimeError("filesystem crash proof requires PostgreSQL 16.13")
        versions = tuple(
            connection.scalars(
                sa.text(
                    "SELECT version_num FROM public.eva_alembic_version "
                    "ORDER BY version_num"
                )
            )
        )
        if versions != (EXPECTED_MIGRATION,):
            raise RuntimeError("filesystem crash proof schema differs")


def _field(row: dict[str, Any], snake: str, camel: str) -> Any:
    return row.get(snake, row.get(camel))


def _discovered_tools(token: str, nonce: str) -> dict[str, dict[str, Any]]:
    raw = _json_request(BASE_URL, "GET", "/tools", token=token)
    if not isinstance(raw, list) or len(raw) > 1_024:
        raise RuntimeError("ContextForge catalog is invalid")
    roles = {
        "effect": "write_file",
        "readback": "read_text_file",
        "distractor": "echo",
    }
    selected: dict[str, dict[str, Any]] = {}
    for role, original_name in roles.items():
        matches = [
            row
            for row in raw
            if isinstance(row, dict)
            and _field(row, "original_name", "originalName") == original_name
        ]
        if len(matches) != 1:
            raise RuntimeError(f"ContextForge {role} tool is absent or duplicated")
        row = matches[0]
        name = row.get("name")
        provider_id = _field(row, "gateway_slug", "gatewaySlug")
        schema = _field(row, "input_schema", "inputSchema")
        if (
            not isinstance(name, str)
            or _IDENTIFIER.fullmatch(name) is None
            or not isinstance(provider_id, str)
            or _IDENTIFIER.fullmatch(provider_id) is None
            or not provider_id.endswith(f"_{nonce}")
            or not name.startswith(f"{provider_id}_")
            or not isinstance(schema, dict)
            or not isinstance(row.get("description"), str)
            or not row["description"].strip()
        ):
            raise RuntimeError(f"ContextForge {role} tool authority is invalid")
        selected[role] = row
    if _field(selected["effect"], "gateway_id", "gatewayId") != _field(
        selected["readback"], "gateway_id", "gatewayId"
    ) or _field(selected["effect"], "gateway_id", "gatewayId") == _field(
        selected["distractor"], "gateway_id", "gatewayId"
    ):
        raise RuntimeError("ContextForge provider separation differs")
    return selected


def _catalog_artifact(
    tools: dict[str, dict[str, Any]], *, container_id: str, nonce: str, run_id: str
) -> dict[str, Any]:
    rows = []
    discovery_rows = []
    for role in ("effect", "readback", "distractor"):
        row = tools[role]
        schema = _field(row, "input_schema", "inputSchema")
        rows.append(
            {
                "description": row["description"],
                "inputSchema": schema,
                "name": row["name"],
                "providerId": _field(row, "gateway_slug", "gatewaySlug"),
                "role": role,
            }
        )
        discovery_rows.append(
            mcp_gateway_catalog_binding(row, contract="EvaMcpGatewayProvider/v2")
        )
    return {
        "contract": "EvaTool01RuntimeCatalog/v2",
        "discoveredByContainerId": container_id,
        "discoveryReceiptSha256": canonical_sha256(discovery_rows),
        "nonce": nonce,
        "runId": run_id,
        "tools": rows,
    }


def _configure_provider(
    token: str, tools: dict[str, dict[str, Any]]
) -> tuple[str, str, dict[str, str]]:
    marker_path = "tool01-proof.txt"
    marker_content = "warme kopermaan"
    effect = tools["effect"]
    readback = tools["readback"]
    distractor = tools["distractor"]
    effect_name = str(effect["name"])
    readback_name = str(readback["name"])
    distractor_name = str(distractor["name"])
    effect_sha = canonical_sha256(
        mcp_gateway_catalog_binding(effect, contract="EvaMcpGatewayProvider/v2")
    )
    readback_sha = canonical_sha256(
        mcp_gateway_catalog_binding(readback, contract="EvaMcpGatewayProvider/v2")
    )
    distractor_sha = canonical_sha256(
        mcp_gateway_catalog_binding(distractor, contract="EvaMcpGatewayProvider/v2")
    )
    eva_schema = {
        "additionalProperties": False,
        "properties": {
            "content": {"const": marker_content, "type": "string"},
            "path": {"const": marker_path, "type": "string"},
        },
        "required": ["content", "path"],
        "type": "object",
    }
    catalog = {
        "contract": "EvaMcpGatewayProvider/v2",
        "tools": [
            {
                "completion_guidance": (
                    "Bevestig kort wat exact is opgeslagen, maar alleen nadat de "
                    "onafhankelijke teruglezing dit heeft bewezen."
                ),
                "description": (
                    "Schrijf de gevraagde tekst naar het gevraagde bestand in de "
                    "beschikbare proefwerkmap."
                ),
                "eva_name": effect_name,
                "execute_argument_bindings": [
                    {"argument_name": "content", "provider_name": "content"},
                    {"argument_name": "path", "provider_name": "path"},
                ],
                "execute_catalog_name": effect_name,
                "execute_catalog_sha256": effect_sha,
                "execute_tool_name": effect_name,
                "input_schema": eva_schema,
                "observe_argument_bindings": [
                    {"argument_name": "path", "provider_name": "path"}
                ],
                "observe_catalog_name": readback_name,
                "observe_catalog_sha256": readback_sha,
                "observe_tool_name": readback_name,
                "proof_bindings": [
                    {"argument_name": "content", "observed_name": "content"}
                ],
            },
            {
                "completion_guidance": ("Meld alleen de exact teruggelezen echotekst."),
                "description": (
                    "Herhaal een opgegeven tekst zonder een bestand of andere "
                    "toestand te wijzigen."
                ),
                "eva_name": distractor_name,
                "execute_argument_bindings": [
                    {"argument_name": "message", "provider_name": "message"}
                ],
                "execute_catalog_name": distractor_name,
                "execute_catalog_sha256": distractor_sha,
                "execute_tool_name": distractor_name,
                "input_schema": {
                    "additionalProperties": False,
                    "properties": {
                        "message": {
                            "const": "tool01 distractor",
                            "type": "string",
                        }
                    },
                    "required": ["message"],
                    "type": "object",
                },
                "observe_argument_bindings": [
                    {"argument_name": "message", "provider_name": "message"}
                ],
                "observe_catalog_name": distractor_name,
                "observe_catalog_sha256": distractor_sha,
                "observe_tool_name": distractor_name,
                "proof_bindings": [
                    {"argument_name": "message", "observed_name": "text"}
                ],
            },
        ],
    }
    token_path = Path("/tmp/tool01-contextforge-token")
    catalog_path = Path("/tmp/tool01-contextforge-catalog.json")
    _write_private(token_path, token)
    _write_private(catalog_path, _canonical(catalog).decode("utf-8"))
    os.environ.update(
        {
            "EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP": "true",
            "EVA_MCP_GATEWAY_CATALOG_FILE": str(catalog_path),
            "EVA_MCP_GATEWAY_TOKEN_FILE": str(token_path),
            "EVA_MCP_GATEWAY_URL": BASE_URL,
        }
    )
    return (
        marker_path,
        marker_content,
        {
            "distractor": distractor_sha,
            "effect": effect_sha,
            "readback": readback_sha,
        },
    )


def _counter_server(token: str, tool_ids: set[str]) -> str:
    configured = os.environ.get("EVA_PROOF_COUNTER_SERVER_ID", "").strip()
    if configured:
        raw = _json_request(BASE_URL, "GET", f"/servers/{configured}", token=token)
        rows = [raw] if isinstance(raw, dict) else []
    else:
        raw = _json_request(BASE_URL, "GET", "/servers?limit=0", token=token)
        rows = raw if isinstance(raw, list) else []
    if not rows:
        raise RuntimeError("ContextForge server catalog is invalid")
    matches = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        associated = _field(row, "associated_tool_ids", "associatedToolIds")
        if configured and row["id"] == configured:
            matches.append(row)
        elif (
            not configured
            and isinstance(associated, list)
            and set(associated) == tool_ids
        ):
            matches.append(row)
    if len(matches) != 1:
        raise RuntimeError("ContextForge counter server is absent or duplicated")
    server_id = matches[0]["id"]
    if configured and server_id != configured:
        raise RuntimeError("ContextForge counter server differs")
    return server_id


def _provider_counts(
    token: str,
    server_id: str,
    tools: dict[str, dict[str, Any]],
) -> dict[str, int]:
    rows = _json_request(
        BASE_URL,
        "GET",
        f"/servers/{server_id}/tools?include_metrics=true",
        token=token,
    )
    if not isinstance(rows, list):
        raise RuntimeError("ContextForge per-tool metrics are invalid")
    by_id = {
        row.get("id"): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    counts: dict[str, int] = {}
    for role, tool in tools.items():
        row = by_id.get(tool.get("id"))
        value = (
            None if row is None else _field(row, "execution_count", "executionCount")
        )
        metrics = None if row is None else row.get("metrics")
        total = (
            None
            if not isinstance(metrics, dict)
            else _field(metrics, "total_executions", "totalExecutions")
        )
        failed = (
            None
            if not isinstance(metrics, dict)
            else _field(metrics, "failed_executions", "failedExecutions")
        )
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or total != value
            or failed != 0
        ):
            raise RuntimeError(f"ContextForge {role} counter is invalid")
        counts[role] = value
    return counts


def _runtime_budget() -> RuntimeResourceBudget:
    values: dict[str, Any] = {
        "background_reflections_per_installation_day": 0,
        "background_reflections_per_person_day": 0,
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 8_000,
        "max_output_tokens": 1_500,
        "memory_bytes_per_group": 0,
        "memory_bytes_per_installation": 1_048_576,
        "memory_bytes_per_person": 1_048_576,
        "memory_bytes_per_project": 0,
        "memory_records_per_group": 0,
        "memory_records_per_installation": 100,
        "memory_records_per_person": 100,
        "memory_records_per_project": 0,
        "profile_context_tokens": 100,
        "recall_context_tokens": 2_000,
        "retention_days": 7,
        "retrieval_nodes": 20,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    payload = {"contract": "EvaInstallerBudget/v2", **values}
    return RuntimeResourceBudget(
        tenant_id="tool01-proof-tenant",
        installation_id="tool01-proof-installation",
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        **values,
    )


def _turn(nonce: str) -> ConversationTurn:
    return ConversationTurn.private(
        "tool01-proof-tenant",
        "tool01-proof-installation",
        "tool01-proof-person",
        "tool01-proof-conversation",
        f"tool01-proof-message-{nonce}",
        PROMPT,
        "2026-09-01T08:00:00Z",
    )


def _memory() -> PresenceMemory:
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="tool01-proof-recall",
        query_purpose="conversation_reply",
        tenant_id="tool01-proof-tenant",
        principal_id="tool01-proof-person",
        conversation_id="tool01-proof-conversation",
        project_id=None,
        requested_valid_at="2026-09-01T08:00:00Z",
        admitted_fact_ids_sha256=canonical_sha256([]),
        selected=[],
        excluded_reasons={},
        projection_version="tool01-proof-v1",
        scope_sha256=canonical_sha256({"scope": "tool01-proof"}),
        created_at="2026-09-01T08:00:00Z",
    )
    return PresenceMemory(recall, ())


def _identity() -> IdentityProfile:
    return IdentityProfile(
        "Eva",
        (
            "Een zorgvuldige digitale assistent die expliciete bestandstaken met "
            "de passende beschikbare gereedschappen uitvoert."
        ),
        (
            "Gebruik alleen een gereedschap waarvan de beschrijving echt bij de "
            "opdracht past.",
            "Claim opslag pas nadat de onafhankelijke teruglezing haar bevestigt.",
        ),
        80,
    )


def _model(conversation_tools: tuple[Any, ...]) -> PydanticConversationModel:
    return PydanticConversationModel(
        auth_file=Path("/run/eva/model-lease/auth.json"),
        model=MODEL,
        reasoning_effort=REASONING,
        resource_budget=_runtime_budget(),
        conversation_tools=conversation_tools,
    )


def _database_authority(engine: sa.Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        version = int(connection.scalar(sa.text("SHOW server_version_num")))
        head = connection.scalar(
            sa.text("SELECT version_num FROM public.eva_alembic_version")
        )
        name = connection.scalar(sa.text("SELECT current_database()"))
    if version != 160013 or head != EXPECTED_MIGRATION or name != DATABASE_NAME:
        raise RuntimeError("filesystem proof database authority differs")
    return {
        "alembicHead": head,
        "name": name,
        "postgresVersionNum": version,
    }


def _components(
    *, migrate: bool
) -> tuple[
    sa.Engine,
    Any,
    PostgresExternalConversationTools,
    dict[str, dict[str, Any]],
    dict[str, int],
    str,
    str,
    dict[str, str],
]:
    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    nonce = _nonce()
    tools = _discovered_tools(token, nonce)
    marker_path, marker_content, catalog_hashes = _configure_provider(token, tools)
    url = _database_url(Path("/run/secrets/postgres-password"))
    engine = sa.create_engine(url, pool_pre_ping=False)
    if migrate:
        _migrate(engine, url)
    else:
        _assert_existing_database(engine)
    bundle = build_external_provider_bundle(load_external_provider_settings(), engine)
    effect_name = str(tools["effect"]["name"])
    distractor_name = str(tools["distractor"]["name"])
    expected_names = [effect_name, distractor_name]
    if (
        bundle is None
        or [item.name for item in bundle.conversation_tools] != expected_names
    ):
        raise RuntimeError("MCP provider did not contribute the sealed tool choice")
    matches = [
        item for item in bundle.external_tools if effect_name in item.action_names
    ]
    if len(matches) != 1 or not isinstance(
        matches[0], PostgresExternalConversationTools
    ):
        raise RuntimeError("filesystem provider route uses another durable owner")
    if list(matches[0].action_names) != expected_names:
        raise RuntimeError("filesystem provider durable routes differ")
    server_id = _counter_server(token, {str(row["id"]) for row in tools.values()})
    counts = _provider_counts(token, server_id, tools)
    if counts["distractor"] != 0:
        raise RuntimeError("distractor tool was unexpectedly executed")
    return (
        engine,
        bundle,
        matches[0],
        tools,
        counts,
        marker_path,
        marker_content,
        catalog_hashes,
    )


def _event_rows(
    route: PostgresExternalConversationTools,
    turn: ConversationTurn,
    *,
    first_observers: tuple[str, ...],
) -> list[dict[str, Any]]:
    thread_id = route._thread_id(turn, route.action_names[0])  # noqa: SLF001
    events = route._timeline(turn).events(thread_id)  # noqa: SLF001
    if len(events) != len(first_observers):
        raise RuntimeError("durable event count differs")
    rows = []
    for event, observer in zip(events, first_observers, strict=True):
        if event.operation_id is None or event.payload_sha256 != _sha256(event.payload):
            raise RuntimeError("durable event authority differs")
        rows.append(
            {
                "eventType": event.event_type,
                "firstObservedByContainerId": observer,
                "operationId": event.operation_id,
                "payload": event.payload,
                "payloadSha256": event.payload_sha256,
                "sequence": event.sequence,
            }
        )
    return rows


def _snapshot(
    route: PostgresExternalConversationTools,
    turn: ConversationTurn,
    events: list[dict[str, Any]],
    *,
    container_id: str,
    receipt_sha256: str | None,
) -> dict[str, Any]:
    thread_id = route._thread_id(turn, route.action_names[0])  # noqa: SLF001
    state = route._timeline(turn).load(thread_id)  # noqa: SLF001
    projection = {
        "phase": state.phase,
        "revision": state.revision,
        "status": state.status,
    }
    return {
        "checkpointProjectionSha256": _sha256(projection),
        "containerId": container_id,
        "eventCount": len(events),
        "eventsSha256": _sha256(events),
        "phase": state.phase,
        "receiptSha256": receipt_sha256,
        "revision": state.revision,
        "status": state.status,
    }


def _request_from_events(
    route: PostgresExternalConversationTools,
    turn: ConversationTurn,
) -> ToolRequest:
    thread_id = route._thread_id(turn, route.action_names[0])  # noqa: SLF001
    events = route._timeline(turn).events(thread_id)  # noqa: SLF001
    if not events:
        raise RuntimeError("durable admission is absent")
    admission = route._event_data(events[0])  # noqa: SLF001
    return route._request_from_admission(  # noqa: SLF001
        turn, route.action_names[0], admission
    )


def _capture_provider_calls(
    route: PostgresExternalConversationTools,
) -> list[dict[str, Any]]:
    provider = route._provider  # noqa: SLF001
    if not isinstance(provider, McpGatewayToolAdapter):
        raise RuntimeError("filesystem route does not use the MCP gateway adapter")
    calls: list[dict[str, Any]] = []
    original = provider._call_tool  # noqa: SLF001

    def captured(
        tool_name: str,
        arguments: dict[str, Any],
        rpc_id: str,
    ) -> dict[str, Any]:
        output = original(tool_name, arguments, rpc_id)
        calls.append(
            {
                "arguments": dict(arguments),
                "callId": rpc_id,
                "toolName": tool_name,
            }
        )
        return output

    provider._call_tool = captured  # type: ignore[method-assign]  # noqa: SLF001
    return calls


def _provider_call(
    request: ToolRequest,
    captured_calls: list[dict[str, Any]],
    *,
    phase: str,
    container_id: str,
) -> dict[str, Any]:
    tool_name = (
        request.tool_name if phase == "execute" else request.observation_tool_name
    )
    arguments = (
        request.arguments if phase == "execute" else request.observation_arguments
    )
    if (
        tool_name is None
        or arguments is None
        or request.tool_configuration_sha256 is None
        or len(captured_calls) != 1
    ):
        raise RuntimeError("provider call binding is incomplete")
    captured = captured_calls[0]
    call_id = captured.get("callId")
    if (
        captured.get("toolName") != tool_name
        or captured.get("arguments") != arguments
        or not isinstance(call_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", call_id) is None
    ):
        raise RuntimeError("captured MCP provider call differs from admission")
    return {
        "argumentsSha256": _sha256(arguments),
        "callId": call_id,
        "containerId": container_id,
        "kind": phase,
        "operationId": request.operation_id,
        "toolConfigurationSha256": request.tool_configuration_sha256,
        "toolName": tool_name,
    }


def _counter_snapshot(
    counts: dict[str, int], *, phase: str, container_id: str, nonce: str
) -> dict[str, Any]:
    return {
        "containerId": container_id,
        "executeCalls": counts["effect"],
        "observeCalls": counts["readback"],
        "phase": phase,
        "providerInstanceId": f"contextforge_{nonce}",
    }


class _BlockAfterRealExecute(AsyncToolPort):
    """Expose the real post-provider/pre-TOOL_EXECUTED SIGKILL window."""

    def __init__(
        self,
        delegate: AsyncToolPort,
        durable_state: Callable[[ToolRequest], dict[str, Any]],
    ) -> None:
        self._delegate = delegate
        self._durable_state = durable_state

    async def execute(self, request: ToolRequest) -> ToolDispatchResult:
        await self._delegate.execute(request)
        _write_once("effect-returned.json", self._durable_state(request))
        print(
            json.dumps(
                {
                    "contract": "EvaTool01V2KillWindow/v1",
                    "phase": "effect_returned_waiting_for_sigkill",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        await asyncio.Event().wait()
        raise AssertionError("SIGKILL wait unexpectedly returned")

    async def observe(
        self,
        request: ToolRequest,
        dispatch: ToolDispatchResult | None,
    ) -> ToolProofObservation:
        return await self._delegate.observe(request, dispatch)


def _history_sha256(turn: ConversationTurn, call: Any) -> str:
    return canonical_sha256(
        {
            "call": {
                "arguments": call.arguments,
                "callId": call.call_id,
                "name": call.name,
            },
            "prompt": turn.grounded_text,
        }
    )


def _start() -> int:
    _state_root(require_empty=True)
    run_id = _run_id()
    nonce = _nonce()
    container_id = _container_id()
    image_id = _environment("EVA_PROOF_IMAGE_ID", _IMAGE_ID, limit=71)
    source_commit = _environment("EVA_PROOF_SOURCE_COMMIT", _GIT_COMMIT, limit=40)
    (
        engine,
        bundle,
        route,
        tools,
        before,
        marker_path,
        marker_content,
        _catalog_hashes,
    ) = _components(migrate=True)
    if before != {"effect": 0, "readback": 0, "distractor": 0}:
        raise RuntimeError("filesystem provider counters did not start at zero")
    catalog = _catalog_artifact(
        tools, container_id=container_id, nonce=nonce, run_id=run_id
    )
    _write_once(
        "candidateAuthority.json",
        {
            "contract": "EvaTool01CandidateAuthority/v2",
            "imageId": image_id,
            "proofHarnessSha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "runId": run_id,
            "sourceCommitLabel": source_commit,
        },
    )
    _write_once("catalog.json", catalog)
    turn = _turn(nonce)
    selected = _model(bundle.conversation_tools).generate(
        turn,
        _identity(),
        _memory(),
        (),
        available_actions=(
            str(tools["effect"]["name"]),
            str(tools["distractor"]["name"]),
        ),
    )
    call = selected.action_call
    expected_arguments = {"content": marker_content, "path": marker_path}
    if (
        call is None
        or call.name != tools["effect"]["name"]
        or call.arguments != expected_arguments
    ):
        raise RuntimeError("model did not select the exact filesystem comfort action")
    history_sha256 = _history_sha256(turn, call)
    request_binding = {
        "callId": call.call_id,
        "historySha256": history_sha256,
        "messageId": turn.message_id,
        "operationId": route._operation_id(turn, call.name),  # noqa: SLF001
        "toolName": call.name,
    }
    _write_once(
        "p1-start.json",
        {
            "catalogSha256": _sha256(catalog),
            "containerId": container_id,
            "contract": "EvaTool01P1Start/v1",
            "providerBefore": _counter_snapshot(
                before,
                phase="beforeExecute",
                container_id=container_id,
                nonce=nonce,
            ),
            "requestBinding": request_binding,
            "usage": selected.usage.to_wire(),
        },
    )

    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    server_id = _counter_server(token, {str(row["id"]) for row in tools.values()})
    provider_calls = _capture_provider_calls(route)

    def post_execute(request: ToolRequest) -> dict[str, Any]:
        after = _provider_counts(token, server_id, tools)
        if after != {"effect": 1, "readback": 0, "distractor": 0}:
            raise RuntimeError("filesystem execute counter differs")
        events = _event_rows(route, turn, first_observers=(container_id, container_id))
        if [event["eventType"] for event in events] != [
            "TOOL_REQUEST_ADMITTED",
            "TOOL_PREPARED",
        ]:
            raise RuntimeError("filesystem SIGKILL timeline differs")
        snapshot = _snapshot(
            route,
            turn,
            events,
            container_id=container_id,
            receipt_sha256=None,
        )
        if (
            snapshot["phase"] != "tool_prepared"
            or snapshot["status"] != "waiting_for_proof"
            or snapshot["revision"] != 2
        ):
            raise RuntimeError("filesystem SIGKILL window is not sealed")
        return {
            "contract": "EvaTool01P1EffectReturned/v1",
            "events": events,
            "providerAfterExecute": _counter_snapshot(
                after,
                phase="afterExecuteBeforeKill",
                container_id=container_id,
                nonce=nonce,
            ),
            "providerCall": _provider_call(
                request,
                provider_calls,
                phase="execute",
                container_id=container_id,
            ),
            "snapshot": snapshot,
        }

    route._provider = _BlockAfterRealExecute(  # noqa: SLF001
        route._provider,
        post_execute,  # noqa: SLF001
    )
    route.execute(turn, call)
    engine.dispose()
    raise RuntimeError("p1 returned instead of being killed")


def _failure_category(error: Exception) -> str:
    return {
        "filesystem provider counters did not start at zero": "provider_baseline_differs",
        "model did not select the exact filesystem comfort action": (
            "model_selection_differs"
        ),
        "filesystem execute counter differs": "provider_execute_count_differs",
        "filesystem SIGKILL timeline differs": "durable_timeline_differs",
        "filesystem SIGKILL window is not sealed": "durable_window_not_sealed",
        "provider call binding is incomplete": "provider_binding_incomplete",
        "captured MCP provider call differs from admission": (
            "provider_call_differs"
        ),
    }.get(str(error), "unexpected_runtime_error")


def _failure_stage() -> str:
    if (STATE_ROOT / "p1-start.json").is_file():
        return "after_model_selection"
    if (STATE_ROOT / "catalog.json").is_file():
        return "model_selection"
    return "setup"


def _resume() -> int:
    _state_root(require_empty=False)
    run_id = _run_id()
    nonce = _nonce()
    container_id = _container_id()
    start = _read_state("p1-start.json")
    effect = _read_state("effect-returned.json")
    catalog = _read_state("catalog.json")
    if (
        start.get("catalogSha256") != _sha256(catalog)
        or catalog.get("runId") != run_id
        or catalog.get("nonce") != nonce
    ):
        raise RuntimeError("p2 catalog binding differs")
    (
        engine,
        bundle,
        route,
        tools,
        before,
        _marker_path,
        _marker_content,
        _catalog_hashes,
    ) = _components(migrate=False)
    if before != {"effect": 1, "readback": 0, "distractor": 0}:
        raise RuntimeError("p2 provider baseline differs")
    turn = _turn(nonce)
    request = _request_from_events(route, turn)
    provider_calls = _capture_provider_calls(route)
    receipt = route.pending(turn)
    if receipt is None or receipt.output.get("status") != "verified":
        raise RuntimeError("p2 did not resume the durable filesystem receipt")
    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    server_id = _counter_server(token, {str(row["id"]) for row in tools.values()})
    after = _provider_counts(token, server_id, tools)
    if after != {"effect": 1, "readback": 1, "distractor": 0}:
        raise RuntimeError("p2 readback counter differs")
    receipt_sha256 = canonical_sha256(receipt.output)
    events = _event_rows(
        route,
        turn,
        first_observers=(
            effect["events"][0]["firstObservedByContainerId"],
            effect["events"][1]["firstObservedByContainerId"],
            container_id,
        ),
    )
    if [event["eventType"] for event in events] != [
        "TOOL_REQUEST_ADMITTED",
        "TOOL_PREPARED",
        "STATE_VERIFIED",
    ]:
        raise RuntimeError("p2 durable timeline differs")
    snapshot = _snapshot(
        route,
        turn,
        events,
        container_id=container_id,
        receipt_sha256=receipt_sha256,
    )
    completed = _model(bundle.conversation_tools).generate(
        turn,
        _identity(),
        _memory(),
        (),
        completed_action=receipt,
    )
    if completed.text is None or "warme kopermaan" not in completed.text.casefold():
        raise RuntimeError("p2 final answer omitted the verified file content")
    _write_once(
        "p2-resumed.json",
        {
            "containerId": container_id,
            "contract": "EvaTool01P2Resumed/v1",
            "events": events,
            "finalAnswerSha256": hashlib.sha256(
                completed.text.encode("utf-8")
            ).hexdigest(),
            "providerAfterResume": _counter_snapshot(
                after,
                phase="afterResume",
                container_id=container_id,
                nonce=nonce,
            ),
            "providerCall": _provider_call(
                request,
                provider_calls,
                phase="observe",
                container_id=container_id,
            ),
            "receiptSha256": receipt_sha256,
            "snapshot": snapshot,
            "usage": completed.usage.to_wire(),
        },
    )
    print(
        json.dumps(
            {
                "contract": "EvaTool01P2Complete/v1",
                "receiptSha256": receipt_sha256,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    engine.dispose()
    return 0


def _replay() -> int:
    _state_root(require_empty=False)
    run_id = _run_id()
    nonce = _nonce()
    container_id = _container_id()
    start = _read_state("p1-start.json")
    effect = _read_state("effect-returned.json")
    resumed = _read_state("p2-resumed.json")
    catalog = _read_state("catalog.json")
    (
        engine,
        _bundle,
        route,
        tools,
        before,
        _marker_path,
        _marker_content,
        _catalog_hashes,
    ) = _components(migrate=False)
    if before != {"effect": 1, "readback": 1, "distractor": 0}:
        raise RuntimeError("p3 provider baseline differs")
    turn = _turn(nonce)
    provider_calls = _capture_provider_calls(route)
    receipt = route.pending(turn)
    if receipt is None or receipt.output.get("status") != "verified":
        raise RuntimeError("p3 did not load the verified durable receipt")
    replay = route.execute(turn, receipt.call)
    token = _login(BASE_URL, Path("/run/secrets/gateway-password"))
    server_id = _counter_server(token, {str(row["id"]) for row in tools.values()})
    after = _provider_counts(token, server_id, tools)
    receipt_sha256 = canonical_sha256(receipt.output)
    if (
        replay != receipt
        or receipt_sha256 != resumed.get("receiptSha256")
        or after != before
        or provider_calls
    ):
        raise RuntimeError("p3 replay changed receipt or provider counters")
    p1_id = effect["snapshot"]["containerId"]
    p2_id = resumed["containerId"]
    events = _event_rows(
        route, turn, first_observers=(str(p1_id), str(p1_id), str(p2_id))
    )
    snapshot = _snapshot(
        route,
        turn,
        events,
        container_id=container_id,
        receipt_sha256=receipt_sha256,
    )
    binding = start.get("requestBinding")
    if not isinstance(binding, dict):
        raise RuntimeError("framework request binding is absent")
    trajectory = {
        "budget": BUDGET,
        "contract": "EvaTool01FrameworkTrajectory/v2",
        "events": [
            {
                "containerId": p1_id,
                "data": {"catalogSha256": _sha256(catalog)},
                "kind": "catalog_discovered",
                "sequence": 1,
            },
            {
                "containerId": p1_id,
                "data": binding,
                "kind": "deferred_requests",
                "sequence": 2,
            },
            {
                "containerId": p1_id,
                "data": {
                    "callId": binding.get("callId"),
                    "messageId": binding.get("messageId"),
                    "operationId": binding.get("operationId"),
                    "owner": "ConversationActionPort",
                    "toolName": binding.get("toolName"),
                },
                "kind": "action_port_execute",
                "sequence": 3,
            },
            {
                "containerId": p2_id,
                "data": {
                    "historySha256": binding.get("historySha256"),
                    "inputKind": "DeferredToolResults",
                    "receiptSha256": receipt_sha256,
                    "receiptSource": "durable_timeline",
                },
                "kind": "deferred_results",
                "sequence": 4,
            },
            {
                "containerId": p2_id,
                "data": {"receiptSha256": receipt_sha256},
                "kind": "final_answer",
                "sequence": 5,
            },
            {
                "containerId": container_id,
                "data": {
                    "messageId": binding.get("messageId"),
                    "operationId": binding.get("operationId"),
                },
                "kind": "replay_received",
                "sequence": 6,
            },
            {
                "containerId": container_id,
                "data": {"receiptSha256": receipt_sha256},
                "kind": "replay_result",
                "sequence": 7,
            },
        ],
        "model": MODEL,
        "prompt": PROMPT,
        "reasoning": REASONING,
        "runId": run_id,
    }
    timeline = {
        "contract": "EvaTool01DurableTimeline/v2",
        "database": _database_authority(engine),
        "events": events,
        "runId": run_id,
        "snapshots": {
            "afterExecuteBeforeKill": effect["snapshot"],
            "afterResume": resumed["snapshot"],
            "afterThirdProcessReplay": snapshot,
        },
    }
    provider = {
        "calls": [effect["providerCall"], resumed["providerCall"]],
        "contract": "EvaTool01ExternalProviderCounters/v2",
        "providerInstanceId": f"contextforge_{nonce}",
        "runId": run_id,
        "snapshots": [
            start["providerBefore"],
            effect["providerAfterExecute"],
            resumed["providerAfterResume"],
            _counter_snapshot(
                after,
                phase="afterThirdProcessReplay",
                container_id=container_id,
                nonce=nonce,
            ),
        ],
    }
    _write_once("frameworkTrajectory.json", trajectory)
    _write_once("durableTimeline.json", timeline)
    _write_once("providerCounters.json", provider)
    _write_once(
        "p3-replay.json",
        {
            "containerId": container_id,
            "contract": "EvaTool01P3Replay/v1",
            "providerCounts": after,
            "receiptSha256": receipt_sha256,
            "snapshot": snapshot,
        },
    )
    print(
        json.dumps(
            {
                "artifacts": [
                    "candidateAuthority.json",
                    "catalog.json",
                    "durableTimeline.json",
                    "frameworkTrajectory.json",
                    "providerCounters.json",
                ],
                "contract": "EvaTool01V2CandidateArtifacts/v1",
                "receiptSha256": receipt_sha256,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("start", "resume", "replay"))
    arguments = parser.parse_args()
    try:
        if arguments.phase == "start":
            return _start()
        if arguments.phase == "resume":
            return _resume()
        return _replay()
    except Exception as error:
        failure = {
            "category": _failure_category(error),
            "contract": "EvaTool01CandidateFailure/v1",
            "phase": arguments.phase,
            "stage": _failure_stage(),
        }
        try:
            _write_once("failure.json", failure)
        except Exception:
            pass
        print(json.dumps(failure, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
