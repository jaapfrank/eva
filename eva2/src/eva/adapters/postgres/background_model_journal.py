"""Per-work model accounting without inventing a second user ingress."""
from __future__ import annotations

from dataclasses import replace

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationConflict, ConversationModelUsage, ConversationTurn
from eva.contracts.canonical_json import canonical_sha256


class BackgroundModelJournal:
    """Reuse durable model budgets with bookkeeping IDs that never reach tools.

    The worker must hold its existing durable claim and cancellation guard.
    No INGRESS_COMMITTED or user reply is written for this accounting identity.
    """

    def __init__(self, journal: PostgresConversationJournal, source_turn: ConversationTurn,
                 *, work_id: str) -> None:
        if source_turn.audience.kind != "private_person" or not work_id.strip():
            raise ConversationConflict("background accounting requires private admitted work")
        self._journal = journal
        self._source = source_turn
        digest = canonical_sha256({
            "tenant": source_turn.tenant_id, "installation": source_turn.installation_id,
            "principal": source_turn.principal_id, "conversation": source_turn.conversation_id,
            "message": source_turn.message_id, "work": work_id,
        })
        self._accounting = replace(source_turn,
            conversation_id="background-accounting:" + digest,
            message_id="background-accounting:" + digest)

    def _turn(self, turn: ConversationTurn) -> ConversationTurn:
        if turn != self._source:
            raise ConversationConflict("background model source differs from admitted work")
        return self._accounting

    def begin_model_request(self, turn: ConversationTurn) -> str:
        # Intentionally bypass ordinary admit()/committed_reply(): the original
        # foreground turn already returned its receipt, and is not this job.
        return self._journal._begin_model_request(self._turn(turn))

    def complete_model_request(self, turn: ConversationTurn, request_id: str,
                               usage: ConversationModelUsage, *, outcome: str = "succeeded",
                               local_stream_closed: bool | None = None) -> None:
        self._journal.complete_model_request(self._turn(turn), request_id, usage,
            outcome=outcome, local_stream_closed=local_stream_closed)

    def model_usage(self, turn: ConversationTurn) -> ConversationModelUsage:
        return self._journal.model_usage(self._turn(turn))
