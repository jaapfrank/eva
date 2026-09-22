"""Owner-scoped immutable MCP bindings on the existing operational timeline.

Catalogs reach this store only after the host has admitted their authority. Tool
annotations are never interpreted here. Gateway credentials remain in the host
configuration; durable records bind its URL without storing its bearer token.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy.engine import Engine

from eva.adapters.connectors.mcp_gateway import McpGatewayConfig, McpGatewayToolSpec
from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineNotFound,
    TimelineScope,
    external_timeline_scope,
)
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.contracts.base import validate_json_value
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_discovery import McpConnectorNotFound, McpExecutionBinding
from eva.providers.mcp_gateway import parse_mcp_gateway_catalog


_CONTRACT = "EvaMcpConnectorRegistry/v1"
_CONNECTOR_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_Phase = Literal["execute", "observe"]


class PostgresMcpConnectorRegistry:
    """Append registration/revocation; never replace a stored configuration.

    ``authorize_turn`` checks the real owner turn. ``authorize_current`` accepts
    keyword-only ``phase`` and ``operation_kind`` and checks the current mandate
    even after a restart. It may permit observation of a previously admitted
    effect or the cached result of an admitted invocation after revocation;
    neither observation can start a new invocation. Both callbacks reject
    unauthorized access by raising (or explicitly returning False).
    """

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        owner_principal_id: str,
        gateway_config: McpGatewayConfig,
        authorize_turn: Callable[[ConversationTurn], object],
        authorize_current: Callable[..., object],
        *, canonical_owner: Callable[[ConversationTurn], str] | None = None,
    ) -> None:
        if not isinstance(owner_principal_id, str) or not owner_principal_id.strip():
            raise ValueError("MCP registry owner is required")
        if not isinstance(gateway_config, McpGatewayConfig):
            raise ValueError("MCP registry gateway configuration is invalid")
        if not callable(authorize_turn) or not callable(authorize_current):
            raise ValueError("MCP registry authorization callbacks are required")
        self._scope = scope
        self._owner = owner_principal_id
        self._canonical_owner = canonical_owner
        self._config = gateway_config
        self._authorize_turn = authorize_turn
        self._authorize_current = authorize_current
        self._timeline = PostgresOperationalTimeline(engine, scope)
        self._thread_id = "mcp-connectors:" + hashlib.sha256(
            owner_principal_id.encode("utf-8")
        ).hexdigest()

    def register(
        self,
        turn: ConversationTurn,
        connector_id: str,
        endpoint: str,
        gateway_id: str,
        catalog: dict[str, Any],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_owner(turn)
        self._require_current(phase="execute", operation_kind="effect")
        self._validate_connector_id(connector_id)
        if not isinstance(endpoint, str):
            raise ValueError("MCP registry endpoint is invalid")
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or len(endpoint) > 2048):
            raise ValueError("MCP endpoint must be an HTTP URL without credentials")
        if not isinstance(gateway_id, str) or not gateway_id.strip() or len(gateway_id) > 240:
            raise ValueError("MCP registry gateway id is invalid")
        catalog = self._copy_object(catalog)
        provenance = self._copy_object(provenance)
        specs = self._specs(catalog)
        registration = {
            "connector_id": connector_id,
            "endpoint": endpoint,
            "gateway_id": gateway_id,
            "catalog": catalog,
            "provenance": provenance,
            "gateway": self._gateway_identity(),
        }
        # The constructor secret is never copied into a durable JSON envelope,
        # including accidentally through provenance supplied by the host.
        if self._config.bearer_token in json.dumps(registration, ensure_ascii=False):
            raise ValueError("MCP registry data must not contain gateway credentials")
        record_sha = canonical_sha256(registration)
        registration["registration_sha256"] = record_sha
        registration["source"] = {
            "principal_id": turn.principal_id,
            "conversation_id": turn.conversation_id,
            "source_message_id": turn.message_id,
        }
        if turn.principal_id != self._owner:
            # Host-admitted canonical ownership is separate from immutable ingress.
            registration["source"]["canonical_owner_principal_id"] = self._owner
        hashes = {spec.configuration_sha256 for spec in specs}
        for _ in range(8):
            state, records = self._snapshot()
            existing = records.get(connector_id)
            if existing is not None:
                if existing["registration_sha256"] != record_sha:
                    raise ConversationConflict("MCP connector registration is immutable")
                self._require_active(existing)
                return self._summary(existing)
            for record in records.values():
                if hashes.intersection(
                    spec.configuration_sha256 for spec in self._specs(record["catalog"])
                ):
                    raise ConversationConflict("MCP configuration already belongs to another connector")
            try:
                self._append(state, "registered", {"registration": registration})
                return self._summary({**registration, "revoked": False})
            except CheckpointConflict:
                continue
        raise ConversationConflict("MCP registry changed concurrently; retry registration")

    def discover(self, turn: ConversationTurn, connector_id: str) -> dict[str, Any]:
        self._require_owner(turn)
        self._require_current(phase="execute", operation_kind="read")
        record = self._record(connector_id)
        self._require_active(record)
        self._require_gateway(record)
        return {"connector_id": connector_id, "tools": list(self._tools(record))}

    def resolve_for_turn(self, turn: ConversationTurn, tool_id: str) -> McpExecutionBinding:
        self._require_owner(turn)
        if not isinstance(tool_id, str) or ":" not in tool_id:
            raise ConversationConflict("MCP tool id is unknown")
        connector_id, digest = tool_id.rsplit(":", 1)
        record = self._record(connector_id)
        self._require_active(record)
        for spec in self._specs(record["catalog"]):
            if spec.configuration_sha256 == digest:
                self._require_current(phase="execute", operation_kind=spec.operation_kind)
                return self._binding(record, spec)
        raise ConversationConflict("MCP tool id is unknown")

    def resolve_configuration(self, digest: str, *, phase: _Phase) -> McpExecutionBinding:
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ConversationConflict("MCP configuration hash is invalid")
        if phase not in {"execute", "observe"}:
            raise ValueError("MCP execution phase is invalid")
        _, records = self._snapshot()
        matches = [
            (record, spec)
            for record in records.values()
            for spec in self._specs(record["catalog"])
            if spec.configuration_sha256 == digest
        ]
        if len(matches) != 1:
            raise ConversationConflict("MCP configuration has no unique immutable binding")
        record, spec = matches[0]
        if phase != "observe" or spec.operation_kind not in {"effect", "invoke"}:
            self._require_active(record)
        self._require_current(phase=phase, operation_kind=spec.operation_kind)
        return self._binding(record, spec)

    def revoke(self, turn: ConversationTurn, connector_id: str) -> dict[str, Any]:
        self._require_owner(turn)
        self._validate_connector_id(connector_id)
        for _ in range(8):
            state, records = self._snapshot()
            record = records.get(connector_id)
            if record is None:
                raise ConversationConflict("MCP connector is unknown")
            if record["revoked"]:
                return self._summary(record)
            try:
                self._append(state, "revoked", {
                    "connector_id": connector_id,
                    "registration_sha256": record["registration_sha256"],
                    "source_message_id": turn.message_id,
                    "principal_id": turn.principal_id,
                })
                return self._summary({**record, "revoked": True})
            except CheckpointConflict:
                continue
        raise ConversationConflict("MCP registry changed concurrently; retry revocation")

    def _require_owner(self, turn: ConversationTurn) -> None:
        owner = (turn.principal_id if self._canonical_owner is None
                 else self._canonical_owner(turn))
        if (owner != self._owner or turn.audience.kind != "private_person"
                or turn.audience.principal_id != turn.principal_id
                or external_timeline_scope(
                    turn.tenant_id, turn.installation_id,
                    missing_error="MCP registry scope is required",
                ) != self._scope):
            raise ConversationConflict("MCP registry is unavailable for this owner or installation")
        if self._authorize_turn(turn) is False:
            raise ConversationConflict("MCP registry turn authorization was denied")

    def _require_current(self, *, phase: _Phase, operation_kind: str) -> None:
        if self._authorize_current(phase=phase, operation_kind=operation_kind) is False:
            raise ConversationConflict("MCP registry mandate is not active")

    @staticmethod
    def _validate_connector_id(connector_id: str) -> None:
        if not isinstance(connector_id, str) or _CONNECTOR_ID.fullmatch(connector_id) is None:
            raise ValueError("MCP connector id is invalid")

    @staticmethod
    def _copy_object(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("MCP registry data must be a JSON object")
        validate_json_value(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 2_097_152:
            raise ValueError("MCP registry object is too large")
        return json.loads(encoded)

    @staticmethod
    def _specs(catalog: dict[str, Any]) -> tuple[McpGatewayToolSpec, ...]:
        return parse_mcp_gateway_catalog(json.dumps(catalog, ensure_ascii=False))

    def _gateway_identity(self) -> dict[str, Any]:
        return {"base_url": self._config.base_url, "allow_plain_http": self._config.allow_plain_http}

    def _require_gateway(self, record: dict[str, Any]) -> None:
        if record["gateway"] != self._gateway_identity():
            raise ConversationConflict("MCP gateway differs from the immutable registration")

    @staticmethod
    def _require_active(record: dict[str, Any]) -> None:
        if record["revoked"]:
            raise ConversationConflict("MCP connector is revoked")

    def _record(self, connector_id: str) -> dict[str, Any]:
        self._validate_connector_id(connector_id)
        _, records = self._snapshot()
        if connector_id not in records:
            raise McpConnectorNotFound("MCP connector is unknown")
        return records[connector_id]

    def _binding(self, record: dict[str, Any], spec: McpGatewayToolSpec) -> McpExecutionBinding:
        self._require_gateway(record)
        connector_id = record["connector_id"]
        return McpExecutionBinding(
            spec=spec, config=self._config, connector_id=connector_id,
            tool_id=f"{connector_id}:{spec.configuration_sha256}",
        )

    def _tools(self, record: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return tuple({
            "tool_id": f"{record['connector_id']}:{spec.configuration_sha256}",
            "name": spec.descriptor.name,
            "provider_tool_name": spec.execute_catalog_name,
            "completion_guidance": spec.descriptor.completion_guidance,
            "description": spec.descriptor.description,
            "input_schema": spec.descriptor.input_schema,
            "operation_kind": spec.operation_kind,
            "configuration_sha256": spec.configuration_sha256,
        } for spec in self._specs(record["catalog"]))

    def _summary(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "connector_id": record["connector_id"],
            "endpoint": record["endpoint"],
            "gateway_id": record["gateway_id"],
            "registration_sha256": record["registration_sha256"],
            "state": "revoked" if record["revoked"] else "active",
            "tools": list(self._tools(record)),
        }

    def _snapshot(self) -> tuple[CheckpointState | None, dict[str, dict[str, Any]]]:
        for _ in range(8):
            try:
                state = self._timeline.load(self._thread_id)
            except TimelineNotFound:
                return None, {}
            events = self._timeline.events(self._thread_id)
            if not events or events[-1].payload["checkpoint"]["revision"] != state.revision:
                continue
            records: dict[str, dict[str, Any]] = {}
            for event in events:
                data = event.payload["data"]
                if data.get("contract") != _CONTRACT or data.get("owner_principal_id") != self._owner:
                    raise ConversationConflict("MCP registry event authority is invalid")
                if data.get("action") == "registered":
                    record = data["registration"]
                    connector_id = record["connector_id"]
                    identity = {k: v for k, v in record.items()
                                if k not in {"registration_sha256", "source"}}
                    if (connector_id in records
                            or canonical_sha256(identity) != record["registration_sha256"]
                            or (record["source"]["principal_id"] != self._owner
                                and record["source"].get("canonical_owner_principal_id") != self._owner)):
                        raise ConversationConflict("MCP registry immutable registration is invalid")
                    records[connector_id] = {**record, "revoked": False}
                elif data.get("action") == "revoked":
                    record = records.get(data["connector_id"])
                    if record is None or record["registration_sha256"] != data["registration_sha256"]:
                        raise ConversationConflict("MCP registry revocation binding is invalid")
                    record["revoked"] = True
                else:
                    raise ConversationConflict("MCP registry event action is invalid")
            return state, records
        raise ConversationConflict("MCP registry changed concurrently; retry lookup")

    def _append(
        self, state: CheckpointState | None, action: str,
        data: dict[str, Any],
    ) -> None:
        revision = 1 if state is None else state.revision + 1
        arguments = {
            "thread_id": self._thread_id,
            "checkpoint_id": f"{self._thread_id}:{revision}",
            "phase": action,
            "status": "active",
            "event_type": f"MCP_CONNECTOR_{action.upper()}",
            "occurred_at": datetime.now(UTC).replace(microsecond=0),
            "data": {"contract": _CONTRACT, "owner_principal_id": self._owner,
                     "action": action, **data},
        }
        if state is None:
            self._timeline.create_thread(**arguments)
        else:
            self._timeline.transition(expected_revision=state.revision, **arguments)


__all__ = ["PostgresMcpConnectorRegistry"]
