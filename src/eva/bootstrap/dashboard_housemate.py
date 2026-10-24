"""Owner product projection over existing initiative and typed-learning readers."""
from datetime import UTC, datetime
from uuid import uuid4

from eva.application.human_understanding import (
    MAX_LEARNING_RECORDS, validate_human_understanding_context,
)
from eva.application.memory import RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.bootstrap.dashboard_learning import _principals
from eva.bootstrap.dashboard_work_context import owner_turn


def _learning_query(reader, now):
    principal = str(reader._owner)
    return RecallCurrentFacts(
        recall_id="owner-housemate:" + uuid4().hex,
        tenant_id=reader._tenant, installation_id=reader._installation,
        principal_id=principal, conversation_id=f"dashboard:{reader._binding}",
        audience=MemoryAudience.private_person(principal_id=principal), project_id=None,
        query_purpose="owner_memory_view", requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        scope={"kind": "private"}, subject_ids=(principal,),
        query_text="Actuele brongebonden persoonlijke bijstellingen", maximum_facts=MAX_LEARNING_RECORDS,
    )


def _learning_rows(reader, query, principals, now, references=()):
    """Use exact current heads, revisions and erasure checks; no semantic/model call."""
    result, seen = [], set()
    for source in reader.selected_private_sources(query, references, now=now):
        principal = source.get("principal_id")
        if principal not in principals:
            raise RuntimeError("current learning owner differs")
        context = source.get("learning_context")
        if context is None:
            continue
        context = validate_human_understanding_context(
            context, source_ref=source["source_ref"], evidence_ref=source["evidence_ref"])
        head = context["records"][0]
        key = (principal, head["source_ref"], head["evidence_ref"])
        if key in seen:
            continue
        seen.add(key)
        found = reader.document_reference(*key, now=now)
        if found is None:
            continue  # Erased, expired or no longer reachable: do not show old text.
        source_id, document, retention_state = found
        if (document.principal_id, document.source_ref, document.evidence_ref) != key:
            raise RuntimeError("current learning source identity differs")
        record = head["record"]
        result.append({
            "reference": key, "record": record, "version": len(context["records"]), "view": {
            "source_id": source_id, "kind": record["kind"],
            "text": record["interpretation"], "uncertainty_reason": record["uncertainty_reason"],
            "outcome": record["outcome"], "recorded_at": record["recorded_at"],
            "review_at": record["review_at"], "revised": record["previous_source_ref"] is not None,
            "retention_state": retention_state,
            "is_execution_permission": False,
        }})
    ordered = sorted(result, key=lambda item: (item["view"]["recorded_at"], item["view"]["source_id"]), reverse=True)
    return ([row for row in ordered if row["reference"] in references] if references
            else ordered[:MAX_LEARNING_RECORDS])



def _development(runtime, query, principals):
    projection = getattr(getattr(runtime, "_memory", None), "_development_projection", None)
    if projection is None:
        return None, "not_configured", False
    try:
        state = projection.read_current(query)
    except Exception:
        return None, "unavailable", True
    failed = getattr(projection, "last_error", None) is not None
    if state is None:
        return None, "unavailable" if failed else "no_processed_current_sources", failed
    if (not isinstance(state, dict) or state.get("contract") != "EvaHousemateDevelopment/v1"
            or state.get("audience") != "own_private_context"
            or state.get("owner_id") != query.principal_id
            or state.get("is_execution_permission") is not False
            or state.get("shared_trace") is not None
            or not isinstance(state.get("inner_state"), dict)
            or not isinstance(state.get("personality_deltas"), list)
            or len(state["personality_deltas"]) > 3):
        raise RuntimeError("development source owner or contract differs")
    for ref in state["inner_state"].get("evidence_refs", []):
        if not isinstance(ref, dict) or ref.get("principal_id") not in principals:
            raise RuntimeError("development evidence owner differs")
    for delta in state["personality_deltas"]:
        if (delta.get("contract") != "EvaPersonalityDelta/v1"
                or delta.get("source", {}).get("principal_id") not in principals):
            raise RuntimeError("development adjustment owner differs")
    return state, "available", failed


def housemate_state(runtime):
    # Project/history/E112 controls keep their own existing route. The same memory
    # projection that ordinary contact uses supplies source-bound development.
    service, turn = owner_turn(runtime)
    principals = _principals(service)
    reader = runtime._owner_memory_sources()
    now = datetime.now(UTC).replace(microsecond=0)
    query = _learning_query(reader, now)
    development, development_status, processing_attention = _development(runtime, query, principals)
    rows = _learning_rows(reader, query, principals, now)
    by_reference = {row["reference"]: row for row in rows}
    processed = {}
    if development is not None:
        references = tuple((ref["principal_id"], ref["source_ref"], ref["evidence_ref"])
            for ref in development["inner_state"]["evidence_refs"])
        missing = tuple(ref for ref in references if ref not in by_reference)
        if missing:
            by_reference.update((row["reference"], row) for row in
                _learning_rows(reader, query, principals, now, missing))
        # No old state survives a source erased/revised between the two readers.
        if any(ref not in by_reference for ref in references):
            development, development_status = None, "source_changed"
        else:
            for delta in development["personality_deltas"]:
                ref = delta["source"]
                key = (ref["principal_id"], ref["source_ref"], ref["evidence_ref"])
                row = by_reference.get(key)
                if (row is None or row["record"]["kind"] != "personality_delta"
                        or row["record"]["interpretation"] != delta["adjustment"]
                        or row["record"]["uncertainty_reason"] != delta["uncertainty_reason"]
                        or row["record"]["outcome"] != delta["outcome"]
                        or row["version"] != delta["version"]):
                    raise RuntimeError("development adjustment and current source differ")
                processed[key] = delta
    deltas = []
    for row in sorted(by_reference.values(), key=lambda row: (
            row["view"]["recorded_at"], row["view"]["source_id"]), reverse=True):
        if row["record"]["kind"] != "personality_delta":
            continue  # A person's hypothesis is never renamed to Eva's personality.
        item, record = row["view"], row["record"]
        active = record["outcome"] != "refuted" and not (
            record["review_at"] is not None and record["review_at"] <= query.requested_valid_at)
        deltas.append({**item, "contract":"EvaPersonalityDeltaView/v1", "version":row["version"],
            "audience":"own_private_context", "active":active,
            "processing_state":"inactive" if not active else
                "processed" if row["reference"] in processed else "awaiting_dream",
            "epistemic_status":"model_inference_unconfirmed"})
        if len(deltas) == 3:
            break
    inner = None
    if development is not None:
        current = development["inner_state"]
        inner = {key:current[key] for key in
            ("current_curiosity", "open_threads", "recent_tone", "last_updated")}
        inner["evidence_count"] = len(current["evidence_refs"]) + len(current["open_threads"])
    # Current readers enforce erasure and binding individually. Recheck enclosing
    # owner/alias authority after the combined view as well.
    reader.require_learning_query(query, now=datetime.now(UTC).replace(microsecond=0))
    service.validate_authority(turn)
    if _principals(service) != principals:
        raise RuntimeError("private alias changed during housemate read")
    return {
        "contract": "EvaOwnerHousemateState/v1", "checked_at":datetime.now(UTC).isoformat(),
        "current_learning":[row["view"] for row in rows if row["record"]["kind"] != "personality_delta"],
        "learning_limit":MAX_LEARNING_RECORDS, "learning_coverage":"recent_current_authorized_heads",
        "personality_deltas":deltas,
        "personality_deltas_state":"current_typed_sources" if deltas else "no_current_typed_sources",
        "inner_state":inner, "development_state":development_status,
        "development_attention":processing_attention,
        "shared_trace":None, "shared_trace_state":"no_current_authorized_shared_source",
        "curiosity_source":"processed_current_development" if inner else "current_open_project_questions",
        "meaning":"Echte brongebonden bijstellingen en verwerkte ontwikkeling. Geen volledige levensgeschiedenis, bewezen gevoel of nieuwe uitvoertoestemming.",
    }
