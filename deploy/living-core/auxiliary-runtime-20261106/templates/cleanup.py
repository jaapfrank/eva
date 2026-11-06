from pathlib import Path
import hashlib,json,shutil,os
plan=json.loads(Path('/build/role-plan.json').read_text());before=json.loads(Path('/build/site-before.json').read_text());runtime=json.loads(Path('/build/old-runtime.json').read_text());site=Path('/usr/local/lib/python3.12/site-packages')
for root in plan['updatedRoots']:
 target=site/root;assert target.parent==site and not target.is_symlink() and target.exists(),root
 for file in ([target] if target.is_file() else target.rglob('*')):
  if not file.is_file() or '__pycache__' in file.parts or file.suffix in ('.pyc','.pyo'):continue
  assert not file.is_symlink();assert hashlib.sha256(file.read_bytes()).hexdigest()==before['files'][str(file.relative_to(site))],str(file)
for path,sha in plan['ownedOldExternalFiles'].items():
 file=Path(path);assert file.is_file() and not file.is_symlink() and hashlib.sha256(file.read_bytes()).hexdigest()==sha,path
for path,sha in runtime['stdlibFiles'].items():
 file=Path(path);assert file.is_file() and not file.is_symlink() and hashlib.sha256(file.read_bytes()).hexdigest()==sha,path
for path in runtime['stdlibFiles']:Path(path).unlink()
for file in Path('/usr/local/lib/python3.12').rglob('*'):
 if 'site-packages' not in file.parts and file.is_file() and file.suffix in ('.pyc','.pyo'):file.unlink()
for root in plan['updatedRoots']:
 target=site/root
 if target.is_dir():shutil.rmtree(target)
 else:target.unlink()
for path in plan['ownedOldExternalFiles']:Path(path).unlink()
for name in ['role-plan.json','site-before.json','old-runtime.json','cleanup.py']:(Path('/build')/name).unlink()
if not any(Path('/build').iterdir()):Path('/build').rmdir()
