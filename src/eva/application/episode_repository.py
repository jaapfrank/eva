"""Framework-neutral port for the tranche 4B episode repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eva.contracts.lifecycle import EpisodeState
from eva.domain.episode import EpisodeAggregate, EpisodeTransitionCommand


class EpisodeRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TransactionRequired(EpisodeRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "transaction_required", "an existing caller transaction is required"
        )


class TransactionScopeMismatch(EpisodeRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "transaction_scope_mismatch",
            "the caller transaction is already bound to a different scope",
        )


class EpisodeNotFound(EpisodeRepositoryError):
    def __init__(self) -> None:
        super().__init__("episode_not_found", "the scoped episode does not exist")


class ConcurrentEpisodeWrite(EpisodeRepositoryError):
    def __init__(self) -> None:
        super().__init__("concurrent_episode_write", "episode compare-and-swap failed")


class EpisodeCommandConflict(EpisodeRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "episode_command_conflict", "command ID was reused with different bytes"
        )


class CorruptEpisodeState(EpisodeRepositoryError):
    def __init__(self, message: str) -> None:
        super().__init__("corrupt_episode_state", message)


@dataclass(frozen=True, slots=True)
class AppliedEpisodeTransition:
    state: EpisodeState
    revision: int


class EpisodeRepository(Protocol):
    def load_state(self, episode_id: str) -> EpisodeAggregate: ...

    def apply_transition(
        self, command: EpisodeTransitionCommand
    ) -> AppliedEpisodeTransition: ...
