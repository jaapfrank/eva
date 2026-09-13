"""Ephemeral SQLite proof of exact compare-and-swap and transaction rollback."""
import json
from pathlib import Path
import sqlite3
import tempfile

from repair_resource_assignments import repair


def fixture(path, reject_last=False):
    with sqlite3.connect(path) as db:
        extra = ", CHECK (id != '4' OR team_id IS NOT NULL)" if reject_last else ''
        db.execute('CREATE TABLE tools (id TEXT PRIMARY KEY, team_id TEXT, updated_at TEXT, owner_email TEXT, visibility TEXT' + extra + ')')
        db.executemany('INSERT INTO tools VALUES (?,?,?,?,?)', [(str(i), 'current-team', 'after', 'owner', 'private') for i in range(1, 6)])
        db.row_factory = sqlite3.Row
        return {'table_info': [list(r) for r in db.execute('PRAGMA table_info(tools)')],
                'rows': [{'before': dict(r), 'restore': {'team_id': None, 'updated_at': 'before'}}
                         for r in db.execute("SELECT * FROM tools WHERE id != '5' ORDER BY id")]}


def snapshot(path):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM tools ORDER BY id').fetchall()


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'fixture.db'
    manifest = fixture(path)
    before = snapshot(path)
    assert repair(path, manifest)['writes'] == 0 and snapshot(path) == before
    assert repair(path, manifest, apply=True)['writes'] == 4
    after = snapshot(path)
    assert after[-1] == before[-1]
    assert all(r[1:3] == (None, 'before') and r[3:] == ('owner', 'private') for r in after[:4])
    try:
        repair(path, manifest, apply=True)
    except ValueError:
        pass
    else:
        raise AssertionError('Stale manifest was accepted')
    assert snapshot(path) == after
    failed = Path(directory) / 'constraint.db'
    rejected = fixture(failed, reject_last=True)
    baseline = snapshot(failed)
    try:
        repair(failed, rejected, apply=True)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError('Fourth-row constraint should reject transaction')
    assert snapshot(failed) == baseline, 'First three writes were not rolled back'
    locked = Path(directory) / 'locked.db'
    review = fixture(locked)
    with sqlite3.connect(locked) as writer:
        writer.execute('BEGIN IMMEDIATE')
        try:
            repair(locked, review, apply=True)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError('Repair ran without obtaining writer exclusion')
        writer.rollback()
    assert repair(locked, review)['writes'] == 0
print(json.dumps({'status': 'passed', 'readonly_no_writes': True, 'exact_four_rows_only': True,
                  'stale_manifest_rejected': True, 'fourth_row_failure_rolls_back_all': True,
                  'concurrent_writer_fails_without_retry': True}))
