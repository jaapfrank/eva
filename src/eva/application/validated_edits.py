"""Connector-neutral contracts for one bounded, validated text edit."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FocusedAssertionResult:
    kind: str
    passed: bool
    value_sha256: str

    def __post_init__(self) -> None:
        if self.kind not in {"contains", "equals", "not_contains"}:
            raise ValueError("focused assertion kind is invalid")
        if len(self.value_sha256) != 64:
            raise ValueError("focused assertion hash is invalid")


@dataclass(frozen=True, slots=True)
class FocusedValidationResult:
    manifest_path: str
    manifest_sha256: str
    passed: bool
    assertions: tuple[FocusedAssertionResult, ...]

    def __post_init__(self) -> None:
        if not self.manifest_path or len(self.manifest_sha256) != 64:
            raise ValueError("focused validation identity is invalid")
        if not self.assertions:
            raise ValueError("focused validation has no assertions")
        if self.passed != all(item.passed for item in self.assertions):
            raise ValueError("focused validation summary differs")


@dataclass(frozen=True, slots=True)
class ValidatedTextEditPlan:
    target_path: str
    test_manifest_path: str
    original_content: str
    proposed_content: str
    manifest_sha256: str
    worktree_before_sha256: str
    unrelated_before_sha256: str
    baseline_validation: FocusedValidationResult

    def __post_init__(self) -> None:
        if not self.target_path or not self.test_manifest_path:
            raise ValueError("validated edit paths are required")
        for content in (self.original_content, self.proposed_content):
            if not content or len(content.encode("utf-8")) > 65_536:
                raise ValueError("validated edit content is invalid")
        if self.original_content == self.proposed_content:
            raise ValueError("validated edit must change content")
        if any(
            len(value) != 64
            for value in (
                self.manifest_sha256,
                self.worktree_before_sha256,
                self.unrelated_before_sha256,
            )
        ):
            raise ValueError("validated edit hash is invalid")
        if (
            not self.baseline_validation.passed
            or self.baseline_validation.manifest_path != self.test_manifest_path
            or self.baseline_validation.manifest_sha256 != self.manifest_sha256
        ):
            raise ValueError("validated edit baseline is not green")

    @property
    def original_sha256(self) -> str:
        return _sha256(self.original_content)

    @property
    def proposed_sha256(self) -> str:
        return _sha256(self.proposed_content)


@dataclass(frozen=True, slots=True)
class ValidatedTextEditResult:
    status: str
    target_path: str
    original_sha256: str
    proposed_sha256: str
    final_sha256: str
    patch_sha256: str
    worktree_before_sha256: str
    worktree_after_sha256: str
    unrelated_before_sha256: str
    unrelated_after_sha256: str
    validation_after_edit: FocusedValidationResult
    validation_after_repair: FocusedValidationResult | None = None

    def __post_init__(self) -> None:
        if self.status not in {"committed", "rolled_back"}:
            raise ValueError("validated edit status is invalid")
        hashes = (
            self.original_sha256,
            self.proposed_sha256,
            self.final_sha256,
            self.patch_sha256,
            self.worktree_before_sha256,
            self.worktree_after_sha256,
            self.unrelated_before_sha256,
            self.unrelated_after_sha256,
        )
        if any(len(value) != 64 for value in hashes):
            raise ValueError("validated edit result hash is invalid")
        if self.unrelated_before_sha256 != self.unrelated_after_sha256:
            raise ValueError("validated edit changed unrelated work")
        if self.status == "committed":
            if not self.validation_after_edit.passed or (
                self.validation_after_repair is not None
                or self.final_sha256 != self.proposed_sha256
            ):
                raise ValueError("committed validated edit is malformed")
        elif (
            self.validation_after_edit.passed
            or self.validation_after_repair is None
            or not self.validation_after_repair.passed
            or self.final_sha256 != self.original_sha256
            or self.worktree_after_sha256 != self.worktree_before_sha256
        ):
            raise ValueError("rolled-back validated edit is malformed")


class ValidatedTextEditPort(Protocol):
    def prepare(
        self,
        *,
        target_path: str,
        old_text: str,
        new_text: str,
        test_manifest_path: str,
    ) -> ValidatedTextEditPlan: ...

    def execute(self, plan: ValidatedTextEditPlan) -> ValidatedTextEditResult: ...
