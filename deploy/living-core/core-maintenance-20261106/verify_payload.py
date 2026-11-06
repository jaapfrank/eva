"""Verify a reproduced immutable image without live configuration or effects."""
from pathlib import Path
import hashlib,importlib.metadata as md,json,platform,sys,sysconfig
manifest=json.loads(Path('/recipe-manifest.json').read_text());role=sys.argv[1];expected=manifest['roles'][role]
assert platform.python_version()==manifest['targetPython'];site=Path('/usr/local/lib/python3.12/site-packages')
packages={d.metadata['Name'].lower().replace('_','-'):d.version for d in md.distributions(path=[str(site)])};assert packages==expected['packages']
files={str(p.relative_to(site)):hashlib.sha256(p.read_bytes()).hexdigest() for p in site.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')};digest=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest();assert digest==expected['expectedSiteTreeSha256']
for relative,wanted in manifest['sourceSha256'].items():
 if relative.startswith('src/'):path=Path('/opt/eva')/relative
 elif relative in ['pyproject.toml','deploy/requirements-4d.lock']:path=Path('/opt/eva')/relative
 else:raise AssertionError('Unexpected source binding')
 assert hashlib.sha256(path.read_bytes()).hexdigest()==wanted
assert md.metadata('eva2-living-core')['Requires-Python']=='==3.12.16'
stdlib=Path('/usr/local/lib/python3.12');runtime={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in stdlib.rglob('*') if p.is_file() and 'site-packages' not in p.relative_to(stdlib).parts and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
for name in ['/usr/local/bin/python3.12','/usr/local/lib/libpython3.12.so.1.0']:runtime[name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
assert hashlib.sha256(json.dumps(runtime,sort_keys=True,separators=(',',':')).encode()).hexdigest()==manifest['expectedPythonRuntimeFileTreeSha256']
assert {key:sysconfig.get_config_var(key) for key in manifest['expectedPythonAbi']}==manifest['expectedPythonAbi']
import eva.operations.runtime_image_update as runtime_update
import eva.operations.image_update as image_update
import eva.bootstrap.delegated_work_provider as provider
for module,relative in [(runtime_update,'operations/runtime_image_update.py'),(image_update,'operations/image_update.py'),(provider,'bootstrap/delegated_work_provider.py')]:assert Path(module.__file__)==Path('/opt/eva/src/eva')/relative
assert callable(runtime_update.recover_runtime);provider.verify_delegated_provider_dependencies()
print(json.dumps({'verified':True,'role':role,'python':platform.python_version(),'packages':len(packages),'siteTreeSha256':digest,'sourcePriorityVerified':True,'liveActions':0}))
