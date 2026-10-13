"""Return saved work through the existing runtime loop, without model calls."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eva.adapters.connectors.telegram import TelegramProactiveDelivery
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore


def result_messages(work_id: str, output: str, outcome: str = "completed") -> tuple[str, ...]:
    """Keep the complete saved result, including sources, within transport bounds."""
    if not output.strip() or len(output.encode('utf-8')) > 65_536:
        raise ValueError('saved result is outside its contract')
    if not work_id.strip() or len(work_id) > 240:
        raise ValueError('work identity is outside its contract')
    # Telegram counts UTF-16 units. Reserving 1096 units covers the bounded
    # identity, part label and non-BMP characters without cutting code points.
    pieces: list[str] = []
    part: list[str] = []
    units = 0
    for character in output:
        size = 2 if ord(character) > 0xFFFF else 1
        if units + size > 3000:
            pieces.append(''.join(part))
            part, units = [], 0
        part.append(character)
        units += size
    if part:
        pieces.append(''.join(part))
    return tuple(
        f'{"Opdracht niet afgerond" if outcome in {"failed", "needs_help", "ambiguous"} else "Documentproject afgerond" if work_id.startswith("document-task:") else "Opdracht afgerond"} · {work_id}\nDeel {index}/{len(pieces)}\n\n{piece}'
        for index, piece in enumerate(pieces, 1)
    )


class DelegatedWorkDeliveryWorker:
    def __init__(self, store: PostgresDelegatedWorkDeliveryStore,
                 delivery: TelegramProactiveDelivery, *,
                 clock: Callable[[], datetime] | None = None):
        self._store, self._delivery = store, delivery
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    def run_once(self, *, saved_document_work_id: str | None = None) -> str:
        options = {} if saved_document_work_id is None else {'saved_document_work_id': saved_document_work_id}
        claim = self._store.claim_next(now=self._clock(), **options)
        if claim is None:
            return 'idle'
        try:
            authority = dict(owner_principal_id=claim.owner_principal_id,
                             principal_binding_id=claim.principal_binding_id)
            if getattr(claim, 'artifact_sha256', None) is not None:
                authority['artifact_sha256'] = claim.artifact_sha256
            result = self._store.authorize_delivery(claim.work_id, **authority)
            if result is None or result.operation_id != claim.operation_id:
                raise RuntimeError('delivery authorization changed')
            if getattr(claim, 'artifact_sha256', None) is not None:
                current = self._store.authorize_delivery(claim.work_id, **authority)
                if current != result or self._clock() >= claim.deadline:
                    raise RuntimeError('delivery authorization expired or changed')
                receipt = self._delivery.deliver_document(
                    result.output, expected_chat_id=result.destination.chat_id,
                    reply_to_message_id=result.destination.telegram_message_id,
                )
                return self._store.acknowledge(claim, receipt, now=self._clock())
            messages = result_messages(claim.work_id, result.output, result.outcome)
            for message in messages:
                # Every part rechecks authority; a changed pairing cannot receive
                # the remainder of a result belonging to the previous person.
                current = self._store.authorize_delivery(claim.work_id, **authority)
                if (current is None or current != result or self._clock() >= claim.deadline):
                    raise RuntimeError('delivery authorization expired or changed')
                receipt = self._delivery.deliver_text(
                    message, expected_chat_id=result.destination.chat_id,
                    reply_to_message_id=result.destination.telegram_message_id,
                )
            # All parts have verified provider receipts. The final receipt closes
            # this result; a crash before this commit stays ambiguous, never replayed.
            return self._store.acknowledge(claim, receipt, now=self._clock())
        except Exception as error:
            self._store.mark_ambiguous(claim, type(error).__name__, now=self._clock())
            return 'ambiguous'
