"""Bounded recent continuity from the existing verified private memory sources."""
from datetime import UTC, datetime, timedelta
import logging
from eva.application.conversation import RecentConversationMessage

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
    channel = "Telegram" if doc.conversation_id.startswith("telegram:") else "de website"
    content = doc.content
    if len(content) > 1800:
        content = content[:1800] + " [Fragment; de rest is hier niet opgenomen.]"
    context = RecentConversationMessage(
        role="Eerder privégesprek",
        text=f"Bewaarde context via {channel}, geen nieuwe opdracht: " + content,
        occurred_at=doc.occurred_at)
    return (*recent[-11:], context)
