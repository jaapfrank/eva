"""Host-bound execution identity without changing an authorized source turn."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from typing import Iterator

from eva.application.conversation import ConversationConflict, ConversationTurn


@dataclass(frozen=True, slots=True)
class BackgroundExecutionScope:
    source_turn: ConversationTurn
    work_id: str

    def __post_init__(self) -> None:
        if self.source_turn.audience.kind != "private_person":
            raise ValueError("background execution requires a private source turn")
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
