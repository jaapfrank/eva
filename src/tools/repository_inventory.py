"""Versioned file registration; role classification is not semantic code review."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import UTC, datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from tools.file_inspection import hash_regular_file as _hash_regular_file
MANIFEST = 'development/docs/eva-repository-inventory.generated.json'
OUTPUTS = {MANIFEST, 'development/docs/repository-registration-20261017.md', 'development/evidence/reassessment/e31-file-register-20261017.json', 'development/evidence/reassessment/e31-installed-source-20261017.json', 'development/reviews/e31-file-register-20261017.json'}
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'dist', 'project-dist', 'coverage', '.nyc_output', '.playwright-mcp', 'secrets', 'data', '.state', '.codex', 'wheelhouse', 'offline-artifacts'}
SKIP_PREFIXES = ('development/archive/local-generated', 'deploy/voice-session/vendor', 'build')
SKIP_DIRS.update({'wheelhouse', 'offline-artifacts'})
PRIVATE_PATTERNS = ('.env', '.env.*', '*.env', '*.env.*', '*.private.*', '*.db', '*.db-*', '*.sqlite*', '*.key', '*.pem', '*.p12', '*.pfx', '*.ppk', '*.jks', '*.keystore', 'id_rsa*', '*_rsa*', '*_ed25519*', '*-private-proof.*', '*-private-worker.*')
READ_REGISTERS = ('development/docs/reading-register-20261013.json', 'development/evidence/reassessment/reading-register-2026-09-21.json')

def local_file(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError('metadata reference outside repository')
    path = root / relative
    for candidate in [path, *path.parents]:
        if candidate == root:
            break
        if candidate.is_symlink():
            raise RuntimeError('metadata reference contains a symlink')
    if path.exists() and (not path.is_file()):
        raise RuntimeError('metadata reference is not a regular file')
    return path

def git(root, *args, data=None):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(root), '-C', str(root), *args], input=data)

def exclusion(path):
    parts = Path(path).parts
    if any((p in SKIP_DIRS for p in parts)) or any((path == p or path.startswith(p + '/') for p in SKIP_PREFIXES)):
        return 'dependency_cache_or_private_store'
    name = parts[-1]
    if not name.endswith('.example') and any((fnmatch.fnmatchcase(name, p) for p in PRIVATE_PATTERNS)):
        return 'private_material_never_read'
    if name.endswith(('.pyc', '.pyo', '.log', '.pid', ':Zone.Identifier')) or name in {'.DS_Store', 'Thumbs.db'}:
        return 'generated_local_state'
    if path in OUTPUTS:
        return 'self_describing_output'
    return None

def discover(root):
    tracked = set(filter(None, git(root, 'ls-files', '-z').decode().split('\x00')))
    paths, excluded = (set(tracked), [])

    def walk(directory):
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            path = Path(entry.path).relative_to(root).as_posix()
            if entry.is_dir(follow_symlinks=False):
                reason = exclusion(path)
                if reason:
                    excluded.append({'path': path + '/', 'readPolicy': 'never-traverse-or-hash', 'reason': reason})
                else:
                    walk(Path(entry.path))
            else:
                paths.add(path)
    walk(root)
    candidates = sorted(paths - tracked)
    ignored = set()
    if candidates:
        result = subprocess.run(['git', '-c', 'safe.directory=' + str(root), '-C', str(root), 'check-ignore', '--stdin', '-z'], input=('\x00'.join(candidates) + '\x00').encode(), capture_output=True)
        if result.returncode not in {0, 1}:
            raise RuntimeError('cannot establish ignored paths')
        ignored = set(filter(None, result.stdout.decode().split('\x00')))
    return (sorted(paths), tracked, ignored, excluded)

def prior_reads(root):
    reads = {}
    for name in READ_REGISTERS:
        path = local_file(root, name)
        if not path.is_file() or path.is_symlink():
            continue
        register = json.loads(path.read_text())
        for row in register.get('documents', []) + register.get('currentLeadingDocuments', []):
            covered = row.get('reading', {}).get('fullContentCovered') is True or row.get('fullContentRead') is True or row.get('reading_mode') in {'full_content_read', 'full_content_in_tool_output', 'full_content_read_deduplicated_cells'}
            digest = row.get('originalSourceSha256') or row.get('sha256')
            if covered and digest:
                reads.setdefault(digest, name)
    return reads

def classification(path):
    if path.startswith('src/eva/adapters/api/dashboard_static/'):
        return ('generated-build-output', 'sealed-dashboard-build', 'E3.3')
    if path.startswith('development/archive/'):
        return ('historical-reference', 'historical-record', 'E3.4')
    if path.startswith('src/vendor/'):
        return ('vendored-source', 'pinned-upstream', 'E3.3')
    if path.startswith('src/eva/'):
        module = path.split('/')[2]
        return ('product-runtime', 'eva.' + module, 'E3.2' if module in {'application', 'contracts', 'domain', 'bootstrap'} else 'E3.3')
    if path.startswith('src/'):
        return ('development-tool' if path.startswith('src/tools/') else 'optional-connector', path.split('/')[1], 'E3.3')
    if path.startswith('deploy/'):
        return ('deployment', 'installation/' + path.split('/')[1], 'E3.3')
    if path.startswith('development/tests/'):
        return ('verification', 'verification/' + path.split('/')[2], 'E3.4')
    if path.startswith(('development/evidence/', 'development/reviews/')):
        return ('historical-evidence', 'evidence-and-review', 'E3.4')
    if path.startswith('development/tools/'):
        return ('development-tool', 'development/' + path.split('/')[2], 'E3.3')
    if path.endswith('.md') or path.startswith('development/docs/'):
        return ('documentation', 'project-documentation', 'E3.4')
    if path.startswith('development/'):
        return ('design-or-fixture', 'development-records', 'E3.4')
    return ('repository-config', 'repository-composition', 'E3.3')

def protected(path):
    return path.startswith(('development/benchmark/', 'development/validation/')) or (path.startswith('development/') and any((p in path.lower() for p in ('heldout', 'holdout', 'sealed-case', 'private-proof'))))

def snapshot(root, runtime=None):
    root = root.resolve()
    revision = git(root, 'rev-parse', 'HEAD').decode().strip()
    paths, tracked, ignored, excluded = discover(root)
    changed = set(filter(None, git(root, 'diff', 'HEAD', '--name-only', '-z').decode().split('\x00')))
    reads = prior_reads(root)
    installed = {}
    for service in (runtime or {}).get('services', []):
        for row in service.get('files', []):
            installed.setdefault(row['path'], []).append((service['name'], row['sha256']))
    rows = []
    for path in paths:
        role, owner, review_card = classification(path)
        reason = exclusion(path)
        linked_parent = any((parent.is_symlink() for parent in (root / path).parents if parent != root and root in parent.parents))
        present = False if linked_parent else (root / path).is_symlink() or (root / path).exists()
        digest, size = (None, None)
        if linked_parent:
            reason = 'nonregular_path_never_followed'
        elif present:
            info = (root / path).lstat()
            if not stat.S_ISREG(info.st_mode):
                reason = 'nonregular_path_never_followed'
            elif reason is None:
                size, digest = _hash_regular_file(root / path, path)
        state = 'protected_metadata_only' if protected(path) else 'excluded_by_policy' if reason else 'exact_prior_full_read' if digest in reads else 'enumerated'
        rows.append({'path': path, 'gitState': 'tracked' if path in tracked else 'untracked', 'present': present, 'ignored': path in ignored, 'contentSha256': digest, 'bytes': size, 'sourceState': 'modified' if path in changed else 'baseline' if path in tracked else 'added', 'role': role, 'owner': owner, 'roleBasis': 'path_policy_not_semantic_review', 'reviewStatus': state, 'readingEvidence': reads.get(digest) if state == 'exact_prior_full_read' else None, 'decision': 'pending_review', 'reviewCard': review_card, 'consumerStatus': 'not_yet_verified', 'hashExclusion': reason, 'installedIn': [{'service': name, 'state': 'identical' if digest == value else 'different'} for name, value in installed.get(path, [])]})
    material = [(r['path'], r['contentSha256'], r['hashExclusion'], r['present']) for r in rows]
    historical = None
    previous = local_file(root, MANIFEST)
    if previous.is_file() and (not previous.is_symlink()):
        old = json.loads(previous.read_text())
        historical = old.get('previousInventory') or {'revision': revision, 'path': MANIFEST, 'sha256': hashlib.sha256(previous.read_bytes()).hexdigest()}
    return {'schema': 'EvaRepositoryInventory/v1', 'registrationVersion': 2, 'capturedAt': datetime.now(UTC).isoformat(), 'sourceRevision': revision, 'scope': {'sources': ['Git index plus filesystem traversal, including ignored project documentation'], 'claim': 'File registration only. Path-policy role is not semantic review. E3.2–E3.4 own necessity and actual consumer decisions.'}, 'exclusions': excluded, 'outputHashExemptions': sorted(OUTPUTS), 'previousInventory': historical, 'summary': {'files': len(rows), 'present': sum((r['present'] for r in rows)), 'tracked': len(tracked), 'untracked': len(rows) - len(tracked), 'ignoredProjectFiles': len(ignored), 'reviewStatuses': dict(Counter((r['reviewStatus'] for r in rows))), 'categories': dict(Counter((r['role'] for r in rows))), 'excludedDirectories': len(excluded), 'tombstones': sum((not r['present'] for r in rows))}, 'policyErrors': [], 'edges': [], 'files': rows, 'sourceDigest': hashlib.sha256(json.dumps(material, separators=(',', ':')).encode()).hexdigest(), 'runtimeEvidence': 'development/evidence/reassessment/e31-installed-source-20261017.json' if runtime else None, 'runtimeEvidenceSha256': hashlib.sha256(local_file(root, 'development/evidence/reassessment/e31-installed-source-20261017.json').read_bytes()).hexdigest() if runtime else None}

def write_snapshot(path, value):
    header = {k: v for k, v in value.items() if k != 'files'}
    text = json.dumps(header, ensure_ascii=False, indent=2)[:-1] + ',\n  "files": [\n'
    text += ',\n'.join(('    ' + json.dumps(r, ensure_ascii=False, separators=(',', ':')) for r in value['files']))
    path.write_text(text + '\n  ]\n}\n', encoding='utf-8')

def delta(root, expected):
    actual = snapshot(root)
    old = {r['path']: r for r in expected['files']}
    new = {r['path']: r for r in actual['files']}
    return {'added': sorted(set(new) - set(old)), 'removed': sorted(set(old) - set(new)), 'changed': sorted((p for p in set(old) & set(new) if (old[p]['contentSha256'], old[p]['present'], old[p]['hashExclusion']) != (new[p]['contentSha256'], new[p]['present'], new[p]['hashExclusion'])))}

def capture_runtime():
    names = subprocess.check_output(['docker', 'ps', '--filter', 'name=eva2-', '--format', '{{.Names}}'], text=True).splitlines()
    services = []
    for name in sorted(names):
        command = ['docker', 'inspect', '--format', '[{{json .Id}},{{json .Image}},{{json .State.Running}}]', name]
        identifier, image, running = json.loads(subprocess.check_output(command, text=True))
        item = {'name': name, 'container': identifier, 'image': image, 'running': running, 'files': [], 'scope': 'image_metadata_only'}
        if name in {'eva2-managed-b-slot3-core', 'eva2-managed-b-slot3-delegated-work-worker-1'}:
            program = "import os,json,hashlib\nrows=[]\nfor directory,dirs,files in os.walk('/opt/eva/src',followlinks=False):\n dirs[:]=sorted(d for d in dirs if d!='__pycache__' and not os.path.islink(os.path.join(directory,d)))\n for name in sorted(files):\n  p=os.path.join(directory,name)\n  if name.endswith(('.pyc','.pyo')) or os.path.islink(p):continue\n  with open(p,'rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()\n  rows.append({'path':'src/'+os.path.relpath(p,'/opt/eva/src'),'sha256':h})\nprint(json.dumps(rows,separators=(',',':')))\n"
            item['files'] = json.loads(subprocess.check_output(['docker', 'exec', name, '/usr/local/bin/python', '-I', '-c', program], text=True))
            if json.loads(subprocess.check_output(command, text=True)) != [identifier, image, running]:
                raise RuntimeError('runtime changed during registration')
            item['scope'] = 'project_source_digests_only'
        services.append(item)
    return {'contract': 'EvaInstalledFileRegistration/v1', 'capturedAt': datetime.now(UTC).isoformat(), 'services': services, 'nonclaims': ['No environment, secrets, runtime data, models or service mutations.', 'Voice-owned services: image metadata only; no voice source/configuration/API or installation changes.', 'Source equality is not execution quality, valid source pins or family acceptance.']}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--capture-runtime', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    runtime_path = local_file(root, 'development/evidence/reassessment/e31-installed-source-20261017.json')
    if args.capture_runtime:
        runtime_path.write_text(json.dumps(capture_runtime(), indent=2) + '\n')
    runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else None
    if args.write:
        value = snapshot(root, runtime)
        write_snapshot(local_file(root, MANIFEST), value)
        print(json.dumps(value['summary']))
        return 0
    result = delta(root, json.loads(local_file(root, MANIFEST).read_text()))
    print(json.dumps(result))
    return int(any(result.values()))
if __name__ == '__main__':
    raise SystemExit(main())
