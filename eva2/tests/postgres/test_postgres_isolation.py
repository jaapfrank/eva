from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.schema import episodes
from eva.adapters.postgres.scope import set_local_scope


def test_rls_fails_closed_without_scope_and_separates_tenants(
    postgres_engine,
    seed_episode,
) -> None:
    first = seed_episode("tenant-a")
    second = seed_episode("tenant-b")

    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        assert connection.scalar(sa.select(sa.func.count()).select_from(episodes)) == 0
        set_local_scope(
            connection,
            tenant_id=first.tenant_id,
            installation_id=first.installation_id,
        )
        visible = list(connection.scalars(sa.select(episodes.c.episode_id)))

    assert [str(value) for value in visible] == [first.episode_id]
    assert second.episode_id not in {str(value) for value in visible}


def test_rls_blocks_unscoped_and_cross_tenant_writes(
    postgres_engine,
    seed_episode,
) -> None:
    first = seed_episode("write-tenant-a")
    second = seed_episode("write-tenant-b")

    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        unscoped = connection.execute(
            sa.update(episodes)
            .where(episodes.c.episode_id == first.episode_id)
            .values(state="planning")
        )
        set_local_scope(
            connection,
            tenant_id=first.tenant_id,
            installation_id=first.installation_id,
        )
        cross_tenant = connection.execute(
            sa.update(episodes)
            .where(episodes.c.episode_id == second.episode_id)
            .values(state="planning")
        )

    assert unscoped.rowcount == 0
    assert cross_tenant.rowcount == 0
    with postgres_engine.connect() as connection:
        states = {
            row.episode_id: row.state
            for row in connection.execute(
                sa.select(episodes.c.episode_id, episodes.c.state).where(
                    episodes.c.episode_id.in_((first.episode_id, second.episode_id))
                )
            )
        }
    assert states == {
        UUID(first.episode_id): "accepted",
        UUID(second.episode_id): "accepted",
    }


def test_every_personal_foreign_key_is_tenant_and_installation_scoped(
    postgres_gate,
) -> None:
    with postgres_gate.engine.connect() as connection:
        definitions = list(
            connection.scalars(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE connamespace = 'eva_core'::regnamespace "
                    "AND contype = 'f' ORDER BY conname"
                )
            )
        )

    assert any("FOREIGN KEY (tenant_id)" in value for value in definitions)
    assert (
        sum("FOREIGN KEY (tenant_id, installation_id" in value for value in definitions)
        == 3
    )
