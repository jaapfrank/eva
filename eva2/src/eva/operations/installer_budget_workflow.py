"""Durable, fail-closed resource budgets for Eva's local installer."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from typing import Callable, Literal

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
)
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
)


BudgetPhase = Literal["empty", "staged", "ready"]
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class InstallerBudgetWorkflowError(ValueError):
    """Raised when retained installer budgets cannot be trusted."""


class InstallerBudgetInputError(InstallerBudgetWorkflowError):
    """Raised when a human-supplied budget lies outside Eva's safe bounds."""


@dataclass(frozen=True, slots=True)
class InstallerBudget:
    recall_context_tokens: int
    retrieval_nodes: int
    max_input_tokens: int
    max_output_tokens: int
    profile_context_tokens: int
    memory_records_per_person: int
    memory_bytes_per_person: int
    retention_days: int
    background_reflections_per_person_day: int
    background_reflections_per_installation_day: int
    external_monthly_spend_cap_eur_cents: int

    def as_dict(self) -> dict[str, object]:
        return {
            "background_reflections_per_installation_day": self.background_reflections_per_installation_day,
            "background_reflections_per_person_day": self.background_reflections_per_person_day,
            "contract": "EvaInstallerBudget/v1",
            "external_monthly_spend_cap_eur_cents": self.external_monthly_spend_cap_eur_cents,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "memory_bytes_per_person": self.memory_bytes_per_person,
            "memory_records_per_person": self.memory_records_per_person,
            "profile_context_tokens": self.profile_context_tokens,
            "recall_context_tokens": self.recall_context_tokens,
            "retention_days": self.retention_days,
            "retrieval_nodes": self.retrieval_nodes,
            "unknown_cost_policy": "block_autonomous_spend",
        }


@dataclass(frozen=True, slots=True)
class InstallerBudgetStatus:
    phase: BudgetPhase
    revision: int
    budget_sha256: str | None
    budget: InstallerBudget | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class InstallerBudgetTransition:
    phase: Literal["staged", "ready"]
    revision: int
    budget_sha256: str
    already_in_phase: bool


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise InstallerBudgetWorkflowError("budget timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstallerBudgetWorkflowError("budget timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise InstallerBudgetWorkflowError("budget timestamp must be UTC")
    return value


def _integer(value: object, *, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise InstallerBudgetInputError(f"{label} is outside the safe range")
    return value


def _budget(**values: object) -> InstallerBudget:
    expected = {
        "background_reflections_per_installation_day",
        "background_reflections_per_person_day",
        "external_monthly_spend_cap_eur_cents",
        "max_input_tokens",
        "max_output_tokens",
        "memory_bytes_per_person",
        "memory_records_per_person",
        "profile_context_tokens",
        "recall_context_tokens",
        "retention_days",
        "retrieval_nodes",
    }
    if set(values) != expected:
        raise InstallerBudgetInputError("installer budget fields differ")
    result = InstallerBudget(
        recall_context_tokens=_integer(
            values["recall_context_tokens"],
            label="recall context",
            minimum=2_000,
            maximum=4_000,
        ),
        retrieval_nodes=_integer(
            values["retrieval_nodes"],
            label="retrieval nodes",
            minimum=20,
            maximum=100,
        ),
        max_input_tokens=_integer(
            values["max_input_tokens"],
            label="input tokens",
            minimum=4_000,
            maximum=16_000,
        ),
        max_output_tokens=_integer(
            values["max_output_tokens"],
            label="output tokens",
            minimum=100,
            maximum=1_200,
        ),
        profile_context_tokens=_integer(
            values["profile_context_tokens"],
            label="profile context",
            minimum=100,
            maximum=1_000,
        ),
        memory_records_per_person=_integer(
            values["memory_records_per_person"],
            label="memory records",
            minimum=100,
            maximum=10_000,
        ),
        memory_bytes_per_person=_integer(
            values["memory_bytes_per_person"],
            label="memory bytes",
            minimum=1_048_576,
            maximum=104_857_600,
        ),
        retention_days=_integer(
            values["retention_days"],
            label="retention days",
            minimum=7,
            maximum=3_650,
        ),
        background_reflections_per_person_day=_integer(
            values["background_reflections_per_person_day"],
            label="person reflection budget",
            minimum=0,
            maximum=100,
        ),
        background_reflections_per_installation_day=_integer(
            values["background_reflections_per_installation_day"],
            label="installation reflection budget",
            minimum=0,
            maximum=1_000,
        ),
        external_monthly_spend_cap_eur_cents=_integer(
            values["external_monthly_spend_cap_eur_cents"],
            label="external spend cap",
            minimum=0,
            maximum=1_000_000,
        ),
    )
    if (
        result.background_reflections_per_installation_day
        < result.background_reflections_per_person_day
    ):
        raise InstallerBudgetInputError(
            "installation reflection budget is smaller than the person budget"
        )
    return result


def _budget_sha256(value: InstallerBudget) -> str:
    return hashlib.sha256(canonical_json_bytes(value.as_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class InstallerBudgetWorkflow:
    workspace: Path
    capability_workflow: InstallerCapabilityWorkflow
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        try:
            metadata = self.workspace.lstat()
        except OSError as exc:
            raise InstallerBudgetWorkflowError(
                "installer budget workspace is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise InstallerBudgetWorkflowError(
                "installer budget workspace must be an owner-only directory"
            )

    @property
    def state_path(self) -> Path:
        return self.workspace / "budget-workflow.json"

    @contextmanager
    def _lock(self):
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.workspace / ".budget-workflow.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
                or metadata.st_nlink != 1
            ):
                raise OSError("budget lock is not a private regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise InstallerBudgetWorkflowError(
                "installer budget lock is unavailable"
            ) from exc
        assert descriptor is not None
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _load(self) -> InstallerBudgetStatus | None:
        if not self.state_path.exists() and not self.state_path.is_symlink():
            return None
        try:
            payload = require_canonical_bytes(
                read_private_artifact(
                    self.state_path, label="installer budget workflow"
                )
            )
        except (IdentityPackLifecycleError, ValueError) as exc:
            raise InstallerBudgetWorkflowError(
                "installer budget workflow is invalid"
            ) from exc
        if set(payload) != {
            "budget",
            "budget_sha256",
            "contract",
            "events",
            "phase",
            "revision",
            "updated_at",
        } or payload.get("contract") != "EvaInstallerBudgetWorkflow/v1":
            raise InstallerBudgetWorkflowError("installer budget workflow is invalid")
        raw_budget = payload.get("budget")
        if not isinstance(raw_budget, dict) or raw_budget.get("contract") != "EvaInstallerBudget/v1":
            raise InstallerBudgetWorkflowError("installer budget is invalid")
        values = dict(raw_budget)
        values.pop("contract", None)
        if values.pop("unknown_cost_policy", None) != "block_autonomous_spend":
            raise InstallerBudgetWorkflowError("installer budget cost policy differs")
        budget = _budget(**values)
        digest = payload.get("budget_sha256")
        phase = payload.get("phase")
        revision = payload.get("revision")
        events = payload.get("events")
        if (
            not isinstance(digest, str)
            or _SHA256_RE.fullmatch(digest) is None
            or not hmac.compare_digest(digest, _budget_sha256(budget))
            or phase not in {"staged", "ready"}
            or revision not in {1, 2}
            or not isinstance(events, list)
            or len(events) != revision
        ):
            raise InstallerBudgetWorkflowError("installer budget state differs")
        transitions = ["staged"] if phase == "staged" else ["staged", "confirmed"]
        previous_at = ""
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict) or set(event) != {
                "at",
                "budget_sha256",
                "revision",
                "transition",
            }:
                raise InstallerBudgetWorkflowError("installer budget history is invalid")
            at = _timestamp(event.get("at"))
            if (
                event.get("revision") != index
                or event.get("transition") != transitions[index - 1]
                or event.get("budget_sha256") != digest
                or (previous_at and at < previous_at)
            ):
                raise InstallerBudgetWorkflowError("installer budget history is invalid")
            previous_at = at
        updated_at = _timestamp(payload.get("updated_at"))
        if updated_at != previous_at:
            raise InstallerBudgetWorkflowError("installer budget update differs")
        return InstallerBudgetStatus(
            phase=phase,
            revision=revision,
            budget_sha256=digest,
            budget=budget,
            updated_at=updated_at,
        )

    def _write(
        self,
        *,
        phase: Literal["staged", "ready"],
        budget: InstallerBudget,
        events: list[dict[str, object]],
    ) -> InstallerBudgetStatus:
        digest = _budget_sha256(budget)
        payload = {
            "budget": budget.as_dict(),
            "budget_sha256": digest,
            "contract": "EvaInstallerBudgetWorkflow/v1",
            "events": events,
            "phase": phase,
            "revision": len(events),
            "updated_at": events[-1]["at"],
        }
        try:
            atomic_write_private(self.state_path, canonical_json_bytes(payload))
        except IdentityPackLifecycleError as exc:
            raise InstallerBudgetWorkflowError(
                "installer budget workflow write failed"
            ) from exc
        return InstallerBudgetStatus(
            phase=phase,
            revision=len(events),
            budget_sha256=digest,
            budget=budget,
            updated_at=str(events[-1]["at"]),
        )

    def status(self) -> InstallerBudgetStatus:
        with self._lock():
            retained = self._load()
            return retained or InstallerBudgetStatus(
                phase="empty",
                revision=0,
                budget_sha256=None,
                budget=None,
                updated_at=None,
            )

    def stage(self, **values: object) -> InstallerBudgetTransition:
        if self.capability_workflow.status().step("budgets").state != "selected":
            raise InstallerBudgetWorkflowError(
                "installer budgets must be selected before staging"
            )
        budget = _budget(**values)
        digest = _budget_sha256(budget)
        with self._lock():
            retained = self._load()
            if retained is not None:
                if retained.budget_sha256 != digest:
                    raise InstallerBudgetWorkflowError(
                        "different installer budgets are already retained"
                    )
                return InstallerBudgetTransition(
                    phase=retained.phase,
                    revision=retained.revision,
                    budget_sha256=digest,
                    already_in_phase=True,
                )
            at = _timestamp(self.clock())
            written = self._write(
                phase="staged",
                budget=budget,
                events=[
                    {
                        "at": at,
                        "budget_sha256": digest,
                        "revision": 1,
                        "transition": "staged",
                    }
                ],
            )
            return InstallerBudgetTransition(
                phase="staged",
                revision=written.revision,
                budget_sha256=digest,
                already_in_phase=False,
            )

    def confirm(self, *, expected_budget_sha256: object) -> InstallerBudgetTransition:
        if not isinstance(expected_budget_sha256, str) or _SHA256_RE.fullmatch(
            expected_budget_sha256
        ) is None:
            raise InstallerBudgetWorkflowError("installer budget hash is invalid")
        with self._lock():
            retained = self._load()
            if retained is None or retained.budget is None or retained.budget_sha256 is None:
                raise InstallerBudgetWorkflowError("no installer budgets are staged")
            if not hmac.compare_digest(retained.budget_sha256, expected_budget_sha256):
                raise InstallerBudgetWorkflowError("installer budget hash differs")
            already_ready = retained.phase == "ready"
            if not already_ready:
                first_at = retained.updated_at
                assert first_at is not None
                at = _timestamp(self.clock())
                if at < first_at:
                    raise InstallerBudgetWorkflowError(
                        "budget confirmation precedes staging"
                    )
                retained = self._write(
                    phase="ready",
                    budget=retained.budget,
                    events=[
                        {
                            "at": first_at,
                            "budget_sha256": retained.budget_sha256,
                            "revision": 1,
                            "transition": "staged",
                        },
                        {
                            "at": at,
                            "budget_sha256": retained.budget_sha256,
                            "revision": 2,
                            "transition": "confirmed",
                        },
                    ],
                )
        try:
            self.capability_workflow.record_verification(
                step_id="budgets",
                outcome="ready",
                evidence_sha256=retained.budget_sha256,
            )
        except Exception as exc:
            raise InstallerBudgetWorkflowError(
                "installer budget capability could not be confirmed"
            ) from exc
        return InstallerBudgetTransition(
            phase="ready",
            revision=retained.revision,
            budget_sha256=retained.budget_sha256,
            already_in_phase=already_ready,
        )


__all__ = [
    "InstallerBudget",
    "InstallerBudgetInputError",
    "InstallerBudgetStatus",
    "InstallerBudgetTransition",
    "InstallerBudgetWorkflow",
    "InstallerBudgetWorkflowError",
]
