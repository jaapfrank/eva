"""Durable read-only Home Assistant conversation actions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationClarificationRequired,
    ConversationConflict,
    ConversationTurn,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes


HOME_ASSISTANT_HISTORY_ACTION = "read_home_assistant_history"
HOME_ASSISTANT_STATE_ACTION = "read_home_assistant_state"
_HISTORY_KEYS = {"entity_kind", "period", "target", "transition_to"}
_STATE_KEYS = {"entity_kind", "target"}


class HomeAssistantReadPort(Protocol):
    async def resolve_light(self, target: str) -> dict[str, str]: ...

    async def resolve_switch(self, target: str) -> dict[str, str]: ...

    async def resolve_history_targets(
        self, target: str, entity_kind: str
    ) -> tuple[dict[str, str], ...]: ...

    async def history(
        self,
        *,
        entity_id: str,
        period: str,
        transition_to: str,
        observed_at: datetime,
    ) -> dict[str, JsonValue]: ...

    async def state(self, *, entity_id: str) -> dict[str, JsonValue]: ...

    async def history_group(
        self,
        *,
        entity_ids: tuple[str, ...],
        period: str,
        transition_to: str,
        observed_at: datetime,
    ) -> dict[str, JsonValue]: ...


class PostgresHomeAssistantConversationReads:
    """Commit read authority before I/O and the exact observation afterwards."""

    action_names = (HOME_ASSISTANT_HISTORY_ACTION, HOME_ASSISTANT_STATE_ACTION)

    def __init__(self, engine: sa.Engine, provider: HomeAssistantReadPort) -> None:
        self._engine = engine
        self._provider = provider

    def pending(self, turn: ConversationTurn) -> ConversationActionReceipt | None:
        for action_name in self.action_names:
            thread_id = self.thread_id(turn, action_name)
            events = self._timeline(turn).events(thread_id)
            if not events:
                continue
            if events[-1].event_type != "READ_OBSERVED":
                self._resume(turn, events)
                events = self._timeline(turn).events(thread_id)
            return self._receipt(turn, events)
        return None

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt:
        arguments = self._validated(call.name, call.arguments)

        async def run() -> None:
            if call.name == HOME_ASSISTANT_HISTORY_ACTION:
                resolved_many = await self._provider.resolve_history_targets(
                    arguments["target"], arguments["entity_kind"]
                )
                if len(resolved_many) == 1:
                    resolved: dict[str, JsonValue] = dict(resolved_many[0])
                else:
                    resolved = {
                        "entity_ids": [item["entity_id"] for item in resolved_many],
                        "friendly_names": [
                            item["friendly_name"] for item in resolved_many
                        ],
                    }
                await self._read(turn, call, arguments, resolved)
                return
            resolver = (
                self._provider.resolve_light
                if arguments["entity_kind"] == "light"
                else self._provider.resolve_switch
            )
            resolved_one = await resolver(arguments["target"])
            await self._read(turn, call, arguments, resolved_one)

        try:
            asyncio.run(run())
        except ValueError as error:
            expected = (
                f"Home Assistant {arguments['entity_kind']} target is unknown or ambiguous"
            )
            if str(error) != expected:
                raise
            subject = "lamp" if arguments["entity_kind"] == "light" else "apparaat"
            raise ConversationClarificationRequired(
                f"Welke {subject} bedoel je precies?"
            ) from error
        return self._receipt(
            turn, self._timeline(turn).events(self.thread_id(turn, call.name))
        )

    async def _read(
        self,
        turn: ConversationTurn,
        call: ConversationActionCall,
        arguments: dict[str, str],
        resolved: dict[str, JsonValue],
    ) -> None:
        thread_id = self.thread_id(turn, call.name)
        operation_id = self._operation_id(turn, call.name)
        timeline = self._timeline(turn)
        admission: dict[str, JsonValue] = {
            "action_call_id": call.call_id,
            "action_name": call.name,
            "arguments": arguments,
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
        }
        entity_id = resolved.get("entity_id")
        friendly_name = resolved.get("friendly_name")
        entity_ids = resolved.get("entity_ids")
        friendly_names = resolved.get("friendly_names")
        if isinstance(entity_id, str) and isinstance(friendly_name, str):
            admission.update({"entity_id": entity_id, "friendly_name": friendly_name})
        elif (
            isinstance(entity_ids, list)
            and isinstance(friendly_names, list)
            and entity_ids
            and len(entity_ids) == len(friendly_names)
            and all(isinstance(value, str) and value for value in entity_ids)
            and all(isinstance(value, str) and value for value in friendly_names)
        ):
            admission.update(
                {"entity_ids": entity_ids, "friendly_names": friendly_names}
            )
        else:
            raise ConversationConflict(
                "Home Assistant read target binding is malformed"
            )
        try:
            timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:admitted",
                occurred_at=self._timestamp(turn),
                data=admission,
                event_type="READ_REQUEST_ADMITTED",
                phase="read_admitted",
                status="active",
                operation_id=operation_id,
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            state = timeline.load(thread_id)
            if (
                state.operation_id != operation_id
                or not events
                or self._event_data(events[0]) != admission
            ):
                raise ConversationConflict(
                    "Home Assistant read replay differs"
                ) from None
            if events[-1].event_type == "READ_OBSERVED":
                return

        if call.name == HOME_ASSISTANT_HISTORY_ACTION:
            bound_ids = admission.get("entity_ids")
            bound_entity = admission.get("entity_id")
            if isinstance(bound_entity, str):
                bound_ids = [bound_entity]
            if (
                not isinstance(bound_ids, list)
                or not bound_ids
                or not all(isinstance(value, str) and value for value in bound_ids)
            ):
                raise ConversationConflict(
                    "Home Assistant history binding is malformed"
                )
            if len(bound_ids) == 1:
                observation = await self._provider.history(
                    entity_id=bound_ids[0],
                    period=arguments["period"],
                    transition_to=arguments["transition_to"],
                    observed_at=self._timestamp(turn),
                )
            else:
                observation = await self._provider.history_group(
                    entity_ids=tuple(bound_ids),
                    period=arguments["period"],
                    transition_to=arguments["transition_to"],
                    observed_at=self._timestamp(turn),
                )
            output_key = "history"
        elif call.name == HOME_ASSISTANT_STATE_ACTION:
            assert isinstance(entity_id, str)
            observation = await self._provider.state(entity_id=entity_id)
            output_key = "state"
        else:
            raise ConversationConflict(f"unsupported Home Assistant read {call.name!r}")
        state = timeline.load(thread_id)
        try:
            timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:observed",
                operation_id=operation_id,
                phase="read_observed",
                status="completed",
                event_type="READ_OBSERVED",
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                data={output_key: observation},
            )
        except CheckpointConflict:
            events = timeline.events(thread_id)
            if not events or events[-1].event_type != "READ_OBSERVED":
                raise ConversationConflict(
                    "Home Assistant read raced without evidence"
                ) from None

    def _resume(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> None:
        if not events or events[0].event_type != "READ_REQUEST_ADMITTED":
            raise ConversationConflict("Home Assistant read admission is missing")
        admission = self._event_data(events[0])
        action_name = admission.get("action_name")
        raw_arguments = admission.get("arguments")
        call_id = admission.get("action_call_id")
        entity_id = admission.get("entity_id")
        friendly_name = admission.get("friendly_name")
        entity_ids = admission.get("entity_ids")
        friendly_names = admission.get("friendly_names")
        if (
            not isinstance(action_name, str)
            or not isinstance(raw_arguments, dict)
            or not isinstance(call_id, str)
        ):
            raise ConversationConflict("Home Assistant read admission is malformed")
        if isinstance(entity_id, str) and isinstance(friendly_name, str):
            resolved: dict[str, JsonValue] = {
                "entity_id": entity_id,
                "friendly_name": friendly_name,
                "state": "unknown",
            }
        elif (
            isinstance(entity_ids, list)
            and isinstance(friendly_names, list)
            and all(isinstance(value, str) for value in entity_ids)
            and all(isinstance(value, str) for value in friendly_names)
        ):
            resolved = {"entity_ids": entity_ids, "friendly_names": friendly_names}
        else:
            raise ConversationConflict(
                "Home Assistant read target binding is malformed"
            )
        arguments = self._validated(action_name, raw_arguments)
        asyncio.run(
            self._read(
                turn,
                ConversationActionCall(call_id, action_name, arguments),
                arguments,
                resolved,
            )
        )

    def _receipt(
        self, turn: ConversationTurn, events: tuple[TimelineEvent, ...]
    ) -> ConversationActionReceipt:
        if (
            len(events) != 2
            or events[0].event_type != "READ_REQUEST_ADMITTED"
            or events[1].event_type != "READ_OBSERVED"
        ):
            raise ConversationConflict("Home Assistant read has no durable observation")
        admission = self._event_data(events[0])
        observed = self._event_data(events[1])
        action_name = admission.get("action_name")
        raw_arguments = admission.get("arguments")
        if not isinstance(action_name, str) or not isinstance(raw_arguments, dict):
            raise ConversationConflict("Home Assistant read receipt is malformed")
        arguments = self._validated(action_name, raw_arguments)
        output_key = (
            "history" if action_name == HOME_ASSISTANT_HISTORY_ACTION else "state"
        )
        observation = observed.get(output_key)
        if not isinstance(observation, dict):
            raise ConversationConflict("Home Assistant read observation is malformed")
        expected_scope = {
            "conversation_id": turn.conversation_id,
            "principal_id": turn.principal_id,
            "source_message_id": turn.message_id,
        }
        if any(admission.get(key) != value for key, value in expected_scope.items()):
            raise ConversationConflict("Home Assistant read replay scope differs")
        call_id = admission.get("action_call_id")
        entity_id = admission.get("entity_id")
        friendly_name = admission.get("friendly_name")
        entity_ids = admission.get("entity_ids")
        friendly_names = admission.get("friendly_names")
        if not isinstance(call_id, str) or not call_id:
            raise ConversationConflict("Home Assistant read identity is invalid")
        if isinstance(entity_id, str) and isinstance(friendly_name, str):
            if observation.get("entity_id") != entity_id:
                raise ConversationConflict("Home Assistant read evidence differs")
            receipt_names: JsonValue = friendly_name
        elif (
            isinstance(entity_ids, list)
            and isinstance(friendly_names, list)
            and observation.get("entity_ids") == entity_ids
        ):
            receipt_names = friendly_names
        else:
            raise ConversationConflict("Home Assistant read identity is invalid")
        return ConversationActionReceipt(
            ConversationActionCall(str(call_id), action_name, arguments),
            {
                "contract": "EvaConversationActionReceipt/v1",
                "durable": True,
                "friendly_name": receipt_names,
                output_key: observation,
                "status": "observed",
            },
        )

    @staticmethod
    def _validated(action_name: str, raw: dict[str, JsonValue]) -> dict[str, str]:
        if action_name == HOME_ASSISTANT_HISTORY_ACTION:
            if set(raw) != _HISTORY_KEYS:
                raise ConversationConflict(
                    "Home Assistant history action has wrong fields"
                )
        elif action_name == HOME_ASSISTANT_STATE_ACTION:
            if set(raw) != _STATE_KEYS:
                raise ConversationConflict(
                    "Home Assistant state action has wrong fields"
                )
        else:
            raise ConversationConflict(
                f"unsupported Home Assistant read {action_name!r}"
            )
        target = raw.get("target")
        entity_kind = raw.get("entity_kind")
        if not isinstance(target, str) or not target.strip() or len(target) > 160:
            raise ConversationConflict("Home Assistant read target is invalid")
        if entity_kind not in {"light", "switch"}:
            raise ConversationConflict("Home Assistant read entity kind is invalid")
        result = {"entity_kind": str(entity_kind), "target": target.strip()}
        if action_name == HOME_ASSISTANT_HISTORY_ACTION:
            period = raw.get("period")
            transition_to = raw.get("transition_to")
            if period not in {"yesterday", "today", "last_24_hours"}:
                raise ConversationConflict("Home Assistant history period is invalid")
            if transition_to not in {"on", "off", "any"}:
                raise ConversationConflict(
                    "Home Assistant history transition is invalid"
                )
            result.update({"period": str(period), "transition_to": str(transition_to)})
        return result

    @classmethod
    def _operation_id(cls, turn: ConversationTurn, action_name: str) -> str:
        kind = (
            "home_assistant_history_read"
            if action_name == HOME_ASSISTANT_HISTORY_ACTION
            else "home_assistant_state_read"
        )
        identity = canonical_json_bytes(
            {
                "installation_id": turn.installation_id,
                "kind": kind,
                "message_id": turn.message_id,
                "tenant_id": turn.tenant_id,
            }
        )
        prefix = (
            "home-assistant-history"
            if action_name == HOME_ASSISTANT_HISTORY_ACTION
            else "home-assistant-state"
        )
        return f"{prefix}:" + hashlib.sha256(identity).hexdigest()[:32]

    @classmethod
    def thread_id(cls, turn: ConversationTurn, action_name: str) -> str:
        return f"read:{cls._operation_id(turn, action_name)}"

    @staticmethod
    def _timestamp(turn: ConversationTurn) -> datetime:
        return datetime.fromisoformat(turn.occurred_at.replace("Z", "+00:00"))

    @staticmethod
    def _event_data(event: TimelineEvent) -> dict[str, JsonValue]:
        data = event.payload.get("data")
        if not isinstance(data, dict):
            raise ConversationConflict("Home Assistant read event data is malformed")
        return data

    def _timeline(self, turn: ConversationTurn) -> PostgresOperationalTimeline:
        return PostgresOperationalTimeline(
            self._engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL,
                    f"eva2:{turn.tenant_id}:{turn.installation_id}",
                ),
            ),
        )


__all__ = [
    "HOME_ASSISTANT_HISTORY_ACTION",
    "HOME_ASSISTANT_STATE_ACTION",
    "HomeAssistantReadPort",
    "PostgresHomeAssistantConversationReads",
]
