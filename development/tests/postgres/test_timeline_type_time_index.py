"""Real forward index migration, ordered results, isolation and bounded failure."""
from datetime import UTC,datetime,timedelta
import json
from pathlib import Path
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from eva.operations.install_schema import install_schema
from eva.adapters.postgres.operational_timeline import operational_events as events
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_scheduled_failure_return import database

OLD="4e_0010_memory_compaction"
NEW="4e_0011_timeline_type_time"
INI=Path(__file__).resolve().parents[3]/"deploy/alembic.ini"


def config(database):
    c=Config(str(INI));c.set_main_option("sqlalchemy.url",database.url.render_as_string(hide_password=False).replace("%","%%"));return c


def revision(database):
    with database.connect() as c:return c.scalar(sa.text("select version_num from public.eva_alembic_version"))


def upgrade(database):return install_schema(database.url.render_as_string(hide_password=False),alembic_ini=INI)


def test_forward_index_keeps_exact_scoped_results_and_changes_query_plan(database):
    command.downgrade(config(database),OLD)
    tenant,installation,foreign=uuid4(),uuid4(),uuid4();now=datetime.now(UTC).replace(microsecond=0)
    rows=[]
    for i in range(40000):
        payload={"data":{"message_id":"synthetic-index-"+str(i)}}
        rows.append({"tenant_id":foreign if i%10==1 else tenant,"installation_id":installation,
            "event_id":uuid4(),"thread_id":"synthetic-index:"+str(i),"sequence":1,
            "checkpoint_id":"synthetic-index:"+str(i),"event_type":"INGRESS_COMMITTED" if i%200==0 else "PROFILE_NOISE",
            "payload":payload,"payload_sha256":bytes.fromhex(canonical_sha256(payload)),
            "occurred_at":now+timedelta(seconds=i)})
    with database.begin() as c:
        for start in range(0,len(rows),1000):c.execute(events.insert(),rows[start:start+1000])
        c.exec_driver_sql("ANALYZE eva_core.operational_events")
        security_before=c.execute(sa.text("select relrowsecurity,relforcerowsecurity,relacl::text from pg_class where oid='eva_core.operational_events'::regclass")).one()
    statement=sa.select(events.c.event_id,events.c.payload_sha256).where(events.c.tenant_id==tenant,
        events.c.installation_id==installation,events.c.event_type=="INGRESS_COMMITTED",
        events.c.occurred_at>=now+timedelta(seconds=30000)).order_by(events.c.occurred_at.desc()).limit(20)
    sql=str(statement.compile(database,dialect=database.dialect,compile_kwargs={"literal_binds":True}))
    def read():
        with database.begin() as c:
            c.exec_driver_sql("SET LOCAL ROLE eva_runtime")
            c.execute(sa.text("select set_config('eva.tenant_id',:t,true),set_config('eva.installation_id',:i,true)"),{"t":str(tenant),"i":str(installation)})
            result=c.execute(statement).all()
            plan=c.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+sql).scalar_one()
            return result,plan
    before,oldplan=read();receipt=upgrade(database);after,newplan=read()
    assert receipt.source_revision==OLD and receipt.final_revision==NEW and revision(database)==NEW
    assert len(before)==20 and before==after
    assert "ix_operational_events__type_time" not in json.dumps(oldplan)
    assert "ix_operational_events__type_time" in json.dumps(newplan)
    with database.connect() as c:
        assert c.execute(sa.text("select relrowsecurity,relforcerowsecurity,relacl::text from pg_class where oid='eva_core.operational_events'::regclass")).one()==security_before
    print(json.dumps({"index_plan_evidence":{"before_ms":oldplan[0]["Execution Time"],"after_ms":newplan[0]["Execution Time"],"rows_identical":20,"seed_rows":40000}}))


def test_wrong_existing_index_shape_cannot_advance_revision(database):
    command.downgrade(config(database),OLD)
    with database.begin() as c:c.exec_driver_sql("CREATE INDEX ix_operational_events__type_time ON eva_core.operational_events (installation_id,tenant_id,event_type,occurred_at)")
    try:
        with pytest.raises(RuntimeError,match="index differs"):upgrade(database)
        assert revision(database)==OLD
    finally:
        with database.begin() as c:c.exec_driver_sql("DROP INDEX eva_core.ix_operational_events__type_time")
        upgrade(database)


def test_lock_timeout_rolls_back_without_changing_revision(database):
    command.downgrade(config(database),OLD)
    with database.begin() as blocker:
        blocker.exec_driver_sql("LOCK TABLE eva_core.operational_events IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(sa.exc.DBAPIError) as error:upgrade(database)
        assert getattr(error.value.orig,"sqlstate",None)=="55P03"
        assert revision(database)==OLD
    assert upgrade(database).final_revision==NEW
