import hashlib,json,subprocess,importlib.metadata as md,os,shutil
from pathlib import Path
root=Path('/opt/eva2/filesystem-mcp');node=Path('/usr/bin/node');npm=[]
for p in sorted((root/'node_modules').rglob('package.json')):
 try:q=json.loads(p.read_text())
 except (OSError,ValueError):continue
 if q.get('name') and q.get('version'):npm.append({'path':str(p.relative_to(root)),'name':q['name'],'version':q['version']})
files={}
for p in sorted(Path('/opt/eva2').rglob('*')):
 if p==root or root in p.parents or 'node-runtime' in p.parts or p.is_dir():continue
 if p.is_symlink():files[str(p)]={'symlink':os.readlink(p)}
 elif p.is_file():files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
rpms=subprocess.check_output(['rpm','-qa','--qf','%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n'],text=True).splitlines()
print(json.dumps({'contract':'EvaFilesystemNodeActualImageReadback/v1','nodeVersion':subprocess.check_output([str(node),'--version'],text=True).strip(),'nodePathFromSearch':shutil.which('node'),'nodeVersionThroughPath':subprocess.check_output(['node','--version'],text=True).strip(),'libatomicBinarySha256':hashlib.sha256(Path('/usr/lib64/libatomic.so.1.2.0').read_bytes()).hexdigest() if Path('/usr/lib64/libatomic.so.1.2.0').exists() else None,'nodeSymlink':os.readlink(node),'nodeSha256':hashlib.sha256(node.read_bytes()).hexdigest(),'npm':npm,'lockSha256':hashlib.sha256((root/'package-lock.json').read_bytes()).hexdigest(),'packages':dict(sorted((d.metadata['Name'].lower(),d.version) for d in md.distributions() if d.metadata.get('Name'))),'otherOptEvaFiles':files,'rpms':dict(line.split(' ',1) for line in rpms)},sort_keys=True))
