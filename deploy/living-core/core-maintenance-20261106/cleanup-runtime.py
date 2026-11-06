from pathlib import Path
import hashlib,json,os
expected=json.loads(Path('/build/old-runtime.json').read_text());root=Path('/usr/local/lib/python3.12');actual={};links={}
for p in root.rglob('*'):
 if 'site-packages' in p.relative_to(root).parts or '__pycache__' in p.parts or p.suffix in ('.pyc','.pyo'):continue
 if p.is_symlink():links[str(p)]=os.readlink(p)
 elif p.is_file():actual[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
for name in ['/usr/local/bin/python3.12','/usr/local/lib/libpython3.12.so.1.0']:
 p=Path(name);actual[name]=hashlib.sha256(p.read_bytes()).hexdigest()
assert actual==expected['stdlibFiles'] and links==expected['stdlibSymlinks'],'Actual interpreter differs from frozen vendor14 bytes'
for name in actual:Path(name).unlink()
for p in root.rglob('*'):
 if 'site-packages' in p.relative_to(root).parts:continue
 if p.is_file() and p.suffix in ('.pyc','.pyo'):p.unlink()
for p in sorted(root.rglob('*'),key=lambda p:len(p.parts),reverse=True):
 if 'site-packages' in p.relative_to(root).parts:continue
 if p.is_dir() and not any(p.iterdir()):p.rmdir()
for n in ['old-runtime.json','cleanup-runtime.py']:(Path('/build')/n).unlink()
if not any(Path('/build').iterdir()):Path('/build').rmdir()
