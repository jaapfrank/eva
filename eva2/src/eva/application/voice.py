"""The deliberately thin bridge from a final voice turn to Eva's core.

Media adapters may call this only after creating the server-bound V01
``VoiceSession`` and a final ``VoiceTurn``.  They do not get a parallel voice
conversation, memory path or tool path.
"""

from __future__ import annotations

from eva.application.conversation import ConversationTurn
from eva.contracts.voice import VoiceSession, VoiceTurn


def admit_final_voice_turn(
    *, session: VoiceSession, turn: VoiceTurn
) -> ConversationTurn:
    """Convert one verified final transcript into the ordinary private turn.

    This performs no persistence and no memory admission.  The normal
    ConversationTurn receiver remains responsible for exactly those steps,
    just as it is for text from any other connector.
    """

    session.validate_final_turn(turn)
    return ConversationTurn.private(
        tenant_id=turn.tenant_id,
        installation_id=turn.installation_id,
        principal_id=turn.principal_id,
        conversation_id=turn.conversation_id,
        message_id=turn.conversation_message_id,
        text=turn.final_transcript,
        occurred_at=turn.finalized_at,
    )


__all__ = ["admit_final_voice_turn"]
