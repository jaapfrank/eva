"""Host-bound context for native work; model text never grants authority."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class NativeInitiativeDelegation:
    """Host queue receipt, never a completed native reply."""
    work_id: str


def unstarted_profile_refresh(work) -> bool:
    """Host evidence for a retired admission that never crossed a call boundary.

    This is not a retry policy for failed, uncertain, stopped or leased work.
    The immutable queue row remains the source of the zero-dispatch evidence.
    """
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    return (type(work.spec) is DelegatedInitiativeDigitalWorkSpec
        and work.state == "needs_help" and work.error_code == "unstarted_profile_superseded"
        and work.claim_count == 0 and work.call_id is None and work.call_started_at is None
        and work.lease_owner is None and work.lease_expires_at is None
        and work.stop_requested_at is None and work.result is None and work.usage is None)
