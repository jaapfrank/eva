from __future__ import annotations

import pytest

from eva.contracts.base import JSON_SAFE_INTEGER_MAX
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import EpisodeState
from eva.domain.episode import (
    EpisodeAggregate,
    EpisodeTransitionCommand,
    EpisodeTransitionError,
    transition_accepted_to_planning,
)


TENANT_ID = "00000000-0000-4000-8000-000000000001"
INSTALLATION_ID = "00000000-0000-4000-8000-000000000002"
EPISODE_ID = "00000000-0000-4000-8000-000000000003"
PRINCIPAL_ID = "00000000-0000-4000-8000-000000000004"
CONVERSATION_ID = "00000000-0000-4000-8000-000000000005"
INGRESS_REQUEST_ID = "00000000-0000-4000-8000-000000000006"
INITIAL_EVENT_ID = "00000000-0000-4000-8000-000000000007"
TRANSITION_EVENT_ID = "00000000-0000-4000-8000-000000000008"
COMMAND_ID = "00000000-0000-4000-8000-000000000009"


def _state(*, state: str = "accepted") -> EpisodeState:
    return EpisodeState.model_validate(
        {
            "schema_version": 1,
            "contract": "EpisodeState/v1",
            "episode_id": EPISODE_ID,
            "tenant_id": TENANT_ID,
            "principal_id": PRINCIPAL_ID,
            "conversation_id": CONVERSATION_ID,
            "ingress_request_id": INGRESS_REQUEST_ID,
            "state": state,
            "current_generation": 1,
            "supersedes_generation": None,
            "last_event_id": INITIAL_EVENT_ID,
            "updated_at": "2026-08-20T12:00:00Z",
        }
    )


def _aggregate(*, state: str = "accepted", revision: int = 1) -> EpisodeAggregate:
    episode_state = _state(state=state)
    return EpisodeAggregate(
        installation_id=INSTALLATION_ID,
        revision=revision,
        state=episode_state,
        state_sha256=canonical_sha256(episode_state),
    )


def _command(
    *, expected_sha256: str | None = None, expected_revision: int = 1
) -> EpisodeTransitionCommand:
    aggregate = _aggregate()
    return EpisodeTransitionCommand(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        episode_id=EPISODE_ID,
        command_id=COMMAND_ID,
        event_id=TRANSITION_EVENT_ID,
        expected_revision=expected_revision,
        expected_state_sha256=expected_sha256 or aggregate.state_sha256,
        occurred_at="2026-08-20T12:00:01Z",
    )


def test_accepted_episode_advances_to_planning_without_changing_generation() -> None:
    transition = transition_accepted_to_planning(_aggregate(), _command())

    assert transition.before.state.state == "accepted"
    assert transition.after.state.state == "planning"
    assert transition.after.revision == 2
    assert transition.after.state.current_generation == 1
    assert transition.after.state.supersedes_generation is None
    assert transition.after.state.last_event_id == TRANSITION_EVENT_ID
    assert transition.after.state.updated_at == "2026-08-20T12:00:01Z"
    assert transition.after.state_sha256 == canonical_sha256(transition.after.state)
    assert transition.command_sha256 == canonical_sha256(
        {
            "command": "AdvanceEpisodeToPlanning/v1",
            "command_id": COMMAND_ID,
            "episode_id": EPISODE_ID,
            "event_id": TRANSITION_EVENT_ID,
            "expected_revision": 1,
            "expected_state_sha256": transition.before.state_sha256,
            "installation_id": INSTALLATION_ID,
            "occurred_at": "2026-08-20T12:00:01Z",
            "tenant_id": TENANT_ID,
        }
    )


def test_transition_rejects_a_state_outside_the_single_4b_edge() -> None:
    aggregate = _aggregate(state="executing")
    command = _command(expected_sha256=aggregate.state_sha256)

    try:
        transition_accepted_to_planning(aggregate, command)
    except EpisodeTransitionError as exc:
        assert exc.code == "unsupported_transition"
    else:
        raise AssertionError("4B must implement accepted -> planning only")


def test_transition_rejects_a_stale_state_hash_before_mutation() -> None:
    command = _command(expected_sha256="f" * 64)

    try:
        transition_accepted_to_planning(_aggregate(), command)
    except EpisodeTransitionError as exc:
        assert exc.code == "concurrent_state"
    else:
        raise AssertionError("a stale state hash must fail closed")


def test_aggregate_rejects_a_noncanonical_state_hash() -> None:
    try:
        EpisodeAggregate(
            installation_id=INSTALLATION_ID,
            revision=1,
            state=_state(),
            state_sha256="0" * 64,
        )
    except ValueError as exc:
        assert "state_sha256" in str(exc)
    else:
        raise AssertionError("the aggregate must bind its canonical state bytes")


@pytest.mark.parametrize("revision", [True, 1.0, "1"])
def test_aggregate_rejects_non_integer_revisions(revision: object) -> None:
    with pytest.raises(ValueError, match="revision must be an integer"):
        _aggregate(revision=revision)  # type: ignore[arg-type]


@pytest.mark.parametrize("revision", [True, 1.0, "1"])
def test_command_rejects_non_integer_expected_revisions(revision: object) -> None:
    with pytest.raises(ValueError, match="expected_revision must be an integer"):
        _command(expected_revision=revision)  # type: ignore[arg-type]


def test_revisions_stay_within_the_canonical_integer_range() -> None:
    with pytest.raises(ValueError, match="supported revision range"):
        _aggregate(revision=JSON_SAFE_INTEGER_MAX + 1)
    with pytest.raises(ValueError, match="supported revision range"):
        _command(expected_revision=JSON_SAFE_INTEGER_MAX)
