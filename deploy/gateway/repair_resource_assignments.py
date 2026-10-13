"""Exact reviewed four-row repair; default is a read-only comparison.

Deployment approval, the verified startup guard, and the coordinated gateway
window are prerequisites for an operator using --apply. This file grants none.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def repair(database, manifest, *, apply=False):
    rows = manifest['rows']
    require(len(rows) == 4 and len({r['before']['id'] for r in rows}) == 4, 'Exactly four distinct reviewed IDs required')
    require(all(set(r['restore']) == {'team_id', 'updated_at'} and r['restore']['team_id'] is None for r in rows), 'Only team_id and updated_at restoration allowed')
    db = sqlite3.connect(Path(database).resolve().as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=0)
    db.row_factory = sqlite3.Row
    try:
        if not apply:
            db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        schema = [list(r) for r in db.execute('PRAGMA table_info(tools)')]
        require(schema == manifest['table_info'], 'Tools schema changed')
        require(not db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='tools'").fetchone(), 'Unexpected tools trigger')
        before = {r['id']: dict(r) for r in db.execute('SELECT * FROM tools')}
        require(all(before.get(r['before']['id']) == r['before'] for r in rows), 'Current row differs from reviewed before-image')
        if not apply:
            db.rollback()
            return {'mode': 'read_only', 'matched_rows': 4, 'writes': 0}
        columns = [r[1] for r in schema]
        def quote(name):
            return '"' + name.replace('"', '""') + '"'
        where = ' AND '.join(quote(c) + ' IS ?' for c in columns)
        count = 0
        for row in rows:
            count += db.execute('UPDATE tools SET team_id=?, updated_at=? WHERE ' + where,
                                [row['restore']['team_id'], row['restore']['updated_at']] + [row['before'][c] for c in columns]).rowcount
        require(count == 4, 'Expected exactly four updated rows')
        expected = {k: dict(v) for k, v in before.items()}
        for row in rows:
            expected[row['before']['id']].update(row['restore'])
        after = {r['id']: dict(r) for r in db.execute('SELECT * FROM tools')}
        require(after == expected, 'Unexpected catalog change within repair transaction')
        db.commit()
        return {'mode': 'applied', 'matched_rows': 4, 'writes': 4}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('manifest')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--reviewed-manifest-sha256')
    args = parser.parse_args()
    payload = Path(args.manifest).read_bytes()
    if args.apply:
        require(args.reviewed_manifest_sha256 == hashlib.sha256(payload).hexdigest(), 'Reviewed manifest hash required')
    print(json.dumps(repair(args.database, json.loads(payload), apply=args.apply)))
