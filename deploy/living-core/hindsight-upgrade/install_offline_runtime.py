from pathlib import Path
import subprocess,sys,os,shutil,json,hashlib,importlib.metadata as md
exec(Path('/build/cleanup.py').read_text())
env={**os.environ,'PYTHONPATH':'/build-tools:/app/api/.venv/lib/python3.11/site-packages:/app/api','PIP_CONFIG_FILE':'/dev/null','PIP_DISABLE_PIP_VERSION_CHECK':'1','PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
subprocess.run([sys.executable,'-m','pip','install','--ignore-installed','--no-index','--only-binary=:all:','--no-deps','--require-hashes','--no-compile','--prefix=/app/api/.venv','--find-links=/build/wheels','-r','/build/runtime-full.lock'],env=env,check=True)
site=Path('/app/api/.venv/lib/python3.11/site-packages')
for p in Path('/build/overlay/hindsight_api/engine').glob('*.py'):shutil.copyfile(p,site/'hindsight_api/engine'/p.name)
from pathlib import Path
import csv,io,hashlib,base64,json
site=Path('/app/api/.venv/lib/python3.11/site-packages');record=site/'hindsight_api_slim-1.2.0.dist-info/RECORD';rows=list(csv.reader(io.StringIO(record.read_text())));expected={'hindsight_api/engine/cross_encoder.py','hindsight_api/engine/memory_engine.py'};seen=set()
for row in rows:
 if row[0] in expected:
  p=site/row[0];row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).decode().rstrip('=');row[2]=str(p.stat().st_size);seen.add(row[0])
assert seen==expected
out=io.StringIO(newline='');csv.writer(out,lineterminator='\n').writerows(rows);record.write_text(out.getvalue());proof={'contract':'EvaHindsight120SourceRecordBinding/v1','patchedSourceFiles':sorted(seen),'recordSha256':hashlib.sha256(record.read_bytes()).hexdigest()};Path('/record-proof.json').write_text(json.dumps(proof,sort_keys=True)+'\n');print(json.dumps(proof))

subprocess.run([sys.executable,'-m','pip','check'],env=env,check=True)
probe="import torch,hindsight_api,asyncpg,json;import hindsight_api.engine.cross_encoder,hindsight_api.engine.memory_engine;print(json.dumps({'hindsight':hindsight_api.__version__,'torch':torch.__version__,'torchPath':torch.__file__,'cuda':torch.cuda.is_available(),'asyncpg':asyncpg.__version__}))"
r=subprocess.run([sys.executable,'-B','-c',probe],env=env,check=True,capture_output=True,text=True);proof=json.loads(r.stdout.strip().splitlines()[-1]);assert proof['torch']=='2.13.0+cpu' and proof['hindsight']=='1.2.0' and proof['cuda'] is False;assert Path('/app/api/.venv/bin/hindsight-api').is_file()
proof['contract']='EvaHindsight120OfflineImageBuild/v1';proof['sourceCommit']='34a138684a899b6ec34175f7a2b633860466d289';proof['lockSha256']=hashlib.sha256(Path('/build/runtime-full.lock').read_bytes()).hexdigest();proof['sourceHashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/build/overlay/hindsight_api/engine').glob('*.py')};Path('/build-proof.json').write_text(json.dumps(proof,sort_keys=True)+'\n');print(json.dumps(proof))
