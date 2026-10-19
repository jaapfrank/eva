"""Owner memory presentation and controls over existing read/learning/erasure routes."""
from datetime import UTC, datetime
from uuid import uuid4
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn, GroundedReply
from eva.application.human_understanding import is_human_understanding_reference, parse_human_understanding_record
from eva.application.memory import RecallCurrentFacts
from eva.application.memory_source_lifecycle import CompactedPrivateMemorySource
from eva.application.memory_audience import MemoryAudience
from eva.providers.human_understanding_actions import ACTION, HumanUnderstandingActions


def _now():
    return datetime.now(UTC).replace(microsecond=0)


def _query(reader, text, now, doc=None):
    principal = str(reader._owner) if doc is None else doc.principal_id
    return RecallCurrentFacts(recall_id="owner-memory-view:" + uuid4().hex,
        tenant_id=reader._tenant, installation_id=reader._installation,
        principal_id=principal, audience=MemoryAudience.private_person(principal_id=principal),
        conversation_id=f"dashboard:{reader._binding}" if doc is None else doc.conversation_id,
        project_id=None if doc is None else doc.project_id, query_purpose="owner_memory_view",
        requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), scope={"kind": "private"},
        subject_ids=(principal,), query_text=text, maximum_facts=12)


def source_view(runtime, source_id, *, now=None):
    now = now or _now(); reader = runtime._owner_memory_sources()
    doc, state = reader.document(source_id, now=now)
    record = None
    current_learning, current_source_id = None, None
    if is_human_understanding_reference(doc.evidence_ref):
        record = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
        context = reader.learning_source(_query(reader, "Inspect selected learning", now, doc),
            doc.source_ref, doc.evidence_ref, now=now, follow_revisions=False)
        if context is None:
            raise LookupError("learning source unavailable")
        current = reader.learning_source(_query(reader, "Inspect current learning", now, doc),
            doc.source_ref, doc.evidence_ref, now=now, follow_revisions=True)
        if current is not None:
            head = current["records"][0]
            current_learning = head["record"]
            resolved = reader.document_reference(doc.principal_id, head["source_ref"], head["evidence_ref"], now=now)
            current_source_id = resolved[0] if resolved is not None else None
    kind = "inference" if doc.trust == "model_inference_unconfirmed" else "legacy" if doc.trust == "legacy_unverified" else "source"
    exchange = doc.document_id == f"{doc.conversation_id}:exchange:{doc.source_ref}"
    return {"contract": "EvaOwnerMemorySourceView/v1", "source_id": source_id,
        "content": (None if isinstance(doc, CompactedPrivateMemorySource)
            else record["interpretation"] if record else doc.content),
        "raw_state": "compacted" if isinstance(doc, CompactedPrivateMemorySource) else "available",
        "occurred_at": doc.occurred_at, "retention_state": state,
        "origin": "Telegram" if doc.conversation_id.startswith("telegram:") else "Eigen geheugen",
        "kind": kind, "trust": doc.trust, "learning": record,
        "current_learning": current_learning, "current_source_id": current_source_id,
        "can_forget": state == "retained" or doc.retention_mode == "dream", "erasure_scope": "exchange" if exchange else "memory_document"}


def memory_view(runtime, text=None, *, now=None):
    fixed_time = now; now = now or _now()
    if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 200):
        raise ValueError("memory search is invalid")
    reader = runtime._owner_memory_sources(); reader.page(now=now, limit=1)
    query = _query(reader, text.strip() if text else "Welke personen, familieleden en belangrijke mensen ken ik uit mijn eigen gesprekken en herinneringen?", now)
    memory = runtime._memory.recall_for_presence(query)
    rows, names = [], []
    for fact in memory.confirmed_facts:
        value = fact.value
        if not isinstance(value, dict) or not isinstance(value.get("text"), str): continue
        provenance = value.get("reference_provenance", {})
        if not isinstance(provenance, dict): continue
        found = reader.document_reference(fact.subject,
            provenance.get("source_ref", fact.source_ref), provenance.get("evidence_ref", fact.evidence_ref), now=now)
        if found is None: continue
        source_id, doc, state = found
        presented_text = value["text"]
        learning = value.get("learning_context")
        if isinstance(learning, dict) and learning.get("records"):
            head = learning["records"][0]
            resolved = reader.document_reference(fact.subject, head["source_ref"], head["evidence_ref"], now=now)
            if resolved is None: continue
            source_id, doc, state = resolved
            presented_text = head["record"]["interpretation"]
        trust = value.get("semantic_trust", doc.trust)
        kind = "inference" if trust in {"model_inference_unconfirmed", "host_policy"} or fact.predicate == "memory.observation" else "legacy" if trust == "legacy_unverified" else "source"
        mentioned = value.get("mentioned_names", [])
        if isinstance(mentioned, list): names.extend(n for n in mentioned if isinstance(n, str) and 0 < len(n) <= 120)
        rows.append({"id": fact.fact_id, "text": presented_text[:4096], "kind": kind,
            "trust": trust, "source_id": source_id, "source_at": doc.occurred_at, "retention_state": state})
    fresh = fixed_time or _now(); reader.page(now=fresh, limit=1)
    return {"contract": "EvaOwnerMemoryView/v1", "owner": reader.owner_identity(now=fresh),
        "mentioned_names": list(dict.fromkeys(names))[:32], "memories": rows[:12],
        "search": text, "complete_directory": False,
        "meaning": "Namen en relevante herinneringen uit jouw eigen geheugen; geen complete personenlijst en geen privégesprekken van anderen."}


def correct_source(runtime, source_id, text, *, now=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 12000: raise ValueError("memory correction is invalid")
    now = now or _now(); reader = runtime._owner_memory_sources(); doc, _ = reader.document(source_id, now=now)
    if not is_human_understanding_reference(doc.evidence_ref): return runtime.dashboard_correct_memory_source(source_id, text)
    old = parse_human_understanding_record(doc.content, evidence_ref=doc.evidence_ref)
    if len(text.strip()) > 1600: raise ValueError("learning correction is too long")
    current = reader.learning_source(_query(reader, "Correct current learning", now, doc),
        doc.source_ref, doc.evidence_ref, now=now, follow_revisions=True)
    if current is None or (current["records"][0]["source_ref"], current["records"][0]["evidence_ref"]) != (doc.source_ref, doc.evidence_ref):
        raise ConversationConflict("learning head changed; inspect the current revision")
    turn = ConversationTurn(tenant_id=doc.tenant_id, installation_id=doc.installation_id,
        principal_id=doc.principal_id, conversation_id=doc.conversation_id,
        message_id="dashboard-learning-correction:" + uuid4().hex,
        occurred_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), audience=doc.audience,
        project_id=doc.project_id, text=text.strip())
    journal = runtime._journal(turn); journal.admit(turn)
    actions = HumanUnderstandingActions(runtime._engine, source_reader=runtime._owner_memory_sources,
        submit=runtime._cold_path.submit, clock=lambda: now)
    receipt = actions.execute(turn, ConversationActionCall("owner-learning-correction", ACTION, {
        "kind": old["kind"], "interpretation": text.strip(),
        "uncertainty_reason": "De eigenaar corrigeerde deze eerdere interpretatie; de revisie blijft een afleiding, geen onafhankelijk vastgesteld feit.",
        "review_at": None, "outcome": "refuted", "previous_source_ref": doc.source_ref,
        "previous_evidence_ref": doc.evidence_ref}))
    if receipt.output.get("status") != "queued": raise ConversationConflict("learning correction storage is unavailable")
    reply = GroundedReply(conversation_id=turn.conversation_id, message_id=turn.message_id,
        text="Je correctie is ontvangen en aan de eerdere aanname gekoppeld.",
        generated_at=turn.occurred_at, provider="owner-memory-controls", evidence_memory_ids=())
    reply = journal.commit_reply(turn, reply); runtime.after_response(turn, reply)
    return {"contract": "EvaOwnerMemoryChange/v1", "source_id": source_id, "state": "correction_queued"}


def forget_source(runtime, source_id, *, now=None):
    now = now or _now(); reader = runtime._owner_memory_sources(); doc, state = reader.document(source_id, now=now)
    if doc.document_id == f"{doc.conversation_id}:exchange:{doc.source_ref}":
        return runtime.dashboard_forget_memory_source(source_id)
    source_view(runtime, source_id, now=now)
    if state != "retained": raise ConversationConflict("learning source is not retained")
    erased = runtime._cold_path.erase_document(principal_id=doc.principal_id,
        document_id=doc.document_id, source_message_id=None, now=now)
    return {"contract": "EvaOwnerMemoryChange/v1", "source_id": source_id,
        "state": "erased" if erased else "erasure_requested"}
