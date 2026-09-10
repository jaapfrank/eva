"""Home Assistant service-call and independent state-readback adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib import error, parse, request as urllib_request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eva.adapters.connectors.home_assistant_mcp import (
    HomeAssistantMcpError,
    call_assist_effect,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationClarificationRequired,
    ConversationConflict,
    ConversationToolDescriptor,
    ConversationToolPreparation,
    ConversationTurn,
)
from eva.application.orchestration import (
    ToolDispatchResult,
    ToolExecutionRejected,
    ToolProofObservation,
    ToolRequest,
)
from eva.contracts.base import JsonValue, validate_json_value


_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTITY = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_TARGET_FILLER = frozenset({"aan", "de", "een", "het", "in", "op", "uit", "van"})
_TARGET_EQUIVALENTS = {
    "heater": "heater",
    "kachel": "heater",
    "radiator": "heater",
    "terrasverwarmer": "heater",
    "verwarming": "heater",
    "socket": "socket",
    "stekker": "socket",
    "stopcontact": "socket",
}
_GENERIC_ENTITY_WORDS = frozenset(
    {"apparaat", "apparaten", "lamp", "lampen", "licht", "lichten", "schakelaar"}
)


def _conversation_schema(
    description: str | None = None,
    *,
    enum: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    schema: dict[str, JsonValue] = {"type": "string"}
    if description is not None:
        schema["description"] = description
    if enum:
        schema["enum"] = list(enum)
    return schema


def _conversation_tool(
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


_CONVERSATION_TOOLS = (
    _conversation_tool(
        "set_home_assistant_light",
        (
            "Zet precies één bestaand licht in huis aan of uit. Gebruik de naam "
            "die de gebruiker noemt; de provideradapter resolveert die fail-closed "
            "en geeft pas een resultaat na onafhankelijke statuscontrole."
        ),
        {
            "state": _conversation_schema(enum=("on", "off")),
            "target": _conversation_schema(
                "De letterlijke lamp- of ruimtenaam uit het verzoek."
            ),
        },
        (
            "Het toolresultaat bevat de onafhankelijk gecontroleerde eindtoestand. "
            "Meld succes alleen bij status verified en formuleer anders kort en "
            "eerlijk wat er misging. Noem geen interne ID, statuscode, receipt of "
            "techniek tenzij daar expliciet om wordt gevraagd."
        ),
    ),
    _conversation_tool(
        "set_home_assistant_switch",
        (
            "Zet precies één bestaand aan/uit-apparaat aan of uit, bijvoorbeeld "
            "een verwarming of slim stopcontact. Gebruik de natuurlijke naam uit "
            "het gesprek; de provideradapter resolveert uitsluitend toegestane "
            "schakelaars en claimt pas succes na onafhankelijke statuscontrole. "
            "Gebruik dit niet voor lampen, sloten of alarmsystemen. Een korte "
            "bevestiging neemt het concrete apparaat en de richting uit het direct "
            "voorafgaande voorstel over; vraag daar niet opnieuw naar."
        ),
        {
            "state": _conversation_schema(enum=("on", "off")),
            "target": _conversation_schema(
                "De natuurlijke apparaat- of ruimtenaam uit het gesprek."
            ),
        },
        (
            "Het toolresultaat bevat de onafhankelijk gecontroleerde eindtoestand. "
            "Meld succes alleen bij status verified en formuleer anders kort en "
            "eerlijk wat er misging. Noem geen interne ID, statuscode, receipt of "
            "techniek tenzij daar expliciet om wordt gevraagd."
        ),
    ),
    _conversation_tool(
        "read_home_assistant_history",
        (
            "Lees de echte providerhistorie van één genoemde lamp, schakelaar of "
            "een natuurlijk genoemde lichtgroep om te beantwoorden wanneer die aan "
            "of uit ging. Dit is een alleen-lezen actie met bronbewijs; gok niet "
            "vanuit gesprek of herinneringen."
        ),
        {
            "entity_kind": _conversation_schema(
                "light voor een lamp; switch voor een aan/uit-apparaat.",
                enum=("light", "switch"),
            ),
            "period": _conversation_schema(
                enum=("yesterday", "today", "last_24_hours")
            ),
            "target": _conversation_schema(
                "De natuurlijke lamp-, apparaat- of gebiedsnaam uit de vraag; "
                "behoud bijvoorbeeld 'licht in de overkapping' als groepstaal."
            ),
            "transition_to": _conversation_schema(
                "De gevraagde toestand; any bij een algemene wijziging.",
                enum=("on", "off", "any"),
            ),
        },
        (
            "Het toolresultaat bevat echte alleen-lezen historie in de genoemde "
            "lokale tijdzone. Geef bij een enkelvoudige tijdsvraag alleen de laatste "
            "passende tijd. Een groepsmeting met all_members_off geldt voor de hele "
            "genoemde groep. Als matches leeg is, zeg dan eerlijk dat er geen passende "
            "overgang is gevonden. Noem geen interne ID of techniek."
        ),
    ),
    _conversation_tool(
        "read_home_assistant_state",
        (
            "Lees de actuele echte toestand van precies één lamp of schakelaar bij "
            "de toolprovider. Gebruik dit voor vragen of een apparaat nu aan, uit "
            "of onbeschikbaar is; baseer dit niet op oude gesprekscontext."
        ),
        {
            "entity_kind": _conversation_schema(
                "light voor een lamp; switch voor een aan/uit-apparaat.",
                enum=("light", "switch"),
            ),
            "target": _conversation_schema(
                "De natuurlijke lamp- of apparaatnaam uit de vraag."
            ),
        },
        (
            "Het toolresultaat bevat de actuele, echte toestand. Vertaal on naar aan "
            "en off naar uit. Zeg bij unavailable of unknown eerlijk dat er nu geen "
            "betrouwbare toestand is. Noem geen interne ID, receipt of techniek tenzij "
            "daar expliciet om wordt gevraagd."
        ),
    ),
)


class HomeAssistantConversationEffectPlanner:
    """Keep provider-specific resolution and effect policy outside Eva's core."""

    action_names = ("set_home_assistant_light", "set_home_assistant_switch")

    def __init__(
        self,
        provider: HomeAssistantToolAdapter,
        *,
        allowlist: frozenset[tuple[str, str]] | None,
        principal_sha256_allowlist: frozenset[str],
    ) -> None:
        if not principal_sha256_allowlist:
            raise ValueError("effect principal policy is required")
        self._provider = provider
        self._allowlist = allowlist
        self._principal_sha256_allowlist = principal_sha256_allowlist

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation:
        if call.name not in self.action_names:
            raise ConversationConflict(f"unsupported provider action {call.name!r}")
        if hashlib.sha256(turn.principal_id.encode()).hexdigest() not in (
            self._principal_sha256_allowlist
        ):
            raise ConversationClarificationRequired(
                "Met dit profiel mag je deze externe tool nog niet bedienen."
            )
        if set(call.arguments) != {"state", "target"}:
            raise ConversationConflict("provider effect action has wrong fields")
        state = call.arguments.get("state")
        target = call.arguments.get("target")
        if state not in {"on", "off"}:
            raise ConversationConflict("provider effect state is invalid")
        if not isinstance(target, str) or not target.strip() or len(target) > 160:
            raise ConversationConflict("provider effect target is invalid")
        domain = "switch" if call.name == "set_home_assistant_switch" else "light"
        resolver = (
            self._provider.resolve_switch
            if domain == "switch"
            else self._provider.resolve_light
        )
        try:
            resolved = await resolver(target.strip())
        except ValueError as error:
            subject = "lamp" if domain == "light" else "apparaat"
            raise ConversationClarificationRequired(
                f"Welke {subject} bedoel je precies? Noem de naam zoals die in de tool staat."
            ) from error
        service = "turn_on" if state == "on" else "turn_off"
        entity_id = resolved["entity_id"]
        friendly_name = resolved["friendly_name"]
        if self._allowlist is not None and (entity_id, service) not in self._allowlist:
            allowed_services = {
                admitted_service
                for admitted_entity, admitted_service in self._allowlist
                if admitted_entity == entity_id
            }
            if allowed_services == {"turn_off"}:
                message = f"Ik mag {friendly_name} nu alleen uitzetten, niet aanzetten."
            elif allowed_services == {"turn_on"}:
                message = f"Ik mag {friendly_name} nu alleen aanzetten, niet uitzetten."
            else:
                message = (
                    f"{friendly_name} mag ik nog niet bedienen. Alleen vooraf veilig "
                    "vrijgegeven toolacties zijn nu beschikbaar."
                )
            raise ConversationClarificationRequired(message)
        expected_state = "on" if service == "turn_on" else "off"
        return ConversationToolPreparation(
            tool_name=f"home_assistant.{domain}.{service}",
            arguments={
                "domain": domain,
                "entity_id": entity_id,
                "service": service,
                "service_data": {},
            },
            expected_proof={"entity_id": entity_id, "state": expected_state},
            receipt_output={
                "entity_id": entity_id,
                "friendly_name": friendly_name,
                "requested_state": state,
            },
        )


class HomeAssistantConversationReadPlanner:
    """Resolve provider-owned read requests before generic durable execution."""

    action_names = ("read_home_assistant_history", "read_home_assistant_state")

    def __init__(self, provider: HomeAssistantToolAdapter) -> None:
        self._provider = provider

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation:
        if call.name == "read_home_assistant_history":
            arguments = _validated_history_arguments(call.arguments)
            try:
                native = self._native_target(turn, arguments)
                resolved = ([native] if native is not None else
                    await self._provider.resolve_history_targets(
                        arguments["target"], arguments["entity_kind"]))
            except ValueError as error:
                subject = (
                    "lamp" if arguments["entity_kind"] == "light" else "apparaat"
                )
                raise ConversationClarificationRequired(
                    f"Welke {subject} bedoel je precies?"
                ) from error
            entity_ids = [item["entity_id"] for item in resolved]
            friendly_names = [item["friendly_name"] for item in resolved]
            return ConversationToolPreparation(
                tool_name="home_assistant.read_history",
                arguments={
                    "entity_ids": entity_ids,
                    "observed_at": turn.occurred_at,
                    "period": arguments["period"],
                    "transition_to": arguments["transition_to"],
                },
                expected_proof={"entity_ids": entity_ids},
                receipt_output={
                    "friendly_name": (
                        friendly_names[0]
                        if len(friendly_names) == 1
                        else friendly_names
                    )
                },
                observation_output_key="history",
            )
        if call.name == "read_home_assistant_state":
            arguments = _validated_state_arguments(call.arguments)
            resolver = (
                self._provider.resolve_light
                if arguments["entity_kind"] == "light"
                else self._provider.resolve_switch
            )
            try:
                resolved = self._native_target(turn, arguments)
                if resolved is None:
                    resolved = await resolver(arguments["target"])
            except ValueError as error:
                subject = (
                    "lamp" if arguments["entity_kind"] == "light" else "apparaat"
                )
                raise ConversationClarificationRequired(
                    f"Welke {subject} bedoel je precies?"
                ) from error
            return ConversationToolPreparation(
                tool_name="home_assistant.read_state",
                arguments={"entity_id": resolved["entity_id"]},
                expected_proof={"entity_id": resolved["entity_id"]},
                receipt_output={"friendly_name": resolved["friendly_name"]},
                observation_output_key="state",
            )
        raise ConversationConflict(f"unsupported provider action {call.name!r}")

    @staticmethod
    def _native_target(turn, arguments):
        from eva.application.initiative_execution import require_initiative_context
        if require_initiative_context(turn) is None:
            return None
        # Native planning must perform no network work before dispatch admission.
        # The existing inventory tool supplies actual IDs through its sealed read.
        target = arguments["target"]
        if (_ENTITY.fullmatch(target) is None
                or target.split(".", 1)[0] != arguments["entity_kind"]):
            raise ConversationConflict(
                "Native HA reads require an exact entity ID from an observation or inventory")
        return {"entity_id": target, "friendly_name": target}


@dataclass(frozen=True, slots=True)
class HomeAssistantConfig:
    base_url: str
    token: str
    request_timeout_seconds: int = 5

    def __post_init__(self) -> None:
        parsed = parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Home Assistant URL must be absolute HTTP(S)")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("Home Assistant URL must not contain credentials or query")
        if not self.token:
            raise ValueError("Home Assistant token is required")
        if not 1 <= self.request_timeout_seconds <= 10:
            raise ValueError("Home Assistant timeout must be within 1..10 seconds")


class HomeAssistantToolAdapter:
    def __init__(self, config: HomeAssistantConfig) -> None:
        self._config = config

    @staticmethod
    def conversation_tools() -> tuple[ConversationToolDescriptor, ...]:
        """Expose provider-owned model tools without teaching the model adapter HA."""

        return _CONVERSATION_TOOLS

    def conversation_effect_planner(
        self,
        *,
        allowlist: frozenset[tuple[str, str]] | None,
        principal_sha256_allowlist: frozenset[str],
    ) -> HomeAssistantConversationEffectPlanner:
        return HomeAssistantConversationEffectPlanner(
            self,
            allowlist=allowlist,
            principal_sha256_allowlist=principal_sha256_allowlist,
        )

    def conversation_read_planner(self) -> HomeAssistantConversationReadPlanner:
        return HomeAssistantConversationReadPlanner(self)

    async def health(self) -> bool:
        payload = await self._request_json("GET", "/api/")
        return isinstance(payload, dict) and payload.get("message") == "API running."

    async def resolve_light(self, target: str) -> dict[str, str]:
        """Resolve one available light by exact entity ID, slug, or friendly name."""

        payload = await asyncio.to_thread(self._light_catalog_sync)
        return _resolve_binary_target(target, "light", payload)

    async def resolve_switch(self, target: str) -> dict[str, str]:
        """Resolve one available on/off switch from the user's natural name."""

        payload = await asyncio.to_thread(self._switch_catalog_sync)
        return _resolve_binary_target(target, "switch", payload)

    async def resolve_entities(
        self,
        requests: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> tuple[dict[str, str], ...]:
        """Resolve several automation targets against one live state snapshot."""

        if not 1 <= len(requests) <= 8:
            raise ValueError("Home Assistant entity resolution batch is invalid")
        payload = await asyncio.to_thread(self._state_catalog_sync)
        return tuple(
            _resolve_entity_target(target, admitted_domains, payload)
            for target, admitted_domains in requests
        )

    async def resolve_history_targets(
        self, target: str, entity_kind: str
    ) -> tuple[dict[str, str], ...]:
        """Resolve one named entity or a natural bounded light-area group."""

        if entity_kind not in {"light", "switch"}:
            raise ValueError("Home Assistant history entity kind is invalid")
        payload = await asyncio.to_thread(self._state_catalog_sync)
        single = _resolve_binary_target_or_none(target, entity_kind, payload)
        if single is not None:
            return (single,)
        wanted = _target_tokens(target) - _GENERIC_ENTITY_WORDS
        generic = _target_tokens(target) & _GENERIC_ENTITY_WORDS
        if entity_kind != "light" or not generic or len(wanted) != 1:
            raise ValueError(
                f"Home Assistant {entity_kind} target is unknown or ambiguous"
            )
        matches = _binary_semantic_candidates(wanted, entity_kind, payload)
        if not 2 <= len(matches) <= 8:
            raise ValueError(
                f"Home Assistant {entity_kind} target is unknown or ambiguous"
            )
        return tuple(matches)

    async def history(
        self,
        *,
        entity_id: str,
        period: str,
        transition_to: str,
        observed_at: datetime,
    ) -> dict[str, JsonValue]:
        """Read bounded recorder history and return proven state transitions."""

        if _ENTITY.fullmatch(entity_id) is None or entity_id.split(".", 1)[0] not in {
            "light",
            "switch",
        }:
            raise ValueError("Home Assistant history entity is invalid")
        if period not in {"yesterday", "today", "last_24_hours"}:
            raise ValueError("Home Assistant history period is invalid")
        if transition_to not in {"on", "off", "any"}:
            raise ValueError("Home Assistant history transition is invalid")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Home Assistant history observation time must be aware")

        time_zone, start, end = await self._history_window(period, observed_at)
        payload = await self._history_payload(entity_id, start, end)
        return _select_history(
            payload,
            entity_id=entity_id,
            period=period,
            transition_to=transition_to,
            start=start,
            end=end,
            time_zone=time_zone,
        )

    async def history_group(
        self,
        *,
        entity_ids: tuple[str, ...],
        period: str,
        transition_to: str,
        observed_at: datetime,
    ) -> dict[str, JsonValue]:
        """Read and aggregate a bounded set of lights as any-on/all-off."""

        if (
            not 2 <= len(entity_ids) <= 8
            or len(set(entity_ids)) != len(entity_ids)
            or any(
                _ENTITY.fullmatch(entity_id) is None
                or not entity_id.startswith("light.")
                for entity_id in entity_ids
            )
        ):
            raise ValueError("Home Assistant history group is invalid")
        if period not in {"yesterday", "today", "last_24_hours"}:
            raise ValueError("Home Assistant history period is invalid")
        if transition_to not in {"on", "off", "any"}:
            raise ValueError("Home Assistant history transition is invalid")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Home Assistant history observation time must be aware")
        time_zone, start, end = await self._history_window(period, observed_at)
        payloads = await asyncio.gather(
            *(self._history_payload(entity_id, start, end) for entity_id in entity_ids)
        )
        return _select_group_history(
            payloads,
            entity_ids=entity_ids,
            period=period,
            transition_to=transition_to,
            start=start,
            end=end,
            time_zone=time_zone,
        )

    async def _history_window(
        self, period: str, observed_at: datetime
    ) -> tuple[str, datetime, datetime]:
        config = await self._request_json("GET", "/api/config")
        time_zone = config.get("time_zone") if isinstance(config, dict) else None
        if not isinstance(time_zone, str) or not time_zone:
            raise RuntimeError("Home Assistant did not provide its time zone")
        try:
            local_zone = ZoneInfo(time_zone)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError("Home Assistant time zone is unknown") from exc
        local_now = observed_at.astimezone(local_zone)
        if period == "last_24_hours":
            return time_zone, local_now - timedelta(hours=24), local_now
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "yesterday":
            return time_zone, today - timedelta(days=1), today
        return time_zone, today, local_now

    async def _history_payload(
        self, entity_id: str, start: datetime, end: datetime
    ) -> JsonValue:
        query = parse.urlencode(
            {
                "end_time": end.isoformat(timespec="seconds"),
                "filter_entity_id": entity_id,
                "minimal_response": "",
                "no_attributes": "",
                "significant_changes_only": "",
            }
        )
        encoded_start = parse.quote(start.isoformat(timespec="seconds"), safe=":-")
        return await self._request_json(
            "GET", f"/api/history/period/{encoded_start}?{query}"
        )

    async def state(self, *, entity_id: str) -> dict[str, JsonValue]:
        """Read one current binary entity state directly from Home Assistant."""

        if _ENTITY.fullmatch(entity_id) is None or entity_id.split(".", 1)[0] not in {
            "light",
            "switch",
        }:
            raise ValueError("Home Assistant state entity is invalid")
        payload = await self._request_json(
            "GET", f"/api/states/{parse.quote(entity_id, safe='.')}"
        )
        if not isinstance(payload, dict) or payload.get("entity_id") != entity_id:
            raise RuntimeError("Home Assistant returned an invalid state object")
        state = payload.get("state")
        if not isinstance(state, str):
            raise RuntimeError("Home Assistant state is invalid")
        result: dict[str, JsonValue] = {
            "contract": "EvaHomeAssistantCurrentState/v1",
            "entity_id": entity_id,
            "last_changed": _text_or_none(payload.get("last_changed")),
            "last_updated": _text_or_none(payload.get("last_updated")),
            "source": "home_assistant_state",
            "state": state,
        }
        validate_json_value(result)
        return result

    def read_entity_state(self, *, entity_id: str) -> dict[str, JsonValue]:
        """Read one entity; the caller owns which domains it admits."""
        if _ENTITY.fullmatch(entity_id) is None:
            raise ValueError("Home Assistant state entity is invalid")
        payload = self._request_json_sync(
            "GET", f"/api/states/{parse.quote(entity_id, safe='.')}", None,
        )
        if not isinstance(payload, dict) or payload.get("entity_id") != entity_id:
            raise RuntimeError("Home Assistant returned an invalid state object")
        return payload

    def _light_catalog_sync(self) -> list[dict[str, Any]]:
        return self._state_catalog_sync()

    def _switch_catalog_sync(self) -> list[dict[str, Any]]:
        return self._state_catalog_sync()

    def _state_catalog_sync(self) -> list[dict[str, Any]]:
        request = urllib_request.Request(
            f"{self._config.base_url.rstrip('/')}/api/states",
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.token}",
            },
        )
        with urllib_request.urlopen(
            request, timeout=self._config.request_timeout_seconds
        ) as response:
            raw = response.read(8_388_609)
            if len(raw) > 8_388_608:
                raise RuntimeError("Home Assistant state catalog is too large")
        decoded = json.loads(raw)
        if not isinstance(decoded, list):
            raise RuntimeError("Home Assistant state catalog is invalid")
        return [value for value in decoded if isinstance(value, dict)]

    async def execute(self, command: ToolRequest) -> ToolDispatchResult:
        if command.tool_name == "home_assistant.read_state":
            entity_id = _read_state_command(command)
            observation = await self.state(entity_id=entity_id)
            return ToolDispatchResult(
                accepted=True,
                provider_ref=None,
                details={"observation": observation},
            )
        if command.tool_name == "home_assistant.read_history":
            entity_ids, period, transition_to, observed_at = _read_history_command(
                command
            )
            if len(entity_ids) == 1:
                observation = await self.history(
                    entity_id=entity_ids[0],
                    period=period,
                    transition_to=transition_to,
                    observed_at=observed_at,
                )
            else:
                observation = await self.history_group(
                    entity_ids=entity_ids,
                    period=period,
                    transition_to=transition_to,
                    observed_at=observed_at,
                )
            return ToolDispatchResult(
                accepted=True,
                provider_ref=None,
                details={"observation": observation},
            )
        domain, service, entity_id, _service_data = _command_fields(command)
        try:
            details = await asyncio.to_thread(
                call_assist_effect,
                base_url=self._config.base_url,
                token=self._config.token,
                timeout=self._config.request_timeout_seconds,
                domain=domain,
                service=service,
                entity_id=entity_id,
            )
        except HomeAssistantMcpError as exc:
            raise ToolExecutionRejected(str(exc)) from exc
        return ToolDispatchResult(
            accepted=True,
            provider_ref=None,
            details={**details, "service": f"{domain}.{service}"},
        )

    async def observe(
        self,
        command: ToolRequest,
        dispatch: ToolDispatchResult | None,
    ) -> ToolProofObservation:
        if command.tool_name in {
            "home_assistant.read_history",
            "home_assistant.read_state",
        }:
            observation = (
                dispatch.details.get("observation")
                if dispatch is not None and dispatch.accepted
                else None
            )
            if not isinstance(observation, dict):
                raise RuntimeError("Home Assistant read lacks an observation")
            expected = command.expected_proof
            if command.tool_name == "home_assistant.read_state":
                if observation.get("entity_id") != expected.get("entity_id"):
                    raise RuntimeError("Home Assistant state observation differs")
            else:
                expected_ids = expected.get("entity_ids")
                observed_ids = observation.get("entity_ids")
                if (
                    not isinstance(expected_ids, list)
                    or (
                        len(expected_ids) == 1
                        and observation.get("entity_id") != expected_ids[0]
                    )
                    or (len(expected_ids) > 1 and observed_ids != expected_ids)
                ):
                    raise RuntimeError("Home Assistant history observation differs")
            return ToolProofObservation(
                "verified",
                expected,
                {"observation": observation, "source": "home_assistant_read"},
            )
        _, _, entity_id, _ = _command_fields(command)
        expected = command.expected_proof
        if expected.get("entity_id") != entity_id:
            raise ValueError("expected proof is not bound to the command entity")
        try:
            state = await self._request_json(
                "GET", f"/api/states/{parse.quote(entity_id, safe='.')}"
            )
        except error.HTTPError as exc:
            if exc.code == 404:
                return ToolProofObservation(
                    "conflict",
                    {"entity_id": entity_id, "state": "missing"},
                    {"source": "home_assistant", "status": 404},
                )
            raise
        if not isinstance(state, dict) or state.get("entity_id") != entity_id:
            raise RuntimeError("Home Assistant returned an invalid state object")
        observed = _select_observed(expected, state)
        evidence: dict[str, JsonValue] = {
            "entity_id": entity_id,
            "last_changed": _text_or_none(state.get("last_changed")),
            "last_updated": _text_or_none(state.get("last_updated")),
            "source": "home_assistant",
        }
        return ToolProofObservation(
            "verified" if observed == expected else "pending",
            observed,
            evidence,
        )

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
        endpoint = f"{self._config.base_url.rstrip('/')}{path}"
        request = urllib_request.Request(
            endpoint,
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
                raise RuntimeError("Home Assistant response is too large")
        decoded = json.loads(raw)
        safe = _json_safe(decoded)
        validate_json_value(safe)
        return safe


def _command_fields(
    command: ToolRequest,
) -> tuple[str, str, str, dict[str, JsonValue]]:
    arguments = command.arguments
    if set(arguments) != {"domain", "entity_id", "service", "service_data"}:
        raise ValueError("Home Assistant command has wrong fields")
    domain = arguments["domain"]
    service = arguments["service"]
    entity_id = arguments["entity_id"]
    service_data = arguments["service_data"]
    if not isinstance(domain, str) or _NAME.fullmatch(domain) is None:
        raise ValueError("Home Assistant domain is invalid")
    if not isinstance(service, str) or _NAME.fullmatch(service) is None:
        raise ValueError("Home Assistant service is invalid")
    if not isinstance(entity_id, str) or _ENTITY.fullmatch(entity_id) is None:
        raise ValueError("Home Assistant entity ID is invalid")
    if not isinstance(service_data, dict):
        raise ValueError("Home Assistant service_data must be an object")
    return domain, service, entity_id, service_data


def _validated_state_arguments(raw: dict[str, JsonValue]) -> dict[str, str]:
    if set(raw) != {"entity_kind", "target"}:
        raise ConversationConflict("provider state read has wrong fields")
    return _validated_read_target(raw)


def _validated_history_arguments(raw: dict[str, JsonValue]) -> dict[str, str]:
    if set(raw) != {"entity_kind", "period", "target", "transition_to"}:
        raise ConversationConflict("provider history read has wrong fields")
    result = _validated_read_target(raw)
    period = raw.get("period")
    transition_to = raw.get("transition_to")
    if period not in {"yesterday", "today", "last_24_hours"}:
        raise ConversationConflict("provider history period is invalid")
    if transition_to not in {"on", "off", "any"}:
        raise ConversationConflict("provider history transition is invalid")
    result.update({"period": str(period), "transition_to": str(transition_to)})
    return result


def _validated_read_target(raw: dict[str, JsonValue]) -> dict[str, str]:
    target = raw.get("target")
    entity_kind = raw.get("entity_kind")
    if entity_kind not in {"light", "switch"}:
        raise ConversationConflict("provider read entity kind is invalid")
    if not isinstance(target, str) or not target.strip() or len(target) > 160:
        raise ConversationConflict("provider read target is invalid")
    return {"entity_kind": str(entity_kind), "target": target.strip()}


def _read_state_command(command: ToolRequest) -> str:
    if set(command.arguments) != {"entity_id"}:
        raise ValueError("Home Assistant state read command has wrong fields")
    entity_id = command.arguments.get("entity_id")
    if not isinstance(entity_id, str) or _ENTITY.fullmatch(entity_id) is None:
        raise ValueError("Home Assistant state read entity is invalid")
    if command.expected_proof != {"entity_id": entity_id}:
        raise ValueError("Home Assistant state read proof is invalid")
    return entity_id


def _read_history_command(
    command: ToolRequest,
) -> tuple[tuple[str, ...], str, str, datetime]:
    if set(command.arguments) != {
        "entity_ids",
        "observed_at",
        "period",
        "transition_to",
    }:
        raise ValueError("Home Assistant history read command has wrong fields")
    raw_entity_ids = command.arguments.get("entity_ids")
    period = command.arguments.get("period")
    transition_to = command.arguments.get("transition_to")
    raw_observed_at = command.arguments.get("observed_at")
    if (
        not isinstance(raw_entity_ids, list)
        or not 1 <= len(raw_entity_ids) <= 8
        or len(set(str(value) for value in raw_entity_ids)) != len(raw_entity_ids)
        or any(
            not isinstance(value, str)
            or _ENTITY.fullmatch(value) is None
            or value.split(".", 1)[0] not in {"light", "switch"}
            for value in raw_entity_ids
        )
    ):
        raise ValueError("Home Assistant history read entities are invalid")
    if period not in {"yesterday", "today", "last_24_hours"}:
        raise ValueError("Home Assistant history read period is invalid")
    if transition_to not in {"on", "off", "any"}:
        raise ValueError("Home Assistant history read transition is invalid")
    if not isinstance(raw_observed_at, str):
        raise ValueError("Home Assistant history read time is invalid")
    try:
        observed_at = datetime.fromisoformat(raw_observed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Home Assistant history read time is invalid") from error
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Home Assistant history read time has no zone")
    entity_ids = tuple(raw_entity_ids)
    if command.expected_proof != {"entity_ids": list(entity_ids)}:
        raise ValueError("Home Assistant history read proof is invalid")
    return entity_ids, str(period), str(transition_to), observed_at


def _select_observed(
    expected: dict[str, JsonValue], state: dict[str, Any]
) -> dict[str, JsonValue]:
    allowed = {"attributes", "entity_id", "state"}
    if not set(expected) <= allowed or "entity_id" not in expected:
        raise ValueError("Home Assistant expected proof has wrong fields")
    observed: dict[str, JsonValue] = {"entity_id": str(state["entity_id"])}
    if "state" in expected:
        observed["state"] = str(state.get("state", ""))
    if "attributes" in expected:
        expected_attributes = expected["attributes"]
        actual_attributes = state.get("attributes")
        if not isinstance(expected_attributes, dict) or not isinstance(
            actual_attributes, dict
        ):
            raise RuntimeError("Home Assistant attributes are invalid")
        observed["attributes"] = {
            key: _json_safe(actual_attributes.get(key)) for key in expected_attributes
        }
    validate_json_value(observed)
    return observed


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _normalized_target(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(character for character in normalized if character.isalnum())


def _target_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    tokens = set(re.findall(r"[a-z0-9]+", plain)) - _TARGET_FILLER
    return {_TARGET_EQUIVALENTS.get(token, token) for token in tokens}


def _resolve_binary_target(
    target: str, domain: str, payload: list[dict[str, Any]]
) -> dict[str, str]:
    result = _resolve_binary_target_or_none(target, domain, payload)
    if result is None:
        raise ValueError(f"Home Assistant {domain} target is unknown or ambiguous")
    return result


def _resolve_binary_target_or_none(
    target: str, domain: str, payload: list[dict[str, Any]]
) -> dict[str, str] | None:
    wanted = _normalized_target(target)
    wanted_tokens = _target_tokens(target)
    if not wanted or not wanted_tokens:
        raise ValueError(f"Home Assistant {domain} target is empty")
    prefix = f"{domain}."
    exact: list[dict[str, str]] = []
    semantic: list[dict[str, str]] = []
    for raw in payload:
        entity_id = raw.get("entity_id")
        state = raw.get("state")
        attributes = raw.get("attributes")
        if (
            not isinstance(entity_id, str)
            or not entity_id.startswith(prefix)
            or state not in {"on", "off"}
            or not isinstance(attributes, dict)
        ):
            continue
        friendly_name = attributes.get("friendly_name")
        display_name = (
            friendly_name
            if isinstance(friendly_name, str) and friendly_name
            else entity_id
        )
        result = {
            "entity_id": entity_id,
            "friendly_name": display_name,
            "state": state,
        }
        names = {
            _normalized_target(entity_id),
            _normalized_target(entity_id.removeprefix(prefix)),
            _normalized_target(display_name),
        }
        if wanted in names:
            exact.append(result)
        if wanted_tokens <= _target_tokens(f"{entity_id} {display_name}"):
            semantic.append(result)
    if len(exact) == 1:
        return exact[0]
    if exact or len(semantic) != 1:
        return None
    return semantic[0]


def _binary_semantic_candidates(
    wanted_tokens: set[str], domain: str, payload: list[dict[str, Any]]
) -> list[dict[str, str]]:
    prefix = f"{domain}."
    matches: list[dict[str, str]] = []
    for raw in payload:
        entity_id = raw.get("entity_id")
        state = raw.get("state")
        attributes = raw.get("attributes")
        if (
            not isinstance(entity_id, str)
            or not entity_id.startswith(prefix)
            or state not in {"on", "off"}
            or not isinstance(attributes, dict)
        ):
            continue
        friendly_name = attributes.get("friendly_name")
        display_name = (
            friendly_name
            if isinstance(friendly_name, str) and friendly_name
            else entity_id
        )
        if wanted_tokens <= (
            _target_tokens(f"{entity_id} {display_name}") - _GENERIC_ENTITY_WORDS
        ):
            matches.append(
                {
                    "entity_id": entity_id,
                    "friendly_name": display_name,
                    "state": state,
                }
            )
    return sorted(matches, key=lambda item: item["entity_id"])


def _resolve_entity_target(
    target: str,
    admitted_domains: tuple[str, ...],
    payload: list[dict[str, Any]],
) -> dict[str, str]:
    wanted = _normalized_target(target)
    wanted_tokens = _target_tokens(target)
    domains = frozenset(admitted_domains)
    if (
        not wanted
        or not wanted_tokens
        or not domains
        or any(_NAME.fullmatch(domain) is None for domain in domains)
    ):
        raise ValueError("Home Assistant automation target is invalid")
    exact: list[dict[str, str]] = []
    semantic: list[dict[str, str]] = []
    for raw in payload:
        entity_id = raw.get("entity_id")
        state = raw.get("state")
        attributes = raw.get("attributes")
        if (
            not isinstance(entity_id, str)
            or entity_id.split(".", 1)[0] not in domains
            or not isinstance(state, str)
            or not isinstance(attributes, dict)
        ):
            continue
        friendly_name = attributes.get("friendly_name")
        display_name = (
            friendly_name
            if isinstance(friendly_name, str) and friendly_name
            else entity_id
        )
        result = {
            "entity_id": entity_id,
            "friendly_name": display_name,
            "state": state,
        }
        slug = entity_id.split(".", 1)[1]
        names = {
            _normalized_target(entity_id),
            _normalized_target(slug),
            _normalized_target(display_name),
        }
        if wanted in names:
            exact.append(result)
        candidate_tokens = _target_tokens(f"{entity_id} {display_name}")
        if wanted_tokens <= candidate_tokens:
            semantic.append((len(candidate_tokens - wanted_tokens), result))
    if len(exact) == 1:
        return exact[0]
    if exact or not semantic:
        raise ValueError("Home Assistant automation target is unknown or ambiguous")
    best_score = min(score for score, _result in semantic)
    best = [result for score, result in semantic if score == best_score]
    if len(best) != 1:
        raise ValueError("Home Assistant automation target is unknown or ambiguous")
    return best[0]


def _json_safe(value: Any) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return format(Decimal(str(value)), "f")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _select_history(
    payload: JsonValue,
    *,
    entity_id: str,
    period: str,
    transition_to: str,
    start: datetime,
    end: datetime,
    time_zone: str,
) -> dict[str, JsonValue]:
    states = _history_states(payload, entity_id)
    matches: list[dict[str, JsonValue]] = []
    previous: str | None = None
    for changed, state in states:
        is_change = previous is not None and previous != state
        if (
            start <= changed < end
            and is_change
            and (transition_to == "any" or state == transition_to)
        ):
            matches.append(
                {
                    "changed_at": changed.astimezone(ZoneInfo(time_zone)).isoformat(
                        timespec="seconds"
                    ),
                    "from": previous,
                    "to": state,
                }
            )
        previous = state
    result: dict[str, JsonValue] = {
        "contract": "EvaHomeAssistantHistory/v1",
        "entity_id": entity_id,
        "matches": matches[-32:],
        "period": period,
        "source": "home_assistant_recorder",
        "time_zone": time_zone,
        "transition_to": transition_to,
        "window_end": end.isoformat(timespec="seconds"),
        "window_start": start.isoformat(timespec="seconds"),
    }
    validate_json_value(result)
    return result


def _history_states(payload: JsonValue, entity_id: str) -> list[tuple[datetime, str]]:
    if not isinstance(payload, list) or len(payload) > 1:
        raise RuntimeError("Home Assistant history response is invalid")
    raw_states = [] if not payload else payload[0]
    if not isinstance(raw_states, list) or len(raw_states) > 10_000:
        raise RuntimeError("Home Assistant entity history is invalid or too large")
    states: list[tuple[datetime, str]] = []
    for raw in raw_states:
        if not isinstance(raw, dict) or raw.get("entity_id", entity_id) != entity_id:
            raise RuntimeError("Home Assistant history item is invalid")
        state = raw.get("state")
        changed = raw.get("last_changed")
        if not isinstance(state, str) or not isinstance(changed, str):
            raise RuntimeError("Home Assistant history item lacks state evidence")
        try:
            timestamp = datetime.fromisoformat(changed.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Home Assistant history timestamp is invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise RuntimeError("Home Assistant history timestamp has no time zone")
        states.append((timestamp, state))
    states.sort(key=lambda item: item[0])
    return states


def _select_group_history(
    payloads: list[JsonValue],
    *,
    entity_ids: tuple[str, ...],
    period: str,
    transition_to: str,
    start: datetime,
    end: datetime,
    time_zone: str,
) -> dict[str, JsonValue]:
    if len(payloads) != len(entity_ids):
        raise RuntimeError("Home Assistant group history response is incomplete")
    current: dict[str, str] = {}
    changes: list[tuple[datetime, str, str]] = []
    for entity_id, payload in zip(entity_ids, payloads, strict=True):
        states = _history_states(payload, entity_id)
        if not states:
            raise RuntimeError("Home Assistant group member has no history evidence")
        current[entity_id] = states[0][1]
        changes.extend(
            (changed, entity_id, state)
            for changed, state in states[1:]
            if start <= changed < end
        )
    previous = "on" if any(state == "on" for state in current.values()) else "off"
    transitions: list[tuple[datetime, str, str]] = []
    for changed, entity_id, state in sorted(changes):
        current[entity_id] = state
        aggregate = "on" if any(value == "on" for value in current.values()) else "off"
        if aggregate != previous:
            transitions.append((changed, previous, aggregate))
            previous = aggregate
    matches: list[dict[str, JsonValue]] = []
    for index, (changed, before, after) in enumerate(transitions):
        stable_until = (
            transitions[index + 1][0] if index + 1 < len(transitions) else end
        )
        duration_seconds = max(0, int((stable_until - changed).total_seconds()))
        if duration_seconds >= 30 and (
            transition_to == "any" or after == transition_to
        ):
            matches.append(
                {
                    "changed_at": changed.astimezone(ZoneInfo(time_zone)).isoformat(
                        timespec="seconds"
                    ),
                    "from": before,
                    "stable_for_seconds": duration_seconds,
                    "to": after,
                }
            )
    result: dict[str, JsonValue] = {
        "aggregation": "any_member_on_all_members_off",
        "contract": "EvaHomeAssistantGroupHistory/v1",
        "entity_ids": list(entity_ids),
        "matches": matches[-32:],
        "minimum_stable_seconds": 30,
        "period": period,
        "source": "home_assistant_recorder",
        "time_zone": time_zone,
        "transition_to": transition_to,
        "window_end": end.isoformat(timespec="seconds"),
        "window_start": start.isoformat(timespec="seconds"),
    }
    validate_json_value(result)
    return result


__all__ = [
    "HomeAssistantConfig",
    "HomeAssistantConversationEffectPlanner",
    "HomeAssistantConversationReadPlanner",
    "HomeAssistantToolAdapter",
]
