"""Bounded recent continuity from the existing verified private memory sources."""
from datetime import UTC, datetime, timedelta
import logging
from eva.application.conversation import ConversationTurn, RecentConversationMessage

_LOG = logging.getLogger(__name__)


def with_private_continuity(runtime, turn, recent):
    if turn.audience.kind != "private_person" or turn.project_id is not None:
        return recent
    now = datetime.now(UTC).replace(microsecond=0)
    recent = _with_delivered_work(runtime, turn, recent, now=now)
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




def _with_delivered_work(runtime, turn, recent, *, now):
    """Include acknowledged work in the same bounded conversation, without replay."""
    service = getattr(runtime, '_delegated_work_owner_service', None)
    bindings_loader = getattr(runtime, '_telegram_principal_bindings_loader', None)
    if service is None or not callable(bindings_loader):
        return recent
    try:
        service.validate_authority(turn)
        from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
        delivery = PostgresDelegatedWorkDeliveryStore(service.store, bindings_loader=bindings_loader,
                                                       clock=lambda: now)
        found = delivery.recent_acknowledged_context(**service._owner,
            conversation_id=turn.conversation_id,
            before=datetime.strptime(turn.occurred_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC), now=now)
        service.validate_authority(turn)
        if found is None:
            return recent
        text = ('Afgeleverd resultaat van mijn opdracht ' + found['title']
                + ' (bewaarde modeluitvoer, geen nieuwe opdracht of geverifieerd wereldfeit):\n'
                + _fragment(found['text'], 8192))
        item = RecentConversationMessage(role='Eva', text=text, occurred_at=found['occurred_at'])
        if any(r.text == item.text and r.occurred_at == item.occurred_at for r in recent):
            return recent
        return tuple(sorted((*recent, item), key=lambda r: r.occurred_at)[-12:])
    except Exception:
        _LOG.debug('Delivered private work continuity unavailable')
        return recent


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


def _exchange_content(runtime, doc, *, before=None, before_exclusive=False):
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
    reply_time = (None if reply is None else datetime.strptime(
        reply.generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
    if reply is None or getattr(reply, "response_mode", "reply") == "silent" or (before is not None and (
            reply_time >= before if before_exclusive else reply_time > before)):
        return _fragment(doc.content, 1800)
    user = _fragment(doc.content, 900)
    label = ("\nEerdere reactie van Eva (modeluitvoer, geen geverifieerd feit; "
             "niet bewezen gelezen of gehoord): ")
    return user + label + _fragment(reply.text, 1800-len(user)-len(label))


def private_initiative_day_context(runtime, *, now):
    """Reuse current authorized day sources before dream consolidation, no retain."""
    from hashlib import sha256
    from eva.contracts.canonical_json import canonical_json_bytes
    try:
        found = runtime._owner_memory_sources().recent_private_exchanges(now=now, limit=3)
        sources = []
        for doc, state in found:
            raw = doc.content.encode("utf-8")
            fragment = raw[:512].decode("utf-8", errors="ignore")
            item = {"document_id": doc.document_id, "principal_id": doc.principal_id,
                "source_ref": doc.source_ref, "evidence_ref": doc.evidence_ref,
                "occurred_at": doc.occurred_at, "content_sha256": sha256(raw).hexdigest(),
                "text": fragment, "truncated": len(fragment.encode("utf-8")) < len(raw),
                "retention_state": state}
            sources.append(item)
        view = {"contract": "EvaPrivateDayContext/v1", "available": True,
            "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": sources,
            "coverage": "at_most_3_current_authorized_private_exchanges_in_last_24h_not_all_family_knowledge",
            "classification": "retained_exchange_not_new_request_or_verified_world_fact",
            "is_execution_permission": False, "truncated": False}
        while len(canonical_json_bytes(view)) > 4096:
            sources.pop()
            view["truncated"] = True
        return view
    except Exception:
        return {"contract": "EvaPrivateDayContext/v1", "available": False,
            "reason": "current_private_day_context_unavailable", "is_execution_permission": False}


def private_initiative_day_context_current(runtime, context, *, now):
    """Recheck the exact selected source, including erasure; new messages may arrive."""
    from hashlib import sha256
    try:
        reader = runtime._owner_memory_sources()
        for item in context["sources"]:
            found = reader.document_reference(item["principal_id"], item["source_ref"],
                item["evidence_ref"], now=now, recent_private_exchange=True)
            if found is None:
                return False
            _identifier, doc, state = found
            if (doc.document_id != item["document_id"] or doc.occurred_at != item["occurred_at"]
                    or doc.retention_mode != "dream" or doc.trust != "direct_user"
                    or doc.project_id is not None
                    or sha256(doc.content.encode("utf-8")).hexdigest() != item["content_sha256"]):
                return False
        return True
    except Exception:
        return False
