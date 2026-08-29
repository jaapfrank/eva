"""Operator-only rotation and removal for connector installer secrets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Callable, Literal

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_identity_storage import write_private_once
from eva.operations.installer_secret_store import (
    InstallerSecretSlotStatus,
    InstallerSecretStore,
    InstallerSecretStoreError,
)
from eva.operations.installer_safety import (
    require_owner_only_directory,
    require_utc_timestamp,
)


SecretLifecycleOperation = Literal["rotate", "remove", "recover"]


class InstallerSecretLifecycleError(ValueError):
    """Raised when secret authority and retained bytes cannot move together."""


@dataclass(frozen=True, slots=True)
class InstallerSecretLifecycleResult:
    slot_id: str
    operation: SecretLifecycleOperation
    state: str
    generation: int | None
    invalidation_evidence_sha256: str
    completion_evidence_sha256: str
    capability_revision: int
    capability_head_sha256: str | None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    return require_utc_timestamp(
        value,
        error=InstallerSecretLifecycleError,
        invalid_message="secret lifecycle timestamp is invalid",
        non_utc_message="secret lifecycle timestamp must be UTC",
    )


def _private_directory(path: Path) -> None:
    require_owner_only_directory(
        path,
        error=InstallerSecretLifecycleError,
        unavailable_message="secret lifecycle evidence workspace is unavailable",
        invalid_message="secret lifecycle evidence workspace must be owner-only",
    )


@dataclass(frozen=True, slots=True)
class InstallerSecretLifecycle:
    capability_workflow: InstallerCapabilityWorkflow
    secret_store: InstallerSecretStore
    evidence_workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        _private_directory(self.evidence_workspace)

    def _write_evidence(self, payload: dict[str, object]) -> str:
        encoded = canonical_json_bytes(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            _private_directory(self.evidence_workspace)
            write_private_once(self.evidence_workspace / f"{digest}.json", encoded)
        except IdentityPackLifecycleError as exc:
            raise InstallerSecretLifecycleError(
                "secret lifecycle evidence write failed"
            ) from exc
        return digest

    def _current(self, slot_id: object) -> InstallerSecretSlotStatus:
        if not isinstance(slot_id, str):
            raise InstallerSecretLifecycleError("secret lifecycle slot is invalid")
        try:
            return next(
                item for item in self.secret_store.status() if item.slot_id == slot_id
            )
        except (InstallerSecretStoreError, StopIteration) as exc:
            raise InstallerSecretLifecycleError(
                "secret lifecycle preflight is unavailable"
            ) from exc

    def _invalidate(
        self,
        *,
        slot_id: str,
        operation: Literal["rotate", "remove"],
        from_generation: int,
        to_generation: int | None,
    ) -> tuple[str, int, str | None]:
        evidence = self._write_evidence(
            {
                "contract": "EvaInstallerSecretInvalidationReceipt/v1",
                "from_generation": from_generation,
                "mutation": operation,
                "secret_reference": f"installer-secret://{slot_id}/v1",
                "secret_returned": False,
                "slot_id": slot_id,
                "to_generation": to_generation,
                "written_at": _timestamp(self.clock()),
            }
        )
        try:
            transition = self.capability_workflow.record_invalidation(
                step_id=slot_id,
                evidence_sha256=evidence,
            )
        except InstallerCapabilityWorkflowError as exc:
            raise InstallerSecretLifecycleError(
                "secret lifecycle invalidation failed"
            ) from exc
        return evidence, transition.revision, transition.head_sha256

    def _confirm_invalidation(
        self,
        *,
        slot_id: str,
        evidence_sha256: str,
        from_generation: int,
        to_generation: int | None,
    ) -> Callable[[str, int, int | None], None]:
        def confirm(actual_slot: str, actual_from: int, actual_to: int | None) -> None:
            try:
                capability = self.capability_workflow.status().step(slot_id)
            except InstallerCapabilityWorkflowError as exc:
                raise InstallerSecretLifecycleError(
                    "secret lifecycle invalidation is unavailable"
                ) from exc
            if (
                actual_slot != slot_id
                or actual_from != from_generation
                or actual_to != to_generation
                or capability.state != "blocked"
                or capability.evidence_sha256 != evidence_sha256
            ):
                raise InstallerSecretLifecycleError(
                    "secret lifecycle invalidation differs"
                )

        return confirm

    def _completion(
        self,
        *,
        slot_id: str,
        operation: SecretLifecycleOperation,
        status: InstallerSecretSlotStatus,
        invalidation_evidence_sha256: str,
        capability_revision: int,
        capability_head_sha256: str | None,
    ) -> InstallerSecretLifecycleResult:
        completion = self._write_evidence(
            {
                "capability_head_sha256": capability_head_sha256,
                "capability_revision": capability_revision,
                "completed_at": _timestamp(self.clock()),
                "contract": "EvaInstallerSecretLifecycleCompletionReceipt/v1",
                "generation": status.generation,
                "invalidation_evidence_sha256": invalidation_evidence_sha256,
                "operation": operation,
                "secret_returned": False,
                "slot_id": slot_id,
                "state": status.state,
            }
        )
        return InstallerSecretLifecycleResult(
            slot_id=slot_id,
            operation=operation,
            state=status.state,
            generation=status.generation,
            invalidation_evidence_sha256=invalidation_evidence_sha256,
            completion_evidence_sha256=completion,
            capability_revision=capability_revision,
            capability_head_sha256=capability_head_sha256,
        )

    def rotate(
        self, *, slot_id: object, secret: object
    ) -> InstallerSecretLifecycleResult:
        current = self._current(slot_id)
        if current.state != "present" or current.generation is None:
            raise InstallerSecretLifecycleError(
                "secret lifecycle slot is not ready for rotation"
            )
        try:
            self.secret_store.validate_secret(secret=secret)
        except InstallerSecretStoreError as exc:
            raise InstallerSecretLifecycleError(
                "replacement secret is invalid"
            ) from exc
        checked_slot = current.slot_id
        target = current.generation + 1
        evidence, revision, head = self._invalidate(
            slot_id=checked_slot,
            operation="rotate",
            from_generation=current.generation,
            to_generation=target,
        )
        try:
            status = self.secret_store.rotate(
                slot_id=checked_slot,
                secret=secret,
                invalidate=self._confirm_invalidation(
                    slot_id=checked_slot,
                    evidence_sha256=evidence,
                    from_generation=current.generation,
                    to_generation=target,
                ),
            )
        except InstallerSecretStoreError as exc:
            raise InstallerSecretLifecycleError(
                "secret rotation did not complete; recovery is required"
            ) from exc
        return self._completion(
            slot_id=checked_slot,
            operation="rotate",
            status=status,
            invalidation_evidence_sha256=evidence,
            capability_revision=revision,
            capability_head_sha256=head,
        )

    def remove(self, *, slot_id: object) -> InstallerSecretLifecycleResult:
        current = self._current(slot_id)
        if current.state != "present" or current.generation is None:
            raise InstallerSecretLifecycleError(
                "secret lifecycle slot is not ready for removal"
            )
        checked_slot = current.slot_id
        evidence, revision, head = self._invalidate(
            slot_id=checked_slot,
            operation="remove",
            from_generation=current.generation,
            to_generation=None,
        )
        try:
            status = self.secret_store.remove(
                slot_id=checked_slot,
                invalidate=self._confirm_invalidation(
                    slot_id=checked_slot,
                    evidence_sha256=evidence,
                    from_generation=current.generation,
                    to_generation=None,
                ),
            )
        except InstallerSecretStoreError as exc:
            raise InstallerSecretLifecycleError(
                "secret removal did not complete; recovery is required"
            ) from exc
        return self._completion(
            slot_id=checked_slot,
            operation="remove",
            status=status,
            invalidation_evidence_sha256=evidence,
            capability_revision=revision,
            capability_head_sha256=head,
        )

    def recover(self, *, slot_id: object) -> InstallerSecretLifecycleResult:
        if not isinstance(slot_id, str):
            raise InstallerSecretLifecycleError("secret lifecycle slot is invalid")
        try:
            capability = self.capability_workflow.status().step(slot_id)
        except InstallerCapabilityWorkflowError as exc:
            raise InstallerSecretLifecycleError(
                "secret lifecycle recovery preflight is unavailable"
            ) from exc
        if capability.state != "blocked" or capability.evidence_sha256 is None:
            raise InstallerSecretLifecycleError(
                "secret lifecycle recovery requires durable invalidation"
            )
        try:
            status = self.secret_store.recover(slot_id=slot_id, invalidated=True)
        except InstallerSecretStoreError as exc:
            raise InstallerSecretLifecycleError(
                "secret lifecycle recovery did not complete"
            ) from exc
        return self._completion(
            slot_id=slot_id,
            operation="recover",
            status=status,
            invalidation_evidence_sha256=capability.evidence_sha256,
            capability_revision=self.capability_workflow.status().revision,
            capability_head_sha256=self.capability_workflow.status().head_sha256,
        )


__all__ = [
    "InstallerSecretLifecycle",
    "InstallerSecretLifecycleError",
    "InstallerSecretLifecycleResult",
]
