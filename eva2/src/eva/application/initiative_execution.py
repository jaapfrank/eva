"""Host-bound context for native work; model text never grants authority."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from eva.application.conversation import ConversationConflict, ConversationTurn


class InitiativeGuard(Protocol):
    def check(self, turn: ConversationTurn) -> None: ...
    def action_allowed(self, name: str) -> bool: ...
    def authorize_request(self, request, *, phase: str) -> None: ...
    def instructions(self) -> str: ...


_current: ContextVar[InitiativeGuard | None] = ContextVar("eva_native_initiative", default=None)


def current_initiative() -> InitiativeGuard | None:
    return _current.get()


def require_initiative_context(turn: ConversationTurn) -> InitiativeGuard | None:
    guard = _current.get()
    native = turn.conversation_id.startswith("initiative-task:")
    if native and guard is None:
        raise ConversationConflict("native initiative requires its durable host execution context")
    if guard is not None:
        if not native:
            raise ConversationConflict("initiative context cannot authorize a user turn")
        guard.check(turn)
    return guard


@contextmanager
def initiative_execution(guard: InitiativeGuard):
    token = _current.set(guard)
    try:
        yield
    finally:
        _current.reset(token)


def authorize_initiative_action(turn: ConversationTurn, name: str) -> None:
    guard = require_initiative_context(turn)
    if guard is not None and not guard.action_allowed(name):
        raise ConversationConflict("initiative action has no admitted cost and authority profile")
