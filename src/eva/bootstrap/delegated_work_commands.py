"""Owner-only phrases using the same durable work service as conversation tools."""

from __future__ import annotations

import re
from eva.application.work_result_presentation import readable_work_result as _readable_result
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
                f"{job['work_id']}: {job['state']} — {job['task_preview']}\n{job['delivery']['description']}" for job in jobs
            ) or "Er staat nog geen onderzoekstaak op je werkbord.")
        work_id = self._service.latest_work_id(turn)
        if work_id is None:
            return self._reply(turn, "Er staat nog geen onderzoekstaak op je werkbord.")
        if normalized in _RESULT:
            work = self._execute("result", turn, work_id=work_id)["work"]
            if work["result_available"]:
                return self._reply(turn, _readable_result(work["result"]["output"])
                                   + "\n\n" + work["delivery"]["description"])
            return self._reply(turn, f"Er is nog geen afgerond resultaat. De status is {work['state']}.\n"
                               + work["delivery"]["description"]
                               + ("\n" + work["failure_description"] if work.get("failure_description") else ""))
    def matches(self, turn: ConversationTurn, *, principal_binding_id: UUID | None) -> bool:
        if (principal_binding_id != self._service.principal_binding_id
                or not self._service.available_for(turn)):
            return False
        text = turn.text.strip()
        normalized = text.casefold().rstrip(".?! ")
        return (_SPAWN.fullmatch(text) is not None
                or normalized in _STATUS | _LIST | _RESULT)

    def validate_authority(self, turn: ConversationTurn) -> None:
        """Recheck current owner authority before releasing a saved command reply."""
        self._service.validate_authority(turn)

    def _execute(self, operation: str, turn: ConversationTurn, **values) -> dict:
        return self._service.execute(operation, turn,
                                     request_key=self._service.command_key(turn), **values)

    def _status(self, turn: ConversationTurn) -> GroundedReply:
        work_id = self._service.latest_work_id(turn)
        if work_id is None:
            return self._reply(turn, "Er staat nog geen onderzoekstaak op je werkbord.")
        work = self._execute("get", turn, work_id=work_id)["work"]
        labels = {
            "admitted": "ingepland",
            "call_started": "gestart; er is nog geen bevestigde uitkomst",
            "stopped": "gestopt; wordt alleen op jouw verzoek hervat",
            "succeeded": "klaar; je kunt het resultaat opvragen",
            "verified": "klaar en gecontroleerd; je kunt het resultaat opvragen",
            "failed": "mislukt; er is geen geslaagd resultaat",
            "needs_help": "vraagt aandacht; er is geen geslaagd resultaat",
            "ambiguous": "uitkomst onzeker; wordt niet automatisch opnieuw uitgevoerd",
            "expired": "verlopen; wordt niet automatisch opnieuw uitgevoerd",
        }
        state = labels.get(work["state"], work["state"])
        if work["stop_requested"] and work["state"] == "call_started":
            state = "stopopdracht bewaard; het resultaat wordt niet geleverd"
        return self._reply(turn, f"Je laatste onderzoek is {state}.\n"
                           f"Opdracht: {work['task'][:240]}\n"
                           f"Taaknummer: {work['work_id']} (poging {work['generation']}).\n"
                           + work["delivery"]["description"]
                           + ("\n" + work["failure_description"] if work.get("failure_description") else ""))

    def _reply(self, turn: ConversationTurn, text: str) -> GroundedReply:
        return GroundedReply(conversation_id=turn.conversation_id, message_id=turn.message_id,
                             text=text, evidence_memory_ids=(),
                             generated_at=self._service.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                             provider="eva-delegated-work-command")




__all__ = ["DelegatedWorkConversationCommands"]
