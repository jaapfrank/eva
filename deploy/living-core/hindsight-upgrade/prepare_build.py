"""Prepare verified offline Docker contexts; optionally build, never activate services."""
from __future__ import annotations
import argparse,hashlib,json,platform,shutil,subprocess,sys
from pathlib import Path
import pip
from prepare_sources import prepare,sha
HERE=Path(__file__).resolve().parent

def command(args):
    return subprocess.check_output(args,text=True).strip()

def make_contexts(upstream: Path, wheelhouse: Path, old_engine: Path, old_entrypoint: Path, output: Path) -> dict:
    if sys.version_info[:2]!=(3,11) or sys.platform!='linux' or platform.machine()!='x86_64':
        raise ValueError('Use the pinned Linux x86_64 CPython3.11 build environment')
    if pip.__version__!='26.3':
        raise ValueError('Install the checksum-bound build-toolchain.lock first')
    manifest=json.loads((HERE/'source-build-manifest.json').read_text())
    if sha(HERE/'runtime-cp311-linux-amd64.lock')!=manifest['lockSha256']:
        raise ValueError('Runtime lock changed')
    if sha(HERE/'wheel-closure-manifest.json')!=manifest['wheelManifestSha256']:
        raise ValueError('Wheel manifest changed')
    wheels=json.loads((HERE/'wheel-closure-manifest.json').read_text())['wheels']
    if len(wheels)!=202 or {p.name for p in wheelhouse.glob('*.whl')}!={v['file'] for v in wheels}:
        raise ValueError('The wheelhouse must contain exactly the202 reviewed wheels')
    for wheel in wheels:
        path=wheelhouse/wheel['file']
        if not path.is_file() or path.is_symlink() or sha(path)!=wheel['sha256']:
            raise ValueError('A runtime wheel differs from its reviewed checksum')
    base=manifest['localBaseImage']
    if json.loads(command(['docker','image','inspect',base]))[0]['Id']!=base:
        raise ValueError('The exact local retained base image is unavailable')
    if output.exists():
        raise ValueError('Preserve existing build output and choose a new directory')
    output.mkdir(parents=True)
    sources=output/'sources'
    source_proof=prepare(upstream,old_engine,old_entrypoint,sources)
    runtime=output/'runtime';activation=output/'activation';rollback=output/'rollback'
    for directory in [runtime,activation,rollback]:
        directory.mkdir()
    (runtime/'wheels').mkdir()
    for wheel in wheels:
        shutil.copyfile(wheelhouse/wheel['file'],runtime/'wheels'/wheel['file'])
    shutil.copytree(Path(pip.__file__).parent,runtime/'pip',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for source,target in [('Dockerfile.runtime','Dockerfile'),('runtime-cp311-linux-amd64.lock','runtime-full.lock'),('install_offline_runtime.py','install.py'),('clean_runtime_prefix.py','cleanup.py')]:
        shutil.copyfile(HERE/source,runtime/target)
    shutil.copytree(sources/'runtime',runtime/'overlay')
    shutil.copyfile(HERE/'Dockerfile.activation',activation/'Dockerfile')
    shutil.copyfile(sources/'activation/hindsight_entrypoint.py',activation/'hindsight_entrypoint.py')
    shutil.copyfile(HERE/'Dockerfile.rollback',rollback/'Dockerfile')
    shutil.copyfile(sources/'rollback/hindsight_api/engine/memory_engine.py',rollback/'memory_engine.py')
    profile={'contract':'EvaHindsightActivationSourceBinding/v1','entrypoint':manifest['entrypoint'],'upstream':manifest['upstream'],'runtimeLockSha256':manifest['lockSha256'],'baseImageAssignedAtBuild':True}
    (activation/'manifest.json').write_text(json.dumps(profile,indent=2)+'\n')
    guard={'contract':'EvaHindsightRollbackSourceBinding/v1','baseImage':base,'source':manifest['rollbackPatch'],'guardDefault':'1','migrationsDefault':'false','sourceRecordCoverage':'editable source outside distribution RECORD; explicit checksum binding'}
    (rollback/'manifest.json').write_text(json.dumps(guard,indent=2)+'\n')
    result={'contract':'EvaHindsightOfflineBuildContexts/v1','baseImage':base,'wheelCount':202,'sourceProof':source_proof,'contexts':{k:str(output/k) for k in ['runtime','activation','rollback']},'liveServicesChanged':False}
    (output/'contexts.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

def build_images(output: Path, base: str) -> dict:
    def build(role: str, arguments: dict) -> str:
        receipt=output/(role+'-image.txt')
        args=['docker','build','--network=none','--pull=false','--iidfile',str(receipt)]
        for name,value in arguments.items():
            args+=['--build-arg',name+'='+value]
        subprocess.run([*args,str(output/role)],check=True)
        return receipt.read_text().strip()
    runtime=build('runtime',{'BASE_IMAGE':base})
    profile=output/'activation/manifest.json';value=json.loads(profile.read_text());value['baseImage']=runtime;profile.write_text(json.dumps(value,indent=2)+'\n')
    activation=build('activation',{'RUNTIME_IMAGE':runtime})
    rollback=build('rollback',{'BASE_IMAGE':base})
    result={'contract':'EvaHindsightBuiltImages/v1','runtime':runtime,'activation':activation,'rollback':rollback,'liveServicesChanged':False}
    (output/'images.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['upstream','wheelhouse','old-engine','old-entrypoint','output']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--build-images',action='store_true')
    args=parser.parse_args()
    result=make_contexts(args.upstream.resolve(),args.wheelhouse.resolve(),args.old_engine.resolve(),args.old_entrypoint.resolve(),args.output.resolve())
    if args.build_images:
        result['images']=build_images(args.output.resolve(),result['baseImage'])
    print(json.dumps(result))
