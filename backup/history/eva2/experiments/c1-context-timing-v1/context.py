import json,time,datetime
import sqlalchemy as sa
from eva.bootstrap.runtime import RuntimeSettings
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.application.conversation import ConversationTurn
from eva.application.memory_audience import MemoryAudience
s=RuntimeSettings.from_environment()
e=sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn))
statements=[]
@sa.event.listens_for(e,'before_cursor_execute')
def read_queries_only(conn,cursor,statement,parameters,context,executemany):
    verb=statement.lstrip().split(None,1)[0].upper()
    if verb not in {'SELECT','SET','SHOW'}:
        raise RuntimeError('Diagnostic rejected a mutating SQL statement')
    statements.append(verb)
principal=s.background_principal_id
turn=ConversationTurn(tenant_id=s.background_tenant_id,installation_id=s.background_installation_id,principal_id=principal,conversation_id='dashboard:'+s.dashboard_principal_binding_id,message_id='diagnostic:context-read-only',text='Hallo.',occurred_at=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),audience=MemoryAudience(kind='private_person',audience_id='person:'+principal,principal_id=principal))

from uuid import UUID
from eva.providers.runtime import build_external_provider_bundle
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.operational_timeline import TimelineScope,external_timeline_scope
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
bundle=build_external_provider_bundle(s.external_provider,e)
store=PostgresDelegatedWorkStore(e,TimelineScope(UUID(s.dashboard_tenant_id),UUID(s.dashboard_installation_id)))
service=DelegatedWorkOwnerService(store,owner_principal_id=UUID(principal),principal_binding_id=UUID(s.dashboard_principal_binding_id),conversation_scope=external_timeline_scope(s.background_tenant_id,s.background_installation_id,'bounded context measurement'))
base=PostgresConversationActions(e,durable_goal_provider=s.durable_goal_provider,durable_goal_recipients=s.durable_goal_recipients)
rows=[]
for provider in (base,*bundle.external_tools,PostgresDelegatedWorkActions(e,service)):
    method=getattr(provider,'active_context',None)
    if callable(method):
        start=time.monotonic(); values=method(turn)
        rows.append({'provider':type(provider).__name__,'elapsedSeconds':round(time.monotonic()-start,4),'entries':len(values),'utf8Bytes':sum(len(v.encode()) for v in values)})
print(json.dumps({'kind':'correct conversation scope; separate delegated storage authority','rows':rows,'totalContextSeconds':round(sum(x['elapsedSeconds'] for x in rows),4),'totalContextBytes':sum(x['utf8Bytes'] for x in rows),'modelCalls':0,'databaseMode':'SELECT/SET/SHOW statement guard; includes existing shared binding locks','sqlStatements':len(statements)}))
e.dispose()
