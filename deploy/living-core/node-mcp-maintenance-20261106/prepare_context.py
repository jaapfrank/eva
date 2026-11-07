"""Prepare one pinned maintenance context without private configuration or repo-wide copying."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,tarfile,tempfile,urllib.request
HERE=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('kind',choices=['provider','core']);p.add_argument('--role',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repository',type=Path);p.add_argument('--node-archive',type=Path);args=p.parse_args();manifest=json.loads((HERE/'manifest.json').read_text());target=manifest['providerTargets' if args.kind=='provider' else 'coreTargets'];assert args.role in target,'Unsupported explicit role';out=args.output.resolve();assert not out.exists(),'Use a new context directory';out.mkdir(mode=0o755,parents=False)
 if args.kind=='core':
  assert args.repository is not None,'An explicit repository is required';repo=args.repository.resolve(strict=True)
  for absolute,value in manifest['sourceOverlay']['sourceFiles'].items():
   assert absolute.startswith('/opt/eva/src/eva/');relative=absolute.removeprefix('/opt/eva/');source=repo/relative;assert not source.is_symlink() and source.resolve().is_relative_to(repo) and sha(source)==value['after'],'Reviewed source differs';dest=out/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest);dest.chmod(0o644)
  shutil.copyfile(HERE/'Dockerfile.core-source',out/'Dockerfile')
 else:
  assert sha(HERE/'package-lock.json')==manifest['packageLockSha256']
  with tempfile.TemporaryDirectory(prefix='eva-node26-repro-',dir=out.parent) as temporary:
   temp=Path(temporary);archive=args.node_archive
   if archive is None:
    archive=temp/'node.tar.xz';request=urllib.request.Request(manifest['vendorNode']['source']);data=urllib.request.urlopen(request,timeout=60).read(128*1024*1024+1);assert len(data)<=128*1024*1024;archive.write_bytes(data)
   assert sha(archive)==manifest['vendorNode']['sha256'],'Official archive hash differs'
   with tarfile.open(archive,'r:xz') as tar:tar.extractall(temp/'toolchain',filter='data')
   tool=temp/'toolchain/node-v26.13.0-linux-x64';node=tool/'bin/node';assert sha(node)==manifest['nodeBinarySha256'];assert sha(tool/'LICENSE')==manifest['nodeLicenseSha256'];npm=tool/'lib/node_modules/npm/bin/npm-cli.js'
   fs=out/'filesystem-mcp';fs.mkdir()
   for name in ['package.json','package-lock.json']:shutil.copyfile(HERE/name,fs/name)
   env=dict(os.environ,PATH=str(node.parent)+os.pathsep+os.environ.get('PATH',''),npm_config_cache=str(temp/'npm-cache'))
   subprocess.run([str(node),str(npm),'ci','--ignore-scripts','--engine-strict','--no-audit','--no-fund'],cwd=fs,env=env,check=True)
   assert sha(fs/'package-lock.json')==manifest['packageLockSha256'];runtime=out/'node-runtime';runtime.mkdir();shutil.copyfile(node,runtime/'node');(runtime/'node').chmod(0o755);shutil.copyfile(tool/'LICENSE',runtime/'LICENSE');(runtime/'LICENSE').chmod(0o644)
  shutil.copyfile(HERE/'Dockerfile.provider',out/'Dockerfile')
 proof={'contract':'EvaNodeMcpPublicPreparedContext/v1','kind':args.kind,'role':args.role,'retainedBaseImage':target[args.role]['baseImage'],'sourceManifestSha256':sha(HERE/'manifest.json'),'privateConfigurationIncluded':False,'noApplicationOrProviderRequests':True};(out/'prepared-context.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
if __name__=='__main__':main()
