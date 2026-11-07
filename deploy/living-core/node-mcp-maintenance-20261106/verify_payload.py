"""Verify the actual scoped runtime/application payload against its retained immutable base."""
from pathlib import Path
import argparse,json,subprocess,hashlib
HERE=Path(__file__).resolve().parent

def inspect(image):return json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
def probe(image,script,python):
 return json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=1','--memory=768m','--mount','type=bind,src='+str(HERE/script)+',dst=/probe.py,readonly','--entrypoint',python,image,'-B','/probe.py'],text=True,timeout=120))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('kind',choices=['provider','core']);p.add_argument('--role',required=True);p.add_argument('--image',required=True);args=p.parse_args();m=json.loads((HERE/'manifest.json').read_text());targets=m['providerTargets' if args.kind=='provider' else 'coreTargets'];assert args.role in targets;base=targets[args.role]['baseImage'];old=inspect(base);new=inspect(args.image);assert new['RootFS']['Layers'][:len(old['RootFS']['Layers'])]==old['RootFS']['Layers'],'Base was not retained';script='probe_provider.py' if args.kind=='provider' else 'probe_core_source.py';python='python3' if args.kind=='provider' else '/usr/local/bin/python';before=probe(base,script,python);after=probe(args.image,script,python)
 if args.kind=='provider':
  assert before['packages']==after['packages'] and before['otherOptEvaFiles']==after['otherOptEvaFiles'],'Unrelated Python/application payload changed';assert after['nodeVersion']=='v26.13.0' and after['nodeVersionThroughPath']=='v26.13.0' and after['libatomicBinarySha256']==m['libatomicBinarySha256'] and after['nodeSha256']==m['nodeBinarySha256'] and after['nodeSymlink']=='/opt/eva2/node-runtime/node';assert after['lockSha256']==m['packageLockSha256'];added=after['rpms'].keys()-before['rpms'].keys();assert added=={'libatomic'} and not before['rpms'].keys()-after['rpms'].keys();assert all(after['rpms'][k]==v for k,v in before['rpms'].items());assert after['rpms']['libatomic']=='0:14.3.1-4.4.el10.x86_64';packages={(v['name'],v['version']) for v in after['npm']};assert ('@modelcontextprotocol/sdk','1.32.2') in packages and ('@modelcontextprotocol/server-filesystem','2.2.0') in packages
  result={'pythonPackagesPreserved':len(after['packages']),'otherOptEvaFilesPreserved':len(after['otherOptEvaFiles']),'onlyNewRpm':'libatomic14.3.1-4.4.el10','node':after['nodeVersion'],'npmPackageJsonRecords':len(after['npm'])}
 else:
  assert before['site']==after['site'] and before['packages']==after['packages'] and before['pythonRuntime']==after['pythonRuntime'],'Wheel/site/stdlib payload changed';expected=dict(before['source'])
  for absolute,v in m['sourceOverlay']['sourceFiles'].items():expected[absolute.removeprefix('/opt/eva/src/eva/')]=v['after']
  assert after['source']==expected;assert all(v['path'].startswith('/opt/eva/src/eva/') for v in after['imported'].values());assert len(after['imported'])==3
  result={'siteFilesPreserved':len(after['site']),'allWheelRecordsPreserved':True,'stdlibRegularFilesPreserved':len(after['pythonRuntime']),'sourceDeltas':3,'actualImportedSources':'/opt/eva/src/eva'}
 print(json.dumps({'contract':'EvaNodeMcpPublicPayloadVerification/v1','kind':args.kind,'role':args.role,'actualImage':new['Id'],'retainedBase':base,'verified':True,'proof':result,'liveAction':False},sort_keys=True))
if __name__=='__main__':main()
