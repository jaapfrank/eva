from pathlib import Path
import hashlib
import json
import subprocess
import pytest
from tools import repository_inventory as inventory

@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'fixture'], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'fixture@example.invalid'], check=True)
    (root / 'seed.txt').write_text('seed')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'fixture'], check=True)
    return root

def test_ignored_project_docs_tracked_secrets_and_dependency_exclusions(repository, monkeypatch):
    root = repository
    (root / '.gitignore').write_text('notes/*.md\nnode_modules/\n.env\n')
    (root / 'notes').mkdir()
    (root / 'notes/design.md').write_text('project design')
    (root / 'node_modules').mkdir()
    (root / 'node_modules/private.txt').write_text('must not read')
    (root / '.env').write_text('must not read')
    subprocess.run(['git', '-C', str(root), 'add', '-f', '.env'], check=True)
    original = inventory._hash_regular_file
    seen = []

    def checked(path, relative):
        assert relative != '.env' and 'node_modules' not in relative
        seen.append(relative)
        return original(path, relative)
    monkeypatch.setattr(inventory, '_hash_regular_file', checked)
    value = inventory.snapshot(root)
    rows = {r['path']: r for r in value['files']}
    assert rows['notes/design.md']['ignored'] is True
    assert rows['notes/design.md']['contentSha256'] == hashlib.sha256(b'project design').hexdigest()
    assert rows['.env']['gitState'] == 'tracked' and rows['.env']['contentSha256'] is None
    assert 'node_modules/private.txt' not in rows
    assert any((r['path'] == 'node_modules/' for r in value['exclusions']))

def test_no_link_or_tracked_child_below_link_is_opened(repository, tmp_path, monkeypatch):
    root = repository
    (root / 'src').mkdir()
    (root / 'src/sensitive.txt').write_text('old')
    subprocess.run(['git', '-C', str(root), 'add', 'src/sensitive.txt'], check=True)
    (root / 'src/sensitive.txt').unlink()
    (root / 'src').rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'sensitive.txt').write_text('secret')
    (root / 'src').symlink_to(outside, target_is_directory=True)
    (root / 'linked.md').symlink_to(outside / 'sensitive.txt')
    original = inventory._hash_regular_file

    def checked(path, relative):
        assert relative not in {'src/sensitive.txt', 'linked.md', 'src'}
        return original(path, relative)
    monkeypatch.setattr(inventory, '_hash_regular_file', checked)
    rows = {r['path']: r for r in inventory.snapshot(root)['files']}
    for name in ['src', 'src/sensitive.txt', 'linked.md']:
        assert rows[name]['contentSha256'] is None
        assert rows[name]['hashExclusion'] == 'nonregular_path_never_followed'

def test_reading_reuse_requires_full_coverage_and_exact_hash(repository):
    root = repository
    register = root / inventory.READ_REGISTERS[0]
    register.parent.mkdir(parents=True)
    source = root / 'a.md'
    source.write_text('known')
    digest = hashlib.sha256(b'known').hexdigest()
    register.write_text(json.dumps({'documents': [{'originalSourceSha256': digest, 'reading': {'fullContentCovered': False}}]}))
    assert next((r for r in inventory.snapshot(root)['files'] if r['path'] == 'a.md'))['reviewStatus'] == 'enumerated'
    register.write_text(json.dumps({'documents': [{'originalSourceSha256': digest, 'reading': {'fullContentCovered': True}}]}))
    assert next((r for r in inventory.snapshot(root)['files'] if r['path'] == 'a.md'))['reviewStatus'] == 'exact_prior_full_read'
    source.write_text('changed')
    assert next((r for r in inventory.snapshot(root)['files'] if r['path'] == 'a.md'))['reviewStatus'] == 'enumerated'

def test_roundtrip_and_delta_keep_fixed_snapshot_and_ignore_self_hash(repository):
    root = repository
    path = root / inventory.MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    value = inventory.snapshot(root)
    inventory.write_snapshot(path, value)
    assert json.loads(path.read_text()) == value
    assert inventory.delta(root, value) == {'added': [], 'removed': [], 'changed': []}
    (root / 'seed.txt').write_text('new')
    (root / 'new.md').write_text('added')
    delta = inventory.delta(root, value)
    assert delta['changed'] == ['seed.txt'] and delta['added'] == ['new.md']
    (root / 'seed.txt').unlink()
    assert 'seed.txt' in inventory.delta(root, value)['changed']

def test_direct_reading_metadata_never_follows_an_ancestor_link(repository, tmp_path):
    root = repository
    outside = tmp_path / 'outside-metadata'
    (outside / 'docs').mkdir(parents=True)
    (outside / 'docs/reading-register-20261013.json').write_text('private, never parse')
    (root / 'development').symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        inventory.prior_reads(root)
    with pytest.raises(RuntimeError, match='symlink'):
        inventory.snapshot(root)
    with pytest.raises(RuntimeError, match='symlink'):
        inventory.local_file(root, 'development/evidence/reassessment/e31-installed-source-20261017.json')
