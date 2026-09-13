"""Run only in the pinned isolated gateway image with an empty fixture SQLite DB."""
import asyncio
import datetime
import json
import os

import sqlalchemy as sa
from mcpgateway import bootstrap_db as boot
from mcpgateway.db import A2AAgent, EmailTeam, EmailUser, Gateway, Prompt, Resource, Server, Tool


async def main():
    assert os.environ['DATABASE_URL'] == 'sqlite:////tmp/guard.db'
    assert not os.environ.get('EVA_MCP_RESOURCE_ASSIGNMENT_MODE')
    await boot.main()
    engine = sa.create_engine(os.environ['DATABASE_URL'])
    with engine.connect() as conn:
        admin = conn.execute(sa.select(EmailUser.__table__)).mappings().one()
        team = conn.execute(sa.select(EmailTeam.__table__)).mappings().one()
        assert admin['is_admin'] and team['is_personal']
        assert conn.execute(sa.text('SELECT count(*) FROM roles')).scalar_one() > 0
    classes = [Server, Tool, Resource, Prompt, Gateway, A2AAgent]
    cases = ['public_owner_no_team', 'private_team_no_owner', 'name_conflict', 'federation_missing', 'conflict_target']
    def required_value(column, label):
        if isinstance(column.type, sa.JSON):
            return {}
        if isinstance(column.type, sa.Boolean):
            return True
        if isinstance(column.type, sa.Integer):
            return 1
        if isinstance(column.type, sa.DateTime):
            return datetime.datetime(2026, 9, 13)
        return label
    with engine.begin() as conn:
        for model in classes:
            table = model.__table__
            for index, case in enumerate(cases):
                label = model.__name__.lower() + '-' + str(index)
                row = {c.name: required_value(c, label) for c in table.columns
                       if not c.nullable and c.default is None and c.server_default is None}
                row.update(id=label, name=label, team_id=team['id'] if index in (1, 4) else None,
                           owner_email=None if index == 1 else admin['email'],
                           visibility='private' if index == 1 else 'public')
                if 'federation_source' in table.c:
                    row['federation_source'] = None if index == 3 else 'fixture-source'
                if 'slug' in table.c:
                    row['slug'] = label
                if index == 4:
                    # A real collision in the legacy assignment's unique field.
                    conflict = model.__name__.lower() + '-2'
                    field = {'Resource': 'uri', 'Gateway': 'slug', 'A2AAgent': 'slug'}.get(model.__name__, 'name')
                    row[field] = conflict
                conn.execute(table.insert().values(**row))
    def snapshot():
        with engine.connect() as conn:
            return {m.__tablename__: [dict(x) for x in conn.execute(
                sa.select(m.__table__).order_by(m.__table__.c.id)).mappings()] for m in classes}
    before = snapshot()
    # Actual schema-at-head startup path, then the explicit skip-migrations path.
    await boot.main()
    assert snapshot() == before, 'Fast-path startup changed a resource column'
    previous = boot.settings.mcpgateway_skip_migrations
    boot.settings.mcpgateway_skip_migrations = True
    await boot.main()
    boot.settings.mcpgateway_skip_migrations = previous
    assert snapshot() == before, 'Skip-migrations startup changed a resource column'
    # Invalid operator setting fails before the resource helper can mutate rows.
    os.environ['EVA_MCP_RESOURCE_ASSIGNMENT_MODE'] = 'typo'
    with engine.connect() as conn:
        try:
            await boot.bootstrap_resource_assignments(conn)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid mode accepted')
    assert snapshot() == before
    # Legacy nullable-visibility schemas cannot be represented by today's NOT NULL
    # tables. Clone the actual metadata into a separate ephemeral legacy DB.
    legacy = sa.create_engine('sqlite:////tmp/legacy.db')
    metadata = sa.MetaData()
    for table in EmailUser.metadata.sorted_tables:
        table.to_metadata(metadata)
    for model in classes:
        metadata.tables[model.__tablename__].c.visibility.nullable = True
    metadata.create_all(legacy)
    with engine.connect() as source, legacy.begin() as target:
        for table in EmailUser.metadata.sorted_tables:
            rows = [dict(r) for r in source.execute(sa.select(table)).mappings()]
            if rows:
                target.execute(metadata.tables[table.name].insert(), rows)
        for model in classes:
            table = metadata.tables[model.__tablename__]
            target.execute(table.update().where(table.c.id == model.__name__.lower()+'-0').values(visibility=None))
    def legacy_snapshot():
        with legacy.connect() as conn:
            return {m.__tablename__: [dict(r) for r in conn.execute(sa.select(m.__table__).order_by(m.__table__.c.id)).mappings()] for m in classes}
    legacy_before = legacy_snapshot()
    os.environ.pop('EVA_MCP_RESOURCE_ASSIGNMENT_MODE', None)
    with legacy.connect() as conn:
        await boot._run_post_migration_bootstrap(conn)
        conn.commit()
    assert legacy_snapshot() == legacy_before, 'Legacy null visibility changed'
    # Explicit legacy migration remains available; never used on product data.
    # Collision handling is legacy behavior outside this guard; use non-colliding
    # fixture rows for the opt-in availability check.
    with engine.begin() as conn:
        for model in classes:
            conn.execute(model.__table__.delete().where(model.__table__.c.id == model.__name__.lower()+'-4'))
    os.environ['EVA_MCP_RESOURCE_ASSIGNMENT_MODE'] = 'migrate-orphans'
    with engine.connect() as conn:
        await boot.bootstrap_resource_assignments(conn)
        conn.commit()
    assert snapshot() != before, 'Explicit legacy migration was silently disabled'
    assert all(r['team_id'] == team['id'] and r['owner_email'] == admin['email']
               and r['visibility'] == 'public' for rows in snapshot().values() for r in rows)
    print(json.dumps({'status': 'passed', 'resource_classes': 6, 'resource_rows': 30,
                      'fresh_database_admin_team_roles': True,
                      'fast_path_all_resource_columns_unchanged': True,
                      'skip_migrations_all_resource_columns_unchanged': True,
                      'invalid_mode_rejected_without_resource_change': True,
                      'legacy_null_visibility_all_resource_columns_unchanged': True,
                      'explicit_legacy_migration_available': True}))


asyncio.run(main())
