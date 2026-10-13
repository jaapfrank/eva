from dataclasses import replace
from datetime import datetime
import hashlib
import pytest
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.application.procedure_references import ProcedureReferences
from tests.postgres.test_people_memory_overview import people_memory_engine
from tests.application.test_procedure_references import Host,SCOPE,NOW,designate


def test_source_only_restart_erasure_and_no_semantic_queue(people_memory_engine):
    engine=people_memory_engine
    def store():return PostgresMemoryOutbox(engine,tenant_id=SCOPE.tenant_id,installation_id=SCOPE.installation_id)
    overview=PostgresPeopleMemoryOverview(engine,tenant_id=SCOPE.tenant_id,installation_id=SCOPE.installation_id)
    observed_at=datetime.fromisoformat(NOW.replace("Z","+00:00"))
    assert overview.snapshot(now=observed_at,hindsight_state="ready").memory_flow == "no_signals"
    host=Host();first=store();reference=designate(ProcedureReferences(first,host))
    assert overview.snapshot(now=observed_at,hindsight_state="ready").memory_flow == "no_signals"
    assert first.pending()==() # no Hindsight publication or personal fact extraction
    # Ordinary personal memory continues to queue through the unchanged route.
    ordinary = replace(first.resolve_source(reference.document_id, principal_id=SCOPE.principal_id,
                       project_id=None), document_id='ordinary-source', content='An ordinary preference',
                       trust='direct_user')
    with pytest.raises(ValueError):
        first.enqueue(first.resolve_source(reference.document_id,principal_id=SCOPE.principal_id,project_id=None))
    first.enqueue(ordinary)
    assert overview.snapshot(now=observed_at,hindsight_state='ready').memory_flow == 'processing'
    assert tuple(job.document.document_id for job in first.pending()) == ('ordinary-source',)
    engine.dispose() # new database connection and new source/port instances
    reopened=store();service=ProcedureReferences(reopened,host)
    assert service.resolve_candidates(SCOPE,(reference.document_id,),now=NOW)[0].reference==reference
    assert reopened.resolve_source(reference.document_id,principal_id='other',project_id=None) is None
    assert reopened.resolve_source(reference.document_id,principal_id=SCOPE.principal_id,project_id='other') is None
    reopened.erase_content(principal_sha256=hashlib.sha256(SCOPE.principal_id.encode()).hexdigest(),
        document_id=reference.document_id,operation_sha256='e'*64,erased_at=datetime.fromisoformat(NOW.replace('Z','+00:00')))
    assert service.resolve_candidates(SCOPE,(reference.document_id,),now=NOW)==()
    assert tuple(job.document.document_id for job in reopened.pending()) == ('ordinary-source',)
    with pytest.raises(RuntimeError):designate(ProcedureReferences(reopened,host)) # cannot resurrect erased source by replay

    assert overview.snapshot(now=observed_at,hindsight_state='ready').memory_flow == 'processing'
