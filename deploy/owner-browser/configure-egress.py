#!/usr/bin/env python3
from pathlib import Path
import subprocess,json
root=Path(__file__).resolve().parent
ids=json.loads((root/'container-ids.json').read_text());name=root.name+'-egress'
r=subprocess.run(['docker','network','inspect',name],capture_output=True,text=True)
if r.returncode:
 subprocess.run(['docker','network','create','--subnet','192.168.16.0/20','--gateway','192.168.16.1','--label','io.eva2.browser-candidate='+root.name,name],check=True)
net=json.loads(subprocess.run(['docker','network','inspect',name],capture_output=True,text=True,check=True).stdout)[0]
assert net['EnableIPv6'] is False and net['IPAM']['Config'][0]['Subnet']=='192.168.16.0/20'
d=json.loads(subprocess.run(['docker','inspect',ids['browser']],capture_output=True,text=True,check=True).stdout)[0]
if name not in d['NetworkSettings']['Networks']:
 subprocess.run(['docker','network','connect','--gw-priority','1','--ip','192.168.16.2',name,ids['browser']],check=True)
print('Separate egress attached. Apply browser-firewall.py as root before any public navigation.')
