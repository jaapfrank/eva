"""Compact development selections; meaning remains in the existing source memory.

The persisted selection contains references only. Every projection resolves current
sources and project status again; an old selection cannot revive a correction,
erased experience or stopped project. Private evidence is never household state.
"""
from __future__ import annotations

from datetime import UTC, datetime

from eva.application.human_understanding import validate_human_understanding_context
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

MAX_PROJECTS = 2
MAX_DELTAS = 3
SELECTION_CONTRACT = "EvaDevelopmentSelection/v1"
STATE_CONTRACT = "EvaHousemateDevelopment/v1"


def _time(value):
    if not isinstance(value, str):
        raise ValueError("development activity time is missing")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("development activity time lacks timezone")
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deltas(sources, principals, now):
    current = {}
    for source in sources:
        principal = source.get("principal_id")
        if principal not in principals:
            continue
        try:
            context = validate_human_understanding_context(source.get("learning_context"),
                source_ref=source["source_ref"], evidence_ref=source["evidence_ref"])
            head = context["records"][0]
            record = head["record"]
            # Generic hypotheses are deliberately not promoted to personality.
            if (record["kind"] != "personality_delta"
                    or record["outcome"] == "refuted"
                    or record["recorded_at"] > now
                    or (record["review_at"] is not None and record["review_at"] <= now)):
                continue
            if head["record"]["recorded_at"] != source["occurred_at"]:
                continue
            ref = {"principal_id": principal, "source_ref": head["source_ref"],
                "evidence_ref": head["evidence_ref"]}
            current[canonical_sha256(ref)] = (ref, record, len(context["records"]))
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    return sorted(current.values(), key=lambda row: (
        row[1]["recorded_at"], canonical_sha256(row[0])), reverse=True)[:MAX_DELTAS]


def _projects(cards, now):
    current = {}
    for card in cards:
        # These are existing authorized owner initiative cards, not a new queue.
        if card.get("status") not in {"working", "attention"}:
            continue
        if card.get("phase") in {"personal_stopped", "release_ready", "initiative_completed"}:
            continue
        if (not isinstance(card.get("intent_id"), str) or not card["intent_id"]
                or not isinstance(card.get("question"), str) or not card["question"].strip()
                or not isinstance(card.get("phase"), str)):
            continue
        try:
            at = _time(card["updated_at"])
        except (ValueError, TypeError, KeyError):
            continue
        if at > now:
            continue
        ref = {"intent_id": card["intent_id"], "status": card["status"],
            "phase": card["phase"], "updated_at": at,
            "source_sha256": canonical_sha256({key: card.get(key) for key in
                ("intent_id", "title", "motivation", "question", "status", "phase", "updated_at")})}
        current[card["intent_id"]] = (ref, card)
    return sorted(current.values(), key=lambda row: (
        row[0]["updated_at"], row[0]["intent_id"]), reverse=True)[:MAX_PROJECTS]


def select_development(*, owner_id, principals, sources, projects, now):
    """Select only current explicit deltas and existing authorized own projects."""
    now = _time(now)
    if not isinstance(owner_id, str) or not owner_id or owner_id not in principals:
        raise ValueError("development selection lacks its current owner")
    deltas, threads = _deltas(sources, principals, now), _projects(projects, now)
    return {"contract": SELECTION_CONTRACT, "owner_id": owner_id,
        "delta_refs": [row[0] for row in deltas],
        "project_refs": [row[0] for row in threads]}


def validate_development_selection(selection):
    """The durable shape admits references only, never an extra text/history field."""
    import re
    if (not isinstance(selection, dict) or set(selection) != {
            "contract", "owner_id", "delta_refs", "project_refs"}
            or selection["contract"] != SELECTION_CONTRACT
            or not isinstance(selection["owner_id"], str) or not selection["owner_id"]
            or not isinstance(selection["delta_refs"], list) or len(selection["delta_refs"]) > MAX_DELTAS
            or not isinstance(selection["project_refs"], list) or len(selection["project_refs"]) > MAX_PROJECTS):
        raise ValueError("development selection contract differs")
    for ref in selection["delta_refs"]:
        if (not isinstance(ref, dict) or set(ref) != {"principal_id", "source_ref", "evidence_ref"}
                or any(not isinstance(v, str) or not 0 < len(v) <= 1024 for v in ref.values())):
            raise ValueError("development delta reference differs")
    for ref in selection["project_refs"]:
        if (not isinstance(ref, dict) or set(ref) != {
                "intent_id", "status", "phase", "updated_at", "source_sha256"}
                or any(not isinstance(v, str) or not 0 < len(v) <= 200 for v in ref.values())
                or ref["status"] not in {"working", "attention"}
                or not re.fullmatch(r"[0-9a-f]{64}", ref["source_sha256"])
                or _time(ref["updated_at"]) != ref["updated_at"]):
            raise ValueError("development project reference differs")
    if (len({canonical_sha256(ref) for ref in selection["delta_refs"]}) != len(selection["delta_refs"])
            or len({ref["intent_id"] for ref in selection["project_refs"]}) != len(selection["project_refs"])):
        raise ValueError("development selection duplicates references")
    return selection


def resolve_development(selection, *, owner_id, principals, sources, projects, now):
    """Resolve a processed selection without falling back to historical sources."""
    now = _time(now)
    validate_development_selection(selection)
    if (not isinstance(selection, dict) or set(selection) != {
            "contract", "owner_id", "delta_refs", "project_refs"}
            or selection["contract"] != SELECTION_CONTRACT
            or selection["owner_id"] != owner_id or owner_id not in principals
            or not isinstance(selection["delta_refs"], list)
            or not isinstance(selection["project_refs"], list)
            or len(selection["delta_refs"]) > MAX_DELTAS
            or len(selection["project_refs"]) > MAX_PROJECTS):
        raise ValueError("development selection scope or contract differs")
    deltas = [row for row in _deltas(sources, principals, now)
              if row[0] in selection["delta_refs"]]
    threads = [row for row in _projects(projects, now)
               if row[0] in selection["project_refs"]]
    if not deltas and not threads:
        return None
    activity_times = [row[1]["recorded_at"] for row in deltas] + [row[0]["updated_at"] for row in threads]
    # This is an observed working stance, not an invented feeling or experience.
    tone = ("own_project_needs_attention" if any(row[0]["status"] == "attention" for row in threads)
        else "continuing_own_project" if threads else "adjusting_from_personal_feedback")
    state = {"contract": STATE_CONTRACT, "audience": "own_private_context",
        "owner_id": owner_id, "is_execution_permission": False,
        "inner_state": {"current_curiosity": threads[0][1]["question"][:160] if threads else None,
            "open_threads": [row[0] for row in threads], "recent_tone": tone,
            "last_updated": max(activity_times), "evidence_refs": [row[0] for row in deltas]},
        "personality_deltas": [{"contract": "EvaPersonalityDelta/v1",
            "adjustment": record["interpretation"], "uncertainty_reason": record["uncertainty_reason"],
            "applies_to": ref["principal_id"], "version": version,
            "recorded_at": record["recorded_at"], "source": ref,
            "previous": (None if record["previous_source_ref"] is None else {
                "source_ref": record["previous_source_ref"], "evidence_ref": record["previous_evidence_ref"]}),
            "epistemic_status": "model_inference_unconfirmed", "outcome": record["outcome"]}
            for ref, record, version in deltas],
        # Private feedback and private project titles never create shared traces.
        "shared_trace": None,
        "coverage": "processed_selection_of_current_private_sources_not_a_complete_life_history"}
    if len(canonical_json_bytes(state)) > 8192:
        raise ValueError("development context exceeds its compact bound")
    return state


def with_current_development(memory, query, projection):
    """Replace optional cached state with current checked context, never a new truth."""
    from dataclasses import replace
    from eva.contracts.memory import RecalledFact
    from eva.contracts.planning import ConfirmedFact

    # A semantic cache can contain an earlier projection. Drop it even when the
    # current read fails, so an erasure/STOP never falls back to cached meaning.
    def own_projection(fact):
        return (fact.predicate == "eva.development_state"
            and fact.fact_id.startswith("eva-development:")
            and fact.source_ref.startswith("eva-development:")
            and fact.evidence_ref.startswith("development-context:"))

    facts = tuple(fact for fact in memory.confirmed_facts if not own_projection(fact))
    ids = {fact.fact_id for fact in facts}
    selected = [item for item in memory.recall.selected if item.fact_id in ids]
    try:
        state = (None if len(facts) >= query.maximum_facts else projection.read_current(query))
    except Exception as error:
        # Optional derived context cannot make the ordinary conversation fail.
        import logging
        logging.getLogger(__name__).warning("Development context unavailable: %s", type(error).__name__)
        state = None
    if state is not None and len(facts) < query.maximum_facts:
        digest = canonical_sha256(state)
        fact_id = "eva-development:" + digest
        # Current typed learning is already part of person context. Do not copy
        # it into a second prompt stack; expose only absent, fitting adjustments.
        known = {(fact.subject, fact.source_ref, fact.evidence_ref) for fact in facts}
        for existing in facts:
            value = existing.value
            if not isinstance(value, dict) or value.get("learning_context") is None:
                continue
            provenance = value.get("reference_provenance", {})
            if not isinstance(provenance, dict):
                continue
            try:
                context = validate_human_understanding_context(value["learning_context"],
                    source_ref=provenance.get("source_ref", existing.source_ref),
                    evidence_ref=provenance.get("evidence_ref", existing.evidence_ref))
                head = context["records"][0]
            except (ValueError, TypeError, KeyError, IndexError):
                continue  # An unchecked payload cannot suppress a genuine current delta.
            known.add((existing.subject, head["source_ref"], head["evidence_ref"]))
        compact = {**state, "personality_deltas":[delta for delta in state["personality_deltas"]
            if (delta["source"]["principal_id"], delta["source"]["source_ref"],
                delta["source"]["evidence_ref"]) not in known]}
        compact["context_omitted_deltas"] = 0
        while len(canonical_json_bytes(compact)) > 2048 and compact["personality_deltas"]:
            compact["personality_deltas"].pop()
            compact["context_omitted_deltas"] += 1
        if len(canonical_json_bytes(compact)) > 2048:
            # Full source refs stay in the bounded owner view. Avoid enlarging
            # ordinary contact for reference metadata already bound by this SHA.
            compact["inner_state"] = {**compact["inner_state"], "evidence_refs":[]}
        if len(canonical_json_bytes(compact)) > 2048:
            return replace(memory, confirmed_facts=facts, recall=memory.recall.model_copy(update={
                "selected":[item.model_copy(update={"rank":rank}) for rank,item in enumerate(selected,start=1)],
                "admitted_fact_ids_sha256":canonical_sha256(sorted(ids))}))
        fact = ConfirmedFact(fact_id=fact_id, subject=query.principal_id,
            predicate="eva.development_state", value={
                "text":canonical_json_bytes(compact).decode("utf-8"),
                "semantic_trust":"model_inference_unconfirmed", "is_execution_permission":False,
                "meaning":"recorded_development_selection_not_verified_traits_or_experiences"},
            source_ref="eva-development:" + state["selection_sha256"],
            evidence_ref="development-context:" + digest,
            observed_at=state["inner_state"]["last_updated"], trust="host_policy")
        facts = (*facts, fact)
        ids.add(fact_id)
        selected.append(RecalledFact(rank=1, fact_id=fact_id,
            source_event_ids=[fact.source_ref], trust="model_inference_unconfirmed",
            ranking_reason="current_source_checked_development_selection"))
    selected = [item.model_copy(update={"rank":rank}) for rank,item in enumerate(selected,start=1)]
    return replace(memory, confirmed_facts=facts, recall=memory.recall.model_copy(update={
        "selected":selected, "admitted_fact_ids_sha256":canonical_sha256(sorted(ids))}))
