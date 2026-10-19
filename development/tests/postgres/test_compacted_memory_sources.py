"""Real current owner reads keep selected knowledge after day-text compaction."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from types import SimpleNamespace
import pytest
import sqlalchemy as sa
from tests.postgres.test_people_memory_overview import (
    people_memory_engine, selected_private_case, _document, NOW,
)
from tests.postgres.test_memory_source_lifecycle import complete_dream_exchange
from eva.application.memory_audience import MemoryAudience
from eva.application.memory_source_lifecycle import CompactedPrivateMemorySource
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.adapters.postgres.current_catalog import metadata
from eva.bootstrap.dashboard_memory import source_view, memory_view
from eva.contracts.planning import ConfirmedFact


def _ready(c):
    conv = f"dashboard:{c.binding}"; message = "day-source:" + str(c.owner)
    doc = replace(_document(message), tenant_id=c.reader._tenant, installation_id=c.reader._installation,
        principal_id=str(c.owner), conversation_id=conv, source_ref=message,
        evidence_ref="conversation-reply:" + message, document_id=f"{conv}:exchange:{message}",
        content="Selected relevant personal knowledge", occurred_at=(NOW-timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        audience=MemoryAudience.private_person(principal_id=str(c.owner)), project_id=None, retention_mode="dream")
    case = complete_dream_exchange(c.engine, doc, now=NOW)
    assert len(case.outbox.compact_day_sources(now=NOW)) == 1
    ref = (doc.principal_id, doc.source_ref, doc.evidence_ref)
    query = replace(c.query, project_id=None)
    return case, ref, query


def test_compacted_owner_sources_keep_provenance_without_restoring_day_text(selected_private_case):
    c=selected_private_case; f,ref,query = _ready(c)
    source_id,doc,state = c.reader.document_reference(*ref,now=NOW)
    assert isinstance(doc,CompactedPrivateMemorySource) and not hasattr(doc,"content")
    assert state == "retained"
    page = c.reader.page(now=NOW)
    assert len(page.sources)==1 and page.sources[0].raw_state=="compacted"
    result = c.reader.selected_private_sources(query,(ref,),now=NOW)
    assert len(result)==1 and result[0]["text"] is None and result[0]["raw_state"]=="compacted"
    assert "synthetic direct source" not in str(result)
    assert c.reader.recent_other_channel(f.turn,now=NOW) is None
    assert c.reader.search_private_history(f.turn,"synthetic",now=NOW)==()
    assert f.journal.recent(f.turn,limit=6)==()
    flow=PostgresPeopleMemoryOverview(c.engine,tenant_id=doc.tenant_id,installation_id=doc.installation_id)
    assert flow.snapshot(now=NOW,hindsight_state="ready").memory_flow=="up_to_date"
    rt=SimpleNamespace(_owner_memory_sources=lambda:c.reader)
    view=source_view(rt,source_id,now=NOW)
    assert view["content"] is None and view["raw_state"]=="compacted" and view["can_forget"]
    fact=ConfirmedFact(fact_id="compact",subject=ref[0],predicate="memory.world",
        value={"text":"Prefers concise answers."},source_ref=ref[1],evidence_ref=ref[2],
        observed_at=doc.occurred_at,trust="direct_user")
    rt._memory=SimpleNamespace(recall_for_presence=lambda q:SimpleNamespace(confirmed_facts=(fact,)))
    assert memory_view(rt,now=NOW)["memories"][0]["text"]=="Prefers concise answers."


def test_compacted_sources_still_respect_person_project_forgetting_and_revocation(selected_private_case):
    c=selected_private_case; f,ref,query = _ready(c)
    source_id,_,_=c.reader.document_reference(*ref,now=NOW)
    assert c.reader.selected_private_sources(replace(query,project_id="foreign"),(ref,),now=NOW)==()
    assert c.reader.document_reference("other",ref[1],ref[2],now=NOW) is None
    f.outbox.erase_content(principal_sha256=hashlib.sha256(ref[0].encode()).hexdigest(),
        document_id=f.document.document_id,operation_sha256="ba"*32,erased_at=NOW)
    assert c.reader.selected_private_sources(query,(ref,),now=NOW)==()
    with pytest.raises(LookupError):c.reader.document(source_id,now=NOW)
    with c.engine.begin() as conn:
        conn.execute(sa.update(metadata.tables["eva_core.principal_bindings"]).where(
            metadata.tables["eva_core.principal_bindings"].c.principal_binding_id==c.binding).values(status="revoked"))
    with pytest.raises(Exception,match="binding is not current"):
        c.reader.page(now=NOW)
