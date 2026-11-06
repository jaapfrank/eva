from pathlib import Path
import hashlib,importlib.metadata as md,json,sysconfig
site=Path(sysconfig.get_paths()['purelib']);packages={};own=[]
for d in md.distributions(path=[str(site)]):
 name=d.metadata['Name'].lower().replace('_','-');assert name not in packages;packages[name]=d.version
 if name=='eva2-living-core':
  own=[f.parts[0] for f in d.files or () if f.name=='METADATA' and len(f.parts)==2 and f.parts[0].endswith('.dist-info')]
assert len(own)==1
files={str(p.relative_to(site)):hashlib.sha256(p.read_bytes()).hexdigest() for p in site.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
print(json.dumps({'site':str(site),'packages':packages,'files':files,'evaMetadata':own},sort_keys=True))
