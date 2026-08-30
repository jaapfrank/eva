"""Run one owner-bound official MCP effect through Eva's durable timeline."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import stat
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from candidate import (
    AssistToolAdmission,
    HomeAssistantOfficialMcpClient,
    HomeAssistantOfficialMcpConfig,
    HomeAssistantOfficialMcpEffectAdapter,
)
from eva.adapters.connectors.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantToolAdapter,
)
from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.application.orchestration import ToolRequest, VerifiedToolOrchestrator
from eva.contracts.base import JsonValue


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--database-dsn-file", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--allowed-principal-sha256", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--readback-entity-id", required=True)
    parser.add_argument("--operation-label", required=True)
    return parser.parse_args()


def _private_text(path: Path, label: str) -> str:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or details.st_nlink != 1:
        raise RuntimeError(f"{label} file is not a private regular file")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise RuntimeError(f"{label} file is accessible by other users")
    raw = path.read_bytes()
    if not 16 <= len(raw) <= 4_097:
        raise RuntimeError(f"{label} file size is invalid")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{label} file is invalid") from exc
    if not value or any(character.isspace() for character in value):
        raise RuntimeError(f"{label} value is invalid")
    return value


class _IndependentStateReader:
    def __init__(self, adapter: HomeAssistantToolAdapter) -> None:
        self._adapter = adapter

    async def state(self, entity_id: str) -> dict[str, JsonValue]:
        return await self._adapter.state(entity_id=entity_id)


def _verified_principal_sha256(
    engine: sa.Engine,
    scope: TimelineScope,
    allowed_sha256: str,
) -> str:
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL ROLE eva_runtime")
        connection.execute(
            sa.text("SELECT set_config('eva.tenant_id', :value, true)"),
            {"value": str(scope.tenant_id)},
        )
        connection.execute(
            sa.text("SELECT set_config('eva.installation_id', :value, true)"),
            {"value": str(scope.installation_id)},
        )
        principals = connection.execute(
            sa.text(
                "SELECT DISTINCT admitted.payload #>> "
                "'{data,context,principal_id}' AS principal_id "
                "FROM eva_core.operational_events AS admitted "
                "WHERE admitted.tenant_id = :tenant_id "
                "AND admitted.installation_id = :installation_id "
                "AND admitted.event_type = 'TOOL_REQUEST_ADMITTED' "
                "AND admitted.payload #>> '{data,context,action_name}' "
                "IN ('set_home_assistant_light', 'set_home_assistant_switch') "
                "AND EXISTS ("
                "SELECT 1 FROM eva_core.operational_events AS verified "
                "WHERE verified.tenant_id = admitted.tenant_id "
                "AND verified.installation_id = admitted.installation_id "
                "AND verified.thread_id = admitted.thread_id "
                "AND verified.event_type = 'STATE_VERIFIED')"
            ),
            {
                "installation_id": scope.installation_id,
                "tenant_id": scope.tenant_id,
            },
        ).scalars()
        matches = [
            principal
            for principal in principals
            if hashlib.sha256(str(principal).encode()).hexdigest() == allowed_sha256
        ]
    if len(matches) != 1:
        raise RuntimeError(
            "Home Assistant authority does not match a previously verified owner action"
        )
    return allowed_sha256


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{64}", arguments.allowed_principal_sha256) is None:
        raise RuntimeError("Home Assistant principal authority hash is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", arguments.operation_label):
        raise RuntimeError("Home Assistant operation label is invalid")

    token = _private_text(arguments.token_file, "Home Assistant token")
    dsn = _private_text(arguments.database_dsn_file, "database DSN")
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{arguments.tenant_id}"),
        installation_id=uuid5(
            NAMESPACE_URL,
            f"eva2:{arguments.tenant_id}:{arguments.installation_id}",
        ),
    )
    engine = sa.create_engine(sqlalchemy_postgres_dsn(dsn), pool_pre_ping=True)
    principal_sha256 = _verified_principal_sha256(
        engine, scope, arguments.allowed_principal_sha256
    )
    rest = HomeAssistantToolAdapter(HomeAssistantConfig(arguments.base_url, token))
    before = await rest.state(entity_id=arguments.readback_entity_id)
    if before.get("state") != "off":
        raise RuntimeError("safe MCP probe target is not already off")

    client = HomeAssistantOfficialMcpClient(
        HomeAssistantOfficialMcpConfig(arguments.base_url, token)
    )
    catalog = await asyncio.to_thread(client.catalog)
    tool = catalog.exact("HassTurnOff")
    admission = AssistToolAdmission(
        "HassTurnOff",
        "effect",
        tool.input_schema_sha256,
        required_arguments=("name",),
    )
    adapter = HomeAssistantOfficialMcpEffectAdapter(
        client,
        catalog_sha256=catalog.sha256,
        admissions=(admission,),
        state_reader=_IndependentStateReader(rest),
    )
    request = ToolRequest(
        thread_id=f"home-assistant-official-mcp:{arguments.operation_label}",
        operation_id=f"operation:home-assistant-official-mcp:{arguments.operation_label}",
        checkpoint_prefix=(
            "checkpoint:home-assistant-official-mcp:"
            + hashlib.sha256(arguments.operation_label.encode()).hexdigest()[:32]
        ),
        tool_name="home_assistant.assist.HassTurnOff",
        arguments={
            "mcp_arguments": {"name": arguments.target_name},
            "mcp_tool_name": "HassTurnOff",
            "readback_entity_id": arguments.readback_entity_id,
        },
        expected_proof={
            "entity_id": arguments.readback_entity_id,
            "state": "off",
        },
    )
    timeline = PostgresOperationalTimeline(engine, scope)
    admission_data: dict[str, JsonValue] = {
        "arguments": request.arguments,
        "catalog_sha256": catalog.sha256,
        "expected_proof": request.expected_proof,
        "principal_sha256": principal_sha256,
        "tool_name": request.tool_name,
        "tool_schema_sha256": tool.input_schema_sha256,
    }
    try:
        try:
            timeline.create_thread(
                thread_id=request.thread_id,
                checkpoint_id=f"{request.checkpoint_prefix}:admitted",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                data=admission_data,
                event_type="TOOL_REQUEST_ADMITTED",
                phase="admitted",
                status="active",
                operation_id=request.operation_id,
            )
        except CheckpointConflict:
            existing = timeline.events(request.thread_id)
            stored = existing[0].payload.get("data") if existing else None
            if stored != admission_data:
                raise RuntimeError("official MCP effect replay admission differs") from None
        first = await VerifiedToolOrchestrator(timeline, adapter).run(request)
        if first.status != "verified":
            raise RuntimeError("official MCP effect was not independently verified")
        second = await VerifiedToolOrchestrator(timeline, adapter).run(request)
        after = await rest.state(entity_id=arguments.readback_entity_id)
        events = timeline.events(request.thread_id)
    finally:
        engine.dispose()
    counts = {
        event_type: sum(event.event_type == event_type for event in events)
        for event_type in (
            "STATE_VERIFIED",
            "TOOL_EXECUTED",
            "TOOL_PREPARED",
            "TOOL_REQUEST_ADMITTED",
        )
    }
    return {
        "catalogSha256": catalog.sha256,
        "eventCounts": counts,
        "firstStatus": first.status,
        "initialState": before.get("state"),
        "operationLabelSha256": hashlib.sha256(
            arguments.operation_label.encode()
        ).hexdigest(),
        "principalAuthorized": True,
        "readbackSource": "independent_home_assistant_rest",
        "replayStatus": second.status,
        "resultState": after.get("state"),
        "status": "verified",
        "targetStored": False,
        "toolSchemaSha256": tool.input_schema_sha256,
    }


def main() -> None:
    print(json.dumps(asyncio.run(_run(_arguments())), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
