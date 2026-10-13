"""Use real registered rows and the pinned core catalog binding, without model calls."""
import pathlib,json,subprocess,hashlib,importlib.util
STAGE=pathlib.Path('/home/administrator/project/.eva2-artifacts/workspace-broker-v2-20260913')
manifest=json.loads((STAGE/'core-prepared-stack.json').read_text())
assert manifest['providerImage']=='sha256:0ef19e699069c3a5d3a26812c5da3d31141fcd5c3faedb14f92c9dcce1209465'
rows={k:v['selectedTools'] for k,v in manifest['instances'].items()}
for label,instance in manifest['instances'].items():
 digest=hashlib.sha256(pathlib.Path(instance['bindingFile']).read_bytes()).hexdigest()
 for row in rows[label].values():
  schema=row.get('input_schema',row.get('inputSchema'))
  assert schema['description']=='Provider binding SHA256 '+digest
  assert schema['additionalProperties'] is False and set(schema['properties'])=={'operation_id','request_binding_sha256','expected_generation','path','content'}
program="""
import sys,json,copy,hashlib,inspect
from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
from eva.contracts.canonical_json import canonical_sha256
rows=json.load(sys.stdin);out={}
for tool in rows['happy']:
 a=mcp_gateway_catalog_binding(rows['happy'][tool],contract='EvaMcpGatewayProvider/v4')
 b=mcp_gateway_catalog_binding(rows['stop'][tool],contract='EvaMcpGatewayProvider/v4')
 assert canonical_sha256(a)!=canonical_sha256(b)
 control=copy.deepcopy(a);control['input_schema']['description']=b['input_schema']['description']
 assert canonical_sha256(control)!=canonical_sha256(a)
 control['input_schema']['description']=a['input_schema']['description']
 assert canonical_sha256(control)==canonical_sha256(a)
 out[tool]={'happy':canonical_sha256(a),'stop':canonical_sha256(b),'schemaDigestChangesFingerprint':True}
print(json.dumps({'catalogHashes':out,'functionSourceSha256':hashlib.sha256(inspect.getsource(mcp_gateway_catalog_binding).encode()).hexdigest()}))
"""
r=subprocess.run(['docker','run','--rm','-i','--network','none','--read-only','--entrypoint','python','832983322ad7','-c',program],input=json.dumps(rows).encode(),capture_output=True,check=True)
proof=json.loads(r.stdout)
helper=pathlib.Path('/home/administrator/project/Eva2/eva2/experiments/tool01-replacement-acceptance-v2/run_physical_acceptance.py')
spec=importlib.util.spec_from_file_location('existing_gateway_helper',helper);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
token=pathlib.Path(manifest['tokenFile']).read_text();receipts={}
for label,instance in manifest['instances'].items():
 args={'operation_id':('a' if label=='happy' else 'b')*64,'request_binding_sha256':hashlib.sha256(pathlib.Path(instance['bindingFile']).read_bytes()).hexdigest(),'expected_generation':0,'path':'catalog-proof.txt','content':'Bound '+label+' artifact\n'}
 def call(tool):return h._gateway_request(manifest['names']['gateway'],'POST','/rpc',token=token,payload={'jsonrpc':'2.0','id':label+'-'+tool,'method':'tools/call','params':{'name':rows[label][tool]['name'],'arguments':args}})
 created=call('workspace_create_bound');observed=call('workspace_observe_bound');assert not created.get('error') and not created['result'].get('isError'),created
 assert created['result']==observed['result'];receipts[label]=created
proof.update(status='passed',image=manifest['providerImage'],manifest=str(STAGE/'core-prepared-stack.json'),instances=2,receipts=receipts,limits=['isolated gateway only; no live catalog changes','source authority and owner mandate remain core checks'])
(STAGE/'catalog-binding-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps({'status':proof['status'],'catalogHashes':proof['catalogHashes']}))
