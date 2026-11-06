"""Download only hash-bound binary dependencies; never run or install them."""
from __future__ import annotations
import argparse,hashlib,json,platform,subprocess,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

def verify(directory):
    wheels=json.loads((HERE/'wheel-closure-manifest.json').read_text())['wheels']
    if {p.name for p in directory.glob('*.whl')}!={w['file'] for w in wheels}:
        raise ValueError('Expected exactly the 202 reviewed wheels')
    for wheel in wheels:
        p=directory/wheel['file']
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=wheel['sha256']:
            raise ValueError('Wheel checksum mismatch: '+wheel['file'])
    return {'verifiedWheels':len(wheels),'installed':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse',type=Path,required=True)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args();directory=args.wheelhouse.resolve()
    if sys.version_info[:2]!=(3,11) or sys.platform!='linux' or platform.machine()!='x86_64':
        raise ValueError('Use Linux x86_64 CPython3.11')
    if not args.verify_only:
        import pip
        if pip.__version__!='26.3':raise ValueError('Use the pinned pip26.3 toolchain')
        own='hindsight_api_slim-1.2.0-py3-none-any.whl'
        expected=next(w for w in json.loads((HERE/'wheel-closure-manifest.json').read_text())['wheels'] if w['file']==own)
        if not (directory/own).is_file() or hashlib.sha256((directory/own).read_bytes()).hexdigest()!=expected['sha256']:
            raise ValueError('Build and verify the pinned upstream source wheel first')
        lines=(HERE/'runtime-cp311-linux-amd64.lock').read_text().splitlines(keepends=True)
        removed=[line for line in lines if line.startswith('hindsight-api-slim==')]
        if len(removed)!=1:raise ValueError('Unexpected own-wheel lock structure')
        with tempfile.TemporaryDirectory(prefix='hindsight-download-') as temporary:
            lock=Path(temporary)/'dependencies.lock';lock.write_text(''.join(line for line in lines if line not in removed))
            subprocess.run([sys.executable,'-m','pip','download','--require-hashes','--only-binary=:all:','--no-deps','--index-url','https://pypi.org/simple','--extra-index-url','https://download.pytorch.org/whl/cpu','--dest',str(directory),'-r',str(lock)],check=True)
    print(json.dumps(verify(directory)))
