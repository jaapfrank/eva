"""Prepare a verified CPython vendor-file overlay; never start or install it."""
from pathlib import Path
import argparse,hashlib,json,shutil,stat,subprocess
HERE=Path(__file__).resolve().parent

def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def owned(root):
    result={}
    for top in ['bin','lib/python3.11','include/python3.11','lib/libpython3.11.so','lib/libpython3.11.so.1.0','lib/pkgconfig/python-3.11.pc','lib/pkgconfig/python-3.11-embed.pc']:
        path=root/top
        for p in ([path] if path.is_file() or path.is_symlink() else path.rglob('*')):
            rel=p.relative_to(root)
            if 'site-packages' in rel.parts or '__pycache__' in rel.parts:continue
            if rel.parts[0]=='bin' and not p.name.startswith('python'):continue
            if p.is_symlink():result[str(rel)]={'symlink':str(p.readlink())}
            elif p.is_file():result[str(rel)]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':stat.S_IMODE(p.stat().st_mode)}
    return result

def prepare(output):
    if output.exists():raise ValueError('Choose a new output directory')
    m=json.loads((HERE/'vendor-inputs.json').read_text())['python31118'];output.mkdir(parents=True)
    for role,image in [('baseline',m['baselineImage']),('vendor',m['vendorImage'])]:
        cid=subprocess.check_output(['docker','create','--network','none',image],text=True).strip();target=output/role;target.mkdir()
        try:subprocess.run(['docker','cp',cid+':/usr/local/.',str(target)],check=True)
        finally:subprocess.run(['docker','rm',cid],check=True,stdout=subprocess.DEVNULL)
    before=owned(output/'baseline');after=owned(output/'vendor')
    if canonical(before)!=m['beforeOwnedCanonicalSha256'] or canonical(after)!=m['afterOwnedCanonicalSha256']:raise ValueError('Interpreter-owned baseline/vendor bytes differ')
    overlay=output/'context/overlay/usr/local';overlay.mkdir(parents=True)
    for rel,record in after.items():
        target=overlay/rel;target.parent.mkdir(parents=True,exist_ok=True)
        if 'symlink' in record:target.symlink_to(record['symlink'])
        else:shutil.copy2(output/'vendor'/rel,target)
    context=output/'context';proof={'contract':'EvaHindsightCPythonOwnedOverlay/v1','baseImage':m['baselineImage'],'vendorImage':m['vendorImage'],'before':before,'after':after,'excluded':['all site-packages','pip scripts','distro libraries','all /app files','all /opt files']}
    (context/'owned-manifest.json').write_text(json.dumps(proof,indent=2)+'\n');shutil.copyfile(HERE/'python_owned_cleanup.py',context/'cleanup.py')
    config=json.loads(subprocess.check_output(['docker','image','inspect',m['baselineImage']],text=True))[0]['Config']
    (context/'Dockerfile').write_text('FROM '+m['baselineImage']+'\nUSER 0:0\nCOPY owned-manifest.json /opt/eva2/hindsight-python-owned-manifest.json\nCOPY cleanup.py /opt/eva2/hindsight-python-cleanup.py\nRUN ["/usr/local/bin/python", "-B", "/opt/eva2/hindsight-python-cleanup.py"]\nCOPY overlay/ /\nUSER '+config['User']+'\n')
    return {'prepared':True,'ownedBefore':len(before),'ownedAfter':len(after),'activated':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--build-image',action='store_true');args=parser.parse_args();output=args.output.resolve();result=prepare(output)
    if args.build_image:subprocess.run(['docker','build','--network=none','--pull=false','--iidfile',str(output/'image.txt'),str(output/'context')],check=True)
    print(json.dumps(result))
