from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from eva.adapters.postgres.episode_repository import PostgresEpisodeRepository
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

if TYPE_CHECKING:
    from conftest import SeededEpisode


def test_episode_state_is_readable_without_dbos_schema(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("export")
    with postgres_engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT to_regnamespace('dbos')")) is None
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        repository = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        )
        aggregate = repository.load_state(seeded.episode_id)

    assert aggregate.installation_id == seeded.installation_id
    assert aggregate.revision == 1
    assert canonical_json_bytes(aggregate.state) == canonical_json_bytes(
        seeded.initial_state
    )
    assert canonical_sha256(aggregate.state) == aggregate.state_sha256


def test_eva_schema_has_no_foreign_key_to_framework_tables(postgres_gate) -> None:
    with postgres_gate.engine.connect() as connection:
        count = connection.scalar(
            sa.text(
                "SELECT count(*) FROM pg_constraint c "
                "JOIN pg_class target ON target.oid = c.confrelid "
                "JOIN pg_namespace target_ns ON target_ns.oid = target.relnamespace "
                "WHERE c.connamespace = 'eva_core'::regnamespace "
                "AND c.contype = 'f' AND target_ns.nspname = 'dbos'"
            )
        )
    assert count == 0
