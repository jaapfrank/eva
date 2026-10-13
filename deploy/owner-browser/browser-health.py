from pathlib import Path
import json,socket,sys
from urllib.request import build_opener,ProxyHandler
from urllib.error import HTTPError
opener=build_opener(ProxyHandler({}))
version=json.load(opener.open('http://127.0.0.1:9222/json/version',timeout=2))
assert version['Browser'].startswith('Chrome/')
try: opener.open('http://127.0.0.1:3000/internal/v1/dashboard/browser/view/',timeout=2)
except HTTPError as error: assert error.code==401
else: raise AssertionError('viewer must require upstream authentication')
with socket.create_connection(('127.0.0.1',8083),timeout=2):pass
main=[];renderers=[]
for directory in Path('/proc').glob('[0-9]*'):
 try:
  args=(directory/'cmdline').read_bytes().replace(bytes([0]),b' ').split()
  if not args or args[0].rsplit(b'/',1)[-1] != b'chromium':continue
  status=dict(line.split(':',1) for line in (directory/'status').read_text().splitlines() if ':' in line)
  if b'--no-sandbox' in args:raise AssertionError('sandbox disabled')
  if b'--type=renderer' in args:renderers.append(status)
  elif not any(arg.startswith(b'--type=') for arg in args):main.append(status)
 except (FileNotFoundError,ProcessLookupError):continue
assert len(main)==1 and renderers
for status in main+renderers:
 assert status['Uid'].split()[0]=='1000'
 assert int(status['CapEff'],16)==0 and int(status['NoNewPrivs'])==1
 assert int(status['Seccomp'])==2
for status in renderers:assert int(status['Seccomp_filters'])>=3
