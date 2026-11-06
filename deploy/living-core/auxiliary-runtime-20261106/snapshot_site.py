"""Read package ownership only; never load app configuration or credentials."""
from pathlib import Path
import hashlib,importlib.metadata as md,json
site=Path('/usr/local/lib/python3.12/site-packages')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files={str(p.relative_to(site)):sha(p)for p in site.rglob('*')if p.is_file()and not p.is_symlink()and '__pycache__'not in p.parts and p.suffix not in {'.pyc','.pyo'}}
distributions={}
for d in md.distributions(path=[str(site)]):
 name=d.metadata['Name'].lower().replace('_','-');assert name not in distributions
 owned={}
 for f in d.files or []:
  p=Path(d.locate_file(f)).resolve()
  if p.is_file()and '__pycache__'not in p.parts and p.suffix not in {'.pyc','.pyo'}:owned[str(p)]=sha(p)
 distributions[name]={'version':d.version,'ownedFiles':owned,'requiresDist':d.metadata.get_all('Requires-Dist')or [],'requiresPython':d.metadata.get('Requires-Python')}
print(json.dumps({'files':files,'distributions':distributions}))
