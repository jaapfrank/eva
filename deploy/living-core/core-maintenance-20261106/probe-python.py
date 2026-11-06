from pathlib import Path
import hashlib,json,sys,sysconfig,ssl,os
root=Path('/usr/local/lib/python3.12');files={};links={}
for p in root.rglob('*'):
 if 'site-packages' in p.relative_to(root).parts or '__pycache__' in p.parts or p.suffix in ('.pyc','.pyo'):continue
 if p.is_symlink():links[str(p)]=os.readlink(p)
 elif p.is_file():files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
for name in ['/usr/local/bin/python3.12','/usr/local/lib/libpython3.12.so.1.0']:
 p=Path(name);files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'python':sys.version.split()[0],'stdlibFiles':files,'stdlibSymlinks':links,'config':{n:sysconfig.get_config_var(n) for n in ['SOABI','EXT_SUFFIX','Py_DEBUG','Py_ENABLE_SHARED','ABIFLAGS','CONFIG_ARGS','MULTIARCH','SIZEOF_VOID_P','LDLIBRARY']},'openssl':ssl.OPENSSL_VERSION},sort_keys=True))
