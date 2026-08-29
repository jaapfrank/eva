from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from tests.support.episode_repository import PostgresEpisodeRepository
from eva.adapters.postgres.schema import episode_events, episodes
from eva.application.episode_repository import (
    AppliedEpisodeTransition,
    ConcurrentEpisodeWrite,
    EpisodeCommandConflict,
    TransactionRequired,
    TransactionScopeMismatch,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import EpisodeState
from eva.domain.episode import (
    EpisodeTransition,
    EpisodeTransitionCommand,
    episode_transition_command_sha256,
)

if TYPE_CHECKING:
    from conftest import SeededEpisode


class _BarrierEpisodeRepository(PostgresEpisodeRepository):
    """Synchronize only the real compare-and-swap call in race tests."""

    def __init__(
        self,
        connection: Connection,
        tenant_id: str,
        installation_id: str,
        barrier: Barrier,
    ) -> None:
        super().__init__(connection, tenant_id, installation_id)
        self._barrier = barrier

    def _update_head(self, transition: EpisodeTransition) -> bool:
        self._barrier.wait(timeout=10)
        return super()._update_head(transition)


def _run_transition_race(
    engine: Engine,
    seeded: SeededEpisode,
    commands: tuple[EpisodeTransitionCommand, EpisodeTransitionCommand],
) -> tuple[list[AppliedEpisodeTransition], list[Exception]]:
    barrier = Barrier(2)

    def apply(command: EpisodeTransitionCommand) -> AppliedEpisodeTransition:
        with engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
            return _BarrierEpisodeRepository(
                connection,
                seeded.tenant_id,
                seeded.installation_id,
                barrier,
            ).apply_transition(command)

    results: list[AppliedEpisodeTransition] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(apply, command) for command in commands]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - the test classifies the loser
                errors.append(exc)
    return results, errors


def test_caller_commit_persists_head_and_event_together(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("commit")

    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        result = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        ).apply_transition(seeded.command)

    assert result.state.state == "planning"
    assert result.revision == 2
    with postgres_engine.connect() as connection:
        head = (
            connection.execute(
                sa.select(episodes).where(
                    episodes.c.tenant_id == UUID(seeded.tenant_id),
                    episodes.c.installation_id == UUID(seeded.installation_id),
                    episodes.c.episode_id == UUID(seeded.episode_id),
                )
            )
            .mappings()
            .one()
        )
        event = (
            connection.execute(
                sa.select(episode_events).where(
                    episode_events.c.tenant_id == UUID(seeded.tenant_id),
                    episode_events.c.installation_id == UUID(seeded.installation_id),
                    episode_events.c.command_id == UUID(seeded.command.command_id),
                )
            )
            .mappings()
            .one()
        )
        event_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(episode_events)
            .where(episode_events.c.episode_id == seeded.episode_id)
        )
    before_state = EpisodeState.model_validate(event["before_episode_state"])
    after_state = EpisodeState.model_validate(event["after_episode_state"])
    result_hash = canonical_sha256(result.state)

    assert head["state"] == "planning"
    assert head["revision"] == 2
    assert str(head["last_event_id"]) == seeded.command.event_id
    assert bytes(head["state_payload_sha256"]).hex() == result_hash
    assert head["updated_at"] == datetime(2026, 8, 20, 12, 0, 1, tzinfo=timezone.utc)
    assert str(event["episode_id"]) == seeded.episode_id
    assert str(event["event_id"]) == seeded.command.event_id
    assert bytes(event["command_sha256"]).hex() == (
        episode_transition_command_sha256(seeded.command)
    )
    assert (event["before_revision"], event["after_revision"]) == (1, 2)
    assert (event["before_state"], event["after_state"]) == (
        "accepted",
        "planning",
    )
    assert before_state == seeded.initial_state
    assert after_state == result.state
    assert bytes(event["before_state_sha256"]).hex() == canonical_sha256(
        seeded.initial_state
    )
    assert bytes(event["after_state_sha256"]).hex() == result_hash
    assert event_count == 2


def test_caller_rollback_discards_head_and_event_together(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("rollback")

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        ).apply_transition(seeded.command)
        transaction.rollback()

    with postgres_engine.connect() as connection:
        head = connection.execute(
            sa.select(episodes.c.state, episodes.c.revision).where(
                episodes.c.episode_id == seeded.episode_id
            )
        ).one()
        event_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(episode_events)
            .where(episode_events.c.episode_id == seeded.episode_id)
        )
    assert tuple(head) == ("accepted", 1)
    assert event_count == 1


def test_same_command_replays_stored_state_and_hash_conflict_fails(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("replay")
    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        repository = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        )
        first = repository.apply_transition(seeded.command)
    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        replay = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        ).apply_transition(seeded.command)
    assert replay == first

    conflicting = EpisodeTransitionCommand(
        tenant_id=seeded.command.tenant_id,
        installation_id=seeded.command.installation_id,
        episode_id=seeded.command.episode_id,
        command_id=seeded.command.command_id,
        event_id=seeded.command.event_id,
        expected_revision=seeded.command.expected_revision,
        expected_state_sha256=seeded.command.expected_state_sha256,
        occurred_at="2026-08-20T12:00:02Z",
    )
    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        repository = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        )
        try:
            repository.apply_transition(conflicting)
        except EpisodeCommandConflict:
            pass
        else:
            raise AssertionError("same command ID with different bytes must conflict")


def test_repository_cannot_escape_its_original_caller_transaction(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("transaction-reuse")

    with postgres_engine.connect() as connection:
        original = connection.begin()
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        repository = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        )
        original.rollback()

        replacement = connection.begin()
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        with pytest.raises(TransactionRequired):
            repository.apply_transition(seeded.command)
        replacement.rollback()

    with postgres_engine.connect() as connection:
        head = connection.execute(
            sa.select(episodes.c.state, episodes.c.revision).where(
                episodes.c.episode_id == UUID(seeded.episode_id)
            )
        ).one()
        event_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(episode_events)
            .where(episode_events.c.episode_id == UUID(seeded.episode_id))
        )
    assert tuple(head) == ("accepted", 1)
    assert event_count == 1


def test_repository_rejects_a_nested_transaction(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("nested-transaction")

    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        repository = PostgresEpisodeRepository(
            connection, seeded.tenant_id, seeded.installation_id
        )
        nested = connection.begin_nested()
        try:
            with pytest.raises(TransactionRequired):
                repository.load_state(seeded.episode_id)
        finally:
            nested.rollback()

        assert repository.load_state(seeded.episode_id).state == seeded.initial_state


def test_transaction_scope_is_bound_once_and_cannot_hop(
    postgres_engine,
    seed_episode,
) -> None:
    first: SeededEpisode = seed_episode("scope-first")
    second: SeededEpisode = seed_episode("scope-second")

    with postgres_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        first_repository = PostgresEpisodeRepository(
            connection, first.tenant_id, first.installation_id
        )
        second_repository = PostgresEpisodeRepository(
            connection, second.tenant_id, second.installation_id
        )

        assert (
            first_repository.load_state(first.episode_id).state == first.initial_state
        )
        with pytest.raises(TransactionScopeMismatch):
            second_repository.load_state(second.episode_id)

        scope = connection.execute(
            sa.text(
                "SELECT current_setting('eva.tenant_id', true), "
                "current_setting('eva.installation_id', true)"
            )
        ).one()
        assert tuple(scope) == (first.tenant_id, first.installation_id)
        assert (
            first_repository.load_state(first.episode_id).state == first.initial_state
        )


def test_two_concurrent_replays_return_one_identical_result(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("concurrent-replay")

    results, errors = _run_transition_race(
        postgres_engine,
        seeded,
        (seeded.command, seeded.command),
    )

    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    with postgres_engine.connect() as connection:
        head = connection.execute(
            sa.select(episodes.c.state, episodes.c.revision).where(
                episodes.c.episode_id == UUID(seeded.episode_id)
            )
        ).one()
        command_event_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(episode_events)
            .where(
                episode_events.c.episode_id == UUID(seeded.episode_id),
                episode_events.c.command_id == UUID(seeded.command.command_id),
            )
        )
    assert tuple(head) == ("planning", 2)
    assert command_event_count == 1


def test_two_concurrent_commands_have_one_cas_winner(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("concurrent-conflict")
    competing = EpisodeTransitionCommand(
        tenant_id=seeded.command.tenant_id,
        installation_id=seeded.command.installation_id,
        episode_id=seeded.command.episode_id,
        command_id=str(uuid5(NAMESPACE_URL, "eva2-4b-competing-command")),
        event_id=str(uuid5(NAMESPACE_URL, "eva2-4b-competing-event")),
        expected_revision=seeded.command.expected_revision,
        expected_state_sha256=seeded.command.expected_state_sha256,
        occurred_at=seeded.command.occurred_at,
    )

    results, errors = _run_transition_race(
        postgres_engine,
        seeded,
        (seeded.command, competing),
    )

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ConcurrentEpisodeWrite)
    with postgres_engine.connect() as connection:
        head = connection.execute(
            sa.select(
                episodes.c.state,
                episodes.c.revision,
                episodes.c.last_event_id,
            ).where(episodes.c.episode_id == UUID(seeded.episode_id))
        ).one()
        transition_events = connection.execute(
            sa.select(episode_events.c.event_id, episode_events.c.command_id).where(
                episode_events.c.episode_id == UUID(seeded.episode_id),
                episode_events.c.event_kind == "state_transition",
            )
        ).all()

    assert (head.state, head.revision) == ("planning", 2)
    assert len(transition_events) == 1
    assert head.last_event_id == transition_events[0].event_id
    assert transition_events[0].command_id in {
        UUID(seeded.command.command_id),
        UUID(competing.command_id),
    }
