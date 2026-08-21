"""Pure tranche 4B episode transition used by the atomicity proof.

This module intentionally implements one edge only.  The complete episode state
machine belongs to tranche 4D.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from eva.contracts.base import JSON_SAFE_INTEGER_MAX
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import EpisodeState


def _require_uuid(value: str, field: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an opaque UUID") from exc
    if str(parsed) != value:
        raise ValueError(f"{field} must use canonical lowercase UUID text")


def _require_timestamp(value: str, field: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"{field} must be canonical UTC seconds") from exc


def _require_revision(value: int, field: str, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"{field} is outside the supported revision range")


class EpisodeTransitionError(ValueError):
    """Closed failure returned before a repository mutation is attempted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EpisodeAggregate:
    """Internal revision wrapper around the durable EpisodeState/v1 contract."""

    installation_id: str
    revision: int
    state: EpisodeState
    state_sha256: str

    def __post_init__(self) -> None:
        _require_uuid(self.installation_id, "installation_id")
        for field, value in (
            ("tenant_id", self.state.tenant_id),
            ("episode_id", self.state.episode_id),
            ("principal_id", self.state.principal_id),
            ("conversation_id", self.state.conversation_id),
            ("ingress_request_id", self.state.ingress_request_id),
            ("last_event_id", self.state.last_event_id),
        ):
            _require_uuid(value, field)
        _require_revision(self.revision, "revision", maximum=JSON_SAFE_INTEGER_MAX)
        if self.state_sha256 != canonical_sha256(self.state):
            raise ValueError("state_sha256 must bind the canonical EpisodeState/v1")


@dataclass(frozen=True, slots=True)
class EpisodeTransitionCommand:
    """Internal command envelope; its hash is persisted, not this Python type."""

    tenant_id: str
    installation_id: str
    episode_id: str
    command_id: str
    event_id: str
    expected_revision: int
    expected_state_sha256: str
    occurred_at: str

    def __post_init__(self) -> None:
        for field in (
            "tenant_id",
            "installation_id",
            "episode_id",
            "command_id",
            "event_id",
        ):
            _require_uuid(getattr(self, field), field)
        _require_revision(
            self.expected_revision,
            "expected_revision",
            maximum=JSON_SAFE_INTEGER_MAX - 1,
        )
        if len(self.expected_state_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.expected_state_sha256
        ):
            raise ValueError("expected_state_sha256 must be lowercase SHA-256")
        _require_timestamp(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class EpisodeTransition:
    command_id: str
    event_id: str
    command_sha256: str
    before: EpisodeAggregate
    after: EpisodeAggregate


def episode_transition_command_sha256(command: EpisodeTransitionCommand) -> str:
    return canonical_sha256(
        {
            "command": "AdvanceEpisodeToPlanning/v1",
            "command_id": command.command_id,
            "episode_id": command.episode_id,
            "event_id": command.event_id,
            "expected_revision": command.expected_revision,
            "expected_state_sha256": command.expected_state_sha256,
            "installation_id": command.installation_id,
            "occurred_at": command.occurred_at,
            "tenant_id": command.tenant_id,
        }
    )


def transition_accepted_to_planning(
    aggregate: EpisodeAggregate,
    command: EpisodeTransitionCommand,
) -> EpisodeTransition:
    """Return the one state change needed by tranche 4C, without I/O."""

    if (
        aggregate.state.tenant_id != command.tenant_id
        or aggregate.installation_id != command.installation_id
        or aggregate.state.episode_id != command.episode_id
    ):
        raise EpisodeTransitionError(
            "scope_mismatch", "command and aggregate scope must match"
        )
    if (
        aggregate.revision != command.expected_revision
        or aggregate.state_sha256 != command.expected_state_sha256
    ):
        raise EpisodeTransitionError(
            "concurrent_state", "expected revision or state hash is stale"
        )
    if aggregate.state.state != "accepted":
        raise EpisodeTransitionError(
            "unsupported_transition", "tranche 4B permits accepted -> planning only"
        )
    if command.occurred_at < aggregate.state.updated_at:
        raise EpisodeTransitionError(
            "time_regression", "transition time cannot precede current state"
        )

    after_state = EpisodeState.model_validate(
        {
            **aggregate.state.model_dump(mode="json"),
            "state": "planning",
            "last_event_id": command.event_id,
            "updated_at": command.occurred_at,
        }
    )
    after = EpisodeAggregate(
        installation_id=aggregate.installation_id,
        revision=aggregate.revision + 1,
        state=after_state,
        state_sha256=canonical_sha256(after_state),
    )
    return EpisodeTransition(
        command_id=command.command_id,
        event_id=command.event_id,
        command_sha256=episode_transition_command_sha256(command),
        before=aggregate,
        after=after,
    )
