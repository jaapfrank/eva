"""Assemble one offline, hash-bound runtime context; no live deployment."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,stat,subprocess,zipfile
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def manifest(name):return json.loads((ROOT/name).read_text())
def copy_artifact(artifacts,out,row,subdir):
 name=row['file'];assert Path(name).name==name and name not in {'.','..'}
 src=artifacts/subdir/name
 assert src.is_file() and not src.is_symlink() and src.stat().st_size==row['bytes'] and sha(src)==row['sha256'],name
 out.parent.mkdir(parents=True,exist_ok=True)
 shutil.copyfile(src,out);out.chmod(0o644);assert out.stat().st_size==row['bytes'] and sha(out)==row['sha256'],name
def main():
 parser=argparse.ArgumentParser();parser.add_argument('family',choices=('gateway','filesystem','atomic','projects'));parser.add_argument('artifacts',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
 artifacts=args.artifacts.resolve(strict=True);out=args.output.absolute();assert artifacts.is_dir() and not out.exists() and not out.is_symlink();out.mkdir(mode=0o700,parents=False)
 pay=out/'payload';pay.mkdir()
 rows=manifest('wheel-artifacts.json');assert len(rows)==141 and len({r['file'] for r in rows})==141
 for row in rows:copy_artifact(artifacts,pay/'wheelhouse'/row['file'],row,'wheels')
 rpmset=manifest('runtime-rpm-artifacts.json')['gateway' if args.family=='gateway' else 'provider'];assert len(rpmset)==(30 if args.family=='gateway' else 37)
 for row in rpmset:copy_artifact(artifacts,pay/'rpms'/row['file'],row,'rpms')
 (pay/'runtime-rpm-updates.json').write_text(json.dumps(rpmset,sort_keys=True,indent=2)+chr(10))
 native=manifest('native-artifacts.json')
 for key in ('runtimeBuildPip','runtimePrefixArtifact'):
  row=native[key];copy_artifact(artifacts,pay/row['file'],row,'build-artifacts')
 maps=manifest('application-source-manifest.json');family='gateway' if args.family=='gateway' else 'official'
 # Extract only the known vendor application paths; reject foreign archive entries in this namespace.
 cw=next(x for x in rows if x['name']=='mcp-contextforge-gateway');buildtree=out/'source-assembly';vendor=buildtree/'mcpgateway'
 with zipfile.ZipFile(pay/'wheelhouse'/cw['file']) as z:
  files=[n for n in z.namelist() if n.startswith('mcpgateway/') and not n.endswith('/')];assert len(files)==len(maps['official'])
  for n in files:
   rel=n[len('mcpgateway/'):]
   assert rel in maps['official'] and not rel.startswith('/') and '..' not in Path(rel).parts
   data=z.read(n);assert hashlib.sha256(data).hexdigest()==maps['official'][rel]
   dst=vendor/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data);dst.chmod(0o644)
 if family=='gateway':
  patch=ROOT/'contextforge-1.1.0-eva.patch';assert sha(patch)==maps['gatewayPatchSha256']
  subprocess.run(['patch','--batch','--forward','-p1','-i',str(patch)],cwd=buildtree,check=True,capture_output=True)
 assert {str(p.relative_to(vendor)):sha(p) for p in vendor.rglob('*') if p.is_file()}==maps[family]
 for p in vendor.rglob('*'):assert not p.is_symlink()
 target=pay/'sources'/family;target.parent.mkdir();vendor.rename(target);buildtree.rmdir()
 for local,dest in [('requirements.lock','requirements.lock'),('wheel-artifacts.json','wheel-manifest.json'),('original-image-app-files.json','old-app-source-files.json'),('cpython-prefix-files.json','cpython31216-prefix-files.json'),('native-extension-names.json','native-extension-names.json'),('assemble_runtime.py','assemble_runtime.py'),('verify_runtime.py','verify_runtime.py')]:
  shutil.copyfile(ROOT/local,pay/dest);(pay/dest).chmod(0o644)
 hashes={str(p.relative_to(pay)):sha(p) for p in pay.rglob('*') if p.is_file()}
 (pay/'input-hashes.json').write_text(json.dumps(hashes,sort_keys=True,indent=2)+chr(10))
 base=next(x['baseImage'] for x in manifest('roles-and-artifacts.json')['runtimeFamilies'] if x['family']==args.family)
 assert base.startswith('sha256:') and len(base)==71 and all(c in '0123456789abcdef' for c in base[7:])
 dockerfile='FROM '+base+'\nUSER 0:0\nCOPY payload /run/eva-cf-runtime\nRUN /app/.venv/bin/python -I -B /run/eva-cf-runtime/assemble_runtime.py native '+family+' && rm -rf /run/eva-cf-runtime\nUSER 10001:10001\n'
 (out/'Dockerfile').write_text(dockerfile)
 print(json.dumps({'family':args.family,'baseImage':base,'wheelCount':len(rows),'signedRpmCount':len(rpmset),'applicationSources':len(maps[family]),'offlineInputFiles':len(hashes),'liveActions':0}))
if __name__=='__main__':main()
