from pathlib import Path
import json,hashlib,shutil,subprocess,os
from bundle_filter import remove_blacklisted_roots
import sys
P=Path(sys.argv[1]).resolve()
images=json.loads((P/'baseline-images.json').read_text())
selected=json.loads((P/'selected-index-entries.json').read_text())
blacklist={r['derSha256'] for r in json.loads((P/'ca-blacklist-proof.json').read_text())['removedCertificates']}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def resolve_record(records,name):
 seen=set()
 while 'link' in records.get(name,{}):
  if name in seen:raise ValueError('Link cycle')
  seen.add(name);r=records[name];target=r['link']
  if r['kind']=='1':name=target.lstrip('/')
  else:name=os.path.normpath(str(Path(name).parent/target)) if not target.startswith('/') else target.lstrip('/')
 return name,records.get(name)
for role,image in images.items():
 directory=P/role;records=json.loads((directory/'baseline-filesystem.json').read_text());baseline=directory/'baseline-owned-files';context=directory/'context'
 if context.exists():raise ValueError('Preserve existing context')
 overlay=context/'overlay';overlay.mkdir(parents=True)
 raw=json.loads((directory/'baseline-status-d.json').read_text());updates=[];custom=[];omitted={};allowed={};removals=[]
 def stage_bytes(name,body,reason,mode=None):
  resolved,old=resolve_record(records,name)
  if old is None:raise ValueError('Unexpected new file '+name)
  if 'sha256' not in old:raise ValueError('Expected regular file '+name)
  target=overlay/resolved;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);target.chmod(mode if mode is not None else old['mode']);os.chown(target,old['uid'],old['gid'])
  allowed[resolved]={'before':old,'afterSha256':hashlib.sha256(body).hexdigest(),'reason':reason}
 for name,entry in selected.items():
  root=P/'extracted'/name;data=root/'data';oldmd5={line.split(None,1)[1].lstrip(' *'):line.split(None,1)[0] for line in raw[name+'.md5sums'].splitlines() if line.strip()};omitted[name]=[]
  for path,md5 in oldmd5.items():
   resolved,old=resolve_record(records,path)
   if old is None:omitted[name].append(path);continue
   if old.get('md5')!=md5:
    if path=='usr/lib/os-release' and name=='base-files':custom.append({'package':name,'path':path,'reason':'Existing custom Distroless identity; preserve exact bytes','sha256':old.get('sha256')});continue
    raise ValueError('Modified package-owned baseline '+role+':'+path)
   fresh=data/path
   if not fresh.exists():
    removals.append(resolved);allowed[resolved]={'before':old,'reason':'Authenticated package removes existing owned file'};continue
   if fresh.is_symlink():raise ValueError('File-to-link transition requires review '+path)
   if digest(fresh)!=old['sha256']:stage_bytes(path,fresh.read_bytes(),name+' '+entry['Version'])
  for path in data.rglob('*'):
   if not path.is_symlink():continue
   rel=str(path.relative_to(data));old=records.get(rel)
   if old and old.get('link')!=str(path.readlink()):raise ValueError('Changed existing package symlink requires review '+rel)
  for suffix,source in [('',root/'control/control'),('.md5sums',root/'control/md5sums')]:stage_bytes('var/lib/dpkg/status.d/'+name+suffix,source.read_bytes(),'Truthful authenticated Debian distroless package metadata')
  updates.append({'package':name,'version':entry['Version'],'packageSha256':entry['SHA256'],'omittedFilesKeptOmitted':len(omitted[name])})
 # This is version metadata, not owner configuration. Other conffiles remain untouched.
 old=baseline/'etc/debian_version'
 if old.read_bytes()!=b'13.6\n':raise ValueError('Unexpected base-files point version')
 stage_bytes('etc/debian_version',(P/'extracted/base-files/data/etc/debian_version').read_bytes(),'Authenticated base-files point-release identity')
 oldbundle=baseline/'etc/ssl/certs/ca-certificates.crt';filtered,removed=remove_blacklisted_roots(oldbundle.read_bytes(),blacklist)
 stage_bytes('etc/ssl/certs/ca-certificates.crt',filtered,'Remove ONLY two authenticated Debian-blacklisted DER root hashes; all other bytes preserved')
 manifest={'contract':'EvaDistrolessSignedPackageOverlay/v1','baselineImage':image,'packageUpdates':updates,'allowed':allowed,'remove':sorted(set(removals)),'preservedCustomFiles':custom,'omittedPackageFiles':omitted,'removedCaDerHashes':removed,'allOtherFilesMustRemainExact':True,'allConfigMustRemainExact':True,'noLiveActions':True}
 (context/'overlay-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 cleanup="""from pathlib import Path
import json,hashlib
m=json.loads(Path('/opt/eva2/native-security-overlay.json').read_text())
for name,record in m['allowed'].items():
 p=Path('/')/name
 assert not p.is_symlink() and p.is_file(),name
 assert hashlib.sha256(p.read_bytes()).hexdigest()==record['before']['sha256'],name
for name in m['remove']: (Path('/')/name).unlink()
"""
 (context/'cleanup.py').write_text(cleanup)
 config=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]['Config'];user=config.get('User') or '0:0'
 (context/'baseline-config.json').write_text(json.dumps(config,indent=2)+'\n')
 (context/'Dockerfile').write_text('FROM '+image+'\nUSER 0:0\nCOPY overlay-manifest.json /opt/eva2/native-security-overlay.json\nCOPY cleanup.py /opt/eva2/native-security-overlay-check.py\nRUN ["/usr/local/bin/python", "-B", "/opt/eva2/native-security-overlay-check.py"]\nCOPY overlay/ /\nUSER '+user+'\n')
 print(role,'staged',len(allowed),'removed',len(set(removals)),'custom',len(custom),'CAremoved',len(removed),flush=True)
