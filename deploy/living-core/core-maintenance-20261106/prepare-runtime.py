from pathlib import Path
import base64,csv,hashlib,io,json,shutil
root=Path('/usr/local/lib/python3.12');out=Path('/runtime');expected=json.loads(Path('/build/vendor-runtime.json').read_text())
for name,h in expected['stdlibFiles'].items():
 p=Path(name);assert p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==h;target=out/name.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
assert not expected['stdlibSymlinks']
proof=json.loads(Path('/dependency-proof.json').read_text());previous_metadata=proof['applicationMetadataSha256']
metadata=Path('/application-dist-info/METADATA');shutil.copyfile(metadata,'/application-dist-info-before-METADATA');record=Path('/application-dist-info/RECORD');raw=metadata.read_text();assert raw.count('Requires-Python: ==3.12.14')==1;metadata.write_text(raw.replace('Requires-Python: ==3.12.14','Requires-Python: ==3.12.16'))
rows=list(csv.reader(io.StringIO(record.read_text())));relative='eva2_living_core-0.0.0.dist-info/METADATA';found=0
for row in rows:
 if row[0]==relative:
  row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(metadata.read_bytes()).digest()).decode().rstrip('=');row[2]=str(metadata.stat().st_size);found+=1
assert found==1;stream=io.StringIO(newline='');csv.writer(stream,lineterminator='\n').writerows(rows);record.write_text(stream.getvalue())

assert previous_metadata==hashlib.sha256(Path('/application-dist-info-before-METADATA').read_bytes()).hexdigest()
proof['previousApplicationMetadataSha256']=previous_metadata
proof['applicationMetadataSha256']=hashlib.sha256(metadata.read_bytes()).hexdigest()
proof['applicationRequiresPython']='==3.12.16'
proof['runtimeInterpreterPatch']={'from':'3.12.14','to':'3.12.16','vendorImage':'python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df','nativeOsLibrariesPreserved':True}
Path('/dependency-proof-current.json').write_text(json.dumps(proof,sort_keys=True,separators=(',',':'))+'\n')
Path('/python-runtime-upgrade.json').write_text(json.dumps({'contract':'EvaPythonRuntimePatch/v1','from':'3.12.14','to':'3.12.16','vendorImage':'python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df','ownedRuntimeFileTreeSha256':hashlib.sha256(json.dumps(expected['stdlibFiles'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),'abi':expected['config'],'applicationMetadataSha256':proof['applicationMetadataSha256'],'priorDependencyProofSha256':hashlib.sha256(Path('/dependency-proof.json').read_bytes()).hexdigest(),'nativeOsLibrariesPreserved':True},sort_keys=True,separators=(',',':'))+'\n')
