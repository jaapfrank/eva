"""Exact native mount readback before retained capture/worker, no product behavior changes."""
from pathlib import Path
import hashlib,json,os,runpy,sys
sys.dont_write_bytecode=True
assert os.getuid()==1000 and os.getgid()==65532
checks=[]
for item in json.loads(Path('/native-mount-map.json').read_text()):
 p=Path(item['target']);assert p.exists() and os.access(p,os.R_OK|(os.X_OK if p.is_dir() else 0)),str(p)
 if p.is_file():
  digest=hashlib.sha256(p.read_bytes()).hexdigest();assert not item.get('sha256') or digest==item['sha256'],str(p)
 else:digest=None
 checks.append({'target':str(p),'sha256':digest,'directory':p.is_dir(),'readable':True})
out=Path('/output/native-mount-readback.json')
fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 json.dump({'uid':os.getuid(),'gid':os.getgid(),'checks':checks,'before_capture_and_native_worker':True,'pid':os.getpid(),'proc_starttime':Path('/proc/self/stat').read_text().split()[21],'pid_namespace':os.readlink('/proc/self/ns/pid')},f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
sys.path.insert(0,'/fixture/eva2/benchmark/inspect_ai_v1')
sys.argv=['/xd01/capture.py',*sys.argv[1:]]
runpy.run_path('/xd01/capture.py',run_name='__main__')
