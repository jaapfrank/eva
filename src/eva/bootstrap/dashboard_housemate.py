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


def _current_learning(reader, query, principals, now):
    """Use exact current heads, revisions and erasure checks; no semantic/model call."""
    result, seen = [], set()
    for source in reader.selected_private_sources(query, (), now=now):
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
            "source_id": source_id, "kind": record["kind"],
            "text": record["interpretation"], "uncertainty_reason": record["uncertainty_reason"],
            "outcome": record["outcome"], "recorded_at": record["recorded_at"],
            "review_at": record["review_at"], "revised": record["previous_source_ref"] is not None,
            "retention_state": retention_state,
            "is_execution_permission": False,
        })
    return sorted(result, key=lambda item: (item["recorded_at"], item["source_id"]), reverse=True)[:MAX_LEARNING_RECORDS]


def housemate_state(runtime):
    # Existing initiative/history screens keep their own route. This projection
    # reads typed source heads only, with no repeated style-mirror/semantic lookup.
    service, turn = owner_turn(runtime)
    principals = _principals(service)
    reader = runtime._owner_memory_sources()
    now = datetime.now(UTC).replace(microsecond=0)
    query = _learning_query(reader, now)
    learning = _current_learning(reader, query, principals, now)
    # Each reader resolves current authority itself; also recheck the enclosing
    # product response after all data was assembled, including alias changes.
    reader.require_learning_query(query, now=datetime.now(UTC).replace(microsecond=0))
    service.validate_authority(turn)
    if _principals(service) != principals:
        raise RuntimeError("private alias changed during housemate read")
    return {
        "contract": "EvaOwnerHousemateState/v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "current_learning": learning,
        "personality_deltas": [],
        "personality_deltas_state": "no_separate_typed_delta_route",
        "learning_limit": MAX_LEARNING_RECORDS,
        "learning_coverage": "recent_current_authorized_heads",
        "shared_trace": None,
        "shared_trace_state": "no_separate_registered_trace_route",
        "curiosity_source": "current_open_project_questions",
        "meaning": "Echte geregistreerde projecten en actuele brongebonden afleidingen. Geen verzonnen Inner State of nieuwe zelfstandige activiteit door deze pagina.",
    }
