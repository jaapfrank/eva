from pathlib import Path
import json,hashlib,shutil
p=json.loads(Path('/build/role-plan.json').read_text());b=json.loads(Path('/build/site-before.json').read_text());site=Path('/usr/local/lib/python3.12/site-packages')
for root in p['updatedRoots']:
 target=site/root;assert target.parent==site and not target.is_symlink()
 assert target.exists(),root
 for f in ([target] if target.is_file() else target.rglob('*')):
  if not f.is_file() or '__pycache__' in f.parts or f.suffix in ('.pyc','.pyo'):continue
  assert not f.is_symlink();relative=str(f.relative_to(site));assert b['files'].get(relative)==hashlib.sha256(f.read_bytes()).hexdigest(),relative
for name,h in p['ownedOldExternalFiles'].items():
 f=Path(name);assert f.is_file() and not f.is_symlink() and hashlib.sha256(f.read_bytes()).hexdigest()==h
for root in p['updatedRoots']:
 f=site/root
 if f.is_dir():shutil.rmtree(f)
 else:f.unlink()
for name in p['ownedOldExternalFiles']:Path(name).unlink()

for name in ['role-plan.json','site-before.json','cleanup-site.py']:(Path('/build')/name).unlink()
if not any(Path('/build').iterdir()):Path('/build').rmdir()
