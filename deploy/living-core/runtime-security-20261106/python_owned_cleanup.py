import pathlib,json,hashlib
manifest=json.loads(pathlib.Path('/opt/eva2/hindsight-python-owned-manifest.json').read_text())
for rel,value in manifest['before'].items():
 p=pathlib.Path('/usr/local')/rel
 if 'symlink' in value:
  assert p.is_symlink() and str(p.readlink())==value['symlink'],str(p)
 else:assert p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==value['sha256'],str(p)
for rel in manifest['before']:
 p=pathlib.Path('/usr/local')/rel
 p.unlink()
