"""Bounded Home Assistant automation creation with independent readback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import re
from typing import Any
from urllib import error, parse, request as urllib_request

from eva.application.orchestration import (
    ToolDispatchResult,
    ToolExecutionRejected,
    ToolProofObservation,
    ToolRequest,
)
from eva.application.home_automation import (
    advanced_automation,
    advanced_state_automation,
    state_action_automation,
)
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256


_AUTOMATION_ID = re.compile(r"^eva2_[a-z0-9_]{1,58}$")


@dataclass(frozen=True, slots=True)
class HomeAssistantAutomationConfig:
    base_url: str
    token: str
    request_timeout_seconds: int = 8

    def __post_init__(self) -> None:
        parsed = parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Home Assistant automation URL is invalid")
        if not self.token:
            raise ValueError("Home Assistant automation token is required")
        if not 1 <= self.request_timeout_seconds <= 15:
            raise ValueError("Home Assistant automation timeout is invalid")


def acceptance_automation(automation_id: str) -> dict[str, JsonValue]:
    """A harmless, unreachable event automation for a live create/delete gate."""

    if _AUTOMATION_ID.fullmatch(automation_id) is None:
        raise ValueError("Eva automation ID is invalid")
    return {
        "actions": [
            {
                "action": "logbook.log",
                "data": {
                    "message": "Eva2 automation acceptance event",
                    "name": "Eva2 B",
                },
            }
        ],
        "alias": "Eva2 B automation acceptance",
        "conditions": [],
        "description": "Tijdelijke, onbereikbare B-acceptatietest; wordt direct verwijderd.",
        "mode": "single",
        "triggers": [
            {
                "event_type": "eva2_b_acceptance_never_fire",
                "trigger": "event",
            }
        ],
    }


class HomeAssistantAutomationAdapter:
    def __init__(self, config: HomeAssistantAutomationConfig) -> None:
        self._config = config

    async def execute(self, command: ToolRequest) -> ToolDispatchResult:
        automation_id, config = _command_fields(command)
        try:
            existing = await self._get(automation_id)
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
        else:
            if _selected_config(existing) != config:
                raise ToolExecutionRejected(
                    "Home Assistant automation ID already has different content"
                )
            return ToolDispatchResult(
                True,
                automation_id,
                {"automation_id": automation_id, "disposition": "already_same"},
            )
        try:
            await self._request_json(
                "POST",
                f"/api/config/automation/config/{parse.quote(automation_id, safe='')}",
                config,
            )
        except error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise ToolExecutionRejected(
                    f"Home Assistant rejected automation ({exc.code})"
                ) from exc
            raise
        return ToolDispatchResult(
            True,
            automation_id,
            {"automation_id": automation_id, "disposition": "created"},
        )

    async def observe(
        self, command: ToolRequest, dispatch: ToolDispatchResult | None
    ) -> ToolProofObservation:
        automation_id, config = _command_fields(command)
        try:
            actual = await self._get(automation_id)
        except error.HTTPError as exc:
            if exc.code == 404:
                return ToolProofObservation(
                    "pending",
                    {"automation_id": automation_id, "config_sha256": "missing"},
                    {"source": "home_assistant_automation_config", "status": 404},
                )
            raise
        selected = _selected_config(actual)
        observed = {
            "automation_id": automation_id,
            "config_sha256": canonical_sha256(selected),
        }
        expected = command.expected_proof
        return ToolProofObservation(
            "verified" if observed == expected and selected == config else "conflict",
            observed,
            {"source": "home_assistant_automation_config", "status": 200},
        )

    async def delete_verified(
        self, automation_id: str, *, expected_config_sha256: str
    ) -> str:
        if _AUTOMATION_ID.fullmatch(automation_id) is None:
            raise ValueError("Eva automation ID is invalid")
        actual = await self._get(automation_id)
        if canonical_sha256(_selected_config(actual)) != expected_config_sha256:
            raise RuntimeError("automation rollback hash differs")
        await self._request_json(
            "DELETE",
            f"/api/config/automation/config/{parse.quote(automation_id, safe='')}",
        )
        try:
            await self._get(automation_id)
        except error.HTTPError as exc:
            if exc.code == 404:
                return "deleted"
            raise
        raise RuntimeError("automation still exists after rollback")

    async def _get(self, automation_id: str) -> dict[str, JsonValue]:
        value = await self._request_json(
            "GET",
            f"/api/config/automation/config/{parse.quote(automation_id, safe='')}",
        )
        if not isinstance(value, dict):
            raise RuntimeError("Home Assistant automation config is invalid")
        return value

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        return await asyncio.to_thread(self._request_json_sync, method, path, payload)

    def _request_json_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None,
    ) -> JsonValue:
        body = None
        if payload is not None:
            validate_json_value(payload)
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        request = urllib_request.Request(
            f"{self._config.base_url.rstrip('/')}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.token}",
                "Content-Type": "application/json",
            },
        )
        with urllib_request.urlopen(
            request, timeout=self._config.request_timeout_seconds
        ) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise RuntimeError("Home Assistant automation response is too large")
        if not raw:
            return None
        value = _json_safe(json.loads(raw))
        validate_json_value(value)
        return value


def _command_fields(command: ToolRequest) -> tuple[str, dict[str, JsonValue]]:
    if set(command.arguments) != {"automation_id", "config"}:
        raise ValueError("Home Assistant automation command fields differ")
    automation_id = command.arguments["automation_id"]
    config = command.arguments["config"]
    if (
        not isinstance(automation_id, str)
        or _AUTOMATION_ID.fullmatch(automation_id) is None
    ):
        raise ValueError("Home Assistant automation ID is invalid")
    if not isinstance(config, dict):
        raise ValueError("Home Assistant automation config is invalid")
    validate_json_value(config)
    return automation_id, config


def _selected_config(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    keys = {"actions", "alias", "conditions", "description", "mode", "triggers"}
    if not keys <= set(value):
        raise RuntimeError("Home Assistant automation readback is incomplete")
    selected = {key: value[key] for key in sorted(keys)}
    validate_json_value(selected)
    return selected


def _json_safe(value: Any) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else format(Decimal(str(value)), "f")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


__all__ = [
    "HomeAssistantAutomationAdapter",
    "HomeAssistantAutomationConfig",
    "acceptance_automation",
    "advanced_automation",
    "advanced_state_automation",
    "state_action_automation",
]
