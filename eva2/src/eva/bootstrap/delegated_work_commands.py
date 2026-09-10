"""Owner-only phrases using the same durable work service as conversation tools."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from eva.adapters.postgres.delegated_work import (
    PostgresDelegatedWorkStore,
)
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService

_SPAWN = re.compile(
    r"^(?:laat|vraag)\s+(?:een\s+)?onderzoeker(?:\s+om)?\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_STATUS = frozenset({"hoe staat mijn onderzoek", "hoe staat mijn onderzoek ervoor",
                     "status van mijn onderzoek"})
_STOP = frozenset({"stop mijn onderzoek", "stop het onderzoek"})
_LIST = frozenset({"toon mijn onderzoeken", "toon mijn onderzoekstaken"})
_RESULT = frozenset({"toon het resultaat van mijn onderzoek",
                     "wat is het resultaat van mijn onderzoek"})


class DelegatedWorkConversationCommands:
    """Keep existing Telegram phrases on the shared owner lifecycle."""

    def __init__(
        self, store: PostgresDelegatedWorkStore | None = None, *,
        owner_principal_id: UUID | None = None,
        principal_binding_id: UUID | None = None,
        clock: Callable[[], datetime] | None = None,
        service: DelegatedWorkOwnerService | None = None,
    ) -> None:
        if service is None:
            if store is None or owner_principal_id is None or principal_binding_id is None:
                raise ValueError("delegated commands require an owner service")
            service = DelegatedWorkOwnerService(
                store, owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id, clock=clock,
            )
        elif (store is not None or owner_principal_id is not None
              or principal_binding_id is not None or clock is not None):
            raise ValueError("delegated commands accept either a service or its settings")
        self._service = service

    def handle(self, turn: ConversationTurn, *,
               principal_binding_id: UUID | None) -> GroundedReply | None:
        if not self.matches(turn, principal_binding_id=principal_binding_id):
            return None
        text = turn.text.strip()
        normalized = text.casefold().rstrip(".?! ")
        match = _SPAWN.fullmatch(text)
        if match is not None:
            task = match.group(1).strip()
            if not task or len(task.encode("utf-8")) > 8_192:
                return self._reply(turn, "Die onderzoeksopdracht is leeg of te lang. Geef één concrete taak.")
            self._execute("start", turn, task=task)
            return self._reply(turn, "Ik heb één afgeschermde onderzoeker ingepland. "
                              "Je ziet de taak op het werkbord.")
        if normalized in _STATUS:
            return self._status(turn)
        if normalized in _LIST:
            jobs = self._execute("list", turn)["jobs"]
            return self._reply(turn, "\n".join(
                f"{job['work_id']}: {job['state']} — {job['task_preview']}" for job in jobs
            ) or "Er staat nog geen onderzoekstaak op je werkbord.")
        if normalized in _STOP:
            return self._stop(turn)
        work_id = self._service.latest_work_id(turn)
        if work_id is None:
            return self._reply(turn, "Er staat nog geen onderzoekstaak op je werkbord.")
        if normalized in _RESULT:
            work = self._execute("result", turn, work_id=work_id)["work"]
            if work["result_available"]:
                return self._reply(turn, work["result"]["output"])
            return self._reply(turn, f"Er is nog geen afgerond resultaat. De status is {work['state']}.")
    def matches(self, turn: ConversationTurn, *, principal_binding_id: UUID | None) -> bool:
        if (principal_binding_id != self._service.principal_binding_id
                or not self._service.available_for(turn)):
            return False
        text = turn.text.strip()
        normalized = text.casefold().rstrip(".?! ")
        return (_SPAWN.fullmatch(text) is not None
                or normalized in _STATUS | _STOP | _LIST | _RESULT)

    def _execute(self, operation: str, turn: ConversationTurn, **values) -> dict:
        return self._service.execute(operation, turn,
                                     request_key=self._service.command_key(turn), **values)

    def _status(self, turn: ConversationTurn) -> GroundedReply:
        board = self._service.board(turn)
        if not board.cards:
            return self._reply(turn, "Er staat nog geen onderzoekstaak op je werkbord.")
        counts = {column: sum(card.column == column for card in board.cards)
                  for column in ("todo", "doing", "attention", "review", "done")}
        return self._reply(turn, "Je werkbord: "
                           f"{counts['todo']} wachtend, {counts['doing']} bezig, "
                           f"{counts['attention']} vraagt aandacht, {counts['review']} te controleren "
                           f"en {counts['done']} klaar.")

    def _stop(self, turn: ConversationTurn) -> GroundedReply:
        work_id = self._service.latest_work_id(turn, stoppable=True)
        if work_id is None:
            return self._reply(turn, "Er loopt geen onderzoek dat ik kan stoppen.")
        work = self._execute("stop", turn, work_id=work_id)["work"]
        if work["state"] == "call_started" or (work["state"] == "stopped" and work["call_id"] is not None):
            return self._reply(turn, "De stopopdracht is bewaard. Het resultaat van deze actieve modelcall "
                               "wordt niet aan Eva geleverd en Eva start hem niet opnieuw.")
        if work["state"] != "stopped":
            return self._reply(turn, "Deze onderzoekstaak was al afgerond.")
        return self._reply(turn, "Gestopt voordat een modelcall begon. Deze taak wordt niet alsnog uitgevoerd.")

    def _reply(self, turn: ConversationTurn, text: str) -> GroundedReply:
        return GroundedReply(conversation_id=turn.conversation_id, message_id=turn.message_id,
                             text=text, evidence_memory_ids=(),
                             generated_at=self._service.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                             provider="eva-delegated-work-command")


__all__ = ["DelegatedWorkConversationCommands"]
