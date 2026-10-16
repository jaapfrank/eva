"""Real current PostgreSQL source/revision checks in a private disposable database."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from alembic import command
from alembic.config import Config
from tests.postgres.test_people_memory_overview import (selected_private_case, _upgrade, _seed_learning, _retain_learning, NOW)
from eva.bootstrap.dashboard_memory import memory_view, source_view, correct_source, forget_source
from eva.application.conversation import ConversationConflict
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.contracts.planning import ConfirmedFact


@pytest.fixture(scope="module")
def people_memory_engine():
    dsn = os.environ["EVA2_TEST_POSTGRES_DSN"]
    assert make_url(dsn).database.startswith("eva2_4b_disposable_")
    engine = sa.create_engine(dsn)
    config = Config(str(Path(__file__).resolve().parents[3] / 'deploy/alembic.ini'))
    config.set_main_option('sqlalchemy.url', dsn.replace('%','%%'))
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'eva_%'")) == 0
    _upgrade(engine, config)
    yield engine
    command.downgrade(config, 'base'); engine.dispose()


def runtime(case, *, submit=None):
    return SimpleNamespace(_owner_memory_sources=lambda: case.reader, _engine=case.engine,
        _journal=lambda turn: PostgresConversationJournal(case.engine, case.reader._scope),
        _cold_path=SimpleNamespace(submit=submit or (lambda doc: _retain_learning(case, doc))),
        _work_memory=None, after_response=lambda *args: None)


def test_reference_lookup_rejects_other_people_group_expiry_and_erasure(selected_private_case):
    c = selected_private_case
    doc, ref = c.seed('mine', text='Mila is my daughter.', project=None)
    found = c.reader.document_reference(*ref, now=NOW)
    assert found and found[1] == doc
    assert c.reader.document_reference('other-person', doc.source_ref, doc.evidence_ref, now=NOW) is None
    assert c.reader.document_reference(*ref[:2], 'wrong-evidence', now=NOW) is None
    c.outbox.erase_content(principal_sha256=hashlib.sha256(doc.principal_id.encode()).hexdigest(),
        document_id=doc.document_id, operation_sha256='ba'*32, erased_at=NOW)
    assert c.reader.document_reference(*ref, now=NOW) is None


def test_person_names_and_inference_only_from_owned_live_sources(selected_private_case):
    c = selected_private_case; doc, ref = c.seed('person', text='Mila is my daughter.', project=None)
    fact = ConfirmedFact(fact_id='one', subject=ref[0], predicate='memory.observation',
        value={'text':'Mila may enjoy maths.', 'semantic_trust':'model_inference_unconfirmed', 'mentioned_names':['Mila']},
        source_ref=ref[1], evidence_ref=ref[2], observed_at=doc.occurred_at, trust='host_policy')
    foreign=fact.model_copy(update={'fact_id':'other','subject':'person:other','value':{'text':'SECRET_OTHER','mentioned_names':['SECRET_NAME']}})
    rt=runtime(c); rt._memory=SimpleNamespace(recall_for_presence=lambda query: SimpleNamespace(confirmed_facts=(fact,foreign)))
    view=memory_view(rt, 'Mila', now=NOW)
    assert view['mentioned_names']==['Mila'] and len(view['memories'])==1
    assert view['memories'][0]['kind']=='inference' and view['complete_directory'] is False
    assert 'SECRET' not in str(view)


def test_typed_correction_preserves_source_and_later_reads_revised_head(selected_private_case):
    c=selected_private_case
    parent=_seed_learning(c,'parent',seconds_ago=30,kind='person_hypothesis',review_at=None,interpretation='Original assumption.')
    sid=c.reader.document_reference(parent.principal_id,parent.source_ref,parent.evidence_ref,now=NOW)[0]
    submitted=[]
    def submit(doc): submitted.append(doc); _retain_learning(c,doc)
    rt=runtime(c,submit=submit)
    # Use the real journal and source/revision store, without a model/provider.
    rt._journal=lambda turn: PostgresConversationJournal(c.engine, c.reader._scope)
    assert correct_source(rt,sid,'Mila prefers geography, not maths.',now=NOW)['state']=='correction_queued'
    assert len(submitted)==1 and submitted[0].trust=='model_inference_unconfirmed'
    assert c.reader.document(sid,now=NOW)[0].content==parent.content
    later=source_view(rt,sid,now=NOW)
    assert later['learning']['interpretation']=='Original assumption.'
    assert later['current_learning']['interpretation']=='Mila prefers geography, not maths.'
    assert later['current_learning']['outcome']=='refuted' and later['current_source_id']!=sid
    with pytest.raises(ConversationConflict):correct_source(rt,sid,'Another stale correction.',now=NOW)


def test_typed_forget_uses_exact_document_without_erasing_original_ingress(selected_private_case):
    c=selected_private_case;doc=_seed_learning(c,'forget',seconds_ago=20,kind='person_hypothesis',review_at=None)
    sid=c.reader.document_reference(doc.principal_id,doc.source_ref,doc.evidence_ref,now=NOW)[0]
    rt=runtime(c); calls=[]
    def erase(**kwargs):
        calls.append(kwargs);c.outbox.erase_content(principal_sha256=hashlib.sha256(doc.principal_id.encode()).hexdigest(),
          document_id=doc.document_id,operation_sha256='bb'*32,erased_at=NOW);return True
    rt._cold_path.erase_document=erase
    assert forget_source(rt,sid,now=NOW)['state']=='erased'
    assert calls[0]['document_id']==doc.document_id and calls[0]['source_message_id'] is None
    assert c.timeline.events('conversation:'+doc.source_ref)[0].payload['data']['text']
    with pytest.raises(LookupError):c.reader.document(sid,now=NOW)


def test_owner_api_scope_and_revocation_after_recall(selected_private_case):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.dashboard_access import DashboardAccess
    from eva.adapters.api.dashboard_memory import install_dashboard_memory
    from eva.adapters.postgres.current_catalog import metadata
    c=selected_private_case;doc,ref=c.seed('api-person',text='Mila is my daughter.',project=None)
    fact=ConfirmedFact(fact_id='api',subject=ref[0],predicate='memory.world',
        value={'text':'Mila is my daughter.','semantic_trust':'direct_user','mentioned_names':['Mila']},
        source_ref=ref[1],evidence_ref=ref[2],observed_at=doc.occurred_at,trust='direct_user')
    rt=runtime(c);rt._memory=SimpleNamespace(recall_for_presence=lambda query:SimpleNamespace(confirmed_facts=(fact,)))
    app=FastAPI();install_dashboard_memory(app,access=DashboardAccess('x'*64),runtime=rt)
    auth={'authorization':'Bearer '+'x'*64};client=TestClient(app)
    assert client.get('/internal/v1/dashboard/memory-view').status_code==401
    assert client.get('/internal/v1/dashboard/memory-view?principal_id=other',headers=auth).status_code==422
    assert client.post('/internal/v1/dashboard/memory-view/search',headers=auth,json={'text':'Mila','principal_id':'other'}).status_code==422
    assert client.post('/internal/v1/dashboard/memory-view/search',headers={**auth,'origin':'http://external.invalid'},json={'text':'Mila'}).status_code==403
    # Fixed source time keeps this synthetic case's authority valid.
    import eva.bootstrap.dashboard_memory as module
    original=module._now;module._now=lambda:NOW
    try:
        result=client.post('/internal/v1/dashboard/memory-view/search',headers=auth,json={'text':'Mila'})
        assert result.status_code==200 and result.json()['mentioned_names']==['Mila']
        assert result.headers['cache-control']=='no-store'
        def revoked(query):
            table=metadata.tables['eva_core.principal_bindings']
            with c.engine.begin() as conn:conn.execute(sa.update(table).where(table.c.tenant_id==c.authority.tenant_id,table.c.principal_binding_id==c.binding).values(status='revoked'))
            return SimpleNamespace(confirmed_facts=(fact,))
        rt._memory.recall_for_presence=revoked
        denied=client.post('/internal/v1/dashboard/memory-view/search',headers=auth,json={'text':'Mila'})
        assert denied.status_code==503 and 'Mila' not in denied.text
    finally:module._now=original


def test_generic_owned_inference_forget_preserves_original_ingress(selected_private_case):
    c=selected_private_case;doc,ref=c.seed('generic-inference',trust='model_inference_unconfirmed')
    _retain_learning(c,doc)
    sid=c.reader.document_reference(*ref,now=NOW)[0];rt=runtime(c);calls=[]
    assert source_view(rt,sid,now=NOW)['erasure_scope']=='memory_document'
    assert source_view(rt,sid,now=NOW)['can_forget'] is True
    def erase(**kw):
        calls.append(kw);c.outbox.erase_content(principal_sha256=hashlib.sha256(doc.principal_id.encode()).hexdigest(),document_id=doc.document_id,operation_sha256='bc'*32,erased_at=NOW);return True
    rt._cold_path.erase_document=erase
    assert forget_source(rt,sid,now=NOW)['state']=='erased'
    assert calls[0]['source_message_id'] is None
    assert c.timeline.events('conversation:'+doc.source_ref)[0].payload['data']['text']
    with pytest.raises(LookupError):c.reader.document(sid,now=NOW)
