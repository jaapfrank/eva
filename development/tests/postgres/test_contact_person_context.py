"""Fresh retained/pending/erased corrections on the actual light contact boundary."""
from datetime import timedelta
import hashlib
from eva.adapters.projection.hindsight import HindsightMemoryEngine,HindsightProjectionConfig
from eva.application.human_understanding import current_person_context
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_people_memory_overview import (people_memory_engine, selected_private_case,
    _seed_learning, _retain_learning, NOW)


def test_contact_follows_current_correction_blocks_pending_and_erased_successors(selected_private_case):
    case=selected_private_case
    parent=_seed_learning(case,"contact-before",seconds_ago=30,kind="person_hypothesis",review_at=None,
        interpretation="Previous provisional understanding")
    memory=HindsightMemoryEngine(HindsightProjectionConfig("http://127.0.0.1:1"),
        private_source_resolver=lambda query,refs:case.reader.selected_private_sources(query,refs,now=NOW))
    memory.read_style_context=lambda query:None
    first=current_person_context(memory.read_contact_context(case.query))
    assert first["entries"][0]["text"]=="Previous provisional understanding"
    revised=_seed_learning(case,"contact-correction",seconds_ago=20,previous=parent,retained=False,
        kind="person_hypothesis",review_at=None,outcome="refuted",interpretation="Corrected provisional understanding")
    data={"principal_id":parent.principal_id,"project_id":parent.project_id,
        "source_ref":revised.source_ref,"evidence_ref":revised.evidence_ref,
        "previous_source_ref":parent.source_ref,"previous_evidence_ref":parent.evidence_ref}
    case.timeline.create_thread(thread_id="human-understanding-action:"+canonical_sha256(data),
        checkpoint_id="learning-revision:admitted",occurred_at=NOW-timedelta(seconds=20),
        data=data,event_type="HUMAN_UNDERSTANDING_ADMITTED",phase="learning_admitted")
    assert current_person_context(memory.read_contact_context(case.query))["entries"]==[]
    _retain_learning(case,revised)
    later=current_person_context(memory.read_contact_context(case.query))
    assert len(later["entries"])==1
    assert later["entries"][0]["text"]=="Corrected provisional understanding"
    assert later["entries"][0]["outcome"]=="refuted"
    case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
        document_id=revised.document_id,operation_sha256="ae"*32,erased_at=NOW)
    assert current_person_context(memory.read_contact_context(case.query))["entries"]==[]
