"""Materialize only the reviewed r2 preview; default is read-only validation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


STAGE = Path('/home/administrator/project/.eva2-artifacts/au006-activation-20260913')
PREVIEW_SHA256 = 'd453a963beae07839677653e5c4cd9e7b9aed5926094a72e3660c04d7a6a6993'
NAMES = {'goal.json', 'unsealed-inputs.json', 'catalog.json', 'plan.json', 'definition.json', 'generation.json'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Private host material requires operator root')
    path = STAGE / 'reprobe-offline-review/preview.json'
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o400,
            'Preview must be a private root-owned regular file')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == PREVIEW_SHA256, 'Reviewed preview changed')
    preview = json.loads(raw)
    require(preview['goal_id'] == 'au006-document-project-20260913-r2', 'Wrong goal')
    require(preview['old_operation_ids_reused'] is False and preview['journal_reset'] is False,
            'Old-operation isolation missing')
    require(set(preview['outputs']) == NAMES, 'Unexpected output set')
    encoded = {name: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
               for name, value in preview['outputs'].items()}
    require({name: hashlib.sha256(value).hexdigest() for name, value in encoded.items()} == preview['output_sha256'],
            'Canonical output hash mismatch')
    journal = STAGE / 'runtime-prepared/journal'
    require(not list(journal.iterdir()), 'Existing journal is no longer empty; never reset it')
    binding = json.loads((STAGE / 'runtime-prepared/provider/atomic-binding.json').read_text())
    workspace = Path('/var/lib/docker/volumes/eva2-workspace-files/_data')
    for directory, key in [(workspace, 'root'), (journal, 'journal')]:
        info = directory.stat()
        require({'dev': info.st_dev, 'ino': info.st_ino} == binding[key], 'Existing directory binding changed')
    plan = preview['outputs']['plan.json']
    for name in [plan['first_path'], plan['final_path']]:
        require(not os.path.lexists(workspace / Path(name).name), 'New target already exists')
    destination = STAGE / 'runtime-reprobe/project'
    require(not os.path.lexists(destination), 'Never overwrite an existing reprobe project')
    if not args.apply:
        print(json.dumps({'checked': True, 'writes': 0, 'output_count': 6, 'old_journal_unchanged': True}))
        return
    parent = destination.parent
    require(not os.path.lexists(parent), 'Unexpected reprobe parent; inspect instead of retrying')
    parent.mkdir(mode=0o755)
    destination.mkdir(mode=0o700)
    os.chown(destination, 65532, 65532)
    # Exclusive files, no retry. A partial bundle remains for operator review.
    for name, value in encoded.items():
        output = destination / name
        with output.open('xb') as stream:
            stream.write(value)
        os.chown(output, 65532, 65532)
        output.chmod(0o400)
    require({name: hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in encoded}
            == preview['output_sha256'], 'Written bundle differs')
    print(json.dumps({'sealed': True, 'output_count': 6, 'old_goal_and_journal_not_modified': True}))


if __name__ == '__main__':
    main()
