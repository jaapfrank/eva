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


# Independently audited historical producer: the first selected tool failed in
# OwnerResearchContinuationTool.prepare before tool admission or dispatch.
# This is a finite repair certificate, not a retry policy for uncertain work.
_PREPARATION_FAILURE_PRODUCERS = {
    ("3af2711588f5613ec52aa15232ae8335f93b8fa08af6f4ce94609d1009f2abad",
     "4e9ad0325ee0840cfe16d2212b3a465413f44870d52aba11cac46b68f2874cea"):
        "native-research-public-projection-before-first-dispatch-v1",
    ("f478f99bc3c99816bf41cd64a09a50e5553ce9a82aee1f80cc17e83891d21f93",
     "02e70758637b3ceefec093f5fede3d74a5e42d5081a68ab2fb228e5de1e774a9"):
        "owner-research-native-prepare-before-first-dispatch-v1",
}


_CERTIFIED_PREPARATION_EVIDENCE_SHA256 = frozenset({
    "c23436b8afb3077371bfa73aba97c58da689e3ee6936ca2b77a10e7c934a4219",
    "aac8aeca4077805e37b30afd9fc64fcf541fa551c0ddea0770d92db9335062ad",
})


def certified_preparation_evidence(work):
    """Proof of no task-tool dispatch; actual model/DB usage is NOT zero."""
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    from eva.contracts.canonical_json import canonical_sha256
    spec, usage = work.spec, work.usage
    if type(spec) is not DelegatedInitiativeDigitalWorkSpec:
        return None
    cause = _PREPARATION_FAILURE_PRODUCERS.get((spec.worker_program_sha256, work.error_fingerprint))
    if (type(spec) is not DelegatedInitiativeDigitalWorkSpec or cause is None
            or work.state != "ambiguous" or work.error_code != "internal_error"
            or work.claim_count != 1 or not work.call_id or work.call_started_at is None
            or work.completed_at is None or work.result is not None or work.stop_requested_at is not None
            or work.lease_owner is not None or work.lease_expires_at is not None
            or usage is None or usage.measurement != "partial"
            or usage.completed_model_responses != 1 or usage.tool_calls != 0):
        return None
    evidence = {"contract": "NativePreparationRecoveryEvidence/v1", "cause": cause,
        "work_id": spec.work_id, "spec_sha256": spec.sha256, "call_id": work.call_id,
        "producer_program_sha256": spec.worker_program_sha256,
        "error_fingerprint": work.error_fingerprint, "usage_sha256": canonical_sha256(usage.to_payload()),
        "scope": "no_task_tool_dispatch_not_zero_model_or_database_usage"}
    return evidence if canonical_sha256(evidence) in _CERTIFIED_PREPARATION_EVIDENCE_SHA256 else None


def valid_preparation_evidence(value, *, work_id, spec_sha256):
    """Validate the immutable certificate already checked against a locked row."""
    import re
    from eva.contracts.canonical_json import canonical_sha256
    return (isinstance(value, dict) and set(value) == {"contract", "cause", "work_id",
            "spec_sha256", "call_id", "producer_program_sha256", "error_fingerprint", "usage_sha256", "scope"}
        and value["contract"] == "NativePreparationRecoveryEvidence/v1"
        and value["work_id"] == work_id and value["spec_sha256"] == spec_sha256
        and isinstance(value["call_id"], str) and bool(value["call_id"])
        and re.fullmatch(r"[0-9a-f]{64}", str(value["usage_sha256"])) is not None
        and value["cause"] == _PREPARATION_FAILURE_PRODUCERS.get(
            (value["producer_program_sha256"], value["error_fingerprint"]))
        and value["cause"] is not None
        and value["scope"] == "no_task_tool_dispatch_not_zero_model_or_database_usage"
        and canonical_sha256(value) in _CERTIFIED_PREPARATION_EVIDENCE_SHA256)
