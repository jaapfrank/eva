"""Exact private day-text lifecycle; synthetic PostgreSQL records, no providers."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import operational_events
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_source_lifecycle import CompactedPrivateMemorySource
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.postgres.test_dream_memory_handoff import _case, _dream
from tests.postgres.test_runtime_resource_usage import NOW


def complete_dream_exchange(engine, document, *, now=NOW, selected=1, bind=True, finish=True):
    """Reusable genuine journal/outbox fixture; returns all original immutable bindings."""
    outbox = PostgresMemoryOutbox(engine, tenant_id=document.tenant_id,
                                  installation_id=document.installation_id)
    journal = PostgresConversationJournal(engine, outbox._scope_value)
    turn = ConversationTurn(tenant_id=document.tenant_id, installation_id=document.installation_id,
        principal_id=document.principal_id, conversation_id=document.conversation_id,
        message_id=document.source_ref, text="synthetic direct source", occurred_at=document.occurred_at,
        audience=document.audience, project_id=document.project_id)
    reply = GroundedReply(conversation_id=turn.conversation_id, message_id=turn.message_id,
        text="synthetic reply", evidence_memory_ids=(), generated_at=document.occurred_at, provider="synthetic")
    journal.admit(turn)
    journal.commit_reply(turn, reply)
    job = outbox.enqueue(document)
    journal.memory_queued(turn, journal._timeline.load("conversation:"+turn.message_id),
                          operation_document_id=document.document_id)
    batch = outbox.prepare_dream_batch((job,), now=now)
    outbox.bind_dream_operation(batch, bank_id="bank", operation_id="native-operation", now=now)
    receipt = MemoryRetentionReceipt("bank", document.document_id,
        hashlib.sha256(document.content.encode()).hexdigest(),
        "retained" if selected else "not_selected", selected, "native-operation")
    if bind:
        outbox.bind_dream_receipts(batch, (receipt,), now=now)
    claimed = outbox.begin(job, now=now)
    outbox.succeed(claimed, receipt, now=now)
    if finish:
        outbox.finish_dream_batch(batch, now=now)
    return SimpleNamespace(outbox=outbox, journal=journal, turn=turn, reply=reply,
                           document=document, batch=batch, receipt=receipt)


def _fixture(engine, tmp_path, **kwargs):
    _, installation = _case(engine, tmp_path)
    return complete_dream_exchange(engine, _dream(installation,"lifecycle"), **kwargs)


def _payloads(f):
    return (f.outbox._timeline.events(f.outbox._thread_id(f.document.document_id))[0],
            *[e for e in f.journal._timeline.events("conversation:"+f.turn.message_id)
              if e.event_type in {"INGRESS_COMMITTED","REPLY_COMMITTED"}])


@pytest.mark.parametrize("selected", [0, 2])
def test_exact_ack_compacts_three_sources_and_replays_without_dispatch(postgres_engine,tmp_path,selected):
    f = _fixture(postgres_engine,tmp_path,selected=selected)
    before = _payloads(f)
    assert f.outbox.compact_day_sources(now=NOW) == ()  # never same UTC day
    result = f.outbox.compact_day_sources(now=NOW+timedelta(days=1))
    assert len(result) == 1
    after = _payloads(f)
    proof = after[0].payload["data"]["content_compaction"]
    assert [p.payload["data"]["content_compaction"] for p in after] == [proof]*3
    for old,new,raw_key in zip(before,after,("content","text","text")):
        assert raw_key not in new.payload["data"]
        expected = {k:v for k,v in old.payload["data"].items() if k != raw_key}
        assert new.payload["data"] == {**expected,"content_compaction":proof}
        assert canonical_sha256(new.payload) == new.payload_sha256
    assert proof["queued_payload_sha256"] == before[0].payload_sha256
    assert proof["source_payload_sha256"] == canonical_sha256(before[0].payload["data"])
    assert proof["ingress_payload_sha256"] == before[1].payload_sha256
    assert proof["reply_payload_sha256"] == before[2].payload_sha256
    assert proof["selected_fact_count"] == selected
    compacted = f.outbox.read_dream_batch(f.batch.state.thread_id)
    assert isinstance(compacted.jobs[0].document,CompactedPrivateMemorySource)
    assert not hasattr(compacted.jobs[0].document,"content")
    assert f.outbox.begin(compacted.jobs[0],now=NOW+timedelta(days=1)) is None
    assert f.outbox.pending() == ()
    events = f.outbox._timeline.events(f.batch.state.thread_id)
    f.outbox.bind_dream_receipts(compacted,(f.receipt,),now=NOW+timedelta(days=1))
    assert f.outbox._timeline.events(f.batch.state.thread_id) == events
    assert f.outbox.compact_day_sources(now=NOW+timedelta(days=2)) == ()


@pytest.mark.parametrize("bind,finish", [(False,True),(True,False)])
def test_unknown_or_incomplete_cohort_keeps_raw_source(postgres_engine,tmp_path,bind,finish):
    f = _fixture(postgres_engine,tmp_path,bind=bind,finish=finish)
    assert f.outbox.compact_day_sources(now=NOW+timedelta(days=1)) == ()
    assert all("content_compaction" not in p.payload["data"] for p in _payloads(f))


@pytest.mark.parametrize("field,value", [("operation_id","other"),("content_sha256","a"*64),
    ("bank_id","other"),("selected_fact_count",None)])
def test_receipt_binding_requires_exact_native_selection(postgres_engine,tmp_path,field,value):
    f = _fixture(postgres_engine,tmp_path,bind=False)
    with pytest.raises(RuntimeError):
        f.outbox.bind_dream_receipts(f.batch,(replace(f.receipt,**{field:value}),),now=NOW)
    assert f.outbox.compact_day_sources(now=NOW+timedelta(days=1)) == ()


def test_compacted_source_can_be_explicitly_erased_with_proof_preserved(postgres_engine,tmp_path):
    f = _fixture(postgres_engine,tmp_path)
    when=NOW+timedelta(days=1)
    f.outbox.compact_day_sources(now=when)
    proof=_payloads(f)[0].payload["data"]["content_compaction"]
    result=f.outbox.erase_content(principal_sha256=hashlib.sha256(f.document.principal_id.encode()).hexdigest(),
        document_id=f.document.document_id,operation_sha256="c"*64,erased_at=when)
    assert result.redacted_event_count == 1
    current=_payloads(f)[0].payload["data"]
    assert current["content_compaction"] == proof
    assert current["content_erasure"]["status"] == "erased"
    again=f.outbox.erase_content(principal_sha256=hashlib.sha256(f.document.principal_id.encode()).hexdigest(),
        document_id=f.document.document_id,operation_sha256="c"*64,erased_at=when)
    assert again.replayed
    with pytest.raises(RuntimeError,match="erased"):
        f.outbox.read_dream_batch(f.batch.state.thread_id)


def test_sql_bad_reply_replacement_is_atomic_and_cannot_remove_other_fields(postgres_engine,tmp_path):
    f=_fixture(postgres_engine,tmp_path)
    f.outbox.compact_day_sources(now=NOW+timedelta(days=1))
    events=_payloads(f)
    proof=events[0].payload["data"]["content_compaction"]
    changed={**events[2].payload,"data":{**events[2].payload["data"],"provider":"forged"}}
    with pytest.raises(sa.exc.DBAPIError):
        with postgres_engine.begin() as conn:
            f.outbox._scope(conn)
            conn.scalar(sa.text("SELECT eva_core.compact_private_memory_source(:q,:i,:r,:p,:qp,:ip,:rp)"),
                dict(q=events[0].event_id,i=events[1].event_id,r=events[2].event_id,
                    p=canonical_json_bytes(proof).decode(),qp=canonical_json_bytes(events[0].payload).decode(),
                    ip=canonical_json_bytes(events[1].payload).decode(),rp=canonical_json_bytes(changed).decode()))
    assert _payloads(f) == events


def test_legacy_and_already_compacted_cohorts_do_not_starve_later_day(postgres_engine,tmp_path):
    _,installation=_case(postgres_engine,tmp_path)
    legacy=complete_dream_exchange(postgres_engine,_dream(installation,"legacy"),bind=False)
    first=complete_dream_exchange(postgres_engine,_dream(installation,"first"))
    assert len(first.outbox.compact_day_sources(now=NOW+timedelta(days=1),limit=1)) == 1
    later=complete_dream_exchange(postgres_engine,_dream(installation,"later"))
    changed=later.outbox.compact_day_sources(now=NOW+timedelta(days=1),limit=1)
    assert [r["document_id"] for r in changed] == [later.document.document_id]
    assert "content" in _payloads(legacy)[0].payload["data"]


def test_journal_memory_queue_binding_cannot_point_to_another_source(postgres_engine,tmp_path):
    f=_fixture(postgres_engine,tmp_path)
    # A second conflicting binding remains immutable; it cannot authorize compaction.
    state=f.journal._timeline.load("conversation:"+f.turn.message_id)
    f.journal._timeline.transition(thread_id=state.thread_id,expected_revision=state.revision,
        checkpoint_id=state.thread_id+":conflicting-memory",operation_id=state.operation_id,
        occurred_at=NOW,event_type="MEMORY_RETAIN_QUEUED",phase="memory_queued",status="completed",
        data={"kind":"memory_retain_queued","conversation_id":f.turn.conversation_id,
              "document_id":"other","message_id":f.turn.message_id,"principal_id":f.turn.principal_id})
    before=_payloads(f)
    with pytest.raises(sa.exc.DBAPIError):
        f.outbox.compact_day_sources(now=NOW+timedelta(days=1))
    assert _payloads(f) == before


def test_erased_member_keeps_cohort_raw_rest_without_starving_next_cohort(postgres_engine,tmp_path):
    _,installation=_case(postgres_engine,tmp_path)
    # Build one actual two-source cohort, each with a completed journal.
    one=complete_dream_exchange(postgres_engine,_dream(installation,"one"),bind=False,finish=False)
    # Its helper has already assigned one source: add a second source via a new immutable cohort below.
    # Close the single-source fixture first; this retained source is not our mixed cohort.
    one.outbox.bind_dream_receipts(one.batch,(one.receipt,),now=NOW)
    one.outbox.finish_dream_batch(one.batch,now=NOW)
    outbox=one.outbox
    docs=(_dream(installation,"mixed-a"),_dream(installation,"mixed-b"))
    receipts=[]; jobs=[]
    for doc in docs:
        turn=replace(one.turn,conversation_id=doc.conversation_id,message_id=doc.source_ref)
        outbox_journal=PostgresConversationJournal(postgres_engine,outbox._scope_value)
        outbox_journal.admit(turn)
        outbox_journal.commit_reply(turn,replace(one.reply,conversation_id=turn.conversation_id,message_id=turn.message_id))
        jobs.append(outbox.enqueue(doc))
        outbox_journal.memory_queued(turn,outbox_journal._timeline.load("conversation:"+turn.message_id),operation_document_id=doc.document_id)
        receipts.append(replace(one.receipt,document_id=doc.document_id,
                               content_sha256=hashlib.sha256(doc.content.encode()).hexdigest()))
    batch=outbox.prepare_dream_batch(tuple(jobs),now=NOW)
    outbox.bind_dream_operation(batch,bank_id="bank",operation_id="native-operation",now=NOW)
    outbox.bind_dream_receipts(batch,tuple(receipts),now=NOW)
    for job,receipt in zip(jobs,receipts): outbox.succeed(outbox.begin(job,now=NOW),receipt,now=NOW)
    outbox.finish_dream_batch(batch,now=NOW)
    outbox.erase_content(principal_sha256=hashlib.sha256(docs[0].principal_id.encode()).hexdigest(),
        document_id=docs[0].document_id,operation_sha256="d"*64,erased_at=NOW)
    # Drain the earlier independent cohort, then prove the mixed erased cohort consumes no slot.
    assert len(outbox.compact_day_sources(now=NOW+timedelta(days=1),limit=1)) == 1
    later=complete_dream_exchange(postgres_engine,_dream(installation,"later-after-forget"))
    changed=outbox.compact_day_sources(now=NOW+timedelta(days=1),limit=1)
    assert [r["document_id"] for r in changed] == [later.document.document_id]
    raw=outbox._timeline.events(outbox._thread_id(docs[1].document_id))[0].payload["data"]
    assert raw["content"] == docs[1].content
    assert "content_compaction" not in raw
