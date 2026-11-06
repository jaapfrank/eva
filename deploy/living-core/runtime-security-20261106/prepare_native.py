"""Verify public vendor inputs and prepare offline image-only contexts; never activate."""
from pathlib import Path
import argparse,hashlib,json,lzma,shutil,subprocess,tempfile,urllib.request
HERE=Path(__file__).resolve().parent

def sha(body):return hashlib.sha256(body).hexdigest()
def download(url):
    with urllib.request.urlopen(url,timeout=60) as response:return response.read()
def package_entries(text):
    for stanza in text.split('\n\n'):
        fields={};last=None
        for line in stanza.splitlines():
            if line.startswith((' ','\t')) and last:fields[last]+='\n'+line
            elif ':' in line:last,value=line.split(':',1);fields[last]=value.strip()
        if fields:yield fields

def prepare(output,index_directory=None,deb_directory=None):
    if output.exists():raise ValueError('Choose a new output; preserve previous build evidence')
    m=json.loads((HERE/'vendor-inputs.json').read_text())['native'];output.mkdir(parents=True)
    vendor=m['keyTrust']['vendorDigest'];key=output/'debian-archive-keyring.gpg'
    cid=subprocess.check_output(['docker','create','--network','none',vendor],text=True).strip()
    try:subprocess.run(['docker','cp','-L',cid+':/usr/share/keyrings/debian-archive-keyring.gpg',str(key)],check=True)
    finally:subprocess.run(['docker','rm',cid],check=True,stdout=subprocess.DEVNULL)
    if sha(key.read_bytes())!=m['keyTrust']['vendorKeyringSha256']:raise ValueError('Vendor keyring changed')
    entries={}
    for suite,proof in m['indices'].items():
        release=HERE/(suite+'.InRelease')
        if sha(release.read_bytes())!=proof['inReleaseSha256']:raise ValueError('Signed release bytes changed')
        subprocess.run(['gpgv','--keyring',str(key),str(release)],check=True)
        body=(index_directory/suite/'Packages.xz').read_bytes() if index_directory else download(proof['url'])
        if len(body)!=proof['bytes'] or sha(body)!=proof['sha256']:raise ValueError('Use the exact reviewed historical Packages.xz; current index has advanced')
        if proof['sha256'] not in release.read_text():raise ValueError('Index not bound by signed release')
        for e in package_entries(lzma.decompress(body).decode()):entries[(suite,e['Package'],e['Version'])]=e
    selected=m['packages'];debs=output/'debs';debs.mkdir()
    old_ca=entries[('trixie','ca-certificates',m['caBlacklist']['oldVersion'])]
    if old_ca['SHA256']!=m['caBlacklist']['oldDebSha256']:raise ValueError('Old CA proof changed')
    for name,pin in [*selected.items(),('ca-certificates-old',{**old_ca,'suite':'trixie'})]:
        source=entries[(pin['suite'],pin['Package'],pin['Version'])]
        for k in ['Filename','SHA256','Size']:
            if source[k]!=pin[k]:raise ValueError('Package not in signed index')
        filename=Path(pin['Filename']).name
        base='https://security.debian.org/debian-security/' if pin['suite']=='trixie-security' else 'https://deb.debian.org/debian/'
        body=(deb_directory/filename).read_bytes() if deb_directory else download(base+pin['Filename'])
        if len(body)!=int(pin['Size']) or sha(body)!=pin['SHA256']:raise ValueError('Debian package checksum mismatch')
        path=debs/filename;path.write_bytes(body);root=output/'extracted'/name;root.mkdir(parents=True)
        subprocess.run(['dpkg-deb','--extract',str(path),str(root/'data')],check=True)
        subprocess.run(['dpkg-deb','--control',str(path),str(root/'control')],check=True)
    (output/'baseline-images.json').write_text(json.dumps(m['baselines'],indent=2)+'\n')
    (output/'selected-index-entries.json').write_text(json.dumps(selected,indent=2)+'\n')
    (output/'ca-blacklist-proof.json').write_text(json.dumps(m['caBlacklist'],indent=2)+'\n')
    for role,image in m['baselines'].items():
        target=output/role;target.mkdir();status=target/'status-input';status.mkdir()
        cid=subprocess.check_output(['docker','create','--network','none',image],text=True).strip()
        try:subprocess.run(['docker','cp',cid+':/var/lib/dpkg/status.d/.',str(status)],check=True)
        finally:subprocess.run(['docker','rm',cid],check=True,stdout=subprocess.DEVNULL)
        raw={p.name:p.read_text() for p in status.iterdir() if p.is_file()}
        (target/'baseline-status-d.json').write_text(json.dumps(raw)+'\n')
    subprocess.run(['python3',str(HERE/'extract_baseline.py'),str(output)],check=True)
    subprocess.run(['python3',str(HERE/'prepare_overlays.py'),str(output)],check=True)
    return m['baselines']

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--index-directory',type=Path)
    parser.add_argument('--deb-directory',type=Path)
    parser.add_argument('--build-images',action='store_true')
    args=parser.parse_args();output=args.output.resolve();baselines=prepare(output,args.index_directory,args.deb_directory)
    if args.build_images:
        for role in baselines:
            subprocess.run(['docker','build','--network=none','--pull=false','--iidfile',str(output/role/'image.txt'),str(output/role/'context')],check=True)
    print(json.dumps({'prepared':True,'built':args.build_images,'activated':False,'roles':list(baselines)}))
