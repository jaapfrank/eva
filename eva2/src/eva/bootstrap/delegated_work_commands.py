"""Owner-only natural-language controls for Eva's durable research worker."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from eva.adapters.postgres.delegated_work import (
    DelegatedWorkConflict,
    PostgresDelegatedWorkStore,
)
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.bootstrap.delegated_work_provider import (
    build_unadmitted_delegated_work_spec,
)

_SPAWN = re.compile(
    r"^(?:laat|vraag)\s+(?:een\s+)?onderzoeker(?:\s+om)?\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_STATUS = frozenset(
    {
        "hoe staat mijn onderzoek",
        "hoe staat mijn onderzoek ervoor",
        "status van mijn onderzoek",
    }
)
_STOP = frozenset({"stop mijn onderzoek", "stop het onderzoek"})


class DelegatedWorkConversationCommands:
    """Translate three owner phrases into one sealed PostgreSQL work authority."""

    def __init__(
        self,
        store: PostgresDelegatedWorkStore,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        episode_id: UUID,
        generation: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= generation <= 2_147_483_647:
            raise ValueError("delegated command generation is invalid")
        self._store = store
        self._owner_principal_id = owner_principal_id
        self._principal_binding_id = principal_binding_id
        self._episode_id = episode_id
        self._generation = generation
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self._store.validate_admission_authority(
            episode_id=episode_id,
            generation=generation,
            owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
        )

    def handle(
        self,
        turn: ConversationTurn,
        *,
        principal_binding_id: UUID | None,
    ) -> GroundedReply | None:
        if (
            turn.audience.kind != "private_person"
            or turn.principal_id != str(self._owner_principal_id)
            or principal_binding_id != self._principal_binding_id
        ):
            return None
        text = turn.text.strip()
        normalized = text.casefold().rstrip(".?! ")
        match = _SPAWN.fullmatch(text)
        if match is not None:
            return self._spawn(turn, match.group(1).strip())
        if normalized in _STATUS:
            return self._status(turn)
        if normalized in _STOP:
            return self._stop(turn)
        return None

    def _spawn(self, turn: ConversationTurn, task: str) -> GroundedReply:
        if not task or len(task.encode("utf-8")) > 8_192:
            return self._reply(
                turn,
                "Die onderzoeksopdracht is leeg of te lang. Geef één concrete taak.",
            )
        occurred_at = _timestamp(turn.occurred_at)
        work_id = f"work:eva-chat:{uuid5(NAMESPACE_URL, self._work_key(turn))}"
        spec = build_unadmitted_delegated_work_spec(
            work_id=work_id,
            episode_id=str(self._episode_id),
            generation=self._generation,
            parent_task_id=f"chat:{uuid5(NAMESPACE_URL, self._work_key(turn))}",
            owner_principal_id=str(self._owner_principal_id),
            principal_binding_id=str(self._principal_binding_id),
            task=task,
            created_at=occurred_at,
            deadline=occurred_at + timedelta(minutes=5),
        )
        admission = self._store.admit(
            spec=spec,
            title="Onderzoek vanuit Eva-chat",
            summary="Een afgeschermde onderzoeker werkt aan de gegeven opdracht.",
            worker_role="Onderzoek",
            retention_policy_id="retention:delegated-work:v1",
            retention_until=occurred_at + timedelta(days=7),
            occurred_at=occurred_at,
        )
        del admission
        return self._reply(
            turn,
            "Ik heb één afgeschermde onderzoeker ingepland. "
            "Je ziet de taak op het werkbord.",
        )

    def _status(self, turn: ConversationTurn) -> GroundedReply:
        board = self._store.workboard(
            owner_principal_id=self._owner_principal_id,
            principal_binding_id=self._principal_binding_id,
            now=self._now(),
        )
        if not board.cards:
            return self._reply(turn, "Er staat nog geen onderzoekstaak op je werkbord.")
        counts = {
            column: sum(card.column == column for card in board.cards)
            for column in ("todo", "doing", "attention", "review", "done")
        }
        return self._reply(
            turn,
            "Je werkbord: "
            f"{counts['todo']} wachtend, {counts['doing']} bezig, "
            f"{counts['attention']} vraagt aandacht, {counts['review']} te controleren "
            f"en {counts['done']} klaar.",
        )

    def _stop(self, turn: ConversationTurn) -> GroundedReply:
        work_id = self._store.latest_stoppable_work_id(
            owner_principal_id=self._owner_principal_id,
            principal_binding_id=self._principal_binding_id,
        )
        if work_id is None:
            return self._reply(turn, "Er loopt geen onderzoek dat ik kan stoppen.")
        try:
            self._store.stop_before_call(
                work_id,
                owner_principal_id=self._owner_principal_id,
                principal_binding_id=self._principal_binding_id,
                now=self._now(),
            )
        except DelegatedWorkConflict:
            return self._reply(
                turn,
                "De modelcall is al begonnen. Ik beweer daarom niet dat hij gestopt is; "
                "op het werkbord staat de uitkomst voorlopig als onzeker als de worker uitvalt.",
            )
        return self._reply(
            turn,
            "Gestopt voordat een modelcall begon. Deze taak wordt niet alsnog uitgevoerd.",
        )

    def _reply(self, turn: ConversationTurn, text: str) -> GroundedReply:
        return GroundedReply(
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
            text=text,
            evidence_memory_ids=(),
            generated_at=self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            provider="eva-delegated-work-command",
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
            raise RuntimeError("delegated command clock must use aware whole seconds")
        return value.astimezone(UTC)

    @staticmethod
    def _work_key(turn: ConversationTurn) -> str:
        return ":".join(
            (
                turn.tenant_id,
                turn.installation_id,
                turn.principal_id,
                turn.conversation_id,
                turn.message_id,
            )
        )


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = ["DelegatedWorkConversationCommands"]
