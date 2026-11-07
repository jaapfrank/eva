import sys,os,json,hashlib,subprocess,importlib,importlib.metadata as md,base64,csv
from pathlib import Path
P=Path('/run/eva-cf-runtime');family=sys.argv[1];assert sys.version_info[:3]==(3,12,16);assert sys.base_prefix=='/opt/eva2/cpython31216';assert sys.prefix=='/app/.venv'
expected={x['name'].lower().replace('_','-'):x['version'] for x in json.loads((P/'wheel-manifest.json').read_text())};actual={x.metadata['Name'].lower().replace('_','-'):x.version for x in md.distributions()};assert expected==actual,{'extra':sorted(actual.keys()-expected.keys()),'missing':sorted(expected.keys()-actual.keys()),'different':{k:[expected[k],actual.get(k)] for k in expected if actual.get(k)!=expected[k]}}
records=0
for dist in md.distributions():
 for f in dist.files or []:
  p=Path(dist.locate_file(f))
  if f.hash:
   assert f.hash.mode=='sha256',(dist.metadata['Name'],str(f),f.hash.mode)
   assert base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).rstrip(b'=').decode()==f.hash.value,str(p)
   records+=1
boot="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pip';runpy.run_module('pip',run_name='__main__')"
subprocess.run([sys.executable,'-I','-B','-c',boot,str(P/'pip-26.3-py3-none-any.whl'),'check'],check=True)
modules=json.loads((P/'native-extension-names.json').read_text())
for n in modules:importlib.import_module(n)
import psycopg,psycopg_c,psycopg_binary
assert psycopg.__version__==psycopg_c.__version__==psycopg_binary.__version__=='3.3.8'
assert psycopg.pq.__impl__=='c' and psycopg.pq.version()==160014
sys.path.insert(0,'/app');import mcpgateway
assert Path(mcpgateway.__file__).resolve()==Path('/app/mcpgateway/__init__.py')
source={str(p.relative_to('/app/mcpgateway')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/mcpgateway').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
expectedsource={str(p.relative_to(P/'sources'/family)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (P/'sources'/family).rglob('*') if p.is_file()};assert source==expectedsource
rpms=subprocess.run(['rpm','-qa','--qf','%{NAME} %{EPOCHNUM} %{VERSION} %{RELEASE} %{ARCH}\n'],capture_output=True,text=True,check=True).stdout
for x in json.loads((P/'runtime-rpm-updates.json').read_text()):
 y=x['after'];assert ' '.join(str(y[k]) for k in ('name','epoch','version','release','arch')) in rpms.splitlines()
print(json.dumps({'contract':'EvaContextForgeActualImageBuildReadback/v1','python':sys.version.split()[0],'prefix':sys.prefix,'basePrefix':sys.base_prefix,'cf':md.version('mcp-contextforge-gateway'),'mcp':md.version('mcp'),'httpx2':md.version('httpx2'),'packages':actual,'recordFilesChecked':records,'nativeImports':len(modules),'psycopg':psycopg.__version__,'psycopgImplementation':psycopg.pq.__impl__,'libpq':psycopg.pq.version(),'sourceOrigin':mcpgateway.__file__,'sourceFiles':len(source),'sourceSha256':hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'rpmUpdateCount':len(json.loads((P/'runtime-rpm-updates.json').read_text())),'pipCheck':True},sort_keys=True))
