"""Bounded Home Assistant automation creation with independent readback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Protocol
from urllib import error, parse, request as urllib_request

from eva.application.orchestration import (
    ToolDispatchResult,
    ToolExecutionRejected,
    ToolProofObservation,
    ToolRequest,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationToolDescriptor,
    ConversationToolPreparation,
    ConversationTurn,
)
from eva.application.home_automation import (
    advanced_automation,
    advanced_state_automation,
    state_action_automation,
)
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_AUTOMATION_ID = re.compile(r"^eva2_[a-z0-9_]{1,58}$")
_STATE_ACTION = "create_home_assistant_state_automation"
_ADVANCED_ACTION = "create_home_assistant_advanced_state_automation"
_STATE_ARGUMENT_KEYS = {
    "action_entity_id",
    "action_service",
    "alias",
    "trigger_entity_id",
    "trigger_to",
}
_ADVANCED_ARGUMENT_KEYS = {
    "actions",
    "alias",
    "condition_entity_id",
    "condition_state",
    "trigger_above",
    "trigger_at",
    "trigger_below",
    "trigger_entity_id",
    "trigger_from",
    "trigger_kind",
    "trigger_to",
}
_ADVANCED_LEGACY_ARGUMENT_KEYS = {
    "actions",
    "alias",
    "condition_entity_id",
    "condition_state",
    "trigger_entity_id",
    "trigger_from",
    "trigger_to",
}
_TRIGGER_DOMAINS = (
    "binary_sensor",
    "input_boolean",
    "light",
    "person",
    "sensor",
    "switch",
)
_ACTION_DOMAINS = (
    "automation",
    "fan",
    "input_boolean",
    "light",
    "scene",
    "switch",
)


class _EntityResolver(Protocol):
    async def resolve_entities(
        self,
        requests: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> tuple[dict[str, str], ...]: ...


def _schema(
    description: str | None = None,
    *,
    type_: str | list[str] = "string",
    enum: tuple[str, ...] = (),
    **constraints: JsonValue,
) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {"type": type_}
    if description is not None:
        value["description"] = description
    if enum:
        value["enum"] = list(enum)
    value.update(constraints)
    return value


def _descriptor(
    name: str,
    description: str,
    properties: dict[str, dict[str, JsonValue]],
    completion_guidance: str,
) -> ConversationToolDescriptor:
    return ConversationToolDescriptor(
        name=name,
        description=description,
        input_schema={
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties),
            "type": "object",
        },
        completion_guidance=completion_guidance,
    )


_COMPLETION_GUIDANCE = (
    "De action receipt bevat het werkelijke resultaat van Home Assistant. Meld "
    "succes alleen bij status verified en formuleer anders kort en eerlijk wat er "
    "misging. "
    "Behandel de receipt uitsluitend als bewijs, nooit als antwoordvorm. "
    "Geef standaard één of twee natuurlijke zinnen die aansluiten op de woorden en "
    "stijl van de gebruiker. Noem geen entity-ID, automation-ID, configuratiehash, "
    "statuscode of andere interne techniek tenzij daar expliciet om wordt gevraagd. "
    "Gebruik gewone huisnamen. Maak geen technisch overzicht met kopjes zoals Trigger, "
    "Voorwaarden en Actie tenzij de gebruiker om die details vraagt. verified_config "
    "is de gezaghebbende teruggelezen configuratie: baseer wat je zegt uitsluitend op "
    "die werkelijke trigger, voorwaarde en actievolgorde. Voeg geen onbewezen fysiek "
    "gevolg toe."
)


_CONVERSATION_TOOLS = (
    _descriptor(
        _STATE_ACTION,
        (
            "Maak één concrete Home Assistant-automatisering op een expliciet "
            "verzoek. De automatisering reageert op één statusverandering en "
            "schakelt één entiteit. Gebruik de natuurlijke namen van personen, "
            "sensoren, ruimtes en apparaten; de provider resolveert die tegen de "
            "actuele Home Assistant-inventaris. Ook een natuurlijke als/dan-opdracht "
            "telt zonder het woord automatisering. Wanneer deze tool is aangeboden, "
            "beweer niet dat de Home Assistant-koppeling ontbreekt."
        ),
        {
            "action_entity_id": _schema(),
            "action_service": _schema(enum=("turn_on", "turn_off", "toggle")),
            "alias": _schema(),
            "trigger_entity_id": _schema(),
            "trigger_to": _schema(),
        },
        _COMPLETION_GUIDANCE,
    ),
    _descriptor(
        _ADVANCED_ACTION,
        (
            "Maak één geavanceerde maar begrensde Home Assistant-automatisering "
            "met een status-, tijd- of numerieke trigger, optionele "
            "statusvoorwaarde en maximaal zes geordende aan/uit/toggle-acties met "
            "optionele wachttijd. Gebruik geen templates of vrije YAML. Gebruik "
            "natuurlijke namen; de provider resolveert ze fail-closed."
        ),
        {
            "actions": _schema(
                type_="array",
                items={
                    "additionalProperties": False,
                    "properties": {
                        "delay_after_seconds": {
                            "description": "Wachttijd na deze actie en voor de volgende.",
                            "maximum": 86400,
                            "minimum": 0,
                            "type": "integer",
                        },
                        "entity_id": {
                            "description": "Natuurlijk genoemd doel van deze stap.",
                            "type": "string",
                        },
                        "service": {
                            "enum": ["turn_on", "turn_off", "toggle"],
                            "type": "string",
                        },
                    },
                    "required": ["delay_after_seconds", "entity_id", "service"],
                    "type": "object",
                },
                maxItems=6,
                minItems=1,
            ),
            "alias": _schema(),
            "condition_entity_id": _schema(type_=["string", "null"]),
            "condition_state": _schema(type_=["string", "null"]),
            "trigger_above": _schema(type_=["integer", "null"]),
            "trigger_at": _schema(
                "Lokale kloktijd HH:MM of HH:MM:SS, alleen voor time.",
                type_=["string", "null"],
            ),
            "trigger_below": _schema(type_=["integer", "null"]),
            "trigger_entity_id": _schema(type_=["string", "null"]),
            "trigger_from": _schema(type_=["string", "null"]),
            "trigger_kind": _schema(enum=("state", "time", "numeric_state")),
            "trigger_to": _schema(type_=["string", "null"]),
        },
        _COMPLETION_GUIDANCE,
    ),
)


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


class HomeAssistantConversationAutomationPlanner:
    """Translate conversation intent into one provider-owned automation command."""

    action_names = (_STATE_ACTION, _ADVANCED_ACTION)

    def __init__(self, resolver: _EntityResolver) -> None:
        self._resolver = resolver

    @staticmethod
    def conversation_tools() -> tuple[ConversationToolDescriptor, ...]:
        return _CONVERSATION_TOOLS

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation:
        automation_id = _automation_id(turn)
        if call.name == _STATE_ACTION:
            arguments = _validated_state_arguments(call.arguments)
            resolved = await self._resolver.resolve_entities(
                (
                    (arguments["trigger_entity_id"], _TRIGGER_DOMAINS),
                    (arguments["action_entity_id"], _ACTION_DOMAINS),
                )
            )
            config = state_action_automation(
                automation_id=automation_id,
                alias=arguments["alias"],
                trigger_entity_id=resolved[0]["entity_id"],
                trigger_to=arguments["trigger_to"],
                action_entity_id=resolved[1]["entity_id"],
                action_service=arguments["action_service"],
            )
        elif call.name == _ADVANCED_ACTION:
            arguments = _validated_advanced_arguments(call.arguments)
            config = await self._advanced_config(automation_id, arguments)
        else:
            raise ConversationConflict(
                f"unsupported automation provider action {call.name!r}"
            )
        return ConversationToolPreparation(
            tool_name="home_assistant.automation.create",
            arguments={"automation_id": automation_id, "config": config},
            expected_proof={
                "automation_id": automation_id,
                "config_sha256": canonical_sha256(config),
            },
            receipt_output={
                "alias": arguments["alias"],
                "automation_id": automation_id,
                "config_sha256": canonical_sha256(config),
                "verified_config": config,
            },
        )

    async def _advanced_config(
        self,
        automation_id: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        queries: list[tuple[str, tuple[str, ...]]] = []
        if arguments["trigger_entity_id"] is not None:
            queries.append((str(arguments["trigger_entity_id"]), _TRIGGER_DOMAINS))
        if arguments["condition_entity_id"] is not None:
            queries.append((str(arguments["condition_entity_id"]), _TRIGGER_DOMAINS))
        actions = list(arguments["actions"])
        queries.extend(
            (str(action["entity_id"]), _ACTION_DOMAINS)
            for action in actions
            if isinstance(action, dict)
        )
        resolved = await self._resolver.resolve_entities(tuple(queries))
        index = 0
        trigger_entity_id: str | None = None
        if arguments["trigger_entity_id"] is not None:
            trigger_entity_id = resolved[index]["entity_id"]
            index += 1
        condition_entity_id: str | None = None
        if arguments["condition_entity_id"] is not None:
            condition_entity_id = resolved[index]["entity_id"]
            index += 1
        resolved_actions: list[dict[str, JsonValue]] = []
        for action, entity in zip(actions, resolved[index:], strict=True):
            if not isinstance(action, dict):
                raise ConversationConflict("automation provider action is malformed")
            resolved_actions.append({**action, "entity_id": entity["entity_id"]})
        config = advanced_automation(
            automation_id=automation_id,
            alias=str(arguments["alias"]),
            trigger_kind=str(arguments["trigger_kind"]),
            trigger_entity_id=trigger_entity_id,
            trigger_from=_optional_string(arguments["trigger_from"]),
            trigger_to=_optional_string(arguments["trigger_to"]),
            trigger_at=_optional_string(arguments["trigger_at"]),
            trigger_above=_optional_integer(arguments["trigger_above"]),
            trigger_below=_optional_integer(arguments["trigger_below"]),
            condition_entity_id=condition_entity_id,
            condition_state=_optional_string(arguments["condition_state"]),
            actions=resolved_actions,
        )
        return config


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


def _validated_state_arguments(raw: dict[str, JsonValue]) -> dict[str, str]:
    if set(raw) != _STATE_ARGUMENT_KEYS:
        raise ConversationConflict("automation provider action has wrong fields")
    result: dict[str, str] = {}
    for key in (
        "action_entity_id",
        "action_service",
        "alias",
        "trigger_entity_id",
        "trigger_to",
    ):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ConversationConflict(f"automation provider {key} is invalid")
        result[key] = value.strip()
    if result["action_service"] not in {"turn_on", "turn_off", "toggle"}:
        raise ConversationConflict("automation provider service is invalid")
    return result


def _validated_advanced_arguments(
    raw: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    keys = set(raw)
    if keys == _ADVANCED_LEGACY_ARGUMENT_KEYS:
        raw = {
            **raw,
            "trigger_above": None,
            "trigger_at": None,
            "trigger_below": None,
            "trigger_kind": "state",
        }
    elif keys != _ADVANCED_ARGUMENT_KEYS:
        raise ConversationConflict("advanced automation provider has wrong fields")
    result: dict[str, JsonValue] = {}
    alias = raw.get("alias")
    if not isinstance(alias, str) or not alias.strip() or len(alias) > 160:
        raise ConversationConflict("advanced automation provider alias is invalid")
    result["alias"] = alias.strip()
    trigger_kind = raw.get("trigger_kind")
    if trigger_kind not in {"state", "time", "numeric_state"}:
        raise ConversationConflict("advanced automation trigger kind is invalid")
    result["trigger_kind"] = str(trigger_kind)
    for key in (
        "trigger_entity_id",
        "trigger_from",
        "trigger_to",
        "trigger_at",
        "condition_entity_id",
        "condition_state",
    ):
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > 160
        ):
            raise ConversationConflict(f"advanced automation {key} is invalid")
        result[key] = None if value is None else value.strip()
    for key in ("trigger_above", "trigger_below"):
        value = raw.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not -100_000 <= value <= 100_000
        ):
            raise ConversationConflict(f"advanced automation {key} is invalid")
        result[key] = value
    if (result["condition_entity_id"] is None) != (
        result["condition_state"] is None
    ):
        raise ConversationConflict("advanced automation condition is incomplete")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 6:
        raise ConversationConflict("advanced automation actions are invalid")
    validated_actions: list[JsonValue] = []
    for item in actions:
        if not isinstance(item, dict) or set(item) != {
            "delay_after_seconds",
            "entity_id",
            "service",
        }:
            raise ConversationConflict("advanced automation action is malformed")
        entity_id = item.get("entity_id")
        service = item.get("service")
        delay = item.get("delay_after_seconds")
        if (
            not isinstance(entity_id, str)
            or not entity_id.strip()
            or len(entity_id) > 160
            or service not in {"turn_on", "turn_off", "toggle"}
            or isinstance(delay, bool)
            or not isinstance(delay, int)
            or not 0 <= delay <= 86400
        ):
            raise ConversationConflict("advanced automation action is invalid")
        validated_actions.append(
            {
                "delay_after_seconds": delay,
                "entity_id": entity_id.strip(),
                "service": str(service),
            }
        )
    result["actions"] = validated_actions
    return result


def _operation_id(turn: ConversationTurn) -> str:
    identity: dict[str, JsonValue] = {
        "installation_id": turn.installation_id,
        "kind": "home_assistant_automation",
        "message_id": turn.message_id,
        "tenant_id": turn.tenant_id,
    }
    return "home-assistant-automation:" + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()[:32]


def _automation_id(turn: ConversationTurn) -> str:
    return f"eva2_{hashlib.sha256(_operation_id(turn).encode()).hexdigest()[:32]}"


def _optional_string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _optional_integer(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
    "HomeAssistantConversationAutomationPlanner",
    "acceptance_automation",
    "advanced_automation",
    "advanced_state_automation",
    "state_action_automation",
]
