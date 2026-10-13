"""Contracts for one self-chosen, bounded repository improvement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol


_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELATIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,239}")


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    path: str
    content: str
    sha256: str

    def __post_init__(self) -> None:
        if (
            _RELATIVE.fullmatch(self.path) is None
            or not self.content
            or len(self.content.encode("utf-8")) > 65_536
            or _SHA256.fullmatch(self.sha256) is None
        ):
            raise ValueError("repository file is invalid")


@dataclass(frozen=True, slots=True)
class RepositoryCheck:
    check_id: str
    target_path: str
    kind: str
    value: str
    passed: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        if (
            not self.check_id.strip()
            or len(self.check_id) > 120
            or _RELATIVE.fullmatch(self.target_path) is None
            or self.kind not in {"contains", "equals", "not_contains"}
            or not self.value
            or len(self.value.encode("utf-8")) > 8_192
            or _SHA256.fullmatch(self.evidence_sha256) is None
        ):
            raise ValueError("repository check is invalid")


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    project_directory: str
    repository_sha256: str
    checks_manifest_sha256: str
    files: tuple[RepositoryFile, ...]
    checks: tuple[RepositoryCheck, ...]
    captured_at: datetime

    def __post_init__(self) -> None:
        if (
            _RELATIVE.fullmatch(self.project_directory) is None
            or _SHA256.fullmatch(self.repository_sha256) is None
            or _SHA256.fullmatch(self.checks_manifest_sha256) is None
            or not self.files
            or not self.checks
            or len({item.path for item in self.files}) != len(self.files)
            or len({item.check_id for item in self.checks}) != len(self.checks)
            or self.captured_at.tzinfo is None
        ):
            raise ValueError("repository snapshot is invalid")
        paths = {item.path for item in self.files}
        if any(item.target_path not in paths for item in self.checks):
            raise ValueError("repository check target is absent")

    @property
    def failed_checks(self) -> tuple[RepositoryCheck, ...]:
        return tuple(item for item in self.checks if not item.passed)


@dataclass(frozen=True, slots=True)
class RepositoryImprovementProposal:
    check_id: str
    problem: str
    evidence: str
    target_path: str
    old_text: str
    new_text: str

    def __post_init__(self) -> None:
        if (
            not self.check_id.strip()
            or not self.problem.strip()
            or not self.evidence.strip()
            or len(self.problem.encode("utf-8")) > 1_000
            or len(self.evidence.encode("utf-8")) > 2_000
            or _RELATIVE.fullmatch(self.target_path) is None
            or not self.old_text
            or self.old_text == self.new_text
            or max(
                len(self.old_text.encode("utf-8")),
                len(self.new_text.encode("utf-8")),
            )
            > 8_192
        ):
            raise ValueError("repository improvement proposal is invalid")


@dataclass(frozen=True, slots=True)
class RepositoryImprovementResult:
    status: str
    selected_check_id: str
    target_path: str
    repository_before_sha256: str
    repository_after_sha256: str
    unrelated_before_sha256: str
    unrelated_after_sha256: str
    original_sha256: str
    proposed_sha256: str
    final_sha256: str
    patch_sha256: str
    checks_before: tuple[RepositoryCheck, ...]
    checks_after: tuple[RepositoryCheck, ...]

    def __post_init__(self) -> None:
        hashes = (
            self.repository_before_sha256,
            self.repository_after_sha256,
            self.unrelated_before_sha256,
            self.unrelated_after_sha256,
            self.original_sha256,
            self.proposed_sha256,
            self.final_sha256,
            self.patch_sha256,
        )
        if (
            self.status not in {"committed", "rolled_back"}
            or not self.selected_check_id
            or _RELATIVE.fullmatch(self.target_path) is None
            or any(_SHA256.fullmatch(value) is None for value in hashes)
            or not self.checks_before
            or not self.checks_after
            or self.unrelated_before_sha256 != self.unrelated_after_sha256
        ):
            raise ValueError("repository improvement result is invalid")
        before = {item.check_id: item for item in self.checks_before}
        after = {item.check_id: item for item in self.checks_after}
        if set(before) != set(after) or self.selected_check_id not in before:
            raise ValueError("repository improvement check set differs")
        if before[self.selected_check_id].passed:
            raise ValueError("repository improvement selected no real problem")
        if any(not after[item.check_id].passed for item in self.checks_before if item.passed):
            raise ValueError("repository improvement regressed a green check")
        selected_fixed = after[self.selected_check_id].passed
        if self.status == "committed":
            if not selected_fixed or self.final_sha256 != self.proposed_sha256:
                raise ValueError("committed repository improvement is unproven")
        elif selected_fixed or self.final_sha256 != self.original_sha256:
            raise ValueError("rolled-back repository improvement is malformed")


class RepositoryImprovementPort(Protocol):
    def inspect(
        self, *, project_directory: str, captured_at: datetime
    ) -> RepositorySnapshot: ...

    def apply(
        self,
        snapshot: RepositorySnapshot,
        proposal: RepositoryImprovementProposal,
    ) -> RepositoryImprovementResult: ...


class RepositoryImprovementModelPort(Protocol):
    def choose_repository_improvement(
        self, snapshot: RepositorySnapshot
    ) -> RepositoryImprovementProposal: ...
