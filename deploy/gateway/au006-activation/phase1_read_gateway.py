import json,os,urllib.request,time
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.connectors.mcp_registration import McpRegistrationConfig,ContextForgeRegistrationClient
endpoint='http://eva2-document-workspace-au006:9000/mcp'
def rpc(method,params):
 req=urllib.request.Request(endpoint,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
 return json.load(urllib.request.urlopen(req,timeout=10))
for i in range(12):
 try:
  initialized=rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'au006-phase1-readiness','version':'1'}})
  if not ('result' in initialized):
   raise RuntimeError('Provider initialization failed')
  break
 except Exception:
  if i==11:raise
  time.sleep(.5)
metadata=rpc('tools/list',{})['result']
token=read_runtime_secret_file(os.environ['EVA_MCP_REGISTRATION_TOKEN_FILE'],setting_name='EVA_MCP_REGISTRATION_TOKEN_FILE')
client=ContextForgeRegistrationClient(McpRegistrationConfig(os.environ['EVA_MCP_GATEWAY_URL'],token,allow_plain_http=True,visibility='private'))
gateways=client._rows(client._request('GET','/gateways?include_pagination=false&limit=0'))
tools=client._rows(client._request('GET','/tools?include_pagination=false&limit=0'))
print(json.dumps({'provider_metadata':metadata,'gateways':gateways,'tools':tools}))
