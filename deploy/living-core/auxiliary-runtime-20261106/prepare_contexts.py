from pathlib import Path
import json,hashlib,shutil,re,subprocess
import sys
P=Path(sys.argv[1]).resolve();R=P;roles=json.loads((P/'baselines.json').read_text());vendor='python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
for role,row in roles.items():
 directory=P/role;context=directory/'build';assert not context.exists();context.mkdir();family='bridge-document' if role in ['eva2-shared-chrome','eva2-document-goal-worker-au006'] else 'lease';closure=json.loads((P/family/'wheelhouse-manifest.json').read_text())['wheels'];targets={w['name']:w['version'] for w in closure};before=json.loads((directory/'site-ownership-before.json').read_text());changed={name:value for name,value in targets.items() if before['distributions'][name]['version']!=value};roots=set();external={}
 for name in changed:
  for path,h in before['distributions'][name]['ownedFiles'].items():
   p=Path(path);site=Path('/usr/local/lib/python3.12/site-packages')
   if p.is_relative_to(site):roots.add(str(p.relative_to(site)).split('/')[0])
   else:external[path]=h
 pyproject=(directory/'pyproject.before.toml').read_text();requirements={}
 for value in before['distributions']['eva2-living-core']['requiresDist']:
  match=re.fullmatch(r'([A-Za-z0-9_.-]+)(\[[^]]+\])?==(.+)',value);assert match,value;name=match[1].lower().replace('_','-');requirements[value]=match[1]+(match[2] or '')+'=='+targets[name]
 for old,new in requirements.items():assert '"'+old+'"' in pyproject;pyproject=pyproject.replace('"'+old+'"','"'+new+'"')
 pyproject=pyproject.replace('"setuptools==84.0.0"','"setuptools==84.2.1"').replace('"==3.12.14"','"==3.12.16"').replace('target-python = "3.12.14"','target-python = "3.12.16"');(context/'pyproject.toml').write_text(pyproject)
 plan={'contract':'EvaAuxiliaryRoleDependencyRuntimeOverlay/v1','role':role,'baselineImage':row['image'],'runtimeVendor':vendor,'targetPython':'3.12.16','targetVersions':targets,'changedVersions':changed,'updatedRoots':sorted(roots),'ownedOldExternalFiles':external,'applicationRequiresDistReplacements':requirements,'applicationRequiresPythonBefore':before['distributions']['eva2-living-core']['requiresPython'],'codeChanges':{},'unchangedPackageFamilies':True,'lockSha256':sha(P/family/'requirements.lock')}
 candidate=directory/'model_credentials.candidate.py'
 if candidate.exists():plan['codeChanges']['eva/operations/model_credentials.py']={'beforeSha256':sha(directory/'source/eva/operations/model_credentials.py'),'afterSha256':sha(candidate)};shutil.copyfile(candidate,context/'model_credentials.py')
 (context/'role-plan.json').write_text(json.dumps(plan,indent=2)+'\n');shutil.copyfile(directory/'site-ownership-before.json',context/'site-before.json');shutil.copyfile(R/'python16-build-core/old-runtime.json',context/'old-runtime.json');shutil.copyfile(R/'python16-build-core/vendor-runtime.json',context/'vendor-runtime.json');shutil.copytree(P/family/'wheelhouse',context/'wheels');shutil.copyfile(P/family/'requirements.lock',context/'requirements.lock')
 (context/'upgrade.lock').write_text(''.join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in closure if w['name'] in changed))
 print(role,'changed',len(changed),'preservedsource',not bool(plan['codeChanges']),flush=True)
