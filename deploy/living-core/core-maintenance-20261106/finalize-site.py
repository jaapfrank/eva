"""Validate one offline role dependency overlay and keep its own METADATA truthful."""
from pathlib import Path
import base64,csv,hashlib,importlib.metadata as md,io,json,re,shutil,sys,sysconfig
SITE=Path(sysconfig.get_paths()['purelib']);before=json.loads(Path('/build/site-before.json').read_text());plan=json.loads(Path('/build/role-plan.json').read_text());target=plan['changedVersions']
dists={};packages={}
for d in md.distributions(path=[str(SITE)]):
 n=d.metadata['Name'].lower().replace('_','-');assert n not in dists,'Duplicate distribution';dists[n]=d;packages[n]=d.version
assert packages==plan['candidatePackageSet'],'Candidate package set differs'
assert 'pip' not in packages and 'wheel' not in packages
own=SITE/before['evaMetadata'][0];metadata=own/'METADATA';record=own/'RECORD';assert hashlib.sha256(metadata.read_bytes()).hexdigest()==before['files'][str(metadata.relative_to(SITE))]
app=json.loads(Path('/build/application-requirements.json').read_text());raw=metadata.read_text();head,separator,body=raw.partition('\n\n')
# Dependencies were already declared by the source package. Align the installed
# wheel metadata with that authority, including its existing optional model group.
lines=head.splitlines();oldrequires=[line for line in lines if line.startswith('Requires-Dist:')]
expected=set(app['normalizedNames'])
for line in oldrequires:
 m=re.match(r'Requires-Dist:\s*([A-Za-z0-9_.-]+)',line);assert m and m[1].lower().replace('_','-') in expected,'Unexpected old application dependency'
lines=[line for line in lines if not line.startswith(('Requires-Dist:','Provides-Extra:'))]
lines+=['Requires-Dist: '+req for req in app['base']]
for group,requirements in sorted(app['optional'].items()):
 lines.append('Provides-Extra: '+group)
 lines+=['Requires-Dist: '+req+'; extra == "'+group+'"' for req in requirements]
metadata.write_text('\n'.join(lines)+'\n\n'+body)
rows=list(csv.reader(io.StringIO(record.read_text())));relative=str(metadata.relative_to(SITE));found=0
for row in rows:
 if row[0]==relative:
  row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(metadata.read_bytes()).digest()).decode().rstrip('=');row[2]=str(metadata.stat().st_size);found+=1
assert found==1
stream=io.StringIO(newline='');csv.writer(stream,lineterminator='\n').writerows(rows);record.write_text(stream.getvalue())
oldroots=set(plan['updatedRoots']);newroots=set();otherroots=set();outside={}
allowed_console={'alembic','fastapi','normalizer','openai','genai-prices','httpx','httpx2','uvicorn','websockets','mako-render','idna'}
OUT=Path('/updated-external');OUT.mkdir()
for n,d in dists.items():
 roots=set()
 for entry in d.files or ():
  p=Path(d.locate_file(entry)).resolve()
  if SITE in p.parents:roots.add(p.relative_to(SITE).parts[0])
  elif n in target:
   assert (p.parent==Path('/usr/local/bin') and p.name in allowed_console) or p==Path('/usr/local/include/python3.12/greenlet/greenlet.h'),'Unexpected wheel external file: '+n+': '+str(p)
   assert p.is_file() and not p.is_symlink()
   dest=OUT/str(p).lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);dest.chmod(0o755 if p.parent==Path('/usr/local/bin') else 0o644);outside[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
 if n in target:newroots.update(roots)
 else:otherroots.update(roots)
assert not newroots&otherroots,'Updated namespace overlaps an unrelated distribution'
files={str(p.relative_to(SITE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in SITE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
allowed={str(metadata.relative_to(SITE)),str(record.relative_to(SITE))}
oldother={n:h for n,h in before['files'].items() if n.split('/')[0] not in oldroots and n not in allowed};newother={n:h for n,h in files.items() if n.split('/')[0] not in newroots and n not in allowed};assert oldother==newother,'Unrelated package files changed'
proof={'contract':'EvaFullCoreDependencyOverlay/v1','changedVersions':target,'packageSet':packages,'unchangedFileCount':len(newother),'unrelatedPackageFilesPreserved':True,'applicationMetadataMatchesSourceRequirements':True,'applicationMetadataSha256':hashlib.sha256(metadata.read_bytes()).hexdigest(),'externalOwnedFiles':outside,'newTargetFileTreeSha256':hashlib.sha256(json.dumps({n:h for n,h in files.items() if n.split('/')[0] in newroots},sort_keys=True,separators=(',',':')).encode()).hexdigest()}
Path('/build-proof.json').write_text(json.dumps(proof,sort_keys=True,separators=(',',':'))+'\n');print(json.dumps({'verified':True,'updatedPackages':len(target),'unchangedFileCount':len(newother)}))
