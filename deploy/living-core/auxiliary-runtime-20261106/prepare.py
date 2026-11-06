"""Prepare checksum-bound offline runtime contexts; activation is deliberately absent."""
from pathlib import Path
import argparse,base64,csv,hashlib,io,json,shutil,subprocess,tempfile,urllib.request
HERE=Path(__file__).resolve().parent

def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(v):return sha(json.dumps(v,sort_keys=True,separators=(',',':')).encode())
def run(args):return subprocess.check_output(args,text=True)
def snapshot(image,script):
 return json.loads(run(['docker','run','--rm','--network','none','--read-only','--mount','type=bind,src='+str(HERE/script)+',dst=/snapshot.py,readonly','--entrypoint','/usr/local/bin/python',image,'-B','/snapshot.py']))
def copy_from(image,mapping):
 cid=run(['docker','create','--network','none',image]).strip()
 try:
  for source,target in mapping.items():
   target.parent.mkdir(parents=True,exist_ok=True);subprocess.run(['docker','cp',cid+':'+source,str(target)],check=True)
 finally:subprocess.run(['docker','rm',cid],check=True,stdout=subprocess.DEVNULL)
def apply_patch(target,patch,before,after):
 if sha(target.read_bytes())!=before:raise ValueError('Exact baseline source mismatch: '+target.name)
 subprocess.run(['patch','--batch','--fuzz=0',str(target),str(patch)],check=True)
 if sha(target.read_bytes())!=after:raise ValueError('Exact patched source mismatch: '+target.name)
def build(context,target):
 subprocess.run(['docker','build','--network','none','--pull=false','--iidfile',str(target),str(context)],check=True)
 return target.read_text().strip()
NATIVE_FILES=('prepare_native.py','extract_baseline.py','prepare_overlays.py','bundle_filter.py','vendor-inputs.json','trixie.InRelease','trixie-security.InRelease')
def verified_native_sources(source,manifest_sha256):
 raw=(source/'artifact-manifest.json').read_bytes()
 if sha(raw)!=manifest_sha256:raise ValueError('Reviewed native recipe manifest changed')
 manifest=json.loads(raw)['files'];bodies={}
 if not set(NATIVE_FILES).issubset(manifest):raise ValueError('Native recipe copied-file whitelist incomplete')
 for name,expected in manifest.items():
  path=Path(name)
  if path.is_absolute()or '..'in path.parts:raise ValueError('Unsafe native recipe manifest path')
  actual=source/path
  if actual.is_symlink()or not actual.is_file():raise ValueError('Native recipe file is not a regular source')
  body=actual.read_bytes()
  if sha(body)!=expected:raise ValueError('Native recipe source changed: '+name)
  if name in NATIVE_FILES:bodies[name]=body
 return bodies

def prepare(output,wheel_cache=None,index_directory=None,deb_directory=None,build_images=False):
 if output.exists():raise ValueError('Choose a new output; retain previous evidence')
 output.mkdir(parents=True);inputs=json.loads((HERE/'build-inputs.json').read_text());roles=inputs['roles'];(output/'baselines.json').write_text(json.dumps(roles))
 native_source=(HERE/inputs['nativeRecipe']).resolve()if build_images else None
 native_bodies=verified_native_sources(native_source,inputs['nativeRecipeArtifactManifestSha256'])if build_images else None
 for family in ['bridge-document','lease']:
  dest=output/family;dest.mkdir();shutil.copy2(HERE/family/'requirements.lock',dest/'requirements.lock');shutil.copy2(HERE/family/'wheelhouse-manifest.json',dest/'wheelhouse-manifest.json');wheels=dest/'wheelhouse';wheels.mkdir()
  for w in json.loads((dest/'wheelhouse-manifest.json').read_text())['wheels']:
   cached=wheel_cache/family/'wheelhouse'/w['file']if wheel_cache else None
   if cached and cached.is_file():body=cached.read_bytes()
   else:
    with urllib.request.urlopen(w['officialUrl'],timeout=60)as response:body=response.read()
   if len(body)!=w['bytes']or sha(body)!=w['sha256']:raise ValueError('Official wheel bytes mismatch: '+w['file'])
   (wheels/w['file']).write_bytes(body)
 runtime=output/'python16-build-core';runtime.mkdir();first=next(iter(roles.values()))['image']
 for label,image,expected in [('old',first,inputs['runtimeFileMappingSha256']['before']),('vendor',inputs['pythonVendor'],inputs['runtimeFileMappingSha256']['after'])]:
  probe=snapshot(image,'snapshot_runtime.py')
  if len(probe['stdlibFiles'])!=inputs['pythonRuntimeFiles']or canonical(probe['stdlibFiles'])!=expected:raise ValueError('Pinned runtime bytes mismatch')
  (runtime/(label+'-runtime.json')).write_text(json.dumps(probe))
 for role,row in roles.items():
  directory=output/role;directory.mkdir();before=snapshot(row['image'],'snapshot_site.py');(directory/'site-ownership-before.json').write_text(json.dumps(before));copy_from(row['image'],{'/opt/eva/pyproject.toml':directory/'pyproject.before.toml'})
  if 'source'in row:
   source=directory/'source/eva/operations/model_credentials.py';copy_from(row['image'],{'/opt/eva/src/eva/operations/model_credentials.py':source});candidate=directory/'model_credentials.candidate.py';shutil.copy2(source,candidate);apply_patch(candidate,HERE/'patches'/(role+'.patch'),row['source']['beforeSha256'],row['source']['afterSha256'])
 subprocess.run(['python3',str(HERE/'prepare_contexts.py'),str(output)],check=True)
 for role in roles:
  context=output/role/'build'
  for name in ['install.py','prepare-runtime.py','cleanup.py']:shutil.copy2(HERE/'templates'/name,context/name)
  shutil.copy2(HERE/'templates'/(role+'.Dockerfile'),context/'Dockerfile')
 # Version-bound native consumer: patch only two owned sources plus one truthful RECORD row.
 spec=inputs['hindsight'];directory=output/'hindsight-consumer';context=directory/'context';context.mkdir(parents=True);site='/app/api/.venv/lib/python3.11/site-packages/'
 copy_from(spec['baseImage'],{site+'hindsight_api/engine/providers/codex_llm.py':context/'codex_llm.py','/opt/eva2/hindsight_entrypoint.py':context/'hindsight_entrypoint.py',site+'hindsight_api_slim-1.2.0.dist-info/RECORD':directory/'RECORD.before'})
 for name in ['codex_llm.py','hindsight_entrypoint.py']:
  item=spec['source'][name];apply_patch(context/name,HERE/'patches'/(name+'.patch'),item['beforeSha256'],item['afterSha256'])
 rows=list(csv.reader(io.StringIO((directory/'RECORD.before').read_text())));count=0;body=(context/'codex_llm.py').read_bytes()
 if sha((directory/'RECORD.before').read_bytes())!=spec['recordBeforeSha256']:raise ValueError('Baseline native RECORD changed')
 for row in rows:
  if row[0]=='hindsight_api/engine/providers/codex_llm.py':row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip('=');row[2]=str(len(body));count+=1
 if count!=1:raise ValueError('Native RECORD row not unique')
 text=io.StringIO();csv.writer(text,lineterminator='\n').writerows(rows);(context/'RECORD').write_text(text.getvalue())
 if sha((context/'RECORD').read_bytes())!=spec['recordAfterSha256']:raise ValueError('Patched RECORD mismatch')
 (context/'manifest.json').write_text(json.dumps(spec,indent=2)+'\n');(context/'Dockerfile').write_text('FROM '+spec['baseImage']+'\nUSER 0:0\nCOPY codex_llm.py '+site+'hindsight_api/engine/providers/codex_llm.py\nCOPY RECORD '+site+'hindsight_api_slim-1.2.0.dist-info/RECORD\nCOPY hindsight_entrypoint.py /opt/eva2/hindsight_entrypoint.py\nCOPY manifest.json /opt/eva2/hindsight-external-access-only-consumer.json\nUSER 1000:1000\n')
 # Minimal package namespace permits the public native tests to use actual publisher code without a whole-workspace overlay.
 publisher=output/'publisher-test-source/eva';(publisher/'operations').mkdir(parents=True);(publisher/'adapters/credentials').mkdir(parents=True)
 source_role='eva2-managed-b-slot3-hindsight-access-lease-1';shutil.copy2(output/source_role/'model_credentials.candidate.py',publisher/'operations/model_credentials.py');copy_from(roles[source_role]['image'],{'/opt/eva/src/eva/adapters/credentials/runtime_files.py':publisher/'adapters/credentials/runtime_files.py'})
 for sub in ['','operations','adapters','adapters/credentials']:(publisher/sub/'__init__.py').write_text('')
 images={}
 if build_images:
  for role in roles:images[role]=build(output/role/'build',output/role/'dependency-image.txt')
  # Reuse the previously reviewed signed-package preparer, changing only its exact image inputs.
  recipe=output/'native-recipe';recipe.mkdir()
  for name,body in native_bodies.items():(recipe/name).write_bytes(body)
  native=json.loads((recipe/'vendor-inputs.json').read_text());native['native']['baselines']=images;(recipe/'vendor-inputs.json').write_text(json.dumps(native,indent=2)+'\n');command=['python3',str(recipe/'prepare_native.py'),'--output',str(output/'native-final'),'--build-images']
  if index_directory:command+=['--index-directory',str(index_directory)]
  if deb_directory:command+=['--deb-directory',str(deb_directory)]
  subprocess.run(command,check=True)
  for role in roles:
   image=(output/'native-final'/role/'image.txt').read_text().strip();(output/role/'final-image.txt').write_text(image+'\n');images[role]=image
  images['hindsight']=build(context,directory/'image-final.txt')
 (output/'reproduction-result.json').write_text(json.dumps({'prepared':True,'built':build_images,'activated':False,'images':images},indent=2)+'\n');return images
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--wheel-cache',type=Path);parser.add_argument('--index-directory',type=Path);parser.add_argument('--deb-directory',type=Path);parser.add_argument('--build-images',action='store_true');args=parser.parse_args();prepare(args.output.resolve(),args.wheel_cache,args.index_directory,args.deb_directory,args.build_images)
