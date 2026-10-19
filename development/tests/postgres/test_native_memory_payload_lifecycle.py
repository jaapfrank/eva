"""Native completed-family cleanup preserves replay and retry input."""
from pathlib import Path
import copy
import hashlib
import json
import os
from uuid import uuid4
import pytest
import sqlalchemy as sa


@pytest.fixture(scope="module")
def native():
    dsn = os.environ["EVA2_TEST_POSTGRES_DSN"]
    url = sa.engine.make_url(dsn)
    assert url.database.startswith("eva2_4b_disposable_")
    engine = sa.create_engine(dsn)
    schema = "native_payload_test"
    with engine.begin() as c:
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
        c.execute(sa.text(f"CREATE TABLE {schema}.async_operations (operation_id uuid PRIMARY KEY, bank_id text NOT NULL, operation_type text NOT NULL, status text NOT NULL, task_payload jsonb, result_metadata jsonb, error_message text, retry_count integer DEFAULT 0)"))
        sql = (Path(__file__).resolve().parents[3]/"deploy/living-core/hindsight-memory/completed_payload_compaction.sql").read_text().replace("__SCHEMA__",schema)
        c.exec_driver_sql(sql)
    yield engine, schema
    with engine.begin() as c:c.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    engine.dispose()


def family(c, schema, *, sibling_status="pending", policy="later-useful-knowledge-v2", content=None, payload_type="batch_retain", errors=0):
    parent, child, sibling = [str(uuid4()) for _ in range(3)]
    def add(identity, kind, status, payload, metadata):
        c.execute(sa.text(f"INSERT INTO {schema}.async_operations (operation_id,bank_id,operation_type,status,task_payload,result_metadata) VALUES (:id,'owner-bank',:kind,:status,CAST(:payload AS jsonb),CAST(:metadata AS jsonb))"),
                  dict(id=identity,kind=kind,status=status,payload=json.dumps(payload),metadata=json.dumps(metadata)))
    item = {"content":content or "A real direct-user source. "*20, "document_id":"source:one", "context":"Eva conversation and experience memory", "metadata":{"selection_policy":policy,"trust":"direct_user","principal_id":"owner"},"tags":["eva-memory","person:owner"]}
    payload = {"type":payload_type,"operation_id":child,"bank_id":"owner-bank","contents":[item]}
    add(parent,"batch_retain","pending",None,{"total_tokens":123,"unit_ids_count":1})
    add(child,"retain","pending",payload,{"parent_operation_id":parent,"unit_ids_count":1,"extraction_errors_count":errors})
    other=copy.deepcopy(payload);other["operation_id"]=sibling;other["contents"][0]["document_id"]="source:two"
    add(sibling,"retain",sibling_status,other,{"parent_operation_id":parent,"unit_ids_count":1,"extraction_errors_count":errors})
    return parent,child,sibling,payload


def status(c,schema,identity,value):
    c.execute(sa.text(f"UPDATE {schema}.async_operations SET status=:status WHERE operation_id=:id"),dict(status=value,id=identity))


def rows(c,schema,ids):
    return {str(x["operation_id"]):dict(x) for x in c.execute(sa.text(f"SELECT * FROM {schema}.async_operations WHERE operation_id::text=ANY(:ids)"),dict(ids=ids)).mappings()}


def test_waits_for_entire_successful_family_then_only_compacts_literal(native):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema)
        ids=[parent,child,sibling]
        status(c,schema,child,"completed")
        assert rows(c,schema,ids)[child]["task_payload"]==payload
        status(c,schema,sibling,"completed")
        assert rows(c,schema,ids)[child]["task_payload"]==payload
        before=rows(c,schema,ids)
        status(c,schema,parent,"completed")
        after=rows(c,schema,ids)
        expected=copy.deepcopy(payload);text=expected["contents"][0].pop("content")
        expected["contents"][0]["content_sha256"]=hashlib.sha256(text.encode()).hexdigest()
        assert after[child]["task_payload"]==expected
        assert len(json.dumps(expected))<len(json.dumps(payload))
        for identity in [child,sibling]:
            assert {k:v for k,v in before[identity].items() if k!="task_payload"}=={k:v for k,v in after[identity].items() if k!="task_payload"}
        assert after[parent]["result_metadata"]==before[parent]["result_metadata"]
        status(c,schema,parent,"completed")
        assert rows(c,schema,ids)==after


def test_failed_or_unfinished_family_retains_retry_input(native):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema,sibling_status="failed")
        status(c,schema,child,"completed");status(c,schema,parent,"completed")
        assert rows(c,schema,[child])[child]["task_payload"]==payload
        status(c,schema,sibling,"pending")
        assert rows(c,schema,[child])[child]["task_payload"]==payload
        status(c,schema,sibling,"completed")
        assert "content" not in rows(c,schema,[child])[child]["task_payload"]["contents"][0]


@pytest.mark.parametrize("policy,content", [("unknown-policy",None),("later-useful-knowledge-v2","Hi")])
def test_other_policy_and_tiny_input_unchanged(native,policy,content):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema,policy=policy,content=content)
        for identity in [child,sibling,parent]:status(c,schema,identity,"completed")
        assert rows(c,schema,[child])[child]["task_payload"]==payload


def test_unknown_payload_shape_and_transaction_rollback(native):
    engine,schema=native
    with engine.connect() as c:
        tx=c.begin();parent,child,sibling,payload=family(c,schema)
        bad=copy.deepcopy(payload);bad.pop("type")
        value=c.scalar(sa.text(f"SELECT {schema}.eva_compact_memory_payload(CAST(:x AS jsonb))"),dict(x=json.dumps(bad)))
        assert value==bad
        for identity in [child,sibling,parent]:status(c,schema,identity,"completed")
        tx.rollback()
        assert rows(c,schema,[parent,child,sibling])=={}


@pytest.mark.parametrize("errors", [1, None, "0"])
def test_completed_with_failed_or_unknown_extraction_keeps_error_source(native, errors):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema,errors=errors)
        for identity in [child,sibling,parent]:status(c,schema,identity,"completed")
        assert rows(c,schema,[child])[child]["task_payload"]==payload


def test_legacy_retain_payload_type_also_compacts(native):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema,payload_type="retain")
        for identity in [child,sibling,parent]:status(c,schema,identity,"completed")
        assert "content" not in rows(c,schema,[child])[child]["task_payload"]["contents"][0]


def test_one_failed_extraction_preserves_entire_family_evidence(native):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema)
        c.execute(sa.text(f"UPDATE {schema}.async_operations SET result_metadata=jsonb_set(result_metadata, '{{extraction_errors_count}}','1'::jsonb) WHERE operation_id=:id"),dict(id=sibling))
        for identity in [child,sibling,parent]:status(c,schema,identity,"completed")
        assert rows(c,schema,[child])[child]["task_payload"]==payload


def test_cross_bank_payload_is_not_compacted(native):
    engine,schema=native
    with engine.begin() as c:
        parent,child,sibling,payload=family(c,schema)
        wrong=copy.deepcopy(payload);wrong["bank_id"]="another-owner-bank"
        c.execute(sa.text(f"UPDATE {schema}.async_operations SET task_payload=CAST(:x AS jsonb) WHERE operation_id=:id"),dict(id=child,x=json.dumps(wrong)))
        for identity in [child,sibling,parent]:status(c,schema,identity,"completed")
        assert rows(c,schema,[child])[child]["task_payload"]==wrong
