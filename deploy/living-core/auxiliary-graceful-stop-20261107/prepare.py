"""Reproduce the two source-only publisher overlays and the readonly proxy code file."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,tempfile
HERE=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def patch_file(path,patch,before,after):
 if sha(path)!=before:raise ValueError('Pinned baseline source differs')
 subprocess.run(['patch','--batch','--fuzz=0','--no-backup-if-mismatch',str(path),str(patch)],check=True,capture_output=True)
 if sha(path)!=after:raise ValueError('Patched source differs from reviewed bytes')
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--proxy-before',required=True,type=Path);p.add_argument('--build-images',action='store_true');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 manifest=json.loads((HERE/'artifact-manifest.json').read_text())
 for name,h in manifest['files'].items():
  if sha(HERE/name)!=h:raise ValueError('Recipe input differs: '+name)
 for role in ['model-publisher','hindsight-publisher']:
  directory=a.output/role;directory.mkdir();metadata=json.loads((HERE/role/'manifest.json').read_text());cid=subprocess.check_output(['docker','create',metadata['baseImage']],text=True).strip()
  try:
   subprocess.run(['docker','cp',cid+':'+metadata['sourcePath'],str(directory/'model_credentials.py')],check=True,capture_output=True)
  finally:
   value=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
   if value['State']['Status']!='created'or value['State']['Running']:raise RuntimeError('Owned extraction container unexpectedly started; inspect without removal')
   subprocess.run(['docker','rm',cid],check=True,capture_output=True)
  patch_file(directory/'model_credentials.py',HERE/role/'source.patch',metadata['beforeSha256'],metadata['afterSha256'])
  for name in ['manifest.json','Dockerfile']:shutil.copyfile(HERE/role/name,directory/name)
  if a.build_images:subprocess.run(['docker','build','--network=none','--iidfile',str(directory/'image.txt'),str(directory)],check=True)
 proxy=json.loads((HERE/'proxy-manifest.json').read_text());shutil.copyfile(a.proxy_before,a.output/'reviewed-proxy.py');patch_file(a.output/'reviewed-proxy.py',HERE/'proxy-main.patch',proxy['beforeSha256'],proxy['afterSha256']);(a.output/'reviewed-proxy.py').chmod(0o444)
 print(json.dumps({'contextsPrepared':2,'proxyPrepared':True,'productionActions':0,'freshOciIdentityNotAssumed':True}))
if __name__=='__main__':main()
