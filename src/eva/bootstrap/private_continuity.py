"""Bounded recent continuity from the existing verified private memory sources."""
from datetime import UTC, datetime, timedelta
import logging
from eva.application.conversation import ConversationTurn, RecentConversationMessage

_LOG = logging.getLogger(__name__)


def with_private_continuity(runtime, turn, recent):
    if turn.audience.kind != "private_person" or turn.project_id is not None:
        return recent
    now = datetime.now(UTC).replace(microsecond=0)
    # Longer-term topical recall stays with the existing memory implementation.
    cutoff = now - timedelta(hours=24)
    if recent:
        cutoff = max(cutoff, max(datetime.strptime(item.occurred_at, "%Y-%m-%dT%H:%M:%SZ")
                                .replace(tzinfo=UTC) for item in recent))
    try:
        found = runtime._owner_memory_sources().recent_other_channel(turn, now=now, after=cutoff)
    except Exception:
        _LOG.debug("Private channel continuity unavailable")
        return recent
    if found is None:
        return recent
    doc, _ = found
    channel = ("Telegram" if doc.conversation_id.startswith("telegram:") else
               "een privéspraakgesprek" if doc.conversation_id.startswith("voice:") else
               "de website")
    content = _exchange_content(runtime, doc)
    context = RecentConversationMessage(
        role="Eerder privégesprek",
        text=f"Bewaarde context via {channel}, geen nieuwe opdracht: " + content,
        occurred_at=doc.occurred_at)
    return (*recent[-11:], context)



def search_private_history(runtime, turn, query_text):
    """Retrieve retained exchanges on demand, without extending initial prompts."""
    if turn.audience.kind != "private_person" or turn.project_id is not None:
        return ()
    try:
        found = runtime._owner_memory_sources().search_private_history(
            turn, query_text, now=datetime.now(UTC).replace(microsecond=0), limit=3,
        )
        return tuple(RecentConversationMessage(
            role="Eerder privégesprek",
            text="Bewaarde privé-uitwisseling, geen nieuwe opdracht: " + _exchange_content(runtime, doc),
            occurred_at=doc.occurred_at,
        ) for doc, _state in found)
    except Exception:
        # Semantic memory remains available when historical source retrieval fails.
        _LOG.debug("Private conversation history search unavailable")
        return ()


def _fragment(text, maximum):
    suffix = " [Fragment; overige tekst niet opgenomen.]"
    return text if len(text) <= maximum else text[:maximum-len(suffix)] + suffix


def _exchange_content(runtime, doc):
    """Pair one authorized memory source with its exact retained assistant reply.

    The memory projection already admits the current private owner, source,
    audience, retention and erasure. Journal lookup binds the reply to that
    source's principal, conversation, message and project, never a nearby turn.
    Neither source is promoted to a verified fact or an audio-delivery receipt.
    """
    try:
        source_turn = ConversationTurn(
            tenant_id=doc.tenant_id, installation_id=doc.installation_id,
            principal_id=doc.principal_id, conversation_id=doc.conversation_id,
            message_id=doc.source_ref, text=doc.content[:4096],
            occurred_at=doc.occurred_at, audience=doc.audience, project_id=doc.project_id,
        )
        reply = runtime._journal(source_turn).committed_reply(source_turn)
    except Exception:
        # Missing, erased or invalid replies must not obstruct ordinary speech.
        _LOG.debug("Private cross-channel reply unavailable")
        reply = None
    if reply is None:
        return _fragment(doc.content, 1800)
    user = _fragment(doc.content, 900)
    label = ("\nEerdere reactie van Eva (modeluitvoer, geen geverifieerd feit; "
             "niet bewezen gelezen of gehoord): ")
    return user + label + _fragment(reply.text, 1800-len(user)-len(label))
