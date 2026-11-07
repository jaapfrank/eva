"""Verify one candidate image using an isolated metadata-only process."""
from pathlib import Path
import argparse,json,subprocess
ROOT=Path(__file__).resolve().parent
CODE=r"""
import sys,json,hashlib,base64,os,subprocess,importlib.metadata as md
from pathlib import Path
assert sys.version_info[:3]==(3,12,16) and sys.prefix=='/app/.venv' and sys.base_prefix=='/opt/eva2/cpython31216'
actual={d.metadata['Name'].lower().replace('_','-'):d.version for d in md.distributions()};assert actual==EXPECTED['packages']
checked=0
for d in md.distributions():
 for f in d.files or []:
  if not f.hash:continue
  assert f.hash.mode=='sha256' and base64.urlsafe_b64encode(hashlib.sha256(Path(d.locate_file(f)).read_bytes()).digest()).rstrip(b'=').decode()==f.hash.value
  checked+=1
assert checked==16348
app=Path('/app/mcpgateway');sources={str(p.relative_to(app)):hashlib.sha256(p.read_bytes()).hexdigest() for p in app.rglob('*') if p.is_file() and '__pycache__' not in p.parts};assert sources==EXPECTED['sources']
sys.path.insert(0,'/app');import mcpgateway,psycopg
assert mcpgateway.__file__=='/app/mcpgateway/__init__.py' and psycopg.__version__=='3.3.8' and psycopg.pq.__impl__=='c' and psycopg.pq.version()==160014
rpm=subprocess.check_output(['rpm','-qa','--qf','%{NAME} %{EPOCHNUM} %{VERSION} %{RELEASE} %{ARCH}'+chr(10)],text=True).splitlines();assert len(rpm)==EXPECTED['rpmCount']
for x in EXPECTED['rpmUpdates']:assert ' '.join(str(x['after'][k]) for k in ('name','epoch','version','release','arch')) in rpm
node=None
if EXPECTED['provider']:
 link=Path('/usr/bin/node');assert link.is_symlink() and os.readlink(link)=='/opt/eva2/node-runtime/node';assert hashlib.sha256(link.read_bytes()).hexdigest()==EXPECTED['node']['sha256'];assert subprocess.check_output([str(link),'--version'],text=True).strip()=='v26.13.0'
 npm=Path('/opt/eva2/filesystem-mcp/node_modules/@modelcontextprotocol');assert json.loads((npm/'sdk/package.json').read_text())['version']=='1.32.2' and json.loads((npm/'server-filesystem/package.json').read_text())['version']=='2.2.0';node='26.13.0'
print(json.dumps({'python':sys.version.split()[0],'packages':len(actual),'wheelRecords':checked,'applicationSources':len(sources),'applicationSourceOrigin':mcpgateway.__file__,'contextForge':actual['mcp-contextforge-gateway'],'mcp':actual['mcp'],'nativeRpmCount':len(rpm),'node':node,'verified':True,'modelsCalled':0,'toolsCalled':0}))
"""
def main():
 parser=argparse.ArgumentParser();parser.add_argument('family',choices=('gateway','filesystem','atomic','projects'));parser.add_argument('--image');args=parser.parse_args()
 roles=json.loads((ROOT/'roles-and-artifacts.json').read_text());row=next(x for x in roles['runtimeFamilies'] if x['family']==args.family);image=args.image or row['candidateImage']
 assert image.startswith('sha256:') and len(image)==71 and all(c in '0123456789abcdef' for c in image[7:])
 packages={r['name'].lower().replace('_','-'):r['version'] for r in json.loads((ROOT/'wheel-artifacts.json').read_text())}
 provider=args.family!='gateway';key='provider' if provider else 'gateway';sources=json.loads((ROOT/'application-source-manifest.json').read_text())['official' if provider else 'gateway'];updates=json.loads((ROOT/'runtime-rpm-artifacts.json').read_text())[key]
 expected={'provider':provider,'sources':sources,'packages':packages,'rpmUpdates':updates,'rpmCount':123 if provider else 113,'node':roles['providerNode']}
 code='EXPECTED='+repr(expected)+chr(10)+CODE
 p=subprocess.run(['docker','run','--rm','-i','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','512m','--entrypoint','/app/.venv/bin/python',image,'-I','-B','-'],input=code,text=True,capture_output=True,check=True,timeout=90)
 result=json.loads(p.stdout.strip().splitlines()[-1]);assert result['verified'];result.update(image=image,family=args.family);print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
