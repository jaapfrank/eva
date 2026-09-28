import json,sqlalchemy as sa
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.operational_timeline import operational_events as events,PostgresOperationalTimeline,external_timeline_scope
s=RuntimeSettings.from_environment();e=sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn),connect_args={'options':'-c default_transaction_read_only=on'})
scope=external_timeline_scope(s.background_tenant_id,s.background_installation_id,'bounded historical greeting timing')
timeline=PostgresOperationalTimeline(e,scope)
report=[]
with e.begin() as conn:
 timeline._scope(conn)
 base=[events.c.tenant_id==scope.tenant_id,events.c.installation_id==scope.installation_id]
 rows=conn.execute(sa.select(events.c.payload,events.c.occurred_at).where(*base,events.c.event_type=='INGRESS_COMMITTED',events.c.payload['data']['principal_id'].astext==s.background_principal_id,sa.func.lower(events.c.payload['data']['text'].astext).in_(['hallo','hallo.','hoi','hoi.'])).order_by(events.c.occurred_at.desc()).limit(5)).all()
 for payload,stamp in rows:
  message=payload['data']['message_id']
  related=conn.execute(sa.select(events.c.event_type,events.c.payload,events.c.occurred_at).where(*base,events.c.payload['data']['message_id'].astext==message)).all()
  reply=next((p['data'] for kind,p,t in related if kind=='REPLY_COMMITTED'),{})
  admissions=[t for kind,p,t in related if kind=='CONVERSATION_MODEL_ADMITTED']
  report.append({'occurredAt':stamp.isoformat(),'modelTiming':reply.get('model_timing'),'modelUsage':reply.get('model_usage'),'secondsToFirstModelAdmissionRounded':None if not admissions else (min(admissions)-stamp).total_seconds(),'admittedModelRequests':len(admissions)})
e.dispose()
print(json.dumps(report))
