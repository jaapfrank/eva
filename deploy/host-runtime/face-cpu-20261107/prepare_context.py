"""Verify the existing face extractor and exact offline CPU library artifacts."""
import argparse,hashlib,json,re,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(name):return re.sub('[-_.]+','-',name).lower()
def verify(wheelhouse,source):
 manifest=json.loads((ROOT/'wheel-manifest.json').read_text());rows=manifest['wheels'];assert len(rows)==24 and len({r['filename']for r in rows})==24
 lock=ROOT/'requirements.lock';assert sha(lock)==manifest['lockSha256'];assert sha(ROOT/'runtime-environment.json')==manifest['environmentSha256'];assert json.loads((ROOT/'runtime-environment.json').read_text())=={'ORT_DISABLE_TELEMETRY':'1'};actual={}
 for line in lock.read_text().splitlines():
  match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})',line);assert match,'Unexpected lock syntax';name,version,digest=match.groups();key=canonical(name);assert key not in actual;actual[key]=(version,digest)
 assert actual=={canonical(row['name']):(row['version'],row['sha256'])for row in rows}
 assert source.is_file()and not source.is_symlink()and sha(source)==manifest['sourceScript']['sha256']
 for row in rows:
  name=row['filename'];assert Path(name).name==name and name.endswith('.whl');p=wheelhouse/name;assert p.is_file()and not p.is_symlink()and p.stat().st_size==row['bytes']and sha(p)==row['sha256'],'Unverified wheel'
 return manifest
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--wheelhouse',type=Path,required=True);p.add_argument('--source-script',type=Path,required=True);p.add_argument('--output',type=Path);args=p.parse_args();m=verify(args.wheelhouse,args.source_script)
 if args.output:
  destination=args.output.resolve();assert not destination.exists(),'Use a new context directory';destination.mkdir(mode=0o700);shutil.copyfile(ROOT/'requirements.lock',destination/'requirements.lock');shutil.copyfile(args.source_script,destination/'local_face_cpu.py');shutil.copyfile(ROOT/'runtime-environment.json',destination/'runtime-environment.json')
  for row in m['wheels']:shutil.copyfile(args.wheelhouse/row['filename'],destination/row['filename'])
 print(json.dumps({'contract':'EvaVerifiedFaceCpuContext/v1','officialWheelBytesVerified':24,'sourceScriptUnchanged':True,'modelsCopiedOrDownloaded':False,'contextPrepared':args.output is not None,'runtimeInstalled':False}))
