"""Typed conversation tools for the existing owner-bound delegated worker."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    external_timeline_scope,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationToolDescriptor,
    ConversationTurn,
)
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.canonical_json import canonical_json_bytes


_OPERATIONS = {
    "start_delegated_work": ("start", "task"),
    "list_delegated_work": ("list", None),
    "get_delegated_work": ("get", "work_id"),
    "stop_delegated_work": ("stop", "work_id"),
    "resume_delegated_work": ("resume", "work_id"),
}
_DESCRIPTIONS = {
    "start": "Besteed een concrete denk-, schrijf- of analysetaak uit aan een duurzame hulpagent. Geef alle benodigde context mee. De hulpagent heeft geen browser of tools. Geeft meteen een work_id terug; gebruik get om later het resultaat te lezen. Gebruik voor parallel hulpwerk op verzoek van de eigenaar.",
    "list": "Bekijk de duurzame hulpopdrachten van deze eigenaar, met hun work_id en actuele status.",
    "get": "Lees de actuele status, gemeten verbruik en het opgeslagen resultaat van één hulpopdracht. Gebruik het echte resultaat in je antwoord; ingepland betekent nog niet klaar.",
    "stop": "Stop de genoemde hulpopdracht op verzoek van de eigenaar. De ontvangstbevestiging kan aangeven dat een reeds begonnen modelcall nog niet is beëindigd.",
    "resume": "Hervat een gestopte of mislukte hulpopdracht alleen op expliciet verzoek van de eigenaar. Maakt één gekoppelde nieuwe poging; bewaart vorige onzekerheid en verbruik. Een nog actieve call kan niet direct opnieuw gestart worden.",
}


class PostgresDelegatedWorkActions:
    """Journal each chosen action; retries retain the same owner work identity."""

    supports_indexed_steps = True

    def __init__(self, engine: sa.Engine, service: DelegatedWorkOwnerService) -> None:
        self._engine = engine
        self._service = service

    @property
    def action_names(self) -> tuple[str, ...]:
        return tuple(_OPERATIONS)

    @staticmethod
    def conversation_tools() -> tuple[ConversationToolDescriptor, ...]:
        return tuple(
            ConversationToolDescriptor(
                name,
                _DESCRIPTIONS[operation],
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {} if key is None else {key: {"type": "string"}},
                    "required": [] if key is None else [key],
                },
                "Rapporteer de opgeslagen status eerlijk. Bewaar work_id voor een vervolg. "
                "status=verified bevestigt alleen deze databasehandeling; de taakstatus en "
                "het resultaat staan in operation_result.work. "
                "Wacht niet in een poll-lus op hulpwerk; geef de eigenaar de taak en laat het gesprek doorgaan.",
            )
            for name, (operation, key) in _OPERATIONS.items()
        )

    def available_for(self, turn: ConversationTurn) -> bool:
        return self._service.available_for(turn)

    def _timeline(self, turn: ConversationTurn) -> PostgresOperationalTimeline:
        if not self.available_for(turn):
            raise ConversationConflict("delegated work requires the private owner")
        self._service.validate_authority(turn)
        return PostgresOperationalTimeline(
            self._engine,
            external_timeline_scope(
                turn.tenant_id, turn.installation_id, "delegated action scope missing"
            ),
        )

    @staticmethod
    def _thread_id(turn: ConversationTurn, step_index: int) -> str:
        if type(step_index) is not int or step_index < 0:
            raise ValueError("delegated action step index must be nonnegative")
        key = canonical_json_bytes({
            "tenant": turn.tenant_id, "installation": turn.installation_id,
            "principal": turn.principal_id, "conversation": turn.conversation_id,
            "message": turn.message_id, "step": step_index,
        })
        return "conversation:delegated:" + hashlib.sha256(key).hexdigest()

    def pending(
        self, turn: ConversationTurn, *, step_index: int = 0
    ) -> ConversationActionReceipt | None:
        if not self.available_for(turn):
            return None
        timeline = self._timeline(turn)
        thread_id = self._thread_id(turn, step_index)
        events = timeline.events(thread_id)
        if not events:
            return None
        data = events[0].payload["data"]
        call = ConversationActionCall(data["call_id"], data["name"], data["arguments"])
        if events[-1].event_type == "DELEGATED_ACTION_COMPLETED":
            return ConversationActionReceipt(call, events[-1].payload["data"]["output"])
        return self.execute(turn, call, step_index=step_index)

    def reply_guard(self, turn, receipts):
        starts = tuple((index, receipt) for index, receipt in enumerate(receipts)
                       if receipt.call.name in {"start_delegated_work", "resume_delegated_work"}
                       and receipt.output.get("status") == "verified"
                       and receipt.output.get("operation_result", {}).get("status")
                       not in {"not_found", "not_resumable"})
        if not starts:
            return None

        def reconcile(text):
            timeline = self._timeline(turn)  # Recheck current private authority.
            stopped = []
            for index, receipt in starts:
                events = timeline.events(self._thread_id(turn, index))
                if (not events or events[-1].event_type != "DELEGATED_ACTION_COMPLETED"
                        or events[-1].payload["data"]["output"] != receipt.output
                        or events[0].payload["data"] != {
                            "call_id": receipt.call.call_id, "name": receipt.call.name,
                            "arguments": receipt.call.arguments}):
                    raise ConversationConflict("delegated reply lacks its exact action receipt")
                result = receipt.output.get("operation_result", {})
                work = result.get("work")
                if receipt.output.get("status") != "verified" or work is None:
                    continue
                work_id = work["work_id"]
                current = self._service.execute("get", turn, work_id=work_id,
                                                request_key=self._thread_id(turn, index))
                current_work = current["work"]
                if current_work is None:
                    raise ConversationConflict("delegated reply work is no longer available")
                if current_work["stop_requested"] or current_work["state"] == "stopped":
                    stopped.append(work_id)
            if not stopped:
                return text
            return ("Je stopopdracht voor " + ", ".join(dict.fromkeys(stopped))
                    + " is al bewaard. Het eerdere startantwoord is daarmee vervallen. "
                    "Het resultaat van deze poging wordt niet geleverd en de taak wordt "
                    "niet automatisch hervat.")
        return reconcile

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall, *, step_index: int = 0
    ) -> ConversationActionReceipt:
        operation_and_key = _OPERATIONS.get(call.name)
        if operation_and_key is None:
            raise ConversationConflict("unknown delegated action")
        operation, key = operation_and_key
        if set(call.arguments) != (set() if key is None else {key}) or (
            key is not None and (
                not isinstance(call.arguments[key], str)
                or not call.arguments[key].strip()
                or len(call.arguments[key].encode("utf-8")) > (8192 if key == "task" else 240)
            )
        ):
            raise ConversationConflict("delegated action arguments differ")
        timeline = self._timeline(turn)
        thread_id = self._thread_id(turn, step_index)
        request = {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
        events = timeline.events(thread_id)
        if not events:
            try:
                timeline.create_thread(
                    thread_id=thread_id, checkpoint_id=thread_id + ":prepared",
                    occurred_at=datetime.now(UTC).replace(microsecond=0),
                    data=request, event_type="DELEGATED_ACTION_PREPARED",
                )
            except CheckpointConflict:
                pass
            events = timeline.events(thread_id)
        if events[0].payload["data"] != request:
            raise ConversationConflict("delegated action changed after admission")
        if events[-1].event_type == "DELEGATED_ACTION_COMPLETED":
            return ConversationActionReceipt(call, events[-1].payload["data"]["output"])
        result = self._service.execute(
            operation, turn, request_key=thread_id,
            **({} if key is None else {key: call.arguments[key]}),
        )
        output = {"status": "verified", "operation_result": result}
        try:
            timeline.transition(
                thread_id=thread_id, expected_revision=1,
                checkpoint_id=thread_id + ":completed", phase="completed",
                status="completed", event_type="DELEGATED_ACTION_COMPLETED",
                occurred_at=datetime.now(UTC).replace(microsecond=0), data={"output": output},
            )
        except CheckpointConflict:
            latest = timeline.events(thread_id)[-1]
            if latest.event_type != "DELEGATED_ACTION_COMPLETED":
                raise
            output = latest.payload["data"]["output"]
        return ConversationActionReceipt(call, output)
