"""Connector-neutral contracts for bounded text-to-Markdown batch work."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BatchFilePlan:
    source_path: str
    source_sha256: str
    destination_path: str
    output: str

    def __post_init__(self) -> None:
        if not self.source_path or not self.destination_path:
            raise ValueError("batch file paths are required")
        if len(self.source_sha256) != 64:
            raise ValueError("batch source hash is invalid")
        if not self.output or len(self.output.encode("utf-8")) > 65_536:
            raise ValueError("batch output is invalid")

    @property
    def output_sha256(self) -> str:
        return hashlib.sha256(self.output.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TextBatchPlan:
    source_directory: str
    destination_directory: str
    files: tuple[BatchFilePlan, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.files) <= 20:
            raise ValueError("text batch must contain 1..20 files")
        if len({item.destination_path for item in self.files}) != len(self.files):
            raise ValueError("text batch destinations are not unique")


@dataclass(frozen=True, slots=True)
class BatchFileResult:
    source_path: str
    destination_path: str
    status: str
    output_sha256: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"written", "already_verified", "failed"}:
            raise ValueError("batch result status is invalid")
        if self.status == "failed":
            if self.output_sha256 is not None or not self.error_code:
                raise ValueError("failed batch result is malformed")
        elif self.error_code is not None or not self.output_sha256:
            raise ValueError("successful batch result is malformed")


@dataclass(frozen=True, slots=True)
class TextBatchResult:
    files: tuple[BatchFileResult, ...]

    @property
    def successful(self) -> tuple[BatchFileResult, ...]:
        return tuple(item for item in self.files if item.status != "failed")

    @property
    def failed(self) -> tuple[BatchFileResult, ...]:
        return tuple(item for item in self.files if item.status == "failed")

    @property
    def retryable_failed(self) -> tuple[BatchFileResult, ...]:
        retryable = {
            "destination_unavailable",
            "destination_unwritable",
            "write_or_readback_failed",
        }
        return tuple(item for item in self.failed if item.error_code in retryable)


class TextBatchPort(Protocol):
    def prepare(
        self, *, source_directory: str, destination_directory: str
    ) -> TextBatchPlan: ...

    def execute(self, plan: TextBatchPlan) -> TextBatchResult: ...
