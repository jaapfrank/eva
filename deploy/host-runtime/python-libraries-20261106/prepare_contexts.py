"""Verify exact public wheel bytes and optionally materialize offline contexts."""
import argparse,hashlib,json,re,shutil
from pathlib import Path
ROOT=Path(__file__).parent
ROLES=('evaluator','browser','face')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(name):return re.sub('[-_.]+','-',name).lower()
def verify(wheelhouse):
 manifest=json.loads((ROOT/'wheel-manifest.json').read_text());by_file={row['filename']:row for row in manifest['uniqueWheels']}
 assert len(by_file)==len(manifest['uniqueWheels'])==78
 for filename,row in by_file.items():
  assert Path(filename).name==filename and filename.endswith('.whl')
  source=wheelhouse/filename;assert source.is_file() and not source.is_symlink() and sha(source)==row['sha256'],'Wheel bytes changed'
 for role in ROLES:
  lock=ROOT/(role+'-requirements.lock');assert sha(lock)==manifest['lockSha256'][role]
  parsed={}
  for line in lock.read_text().splitlines():
   match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})',line);assert match,'Unexpected requirement syntax'
   name,version,digest=match.groups();key=canonical(name);assert key not in parsed;parsed[key]=(version,digest)
  expected={canonical(row['name']):(row['version'],row['sha256']) for row in manifest['roles'][role]}
  assert parsed==expected and len(parsed)=={'evaluator':33,'browser':67,'face':62}[role]
 return manifest
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--wheelhouse',type=Path,required=True);parser.add_argument('--output',type=Path);args=parser.parse_args();manifest=verify(args.wheelhouse)
 if args.output:
  output=args.output.resolve();assert not output.exists(),'Use a new directory; no overwriting';output.mkdir(mode=0o700)
  for role in ROLES:
   target=output/role;target.mkdir(mode=0o700);shutil.copyfile(ROOT/(role+'-requirements.lock'),target/'requirements.lock')
   for row in manifest['roles'][role]:shutil.copyfile(args.wheelhouse/row['filename'],target/row['filename'])
 print(json.dumps({'contract':'EvaVerifiedHostPythonContexts/v1','officialWheelBytesVerified':78,'roles':{'evaluator':33,'browser':67,'face':62},'contextsPrepared':args.output is not None,'runtimeInstalled':False,'sourceOrCredentialsCopied':False}))
