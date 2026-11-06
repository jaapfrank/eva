from pathlib import Path
import subprocess,json,tarfile,hashlib
import sys
P=Path(sys.argv[1]).resolve()
images=json.loads((P/'baseline-images.json').read_text())
selected=set(['etc/ssl/certs/ca-certificates.crt','etc/debian_version','etc/os-release','usr/lib/os-release'])
for role in images:
 raw=json.loads((P/role/'baseline-status-d.json').read_text())
 for name,text in raw.items():
  if name.endswith('.md5sums'):
   for line in text.splitlines():
    if line.strip():selected.add(line.split(None,1)[1].lstrip(' *'))
for root in (P/'extracted').iterdir():
 if root.name.endswith('-old'):continue
 data=root/'data'
 for path in data.rglob('*'):
  if path.is_file() or path.is_symlink():selected.add(str(path.relative_to(data)))
for role,image in images.items():
 output=P/role/'baseline-owned-files';output.mkdir(exist_ok=True)
 cid=subprocess.check_output(['docker','create','--network','none',image],text=True).strip()
 try:
  process=subprocess.Popen(['docker','export',cid],stdout=subprocess.PIPE);records={};count=0
  with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
   for member in archive:
    name=member.name
    if name.startswith('/') or '..' in Path(name).parts:raise ValueError('Unsafe export member')
    if member.isfile():
     h=hashlib.sha256();md5=hashlib.md5();f=archive.extractfile(member);body=[]
     while chunk:=f.read(1024*1024):
      h.update(chunk);md5.update(chunk)
      if name in selected:body.append(chunk)
     records[name]={'sha256':h.hexdigest(),'md5':md5.hexdigest(),'mode':member.mode,'uid':member.uid,'gid':member.gid}
     if name in selected:
      path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b''.join(body));path.chmod(member.mode);count+=1
    elif member.issym() or member.islnk():records[name]={'link':member.linkname,'kind':member.type.decode(),'mode':member.mode,'uid':member.uid,'gid':member.gid}
  assert process.wait()==0
  (P/role/'baseline-filesystem.json').write_text(json.dumps(records,sort_keys=True))
  print(role,'inventory',len(records),'selected extracted',count,flush=True)
 finally:subprocess.run(['docker','rm',cid],check=True,stdout=subprocess.DEVNULL)
