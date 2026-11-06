"""Apply only checksum-bound native source overlays into a new build directory."""
from __future__ import annotations
import argparse,ast,hashlib,json,shutil,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def function(raw: str, name: str):
    return next(node for node in ast.parse(raw).body if isinstance(node,ast.FunctionDef) and node.name==name)

def apply_patch(directory: Path, patch: Path) -> None:
    subprocess.run(['git','-C',str(directory),'apply','--check',str(patch)],check=True)
    subprocess.run(['git','-C',str(directory),'apply',str(patch)],check=True)

def prepare(upstream: Path, old_engine: Path, old_entrypoint: Path, output: Path) -> dict:
    manifest=json.loads((HERE/'source-build-manifest.json').read_text())
    commit=subprocess.check_output(['git','-C',str(upstream),'rev-parse','HEAD'],text=True).strip()
    if commit!=manifest['upstream']['commit']:
        raise ValueError('Upstream checkout is not the reviewed commit')
    if subprocess.check_output(['git','-C',str(upstream),'status','--porcelain','--untracked-files=all'],text=True).strip():
        raise ValueError('Use a clean reviewed upstream checkout')
    if sha(old_engine)!=manifest['rollbackPatch']['beforeSha256'] or sha(old_entrypoint)!=manifest['entrypoint']['beforeSha256']:
        raise ValueError('Extract the exact source files from the pinned local base image')
    if output.exists():
        raise ValueError('Build output already exists; preserve it and choose a new directory')
    output.mkdir(parents=True)
    modern=output/'runtime';legacy=output/'rollback'
    for root in [modern,legacy]:
        (root/'hindsight_api/engine').mkdir(parents=True)
    for name,binding in manifest['nativePatches'].items():
        source=upstream/'hindsight-api-slim/hindsight_api/engine'/name
        patch=HERE/(name+'.patch')
        if sha(source)!=binding['beforeSha256'] or sha(patch)!=binding['patchSha256']:
            raise ValueError('Native source or reviewed patch changed')
        shutil.copyfile(source,modern/'hindsight_api/engine'/name)
        apply_patch(modern,patch)
        if sha(modern/'hindsight_api/engine'/name)!=binding['afterSha256']:
            raise ValueError('Patched native source differs from the reviewed output')
    shutil.copyfile(old_engine,legacy/'hindsight_api/engine/memory_engine.py')
    if sha(HERE/'rollback-memory-engine.patch')!=manifest['rollbackPatch']['patchSha256']:
        raise ValueError('Rollback patch changed')
    apply_patch(legacy,HERE/'rollback-memory-engine.patch')
    if sha(legacy/'hindsight_api/engine/memory_engine.py')!=manifest['rollbackPatch']['afterSha256']:
        raise ValueError('Rollback guard output differs')
    activation=HERE/'activation_entrypoint.py'
    if sha(activation)!=manifest['entrypoint']['afterSha256']:
        raise ValueError('Activation entrypoint changed')
    if ast.dump(function(old_entrypoint.read_text(),'_database_url_from_private_file'),include_attributes=False)!=ast.dump(function(activation.read_text(),'_database_url_from_private_file'),include_attributes=False):
        raise ValueError('Private-file DSN boundary changed')
    (output/'activation').mkdir()
    shutil.copyfile(activation,output/'activation/hindsight_entrypoint.py')
    result={'contract':'EvaHindsightPreparedSourceOverlays/v1','upstreamCommit':commit,
            'sources':{str(p.relative_to(output)):sha(p) for p in output.rglob('*.py')},
            'privateFileReaderAstUnchanged':True,'liveServicesChanged':False}
    (output/'source-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream',type=Path,required=True)
    parser.add_argument('--old-engine',type=Path,required=True)
    parser.add_argument('--old-entrypoint',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.upstream.resolve(),args.old_engine.resolve(),args.old_entrypoint.resolve(),args.output.resolve())))
