"""Verify the exact historical signed vendor snapshot, without installing it."""
import argparse,hashlib,json,subprocess,tarfile,io
from pathlib import Path

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def verify(package,windows=None):
 root=Path(__file__).parent;m=json.loads((root/'vendor-manifest.json').read_text())
 for name,digest in m['vendorMetadata'].items():assert sha(root/name)==digest,'Vendor metadata changed'
 result=subprocess.run(['gpgv','--keyring',str(root/'NodeSource-keyring.gpg'),str(root/'NodeSource-InRelease')],capture_output=True,text=True)
 assert result.returncode==0,'Vendor signature invalid'
 packages=root/'NodeSource-Packages';release=(root/'NodeSource-InRelease').read_text()
 assert any(sha(packages) in line and str(packages.stat().st_size) in line and line.strip().endswith('main/binary-amd64/Packages') for line in release.splitlines()),'Signed package index mismatch'
 blocks=[block for block in packages.read_text().split('\n\n') if block.startswith('Package: nodejs\n') and '\nVersion: 22.23.4-1nodesource1\n' in '\n'+block+'\n'];assert len(blocks)==1
 assert '\nSHA256: '+m['packageSha256'] in blocks[0]
 assert package.stat().st_size==m['packageBytes'] and sha(package)==m['packageSha256'],'Package bytes changed'
 archive=subprocess.check_output(['dpkg-deb','--fsys-tarfile',str(package)])
 with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
  node=tar.extractfile('./usr/bin/node');assert node is not None and hashlib.sha256(node.read()).hexdigest()==m['linuxBinarySha256']
 if windows is not None:assert sha(windows)==m['windowsBinarySha256'],'Windows executable changed'
 return {'contract':'EvaVerifiedNodeVendorSnapshot/v1','version':m['nodeVersion'],'signedIndex':True,'linuxExecutableVerified':True,'windowsExecutableVerified':windows is not None,'installed':False,'snapshotFreshnessAssessed':False}
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('package',type=Path);parser.add_argument('--windows-executable',type=Path);args=parser.parse_args();print(json.dumps(verify(args.package,args.windows_executable)))
