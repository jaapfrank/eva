import os,sys,json,hashlib,subprocess,stat,tarfile,shutil,runpy
from pathlib import Path
P=Path('/run/eva-cf-runtime'); PREFIX=Path('/opt/eva2/cpython31216'); V=Path('/app/.venv'); APP=Path('/app/mcpgateway')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(args):
 p=subprocess.run([str(x) for x in args],capture_output=True,text=True)
 print(json.dumps({'command':[str(x) for x in args[:2]],'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}),flush=True)
 assert p.returncode==0
 return p.stdout
def app_manifest():
 rows={}
 for p in sorted(APP.rglob('*')):
  q=p.lstat();r=str(p.relative_to(APP))
  if p.is_symlink():v={'kind':'symlink','target':os.readlink(p)}
  elif p.is_file():v={'kind':'file','sha256':sha(p),'bytes':q.st_size}
  elif p.is_dir():v={'kind':'directory'}
  else:raise AssertionError(r)
  v.update(mode=stat.S_IMODE(q.st_mode),uid=q.st_uid,gid=q.st_gid);rows[r]=v
 return rows
stage=sys.argv[1];family=sys.argv[2];assert family in ('gateway','official')
if stage=='native':
 for r,h in json.loads((P/'input-hashes.json').read_text()).items():assert sha(P/r)==h,r
 expected=json.loads((P/'old-app-source-files.json').read_text());assert app_manifest()==expected
 assert sys.version_info[:3]==(3,12,13)
 assert not PREFIX.exists()
 with tarfile.open(P/'cpython31216-ubi-native-prefix.tar.gz') as t:
  for m in t.getmembers():assert m.name=='opt/eva2/cpython31216' or m.name.startswith('opt/eva2/cpython31216/'),m.name
  t.extractall('/',filter='data')
 updates=json.loads((P/'runtime-rpm-updates.json').read_text());rpms=[P/'rpms'/x['file'] for x in updates]
 if family=='official':
  node=Path('/usr/bin/node');target=Path('/opt/eva2/node-runtime/node')
  assert node.is_symlink() and os.readlink(node)==str(target) and sha(target)=='0adde40d1b7dfa57c8bc83cbc319758c3ec6ebba21363bb2fa752ea6b267ad1b'
  before_node=node.lstat()
 run(['rpmkeys','--checksig',*rpms]);run(['rpm','-Uvh','--test',*rpms]);run(['rpm','-Uvh',*rpms])
 if family=='official':
  assert node.is_symlink() and os.readlink(node)=='node-22'
  assert sha(target)=='0adde40d1b7dfa57c8bc83cbc319758c3ec6ebba21363bb2fa752ea6b267ad1b'
  node.unlink();node.symlink_to(target);os.lchown(node,before_node.st_uid,before_node.st_gid)
  assert run([node,'--version']).strip()=='v26.13.0'
 os.execv(str(PREFIX/'bin/python3.12'),[str(PREFIX/'bin/python3.12'),'-I','-B',str(P/'assemble_runtime.py'),'venv',family])
elif stage=='venv':
 assert sys.version_info[:3]==(3,12,16)
 cfg=V/'pyvenv.cfg';assert sha(cfg)=='5fb80792d1c7d46b4cd92b00e0e822fd7450b6c5c6ce3e6e7251148f7a48d710';old=cfg.stat()
 cfg.write_text('home = /opt/eva2/cpython31216/bin\ninclude-system-site-packages = false\nversion = 3.12.16\nexecutable = /opt/eva2/cpython31216/bin/python3.12\ncommand = /opt/eva2/cpython31216/bin/python3.12 -m venv /app/.venv\n');os.chown(cfg,old.st_uid,old.st_gid);os.chmod(cfg,stat.S_IMODE(old.st_mode))
 link=V/'bin/python3';assert link.is_symlink() and os.readlink(link)=='/usr/bin/python3';ls=link.lstat();link.unlink();link.symlink_to(PREFIX/'bin/python3.12');os.lchown(link,ls.st_uid,ls.st_gid)
 os.execv(str(V/'bin/python'),[str(V/'bin/python'),'-I','-B',str(P/'assemble_runtime.py'),'wheels',family])
elif stage=='wheels':
 assert sys.version_info[:3]==(3,12,16) and sys.prefix==str(V) and sys.base_prefix==str(PREFIX)
 # A subprocess exits normally so pip's public certificate cleanup runs.
 boot="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pip';runpy.run_module('pip',run_name='__main__')"
 run([V/'bin/python','-I','-B','-c',boot,P/'pip-26.3-py3-none-any.whl','install','--no-index','--no-deps','--require-hashes','--upgrade','--no-compile','--no-cache-dir','--find-links',P/'wheelhouse','-r',P/'requirements.lock'])
 os.execv(str(V/'bin/python'),[str(V/'bin/python'),'-I','-B',str(P/'assemble_runtime.py'),'source',family])
elif stage=='source':
 expected=json.loads((P/'old-app-source-files.json').read_text());assert app_manifest()==expected
 src=P/'sources'/family;new={str(p.relative_to(src)):p for p in src.rglob('*') if p.is_file()};uid=APP.stat().st_uid;gid=APP.stat().st_gid
 # Explicitly registered old image package only; no unregistered path is removed.
 for r,e in expected.items():
  if e['kind']=='file' and r not in new:(APP/r).unlink()
 for r,p in new.items():
  dst=APP/r;dst.parent.mkdir(parents=True,exist_ok=True)
  if dst.exists():assert dst.is_file() and not dst.is_symlink()
  shutil.copyfile(p,dst);os.chmod(dst,stat.S_IMODE(p.stat().st_mode));os.chown(dst,uid,gid)
 for p in sorted(APP.rglob('*'),key=lambda x:len(x.parts),reverse=True):
  if p.is_dir() and not any(p.iterdir()):p.rmdir()
 for p in APP.rglob('*'):
  if p.is_dir():os.chown(p,uid,gid);os.chmod(p,0o755)
 assert {r:v['sha256'] for r,v in app_manifest().items() if v['kind']=='file'}=={r:sha(p) for r,p in new.items()}
 os.execv(str(V/'bin/python'),[str(V/'bin/python'),'-I','-B',str(P/'verify_runtime.py'),family])
else:raise AssertionError(stage)
