"""Measure the exact existing CPython runtime subset, excluding site packages."""
from pathlib import Path
import hashlib,json,sys,sysconfig,ssl
root=Path('/usr/local/lib/python3.12');files={}
for p in [Path('/usr/local/bin/python3.12'),Path('/usr/local/lib/libpython3.12.so.1.0'),*root.rglob('*')]:
 if p.is_file()and not p.is_symlink()and 'site-packages'not in p.parts and '__pycache__'not in p.parts and p.suffix not in {'.pyc','.pyo'}:files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'python':sys.version.split()[0],'stdlibFiles':files,'stdlibSymlinks':{},'openssl':ssl.OPENSSL_VERSION,'config':{k:sysconfig.get_config_var(k)for k in ['ABIFLAGS','CONFIG_ARGS','EXT_SUFFIX','LDLIBRARY','MULTIARCH','Py_DEBUG','Py_ENABLE_SHARED','SIZEOF_VOID_P','SOABI']}}))
