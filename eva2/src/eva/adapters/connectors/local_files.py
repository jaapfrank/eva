"""Bounded local-workspace connector with independent output readback."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

from eva.application.file_batches import (
    BatchFilePlan,
    BatchFileResult,
    TextBatchPlan,
    TextBatchResult,
)


_RELATIVE_DIRECTORY = re.compile(r"[a-z0-9][a-z0-9._/-]{0,199}")


class LocalMarkdownBatchAdapter:
    """Convert bounded UTF-8 text trees to Markdown inside one mounted root."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("file batch root must be an existing absolute directory")
        self._root = root.resolve(strict=True)

    def prepare(
        self, *, source_directory: str, destination_directory: str
    ) -> TextBatchPlan:
        source = self._directory(source_directory)
        destination = self._directory(destination_directory)
        if source == destination:
            raise ValueError("batch source and destination must differ")
        entries = tuple(source.rglob("*"))
        if any(path.is_symlink() for path in entries):
            raise ValueError("batch source contains an unsafe path")
        files = tuple(sorted(path for path in entries if path.suffix == ".txt"))
        if not 1 <= len(files) <= 20:
            raise ValueError("batch source must contain 1..20 text files")
        planned: list[BatchFilePlan] = []
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError("batch source contains an unsafe file")
            relative = path.relative_to(source)
            if any(part.startswith(".") for part in relative.parts):
                raise ValueError("batch source contains a hidden path")
            raw = path.read_bytes()
            if not raw or len(raw) > 32_768:
                raise ValueError("batch source file is empty or too large")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("batch source is not UTF-8") from error
            normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
            title = relative.stem.replace("_", " ").replace("-", " ").strip().title()
            output = f"# {title}\n\n{normalized}\n"
            target = relative.with_suffix(".md")
            planned.append(
                BatchFilePlan(
                    relative.as_posix(),
                    hashlib.sha256(raw).hexdigest(),
                    target.as_posix(),
                    output,
                )
            )
        return TextBatchPlan(source_directory, destination_directory, tuple(planned))

    def execute(self, plan: TextBatchPlan) -> TextBatchResult:
        source = self._directory(plan.source_directory)
        destination = self._directory(plan.destination_directory)
        results: list[BatchFileResult] = []
        for item in plan.files:
            source_path = self._child(source, item.source_path)
            target_path = self._child(destination, item.destination_path)
            if (
                not source_path.is_file()
                or source_path.is_symlink()
                or hashlib.sha256(source_path.read_bytes()).hexdigest()
                != item.source_sha256
            ):
                results.append(
                    BatchFileResult(
                        item.source_path,
                        item.destination_path,
                        "failed",
                        error_code="source_changed",
                    )
                )
                continue
            expected = item.output.encode("utf-8")
            if target_path.exists():
                if target_path.is_file() and not target_path.is_symlink() and (
                    hashlib.sha256(target_path.read_bytes()).hexdigest()
                    == item.output_sha256
                ):
                    results.append(
                        BatchFileResult(
                            item.source_path,
                            item.destination_path,
                            "already_verified",
                            output_sha256=item.output_sha256,
                        )
                    )
                else:
                    results.append(
                        BatchFileResult(
                            item.source_path,
                            item.destination_path,
                            "failed",
                            error_code="destination_conflict",
                        )
                    )
                continue
            parent = target_path.parent
            if not parent.is_dir() or parent.is_symlink():
                results.append(
                    BatchFileResult(
                        item.source_path,
                        item.destination_path,
                        "failed",
                        error_code="destination_unavailable",
                    )
                )
                continue
            temporary: str | None = None
            try:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".eva-batch-", suffix=".tmp", dir=parent
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(expected)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target_path)
                temporary = None
                actual = hashlib.sha256(target_path.read_bytes()).hexdigest()
                if actual != item.output_sha256:
                    raise OSError("batch output readback differs")
                results.append(
                    BatchFileResult(
                        item.source_path,
                        item.destination_path,
                        "written",
                        output_sha256=actual,
                    )
                )
            except PermissionError:
                results.append(
                    BatchFileResult(
                        item.source_path,
                        item.destination_path,
                        "failed",
                        error_code="destination_unwritable",
                    )
                )
            except OSError:
                results.append(
                    BatchFileResult(
                        item.source_path,
                        item.destination_path,
                        "failed",
                        error_code="write_or_readback_failed",
                    )
                )
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
        return TextBatchResult(tuple(results))

    def _directory(self, relative: str) -> Path:
        if (
            not relative
            or _RELATIVE_DIRECTORY.fullmatch(relative) is None
            or "//" in relative
            or any(part in {".", ".."} for part in Path(relative).parts)
        ):
            raise ValueError("batch directory is invalid")
        path = self._child(self._root, relative)
        if not path.is_dir() or path.is_symlink():
            raise ValueError("batch directory is unavailable")
        return path

    def _child(self, parent: Path, relative: str) -> Path:
        candidate = parent.joinpath(*Path(relative).parts)
        resolved_parent = candidate.parent.resolve(strict=True)
        if not resolved_parent.is_relative_to(self._root):
            raise ValueError("batch path escapes its root")
        return resolved_parent / candidate.name
