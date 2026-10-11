"""Host-bound execution identity without changing an authorized source turn."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import math
from typing import Iterator
from collections.abc import Callable

from eva.application.conversation import ConversationConflict, ConversationTurn


@dataclass(frozen=True, slots=True)
class BackgroundExecutionScope:
    source_turn: ConversationTurn
    work_id: str
    authorize_dispatch: Callable[[ConversationTurn], None] | None = None
    remaining_model_output_tokens: Callable[[ConversationTurn], int] | None = None
    remaining_model_time_seconds: Callable[[ConversationTurn], float] | None = None

    def __post_init__(self) -> None:
        if self.source_turn.audience.kind not in {"private_person", "household"}:
            raise ValueError("background execution requires an authorized source audience")
        if (not isinstance(self.work_id, str) or not self.work_id.strip()
                or len(self.work_id.encode("utf-8")) > 512):
            raise ValueError("background work identity is invalid")

    @property
    def namespace(self) -> str:
        return "background-work:" + hashlib.sha256(self.work_id.encode("utf-8")).hexdigest()


_current: ContextVar[BackgroundExecutionScope | None] = ContextVar(
    "eva_background_execution", default=None,
)


@contextmanager
def background_execution(scope: BackgroundExecutionScope) -> Iterator[None]:
    """Bind one admitted work item; nesting must not replace its identity."""
    if not isinstance(scope, BackgroundExecutionScope):
        raise TypeError("background execution scope is required")
    if _current.get() is not None:
        raise ConversationConflict("background execution cannot be nested")
    token = _current.set(scope)
    try:
        yield
    finally:
        _current.reset(token)


def background_execution_namespace(turn: ConversationTurn) -> str | None:
    """Return a job namespace only for the exact host-bound source turn."""
    scope = _current.get()
    if scope is None:
        return None
    if turn != scope.source_turn:
        raise ConversationConflict("background execution source turn differs")
    return scope.namespace


def authorize_background_dispatch(turn: ConversationTurn) -> None:
    """Recheck the durable job immediately before a connector effect starts."""
    scope = _current.get()
    if scope is not None:
        background_execution_namespace(turn)
        if scope.authorize_dispatch is None:
            raise ConversationConflict("background connector dispatch requires a job guard")
        scope.authorize_dispatch(turn)


def background_model_output_limit(turn: ConversationTurn) -> int | None:
    """Read the admitted task's remaining output allowance for its exact source."""
    scope = _current.get()
    if scope is None:
        return None
    background_execution_namespace(turn)
    if scope.remaining_model_output_tokens is None:
        return None
    remaining = scope.remaining_model_output_tokens(turn)
    if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining <= 0:
        raise ConversationConflict("background model output allowance is invalid")
    return remaining


def background_model_time_limit(turn: ConversationTurn) -> float | None:
    """Use the admitted job's remaining time, without renewing its deadline."""
    scope = _current.get()
    if scope is None:
        return None
    background_execution_namespace(turn)
    if scope.remaining_model_time_seconds is None:
        return None
    remaining = scope.remaining_model_time_seconds(turn)
    if (isinstance(remaining, bool) or not isinstance(remaining, (int, float))
            or not math.isfinite(remaining) or remaining <= 0):
        raise ConversationConflict("background model time allowance is invalid")
    return float(remaining)


def has_background_execution() -> bool:
    """Whether the host has bound the current execution to a durable task."""
    return _current.get() is not None
