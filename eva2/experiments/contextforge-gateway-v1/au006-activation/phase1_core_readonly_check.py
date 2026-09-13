import os,json,hashlib,urllib.request
import sqlalchemy as sa
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline,external_timeline_scope,operational_checkpoints
from eva.adapters.postgres.mcp_connector_registry import PostgresMcpConnectorRegistry
from eva.adapters.connectors.mcp_gateway import McpGatewayConfig
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
read=lambda key:read_runtime_secret_file(os.environ[key],setting_name=key)
mandate=json.loads(read('EVA_MCP_AUTONOMY_MANDATE_FILE'))
identity={k:v for k,v in mandate.items() if k!='enabled'}
expected='25db1dfbcaf4e2ce410f850f72fe633be0d1a1f43ae3a93889f036a5fac7ffb5'
if not (mandate['enabled'] is True and canonical_sha256(identity)==expected):
 raise RuntimeError('Core preflight condition failed')
owner_key=canonical_sha256({k:mandate[k] for k in ['tenant_id','installation_id','owner_principal_id']})
scope=external_timeline_scope(os.environ['EVA_RUNTIME_TENANT_ID'],os.environ['EVA_RUNTIME_INSTALLATION_ID'],'scope')
engine=sa.create_engine(sqlalchemy_postgres_dsn(read('EVA_DATABASE_DSN_FILE')),pool_pre_ping=True)
timeline=PostgresOperationalTimeline(engine,scope)
state=timeline.load('connector-mandate:'+owner_key)
events=timeline.events('connector-mandate:'+owner_key)
if not (state.phase=='active' and events[-1].payload['data']['configuration_sha256']==expected):
 raise RuntimeError('Core preflight condition failed')
# Constructor sees the already verified durable mandate and cannot initialize a missing record.
authority=McpOwnerAuthority(engine,os.environ['EVA_MCP_AUTONOMY_MANDATE_FILE'],os.environ.get('EVA_MCP_MANAGED_ENDPOINTS_FILE'))
authority.authorize_current(phase='execute',operation_kind='effect')
gateway=McpGatewayConfig(os.environ['EVA_MCP_GATEWAY_URL'],read('EVA_MCP_GATEWAY_TOKEN_FILE'),allow_plain_http=True)
registry=PostgresMcpConnectorRegistry(engine,scope,authority.owner_principal_id,gateway,authority.authorize_turn,authority.authorize_current)
registry_state,records=registry._snapshot()
if not ('16dc8ce3-7825-5618-a131-ab21840808ef' not in records):
 raise RuntimeError('Core preflight condition failed')
catalog=read('EVA_MCP_GATEWAY_CATALOG_FILE')
if not (hashlib.sha256(catalog.encode()).hexdigest()=='349d6ad0a26ad1471f5b0fad27b5c3a3f43d9b066afba05feec9bf94ea013a86'):
 raise RuntimeError('Core preflight condition failed')
specs=parse_mcp_gateway_catalog(catalog)
if not (not any('workspace_create_bound' in x.execute_tool_name or 'workspace_observe_bound' in x.observe_tool_name for x in specs)):
 raise RuntimeError('Core preflight condition failed')
if not (not os.environ.get('EVA_DOCUMENT_GOAL_DEFINITION_FILE') and not os.environ.get('EVA_DOCUMENT_GOAL_PLAN_FILE')):
 raise RuntimeError('Core preflight condition failed')
with engine.connect() as connection:
 transaction=connection.begin()
 try:
  timeline._scope(connection)
  stmt=sa.select(operational_checkpoints.c.phase,operational_checkpoints.c.status,sa.func.count()).where(operational_checkpoints.c.status.in_(['active','waiting_for_proof'])).group_by(operational_checkpoints.c.phase,operational_checkpoints.c.status)
  phases=[{'phase':r[0],'status':r[1],'count':r[2]} for r in connection.execute(stmt)]
 finally:transaction.rollback()
health=json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=10))
engine.dispose()
print(json.dumps({'mandate_active':True,'mandate_configuration_sha256':expected,'mandate_revision':state.revision,'registry_revision':None if registry_state is None else registry_state.revision,'registry_records_sha256':canonical_sha256(records),'registry_record_count':len(records),'new_core_connector_absent':True,'core_catalog_sha256':hashlib.sha256(catalog.encode()).hexdigest(),'offered_static_tool_names':[x.descriptor.name for x in specs],'new_raw_tools_absent':True,'core_document_feature_off':True,'active_checkpoint_phases':phases,'health':health,'model_calls':0}))
