from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.schema import episode_events, episodes
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import EpisodeState

if TYPE_CHECKING:
    from conftest import SeededEpisode


def _transition_event_values(seeded: SeededEpisode) -> dict[str, object]:
    after_state = EpisodeState.model_validate(
        {
            **seeded.initial_state.model_dump(mode="json"),
            "state": "planning",
            "last_event_id": seeded.command.event_id,
            "updated_at": seeded.command.occurred_at,
        }
    )
    return {
        "tenant_id": seeded.tenant_id,
        "installation_id": seeded.installation_id,
        "episode_id": seeded.episode_id,
        "event_id": seeded.command.event_id,
        "command_id": seeded.command.command_id,
        "command_sha256": bytes.fromhex(
            canonical_sha256({"schema-test": seeded.command.command_id})
        ),
        "event_kind": "state_transition",
        "before_revision": 1,
        "after_revision": 2,
        "before_state": "accepted",
        "after_state": "planning",
        "before_generation": 1,
        "after_generation": 1,
        "before_episode_state": seeded.initial_state.model_dump(mode="json"),
        "after_episode_state": after_state.model_dump(mode="json"),
        "before_state_sha256": bytes.fromhex(canonical_sha256(seeded.initial_state)),
        "after_state_sha256": bytes.fromhex(canonical_sha256(after_state)),
        "occurred_at": datetime(2026, 8, 20, 12, 0, 1, tzinfo=timezone.utc),
    }


def _corrupt_snapshot(values: dict[str, object], case: str) -> None:
    before = deepcopy(values["before_episode_state"])
    after = deepcopy(values["after_episode_state"])
    assert isinstance(before, dict)
    assert isinstance(after, dict)
    if case == "empty_after":
        after = {}
    elif case == "missing_after_key":
        after.pop("principal_id")
    elif case == "extra_after_key":
        after["unexpected"] = True
    elif case == "string_schema_version":
        after["schema_version"] = "1"
    elif case == "float_schema_version":
        after["schema_version"] = 1.0
    elif case == "string_generation":
        after["current_generation"] = "1"
    elif case == "numeric_identifier":
        after["principal_id"] = 1
    elif case == "missing_before_key":
        before.pop("conversation_id")
    elif case == "wrong_before_tenant":
        before["tenant_id"] = "00000000-0000-4000-8000-000000000001"
    elif case == "wrong_before_episode":
        before["episode_id"] = "00000000-0000-4000-8000-000000000002"
    else:  # pragma: no cover - the parametrization is closed below
        raise AssertionError(case)
    values["before_episode_state"] = before
    values["after_episode_state"] = after


@pytest.mark.parametrize(
    "case",
    (
        "empty_after",
        "missing_after_key",
        "extra_after_key",
        "string_schema_version",
        "float_schema_version",
        "string_generation",
        "numeric_identifier",
        "missing_before_key",
        "wrong_before_tenant",
        "wrong_before_episode",
    ),
)
def test_episode_snapshots_reject_unknown_missing_extra_and_wrong_types(
    postgres_engine,
    seed_episode,
    case: str,
) -> None:
    seeded: SeededEpisode = seed_episode(f"snapshot-{case}")
    values = _transition_event_values(seeded)
    _corrupt_snapshot(values, case)

    with pytest.raises(sa.exc.DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
            set_local_scope(
                connection,
                tenant_id=seeded.tenant_id,
                installation_id=seeded.installation_id,
            )
            connection.execute(episode_events.insert().values(**values))


def test_episode_event_cycle_is_deferred_no_action(postgres_gate) -> None:
    with postgres_gate.engine.connect() as connection:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT conname, condeferrable, condeferred, confdeltype "
                    "FROM pg_constraint "
                    "WHERE conname IN ("
                    "'fk_episode_events_episode', 'fk_episodes_last_event') "
                    "ORDER BY conname"
                )
            )
            .mappings()
            .all()
        )

    assert [row["conname"] for row in rows] == [
        "fk_episode_events_episode",
        "fk_episodes_last_event",
    ]
    assert all(row["condeferrable"] and row["condeferred"] for row in rows)
    assert all(row["confdeltype"] == "a" for row in rows)


def test_episode_and_immutable_events_can_be_deleted_atomically(
    postgres_engine,
    seed_episode,
) -> None:
    seeded: SeededEpisode = seed_episode("atomic-delete")

    with postgres_engine.begin() as connection:
        connection.execute(
            sa.delete(episode_events).where(
                episode_events.c.tenant_id == seeded.tenant_id,
                episode_events.c.installation_id == seeded.installation_id,
                episode_events.c.episode_id == seeded.episode_id,
            )
        )
        connection.execute(
            sa.delete(episodes).where(
                episodes.c.tenant_id == seeded.tenant_id,
                episodes.c.installation_id == seeded.installation_id,
                episodes.c.episode_id == seeded.episode_id,
            )
        )

    with postgres_engine.connect() as connection:
        head_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(episodes)
            .where(episodes.c.episode_id == seeded.episode_id)
        )
        event_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(episode_events)
            .where(episode_events.c.episode_id == seeded.episode_id)
        )

    assert head_count == 0
    assert event_count == 0
