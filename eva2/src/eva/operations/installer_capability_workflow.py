"""Restart-safe, connector-neutral onboarding status for Eva's installer.

The wizard may record only whether a capability is wanted or deliberately
skipped. Local modules retain that choice until installation; they do not gain
provider-verification states here. Ready and blocked are reserved for an
operator-side verifier that supplies a SHA-256-bound receipt. No secret or
callable provider configuration belongs in this workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
from pathlib import Path
import re
from typing import Callable, Literal

from eva.application.installer import InstallerUseCaseError
from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
)
from eva.operations.installer_safety import (
    owner_only_lock,
    require_owner_only_directory,
    require_utc_timestamp,
)


CapabilityState = Literal["pending", "selected", "skipped", "ready", "blocked"]
VerifiedState = Literal["ready", "blocked"]
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_EVENTS = 256


class InstallerCapabilityWorkflowError(InstallerUseCaseError):
    """Raised when connector onboarding state cannot be trusted."""


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    step_id: str
    required: bool


CAPABILITY_SPECS = (
    CapabilitySpec("model_provider", True),
    CapabilitySpec("telegram", False),
    CapabilitySpec("home_assistant", False),
    CapabilitySpec("browser", False),
    CapabilitySpec("email", False),
    CapabilitySpec("adaptive_connector", False),
    CapabilitySpec("budgets", True),
)


LOCAL_MODULE_STEPS = frozenset({"browser"})
_SPEC_BY_ID = {item.step_id: item for item in CAPABILITY_SPECS}
_UNSET = object()


@dataclass(frozen=True, slots=True)
class CapabilityStepStatus:
    step_id: str
    required: bool
    state: CapabilityState
    evidence_sha256: str | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class InstallerCapabilityStatus:
    revision: int
    head_sha256: str | None
    steps: tuple[CapabilityStepStatus, ...]

    def step(self, step_id: str) -> CapabilityStepStatus:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise InstallerCapabilityWorkflowError("installer capability step is unknown")


@dataclass(frozen=True, slots=True)
class InstallerCapabilityTransition:
    step_id: str
    state: CapabilityState
    revision: int
    head_sha256: str | None
    evidence_sha256: str | None
    already_in_state: bool


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    return require_utc_timestamp(
        value,
        error=InstallerCapabilityWorkflowError,
        invalid_message="capability timestamp is invalid",
        non_utc_message="capability timestamp must be UTC",
    )


def _sha256(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise InstallerCapabilityWorkflowError("capability evidence hash is invalid")
    return value


def _event_hash(event: dict[str, object]) -> str:
    body = {key: value for key, value in event.items() if key != "event_sha256"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _empty_status() -> InstallerCapabilityStatus:
    return InstallerCapabilityStatus(
        revision=0,
        head_sha256=None,
        steps=tuple(
            CapabilityStepStatus(
                step_id=spec.step_id,
                required=spec.required,
                state="pending",
                evidence_sha256=None,
                updated_at=None,
            )
            for spec in CAPABILITY_SPECS
        ),
    )


def _allowed(previous: CapabilityState, current: CapabilityState) -> bool:
    if previous == "pending":
        return current in {"selected", "skipped"}
    if previous == "selected":
        return current in {"skipped", "ready", "blocked"}
    if previous == "skipped":
        return current == "selected"
    if previous == "blocked":
        return current in {"selected", "skipped", "blocked", "ready"}
    if previous == "ready":
        return current == "blocked"
    return False


@dataclass(frozen=True, slots=True)
class InstallerCapabilityWorkflow:
    workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        require_owner_only_directory(
            self.workspace,
            error=InstallerCapabilityWorkflowError,
            unavailable_message="installer capability workspace is unavailable",
            invalid_message=(
                "installer capability workspace must be an owner-only directory"
            ),
        )

    @property
    def state_path(self) -> Path:
        return self.workspace / "capability-workflow.json"

    def _lock(self):
        return owner_only_lock(
            self.workspace / ".capability-workflow.lock",
            error=InstallerCapabilityWorkflowError,
            unavailable_message="installer capability lock is unavailable",
        )

    def _load(self) -> tuple[list[dict[str, object]], InstallerCapabilityStatus]:
        if not self.state_path.exists() and not self.state_path.is_symlink():
            return [], _empty_status()
        try:
            raw = read_private_artifact(
                self.state_path, label="installer capability workflow"
            )
        except IdentityPackLifecycleError as exc:
            raise InstallerCapabilityWorkflowError(
                "installer capability workflow is unavailable"
            ) from exc
        try:
            payload = require_canonical_bytes(raw)
        except ValueError as exc:
            raise InstallerCapabilityWorkflowError(
                "installer capability workflow is invalid"
            ) from exc
        if (
            set(payload) != {"contract", "events", "head_sha256", "revision"}
            or payload.get("contract") != "EvaInstallerCapabilityWorkflow/v1"
        ):
            raise InstallerCapabilityWorkflowError(
                "installer capability workflow is invalid"
            )
        events = payload.get("events")
        revision = payload.get("revision")
        if (
            not isinstance(events, list)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision != len(events)
            or not 1 <= revision <= _MAX_EVENTS
        ):
            raise InstallerCapabilityWorkflowError(
                "installer capability history is invalid"
            )
        statuses = {item.step_id: item for item in _empty_status().steps}
        previous_hash: str | None = None
        previous_at = ""
        checked_events: list[dict[str, object]] = []
        for index, raw_event in enumerate(events, start=1):
            if not isinstance(raw_event, dict) or set(raw_event) != {
                "at",
                "event_sha256",
                "evidence_sha256",
                "previous_event_sha256",
                "revision",
                "state",
                "step_id",
            }:
                raise InstallerCapabilityWorkflowError(
                    "installer capability event is invalid"
                )
            step_id = raw_event.get("step_id")
            state = raw_event.get("state")
            if step_id not in _SPEC_BY_ID or state not in {
                "selected",
                "skipped",
                "ready",
                "blocked",
            }:
                raise InstallerCapabilityWorkflowError(
                    "installer capability event is invalid"
                )
            spec = _SPEC_BY_ID[step_id]
            assert isinstance(state, str)
            if step_id in LOCAL_MODULE_STEPS and state in {"ready", "blocked"}:
                raise InstallerCapabilityWorkflowError(
                    "local module selection has no provider verification state"
                )
            if spec.required and state == "skipped":
                raise InstallerCapabilityWorkflowError(
                    "required installer capability cannot be skipped"
                )
            evidence = _sha256(raw_event.get("evidence_sha256"), optional=True)
            if (state in {"ready", "blocked"}) != (evidence is not None):
                raise InstallerCapabilityWorkflowError(
                    "capability verification evidence is invalid"
                )
            at = _timestamp(raw_event.get("at"))
            prior = statuses[step_id]
            if (
                raw_event.get("revision") != index
                or raw_event.get("previous_event_sha256") != previous_hash
                or (previous_at and at < previous_at)
                or not _allowed(prior.state, state)
            ):
                raise InstallerCapabilityWorkflowError(
                    "installer capability history is invalid"
                )
            claimed_hash = _sha256(raw_event.get("event_sha256"))
            actual_hash = _event_hash(raw_event)
            assert claimed_hash is not None
            if not hmac.compare_digest(claimed_hash, actual_hash):
                raise InstallerCapabilityWorkflowError(
                    "installer capability event hash differs"
                )
            statuses[step_id] = CapabilityStepStatus(
                step_id=step_id,
                required=spec.required,
                state=state,
                evidence_sha256=evidence,
                updated_at=at,
            )
            checked_events.append(raw_event)
            previous_hash = claimed_hash
            previous_at = at
        head = _sha256(payload.get("head_sha256"))
        assert head is not None and previous_hash is not None
        if not hmac.compare_digest(head, previous_hash):
            raise InstallerCapabilityWorkflowError(
                "installer capability head hash differs"
            )
        return checked_events, InstallerCapabilityStatus(
            revision=revision,
            head_sha256=head,
            steps=tuple(statuses[item.step_id] for item in CAPABILITY_SPECS),
        )

    def _append(
        self,
        events: list[dict[str, object]],
        *,
        step_id: str,
        state: Literal["selected", "skipped", "ready", "blocked"],
        evidence_sha256: str | None,
    ) -> InstallerCapabilityStatus:
        if len(events) >= _MAX_EVENTS:
            raise InstallerCapabilityWorkflowError(
                "installer capability history is full"
            )
        at = _timestamp(self.clock())
        if events and at < str(events[-1]["at"]):
            raise InstallerCapabilityWorkflowError(
                "capability timestamp precedes retained history"
            )
        event: dict[str, object] = {
            "at": at,
            "evidence_sha256": evidence_sha256,
            "previous_event_sha256": (events[-1]["event_sha256"] if events else None),
            "revision": len(events) + 1,
            "state": state,
            "step_id": step_id,
        }
        event["event_sha256"] = _event_hash(event)
        updated = [*events, event]
        payload = {
            "contract": "EvaInstallerCapabilityWorkflow/v1",
            "events": updated,
            "head_sha256": event["event_sha256"],
            "revision": len(updated),
        }
        try:
            atomic_write_private(self.state_path, canonical_json_bytes(payload))
        except IdentityPackLifecycleError as exc:
            raise InstallerCapabilityWorkflowError(
                "installer capability workflow write failed"
            ) from exc
        _, status = self._load()
        return status

    def status(self) -> InstallerCapabilityStatus:
        with self._lock():
            _, status = self._load()
            return status

    def select(
        self, *, step_id: object, wanted: object
    ) -> InstallerCapabilityTransition:
        if not isinstance(step_id, str) or step_id not in _SPEC_BY_ID:
            raise InstallerCapabilityWorkflowError(
                "installer capability step is unknown"
            )
        if not isinstance(wanted, bool):
            raise InstallerCapabilityWorkflowError(
                "installer capability choice is invalid"
            )
        spec = _SPEC_BY_ID[step_id]
        target: Literal["selected", "skipped"] = "selected" if wanted else "skipped"
        if spec.required and target == "skipped":
            raise InstallerCapabilityWorkflowError(
                "required installer capability cannot be skipped"
            )
        with self._lock():
            events, status = self._load()
            current = status.step(step_id)
            if current.state == target:
                return InstallerCapabilityTransition(
                    step_id=step_id,
                    state=target,
                    revision=status.revision,
                    head_sha256=status.head_sha256,
                    evidence_sha256=current.evidence_sha256,
                    already_in_state=True,
                )
            if current.state == "ready":
                raise InstallerCapabilityWorkflowError(
                    "a ready capability requires a separate disconnect workflow"
                )
            updated = self._append(
                events,
                step_id=step_id,
                state=target,
                evidence_sha256=None,
            )
            return InstallerCapabilityTransition(
                step_id=step_id,
                state=target,
                revision=updated.revision,
                head_sha256=updated.head_sha256,
                evidence_sha256=None,
                already_in_state=False,
            )

    def record_verification(
        self,
        *,
        step_id: object,
        outcome: object,
        evidence_sha256: object,
        expected_revision: object = _UNSET,
        expected_head_sha256: object = _UNSET,
    ) -> InstallerCapabilityTransition:
        if not isinstance(step_id, str) or step_id not in _SPEC_BY_ID:
            raise InstallerCapabilityWorkflowError(
                "installer capability step is unknown"
            )
        if step_id in LOCAL_MODULE_STEPS:
            raise InstallerCapabilityWorkflowError(
                "local module selection has no provider verification state"
            )
        if outcome not in {"ready", "blocked"}:
            raise InstallerCapabilityWorkflowError(
                "installer capability verification outcome is invalid"
            )
        evidence = _sha256(evidence_sha256)
        if (expected_revision is _UNSET) != (expected_head_sha256 is _UNSET):
            raise InstallerCapabilityWorkflowError(
                "capability verification expectation is incomplete"
            )
        checked_revision: int | None = None
        checked_head: str | None = None
        if expected_revision is not _UNSET:
            if (
                not isinstance(expected_revision, int)
                or isinstance(expected_revision, bool)
                or expected_revision < 0
            ):
                raise InstallerCapabilityWorkflowError(
                    "capability verification revision is invalid"
                )
            checked_revision = expected_revision
            checked_head = _sha256(expected_head_sha256, optional=True)
        assert evidence is not None and isinstance(outcome, str)
        with self._lock():
            events, status = self._load()
            if checked_revision is not None and (
                status.revision != checked_revision
                or status.head_sha256 != checked_head
            ):
                raise InstallerCapabilityWorkflowError(
                    "capability state changed before verification"
                )
            current = status.step(step_id)
            if current.state == outcome and current.evidence_sha256 is not None:
                if hmac.compare_digest(current.evidence_sha256, evidence):
                    return InstallerCapabilityTransition(
                        step_id=step_id,
                        state=outcome,
                        revision=status.revision,
                        head_sha256=status.head_sha256,
                        evidence_sha256=evidence,
                        already_in_state=True,
                    )
            if current.state not in {"selected", "blocked"}:
                raise InstallerCapabilityWorkflowError(
                    "capability must be selected before verification"
                )
            updated = self._append(
                events,
                step_id=step_id,
                state=outcome,
                evidence_sha256=evidence,
            )
            return InstallerCapabilityTransition(
                step_id=step_id,
                state=outcome,
                revision=updated.revision,
                head_sha256=updated.head_sha256,
                evidence_sha256=evidence,
                already_in_state=False,
            )

    def record_invalidation(
        self, *, step_id: object, evidence_sha256: object
    ) -> InstallerCapabilityTransition:
        """Make retained provider authority unusable before secret mutation."""

        if not isinstance(step_id, str) or step_id not in {
            "model_provider",
            "telegram",
            "home_assistant",
            "email",
        }:
            raise InstallerCapabilityWorkflowError(
                "installer capability invalidation step is unknown"
            )
        evidence = _sha256(evidence_sha256)
        assert evidence is not None
        with self._lock():
            events, status = self._load()
            current = status.step(step_id)
            if current.state == "blocked" and current.evidence_sha256 is not None:
                if hmac.compare_digest(current.evidence_sha256, evidence):
                    return InstallerCapabilityTransition(
                        step_id=step_id,
                        state="blocked",
                        revision=status.revision,
                        head_sha256=status.head_sha256,
                        evidence_sha256=evidence,
                        already_in_state=True,
                    )
            if current.state not in {"selected", "blocked", "ready"}:
                raise InstallerCapabilityWorkflowError(
                    "capability must be selected before invalidation"
                )
            updated = self._append(
                events,
                step_id=step_id,
                state="blocked",
                evidence_sha256=evidence,
            )
            return InstallerCapabilityTransition(
                step_id=step_id,
                state="blocked",
                revision=updated.revision,
                head_sha256=updated.head_sha256,
                evidence_sha256=evidence,
                already_in_state=False,
            )


__all__ = [
    "CAPABILITY_SPECS",
    "LOCAL_MODULE_STEPS",
    "CapabilityStepStatus",
    "InstallerCapabilityStatus",
    "InstallerCapabilityTransition",
    "InstallerCapabilityWorkflow",
    "InstallerCapabilityWorkflowError",
]
