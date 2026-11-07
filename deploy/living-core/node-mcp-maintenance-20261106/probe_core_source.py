from pathlib import Path
import json,hashlib,importlib.metadata as md,importlib,sys
root=Path('/opt/eva/src/eva');site=Path('/usr/local/lib/python3.12/site-packages');runtime=Path('/usr/local/lib/python3.12')
def hashes(p):return {str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file() and not f.is_symlink() and '__pycache__' not in f.parts and f.suffix not in ('.pyc','.pyo')}
mods={}
for n in ['eva.adapters.connectors.mcp_gateway','eva.adapters.connectors.mcp_filesystem_contract','eva.providers.mcp_gateway']:
 try:m=importlib.import_module(n)
 except ModuleNotFoundError:continue
 mods[n]={'path':m.__file__,'sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()}
print(json.dumps({'source':hashes(root),'site':hashes(site),'pythonRuntime':{k:v for k,v in hashes(runtime).items() if not k.startswith('site-packages/')},'packages':dict(sorted((d.metadata['Name'].lower(),d.version) for d in md.distributions() if d.metadata.get('Name'))),'imported':mods,'python':sys.version.split()[0]},sort_keys=True))
