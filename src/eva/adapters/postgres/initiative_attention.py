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


def _retained_source(connection, store, source_event_id):
    """Read an exact retained observation under the existing installation scope."""
    from eva.application.sensory import SensoryEvent
    if not isinstance(source_event_id, str) or not 0 < len(source_event_id) <= 240:
        raise ValueError("attention source identity is invalid")
    e, scope = events.c, store._timeline._scope_value
    row = connection.execute(sa.select(e.payload, e.payload_sha256, e.occurred_at).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == "sensory:" + source_event_id, e.sequence == 1,
        e.event_type == "SENSORY_EVENT_OBSERVED")).one()
    if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
        raise ValueError("attention observation hash differs")
    data = row.payload["data"]
    if data["source_event_id"] != source_event_id:
        raise ValueError("attention observation identity differs")
    return row, SensoryEvent(data["source"], source_event_id, data["event_type"],
                            row.occurred_at, data["payload"])


def _current_source(store, event):
    from eva.application.sensory import SensoryAttentionSource
    resolver = store._attention_source_resolver
    if resolver is None:
        raise ValueError("configured source has no supported event coverage")
    value = resolver(event)
    if (not isinstance(value, SensoryAttentionSource) or value.source != event.source
            or value.event_type != event.event_type
            or event.payload.get(value.selector_key) != value.selector_value):
        raise ValueError("current event source coverage differs")
    return value.to_payload()


def _read_selected_observations(connection, store, context, source_outcomes, now):
    """Join host-verified native read selectors to real retained event revisions.

    A tool receipt is not a sensory event. Missing history/current provider coverage
    leaves a target unavailable; no synthetic change, connector call or new grant.
    """
    import re
    from eva.application.sensory import SensoryEvent
    if (now is None or now.tzinfo is None or now.utcoffset() != timedelta(0)
            or now.microsecond or context["observed_at_utc"] != now.isoformat()):
        raise ValueError("read-selected attention context clock differs")
    e, scope = events.c, store._timeline._scope_value
    selected, seen = [], set()
    text = lambda value, limit: value[:limit] if isinstance(value, str) else None
    for outcome in source_outcomes[:3]:
        for read in outcome.get("verified_reads", ())[:4]:
            if read.get("status") != "verified_original_read":
                continue
            receipt_sha = read.get("source", {}).get("receipt_sha256")
            if re.fullmatch(r"[0-9a-f]{64}", str(receipt_sha)) is None:
                continue
            for target in read.get("attention_selectors", ())[:4]:
                keys = {"source", "event_type", "selector_key", "selector_value"}
                if (not isinstance(target, dict) or set(target) != keys
                        or any(not isinstance(v, str) or not v or len(v) > 255 for v in target.values())
                        or not target["selector_key"].isidentifier() or len(target["selector_key"]) > 80):
                    continue
                key = tuple(target[k] for k in sorted(keys))
                if key in seen:
                    continue
                seen.add(key)
                row = connection.execute(sa.select(e.payload, e.payload_sha256, e.occurred_at).where(
                    e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
                    e.event_type == "SENSORY_EVENT_OBSERVED", e.sequence == 1,
                    e.payload["data"]["source"].astext == target["source"],
                    e.payload["data"]["event_type"].astext == target["event_type"],
                    e.payload["data"]["payload"][target["selector_key"]].astext == target["selector_value"],
                    e.occurred_at >= now - timedelta(hours=24), e.occurred_at <= now,
                ).order_by(e.occurred_at.desc(), e.thread_id).limit(1)).first()
                if row is None:
                    continue
                if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
                    raise ValueError("read-selected attention observation hash differs")
                data = row.payload["data"]; payload = data["payload"]
                event = SensoryEvent(data["source"], data["source_event_id"], data["event_type"], row.occurred_at, payload)
                try:
                    _current_source(store, event)
                except Exception:
                    continue
                selected.append({"source": event.source, "source_event_id": event.source_event_id,
                    "source_sha256": bytes(row.payload_sha256).hex(), "observed_at": row.occurred_at.isoformat(),
                    "entity_id": text(payload.get("entity_id"), 255), "friendly_name": text(payload.get("friendly_name"), 80),
                    "state": text(payload.get("new_state"), 80), "device_class": text(payload.get("device_class"), 40),
                    "unit_of_measurement": text(payload.get("unit_of_measurement"), 24),
                    "historical": True, "is_current_proof": False,
                    "attention_selection": {"kind": "verified_native_read_target",
                        "work_id": outcome.get("work_id"), "receipt_sha256": receipt_sha}})
                if len(selected) == 4:
                    return selected
    context.get("coverage", {})["read_selected_sources_without_current_coverage"] = len(seen) - len(selected)
    return selected


def annotate_attention_sources(connection, store, context, *, source_outcomes=(), now=None):
    """Offer bounded provider-owned metadata, never a new tool/subscription grant."""
    if source_outcomes:
        preferred = _read_selected_observations(connection, store, context, source_outcomes, now)
        identities = {v["source_event_id"] for v in preferred}
        values = preferred + [v for v in context.get("observations", ()) if v["source_event_id"] not in identities]
        context["observations"] = values[:8]
        context.get("coverage", {})["verified_read_targets_offered"] = len(preferred)
        if len(values) > 8:
            context.get("coverage", {})["observations_truncated"] = True
    for observation in context.get("observations", ()):
        try:
            row, event = _retained_source(connection, store, observation["source_event_id"])
            if bytes(row.payload_sha256).hex() != observation["source_sha256"]:
                raise ValueError("offered observation hash differs")
            source = _current_source(store, event)
            observation["source_attention"] = {"available": True,
                "configuration_sha256": source["configuration_sha256"], "coverage": source["coverage"],
                "classification": "configured_coverage_not_live_access_or_action_permission"}
        except Exception:
            observation["source_attention"] = {"available": False,
                "reason": "no_current_supported_event_coverage; use_existing_native_read_if_permitted"}
    # Keep actionable coverage on the newest offered sources instead of silently
    # removing every descriptor from an already full context. This only narrows a
    # read projection; original observations and active commitments are unchanged.
    from eva.contracts.canonical_json import canonical_json_bytes
    omitted = 0
    while len(canonical_json_bytes(context)) > 8192 and context.get("observations"):
        context["observations"].pop()
        omitted += 1
    if omitted:
        # Existing coverage flags preserve the original contract and byte bound.
        coverage = context.get("coverage", {})
        for key in ("observations_truncated", "byte_budget_truncated"):
            if key in coverage:
                coverage[key] = True


def bind_attention_source(connection, store, choice, original, *, now):
    """Bind a model-chosen offered source to its exact revision and own reason."""
    if choice.source_event_id is None:
        return None
    if store._household_binding is not None or store._attention_source_resolver is None:
        raise ValueError("source attention requires its configured private source route")
    store._initiative_authorizer()
    payload = original.payload
    options = list(payload.get("life_context", {}).get("observations", ()))
    options += [payload[k] for k in ("attention_source", "attention_source_change")
                if isinstance(payload.get(k), dict)]
    selected = next((item for item in options if item.get("source_event_id") == choice.source_event_id), None)
    if selected is None:
        raise ValueError("attention source was not offered to this decision")
    row, event = _retained_source(connection, store, choice.source_event_id)
    if bytes(row.payload_sha256).hex() != selected.get("source_sha256") or event.occurred_at > original.occurred_at:
        raise ValueError("attention source revision differs from its offered context")
    current = _current_source(store, event)
    offered = selected.get("source_attention", selected.get("binding"))
    offered_keys = (("configuration_sha256", "coverage") if "source_attention" in selected else tuple(current))
    if (selected.get("available", True) is not True or not isinstance(offered, dict)
            or offered.get("available", True) is not True
            or any(offered.get(k) != current[k] for k in offered_keys)):
        raise ValueError("attention source coverage changed before commit")
    return {"source_event_id": event.source_event_id, "source_sha256": bytes(row.payload_sha256).hex(),
        "selected_at": format_required_utc_timestamp(now), "binding": current}


def source_attention_trigger(connection, store, reference, *, now):
    """Match one current source revision in the existing one-shot attention lane."""
    if store._household_binding is not None:
        return None
    e, scope = events.c, store._timeline._scope_value
    row = connection.execute(sa.select(e.payload, e.payload_sha256).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == reference["source_thread_id"], e.sequence == reference["source_decision_sequence"])).one()
    if (canonical_sha256(row.payload) != bytes(row.payload_sha256).hex()
            or bytes(row.payload_sha256).hex() != reference["source_decision_sha256"]):
        raise ValueError("source attention decision hash differs")
    watch = row.payload["data"]["next_attention"].get("source_watch")
    if watch is None:
        return None
    retained, original = _retained_source(connection, store, watch["source_event_id"])
    if bytes(retained.payload_sha256).hex() != watch["source_sha256"]:
        raise ValueError("source attention retained revision differs")
    source = {**watch, "available": False, "is_current_proof": False,
        "classification": "configured_coverage_not_live_access_or_action_permission"}
    try:
        current = _current_source(store, original)
        if current != watch["binding"]:
            raise ValueError("source configuration changed")
    except Exception:
        source["reason"] = "configured_source_no_longer_available; discover_or_reconnect_before_native_read"
        return {"source": source, "change": None}
    source["available"] = True
    selected_at = parse_utc_timestamp(watch["selected_at"])
    if now < selected_at + timedelta(seconds=300):
        return {"source": source, "change": None}
    # Pure indexed source selection; no provider/model polling. One attention
    # identity consumes all reconnects/duplicates until Eva explicitly chooses again.
    changed = connection.execute(sa.select(e.payload, e.payload_sha256, e.occurred_at).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.event_type == "SENSORY_EVENT_OBSERVED", e.sequence == 1,
        e.payload["data"]["source"].astext == current["source"],
        e.payload["data"]["event_type"].astext == current["event_type"],
        e.payload["data"]["payload"][current["selector_key"]].astext == current["selector_value"],
        e.occurred_at > selected_at, e.occurred_at <= now,
        e.occurred_at >= now - store._observation_freshness,
    ).order_by(e.occurred_at.desc(), e.thread_id).limit(1)).first()
    if changed is None:
        return {"source": source, "change": None}
    if canonical_sha256(changed.payload) != bytes(changed.payload_sha256).hex():
        raise ValueError("changed source hash differs")
    data = changed.payload["data"]
    change = {"source_event_id": data["source_event_id"], "source_sha256": bytes(changed.payload_sha256).hex(),
        "source": current["source"], "observed_at": format_required_utc_timestamp(changed.occurred_at),
        "event_type": data["event_type"], "observation": data["payload"],
        "binding": current, "is_current_proof": False, "replayed": False}
    from eva.contracts.canonical_json import canonical_json_bytes
    if len(canonical_json_bytes(change)) > 4096:
        return {"source": source, "change": None}  # Scheduled exact-source read remains available.
    return {"source": source, "change": change}
