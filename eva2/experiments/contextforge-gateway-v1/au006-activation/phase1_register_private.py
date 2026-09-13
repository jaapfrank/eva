import os,json,time
import sqlalchemy as sa
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.connectors.mcp_registration import McpRegistrationConfig,ContextForgeRegistrationClient,McpRegistrationError
from eva.providers.mcp_owner_authority import McpOwnerAuthority
read=lambda key:read_runtime_secret_file(os.environ[key],setting_name=key)
engine=sa.create_engine(sqlalchemy_postgres_dsn(read('EVA_DATABASE_DSN_FILE')))
authority=McpOwnerAuthority(engine,os.environ['EVA_MCP_AUTONOMY_MANDATE_FILE'],os.environ.get('EVA_MCP_MANAGED_ENDPOINTS_FILE'))
if not (authority.configuration_sha256=='25db1dfbcaf4e2ce410f850f72fe633be0d1a1f43ae3a93889f036a5fac7ffb5'):
 raise RuntimeError('Registration precondition failed')
authority.authorize_current(phase='execute',operation_kind='effect')
client=ContextForgeRegistrationClient(McpRegistrationConfig(os.environ['EVA_MCP_GATEWAY_URL'],read('EVA_MCP_REGISTRATION_TOKEN_FILE'),allow_plain_http=True,visibility='private'))
endpoint='http://eva2-document-workspace-au006:9000/mcp'
if not (client.find(owner_key=authority.owner_key,endpoint=endpoint) is None):
 raise RuntimeError('Registration precondition failed')
registered=client.register(owner_key=authority.owner_key,endpoint=endpoint)
print(json.dumps({'stage':'registered','gateway':registered}),flush=True)
for attempt in range(12):
 try:
  gateway,rows=client.discover(owner_key=authority.owner_key,endpoint=endpoint)
  break
 except McpRegistrationError:
  if attempt==11:raise
  time.sleep(.5)
print(json.dumps({'stage':'discovered','gateway':gateway,'rows':rows}),flush=True)
engine.dispose()
