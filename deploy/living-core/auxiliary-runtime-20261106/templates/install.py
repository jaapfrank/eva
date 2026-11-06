from pathlib import Path
import json,subprocess,sys,hashlib,csv,io,base64,importlib.metadata as md,shutil
plan=json.loads(Path('/build/role-plan.json').read_text());site=Path('/usr/local/lib/python3.12/site-packages')
subprocess.run([sys.executable,'-m','pip','uninstall','--yes',*sorted(plan['changedVersions'])],check=True)
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/build/wheels','--only-binary=:all:','--no-deps','--no-compile','--require-hashes','-r','/build/upgrade.lock'],check=True)
app=md.distribution('eva2-living-core');metadata=Path(app._path)/'METADATA';record=Path(app._path)/'RECORD';raw=metadata.read_text();previous=hashlib.sha256(metadata.read_bytes()).hexdigest()
for old,new in plan['applicationRequiresDistReplacements'].items():
 assert raw.count('Requires-Dist: '+old+'\n')==1,old;raw=raw.replace('Requires-Dist: '+old+'\n','Requires-Dist: '+new+'\n')
assert raw.count('Requires-Python: '+plan['applicationRequiresPythonBefore']+'\n')==1;raw=raw.replace('Requires-Python: '+plan['applicationRequiresPythonBefore']+'\n','Requires-Python: ==3.12.16\n');metadata.write_text(raw)
rows=list(csv.reader(io.StringIO(record.read_text())));found=0
for row in rows:
 if row[0]==str(metadata.relative_to(site)):
  row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(metadata.read_bytes()).digest()).decode().rstrip('=');row[2]=str(metadata.stat().st_size);found+=1
assert found==1;stream=io.StringIO(newline='');csv.writer(stream,lineterminator='\n').writerows(rows);record.write_text(stream.getvalue())
actual={d.metadata['Name'].lower().replace('_','-'):d.version for d in md.distributions(path=[str(site)])};expected={**plan['targetVersions'],'eva2-living-core':app.version};assert actual==expected,(actual,expected)
updated=Path('/updated-external');external={}
for name in plan['changedVersions']:
 d=md.distribution(name)
 for f in d.files or []:
  path=Path(d.locate_file(f)).resolve()
  if not path.is_relative_to(site) and path.is_file():
   assert str(path).startswith(('/usr/local/bin/','/usr/local/include/python3.12/greenlet/')),str(path)
   destination=updated/str(path).lstrip('/');destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination);external[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
subprocess.run([sys.executable,'-m','pip','check'],check=True)
proof={**plan,'previousApplicationMetadataSha256':previous,'applicationMetadataSha256':hashlib.sha256(metadata.read_bytes()).hexdigest(),'applicationRecordSha256':hashlib.sha256(record.read_bytes()).hexdigest(),'versions':actual,'newExternalOwnedFiles':external,'pipCheck':True,'noLiveEffects':True}
Path('/build-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
