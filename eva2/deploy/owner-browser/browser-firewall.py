#!/usr/bin/env python3
"""Exact candidate egress restriction. Run as root: apply | status | rollback."""
from pathlib import Path
import json,os,subprocess,sys,datetime
root=Path(__file__).resolve().parent
ips=['192.168.0.2','192.168.16.2']
chains={'DOCKER-USER':'EVA2VB3F','INPUT':'EVA2VB3I'}
private=['0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.0.0.0/24','192.168.0.0/16','198.18.0.0/15','224.0.0.0/4','240.0.0.0/4']
assert os.geteuid()==0
mode=sys.argv[1] if len(sys.argv)==2 else ''
assert mode in {'apply','status','rollback'}
def run(args,check=True):return subprocess.run(args,capture_output=True,text=True,check=check)
def ipt(*args,check=True):return run(['/usr/sbin/iptables','-w','5',*args],check=check)
if mode=='apply':
 ids=json.loads((root/'container-ids.json').read_text())
 browser=json.loads(run(['docker','inspect',ids['browser']]).stdout)[0]
 nets=browser['NetworkSettings']['Networks']
 assert set(nets)=={root.name+'-net',root.name+'-egress'}
 assert {n['IPAddress'] for n in nets.values()}==set(ips)
 for name in nets:
  n=json.loads(run(['docker','network','inspect',name]).stdout)[0]
  assert n['EnableIPv6'] is False
 ipv6=run(['docker','exec',ids['browser'],'python3','-c',"from pathlib import Path;import json;print(json.dumps({'interfaces_disabled':{d.name:(d/'disable_ipv6').read_text().strip() for d in Path('/proc/sys/net/ipv6/conf').iterdir() if d.name not in {'all','default','lo'}},'interfaces':Path('/proc/net/if_inet6').read_text(),'routes':Path('/proc/net/ipv6_route').read_text()}))"]).stdout
 observed=json.loads(ipv6);assert observed['interfaces_disabled'] and all(value=='1' for value in observed['interfaces_disabled'].values())
 assert all(line.split()[-1]=='lo' for line in observed['interfaces'].splitlines())
 assert all(line.split()[-1]=='lo' for line in observed['routes'].splitlines())
 (root/'ipv6-exclusion.json').write_text(json.dumps(observed,indent=2))
 before={hook:ipt('-S',hook).stdout for hook in chains}
 if not (root/'firewall-before.json').exists():(root/'firewall-before.json').write_text(json.dumps(before,indent=2))
 for hook,chain in chains.items():
  existing=ipt('-S',chain,check=False)
  if existing.returncode:
   ipt('-N',chain)
   ipt('-A',chain,'-m','conntrack','--ctstate','ESTABLISHED,RELATED','--ctdir','REPLY','-j','RETURN')
   if hook=='DOCKER-USER':
    for block in private:ipt('-A',chain,'-d',block,'-j','REJECT','--reject-with','icmp-port-unreachable')
    ipt('-A',chain,'-j','RETURN')
   else:ipt('-A',chain,'-j','REJECT','--reject-with','icmp-port-unreachable')
  for addr in ips:
   args=['-s',addr+'/32','-m','comment','--comment',root.name,'-j',chain]
   if ipt('-C',hook,*args,check=False).returncode:ipt('-I',hook,'1',*args)
elif mode=='rollback':
 for hook,chain in chains.items():
  for addr in ips:
   args=['-s',addr+'/32','-m','comment','--comment',root.name,'-j',chain]
   if not ipt('-C',hook,*args,check=False).returncode:ipt('-D',hook,*args)
  if not ipt('-S',chain,check=False).returncode:
   ipt('-F',chain);ipt('-X',chain)
result={'at':datetime.datetime.now(datetime.UTC).isoformat(),'mode':mode,'sources':ips,'commands':{},'rules':{}}
for hook,chain in chains.items():
 for name in [hook,chain]:
  result['rules'][name]=ipt('-S',name,check=False).stdout
  result['commands'][name]=ipt('-L',name,'-n','-v','-x',check=False).stdout
(root/('firewall-'+mode+'.json')).write_text(json.dumps(result,indent=2))
print(json.dumps({'mode':mode,'sources':ips,'rules':result['rules']},indent=2))
