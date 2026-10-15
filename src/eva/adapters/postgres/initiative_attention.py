"""Model-selected attention on the existing sensory timeline, without another planner."""
from datetime import timedelta
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import operational_events as events, operational_checkpoints as checkpoints
from eva.application.resident_attention import NextAttention, validate_attention_reference
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import format_required_utc_timestamp, parse_utc_timestamp


def attention_fields(choice, *, now):
    if choice is None:
        return {}
    requested = choice.to_payload()
    if choice.at <= now:
        return {"rejected_next_attention": requested, "attention_rejection": "not_future_at_commit"}
    effective = max(choice.at, now + timedelta(seconds=300))
    return {"next_attention": {"requested_at": requested["at"],
        "at": format_required_utc_timestamp(effective), "reason": choice.reason}}


def attention_suffix(reference):
    return str(int(canonical_sha256({"model_attention": reference}), 16))


def _original(connection, store, thread):
    e, scope = events.c, store._timeline._scope_value
    row = connection.execute(sa.select(e.payload, e.payload_sha256, e.occurred_at).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == thread, e.sequence == 1, e.event_type == "SENSORY_EVENT_OBSERVED")).one()
    if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
        raise ValueError("attention original source hash differs")
    return row


def pending_attention(connection, store, *, now, include_consumed=False):
    """One latest nonnull choice per exact audience; unrelated silence keeps it."""
    e, c, scope = events.c, checkpoints.c, store._timeline._scope_value
    row = connection.execute(sa.select(e.thread_id, e.sequence, e.payload,
        e.payload_sha256).where(e.tenant_id == scope.tenant_id,
        e.installation_id == scope.installation_id,
        e.thread_id.startswith(store._decision_prefix),
        e.event_type.in_(("WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED")),
        e.payload["data"]["next_attention"].astext.is_not(None),
        e.occurred_at <= now).order_by(
            e.payload["data"]["next_attention"]["selection_sequence"].astext.cast(sa.BigInteger).desc()).limit(1)).first()
    if row is None:
        return None
    if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
        raise ValueError("attention decision hash differs")
    data = row.payload["data"]
    selected = data["next_attention"]
    choice = NextAttention.from_payload({"at": selected["requested_at"], "reason": selected["reason"]})
    original = _original(connection, store, row.thread_id)
    source = original.payload["data"]
    previous = source["payload"].get("attention_origin")
    if previous is not None:
        validate_attention_reference(previous, prefix=store._decision_prefix)
    origin_thread = row.thread_id if previous is None else previous["origin_thread_id"]
    origin = original if origin_thread == row.thread_id else _original(connection, store, origin_thread)
    # A stopped originating intent must not be resurrected by its timer. Its
    # private metadata is checked here and is never copied into shared context.
    origin_decision = connection.execute(sa.select(e.payload, e.payload_sha256).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == origin_thread,
        e.event_type.in_(("WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED")))
        .order_by(e.sequence.desc()).limit(1)).one()
    if canonical_sha256(origin_decision.payload) != bytes(origin_decision.payload_sha256).hex():
        raise ValueError("attention origin decision hash differs")
    intent = (origin_decision.payload["data"].get("initiative") or {}).get("thread_id")
    if intent is not None:
        phase = connection.execute(sa.select(c.phase).where(c.tenant_id == scope.tenant_id,
            c.installation_id == scope.installation_id, c.thread_id == intent)).scalar_one_or_none()
        if phase is None or phase in {"personal_stopped", "will_abandoned"}:
            return None
    reference = {"source_thread_id": row.thread_id,
        "source_event_id": source["source_event_id"], "source_decision_sequence": row.sequence,
        "source_decision_sha256": bytes(row.payload_sha256).hex(),
        "source_occurred_at": format_required_utc_timestamp(original.occurred_at),
        "origin_thread_id": origin_thread,
        "origin_occurred_at": format_required_utc_timestamp(origin.occurred_at),
        "requested_at": format_required_utc_timestamp(choice.at),
        "effective_at": selected["at"], "reason": choice.reason}
    validate_attention_reference(reference, prefix=store._decision_prefix)
    if not include_consumed:
        consumed = connection.execute(sa.select(c.thread_id).where(c.tenant_id == scope.tenant_id,
            c.installation_id == scope.installation_id,
            c.thread_id == store._decision_prefix + attention_suffix(reference))).first()
        if consumed is not None:
            return None
    return reference


def assign_attention_sequence(connection, store, data):
    """Serialize choices under the scheduler's lane lock, including same-second commits."""
    e, scope = events.c, store._timeline._scope_value
    lane = "sensory:household-initiative:" if store._household_binding is not None else store._decision_prefix
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lane})
    previous = connection.scalar(sa.select(sa.func.max(
        e.payload["data"]["next_attention"]["selection_sequence"].astext.cast(sa.BigInteger))).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id.startswith(store._decision_prefix),
        e.event_type.in_(("WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED"))))
    data["next_attention"]["selection_sequence"] = (previous or 0) + 1
