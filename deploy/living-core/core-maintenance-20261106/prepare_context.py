"""Prepare one secret-free, hash-bound overlay context; never install a service."""
from __future__ import annotations
import argparse,hashlib,json,re,shutil,subprocess,tomllib,urllib.request
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def run(args):return subprocess.check_output(args,text=True,timeout=90)
def dump(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+"\n")
def probe(image,script):
 args=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--mount','type=bind,src='+str(HERE/script)+',dst=/probe.py,readonly','--entrypoint','/usr/local/bin/python',image,'/probe.py']
 return json.loads(run(args))
def copy(source,target):shutil.copy2(source,target)
def prepare(role,output,wheelhouse=None):
 manifest=json.loads((HERE/'manifest.json').read_text());selected=manifest['roles'][role]
 output=output.absolute();assert not output.exists() and not output.is_symlink(),"Output must be a new directory"
 assert output.parent.resolve(strict=True)==output.parent,"Output parent must be canonical"
 for relative,expected in manifest['sourceSha256'].items():assert sha(ROOT/relative)==expected,"Source differs: "+relative
 base=selected['baselineImage'];assert json.loads(run(['docker','image','inspect',base]))[0]['Id']==base
 for image in [manifest['historicalDependencyBuilderImage'],manifest['pythonVendorImage']]:run(['docker','image','inspect',image])
 before=probe(base,'probe-site.py');old_runtime=probe(base,'probe-python.py');vendor14=probe(manifest['historicalDependencyBuilderImage'],'probe-python.py');vendor16=probe(manifest['pythonVendorImage'],'probe-python.py')
 assert old_runtime['python']=='3.12.14' and vendor16['python']=='3.12.16'
 assert old_runtime['stdlibFiles']==vendor14['stdlibFiles'] and old_runtime['config']==vendor16['config']
 assert set(before['packages'])==set(selected['packages']),"Package additions/removals require a new recipe"
 assert {n:v for n,v in selected['packages'].items() if before['packages'][n]!=v}==selected['changedVersions']
 output.mkdir(mode=0o700);dep=output/'dependencies';comp=output/'comparator';native=output/'python';dep.mkdir();comp.mkdir();native.mkdir();(dep/'wheels').mkdir()
 for row in manifest['wheels']:
  if row['name'] not in selected['changedVersions']:continue
  filename=row['filename'];assert Path(filename).name==filename and filename.endswith('.whl')
  target=dep/'wheels'/filename;cached=wheelhouse/filename if wheelhouse else None
  if cached and cached.is_file():copy(cached,target)
  else:
   assert row['url'].startswith('https://files.pythonhosted.org/packages/')
   with urllib.request.urlopen(row['url'],timeout=60) as stream,target.open('wb') as out:shutil.copyfileobj(stream,out)
  assert sha(target)==row['sha256'];target.chmod(0o444)
 wheels={r['name']:r for r in manifest['wheels']}
 (dep/'upgrade.lock').write_text(''.join(name+'=='+version+' --hash=sha256:'+wheels[name]['sha256']+'\n' for name,version in sorted(selected['changedVersions'].items())))
 plan={k:selected[k] for k in ['baselineImage','changedVersions','updatedRoots','ownedOldExternalFiles']};plan['candidatePackageSet']=selected['packages'];dump(dep/'role-plan.json',plan);dump(dep/'site-before.json',before)
 app=tomllib.loads((ROOT/'pyproject.toml').read_text())['project'];base_requirements=app['dependencies'];optional=app.get('optional-dependencies',{});names={re.match(r'([A-Za-z0-9_.-]+)',value)[1].lower().replace('_','-') for value in base_requirements+sum(optional.values(),[])}
 dump(dep/'application-requirements.json',{'base':base_requirements,'optional':optional,'normalizedNames':sorted(names)})
 for source,target in [('Dockerfile.dependencies','Dockerfile'),('install-dependencies.py','install-dependencies.py'),('finalize-site.py','finalize-core-site.py'),('cleanup-site.py','cleanup-site.py')]:copy(HERE/source,dep/target)
 copy(ROOT/'src/eva/bootstrap/delegated_work_provider.py',dep/'delegated_work_provider.py');copy(ROOT/'deploy/requirements-4d.lock',dep/'requirements-4d.lock')
 # These exact historical dependency-stage bytes are not a deployment target.
 old_project=(ROOT/'pyproject.toml').read_text();assert old_project.count('3.12.16')==2;(dep/'pyproject.toml').write_text(old_project.replace('3.12.16','3.12.14'))
 copy(HERE/'Dockerfile.comparator',comp/'Dockerfile');copy(ROOT/'src/eva/operations/image_update.py',comp/'image_update.py')
 for source,target in [('Dockerfile.python','Dockerfile'),('prepare-runtime.py','prepare-runtime.py'),('cleanup-runtime.py','cleanup-runtime.py')]:copy(HERE/source,native/target)
 dump(native/'vendor-runtime.json',vendor16);dump(native/'old-runtime.json',old_runtime);copy(ROOT/'pyproject.toml',native/'pyproject.toml');copy(ROOT/'src/eva/operations/runtime_image_update.py',native/'runtime_image_update.py')
 # Record the exact selected public inputs without environment, credentials or mounts.
 dump(output/'context-proof.json',{'contract':'EvaPreparedPublicOverlayContext/v1','role':role,'baselineImage':base,'sourceSha256':manifest['sourceSha256'],'targetPython':'3.12.16','wheelCount':len(selected['changedVersions']),'files':{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}})
 return {'prepared':True,'role':role,'targetPython':'3.12.16','baselineImage':base,'changedWheelCount':len(selected['changedVersions']),'liveActions':0}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--role',required=True,choices=['core','worker']);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--wheelhouse',type=Path);args=parser.parse_args();print(json.dumps(prepare(args.role,args.output,args.wheelhouse),sort_keys=True))
if __name__=='__main__':main()
