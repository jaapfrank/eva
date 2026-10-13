"""Bounded text-edit connector with declarative focused tests and rollback."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from eva.application.validated_edits import (
    FocusedAssertionResult,
    FocusedValidationResult,
    ValidatedTextEditPlan,
    ValidatedTextEditResult,
)
from eva.contracts.canonical_json import canonical_sha256


_RELATIVE_FILE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,239}")
_MANIFEST_KEYS = {"assertions", "contract", "target_path"}
_ASSERTION_KEYS = {"kind", "value"}


class LocalValidatedTextEditAdapter:
    """Edit one regular UTF-8 file and keep it only after focused validation."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("validated edit root must be an existing absolute directory")
        self._root = root.resolve(strict=True)

    def prepare(
        self,
        *,
        target_path: str,
        old_text: str,
        new_text: str,
        test_manifest_path: str,
    ) -> ValidatedTextEditPlan:
        target = self._file(target_path)
        manifest_path = self._file(test_manifest_path)
        if Path(target_path).parts[0] != Path(test_manifest_path).parts[0]:
            raise ValueError("validated edit and focused test must share one worktree")
        original = self._read_text(target, maximum_bytes=65_536)
        if (
            not old_text
            or len(old_text.encode("utf-8")) > 8_192
            or len(new_text.encode("utf-8")) > 8_192
            or original.count(old_text) != 1
        ):
            raise ValueError("validated edit replacement is not unique")
        proposed = original.replace(old_text, new_text, 1)
        if not proposed or len(proposed.encode("utf-8")) > 65_536:
            raise ValueError("validated edit proposed content is invalid")
        manifest_raw = manifest_path.read_bytes()
        manifest = self._manifest(manifest_raw, target_path)
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        baseline = self._validate(
            target_path, original, test_manifest_path, manifest_sha256, manifest
        )
        if not baseline.passed:
            raise ValueError("focused test is not green before the edit")
        project = self._project_root(target_path)
        worktree_before = self._worktree(project)
        unrelated_before = self._worktree(project, excluded=target)
        return ValidatedTextEditPlan(
            target_path=target_path,
            test_manifest_path=test_manifest_path,
            original_content=original,
            proposed_content=proposed,
            manifest_sha256=manifest_sha256,
            worktree_before_sha256=worktree_before,
            unrelated_before_sha256=unrelated_before,
            baseline_validation=baseline,
        )

    def execute(self, plan: ValidatedTextEditPlan) -> ValidatedTextEditResult:
        target = self._file(plan.target_path)
        manifest_path = self._file(plan.test_manifest_path)
        manifest_raw = manifest_path.read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest() != plan.manifest_sha256:
            raise ValueError("focused test changed after edit admission")
        manifest = self._manifest(manifest_raw, plan.target_path)
        current = self._read_text(target, maximum_bytes=65_536)
        if current == plan.original_content:
            self._atomic_write(target, plan.proposed_content)
        elif current != plan.proposed_content:
            raise ValueError("validated edit target changed after admission")
        after_edit = self._validate(
            plan.target_path,
            plan.proposed_content,
            plan.test_manifest_path,
            plan.manifest_sha256,
            manifest,
        )
        repaired: FocusedValidationResult | None = None
        status = "committed"
        if not after_edit.passed:
            self._atomic_write(target, plan.original_content)
            repaired = self._validate(
                plan.target_path,
                plan.original_content,
                plan.test_manifest_path,
                plan.manifest_sha256,
                manifest,
            )
            if not repaired.passed:
                raise RuntimeError("focused test did not recover after rollback")
            status = "rolled_back"
        final = self._read_text(target, maximum_bytes=65_536)
        project = self._project_root(plan.target_path)
        worktree_after = self._worktree(project)
        unrelated_after = self._worktree(project, excluded=target)
        if unrelated_after != plan.unrelated_before_sha256:
            raise RuntimeError("unrelated worktree content changed during validated edit")
        patch_sha256 = canonical_sha256(
            {
                "new_sha256": plan.proposed_sha256,
                "old_sha256": plan.original_sha256,
                "target_path": plan.target_path,
            }
        )
        return ValidatedTextEditResult(
            status=status,
            target_path=plan.target_path,
            original_sha256=plan.original_sha256,
            proposed_sha256=plan.proposed_sha256,
            final_sha256=hashlib.sha256(final.encode("utf-8")).hexdigest(),
            patch_sha256=patch_sha256,
            worktree_before_sha256=plan.worktree_before_sha256,
            worktree_after_sha256=worktree_after,
            unrelated_before_sha256=plan.unrelated_before_sha256,
            unrelated_after_sha256=unrelated_after,
            validation_after_edit=after_edit,
            validation_after_repair=repaired,
        )

    def _manifest(self, raw: bytes, target_path: str) -> dict[str, Any]:
        if not raw or len(raw) > 32_768:
            raise ValueError("focused test manifest is empty or too large")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("focused test manifest is invalid JSON") from error
        if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
            raise ValueError("focused test manifest shape is invalid")
        assertions = value.get("assertions")
        if (
            value.get("contract") != "EvaFocusedTextTest/v1"
            or value.get("target_path") != target_path
            or not isinstance(assertions, list)
            or not 1 <= len(assertions) <= 20
        ):
            raise ValueError("focused test manifest scope is invalid")
        for assertion in assertions:
            if not isinstance(assertion, dict) or set(assertion) != _ASSERTION_KEYS:
                raise ValueError("focused test assertion shape is invalid")
            kind = assertion.get("kind")
            expected = assertion.get("value")
            if (
                kind not in {"contains", "equals", "not_contains"}
                or not isinstance(expected, str)
                or not expected
                or len(expected.encode("utf-8")) > 8_192
            ):
                raise ValueError("focused test assertion is invalid")
        return value

    @staticmethod
    def _validate(
        target_path: str,
        content: str,
        manifest_path: str,
        manifest_sha256: str,
        manifest: dict[str, Any],
    ) -> FocusedValidationResult:
        results = []
        for assertion in manifest["assertions"]:
            kind = assertion["kind"]
            expected = assertion["value"]
            passed = (
                content == expected
                if kind == "equals"
                else (
                    expected in content
                    if kind == "contains"
                    else expected not in content
                )
            )
            results.append(
                FocusedAssertionResult(
                    kind=kind,
                    passed=passed,
                    value_sha256=hashlib.sha256(expected.encode("utf-8")).hexdigest(),
                )
            )
        return FocusedValidationResult(
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            passed=all(item.passed for item in results),
            assertions=tuple(results),
        )

    def _file(self, relative: str) -> Path:
        if (
            not relative
            or _RELATIVE_FILE.fullmatch(relative) is None
            or "//" in relative
            or any(part in {".", ".."} or part.startswith(".") for part in Path(relative).parts)
        ):
            raise ValueError("validated edit path is invalid")
        candidate = self._root.joinpath(*Path(relative).parts)
        parent = candidate.parent.resolve(strict=True)
        if not parent.is_relative_to(self._root):
            raise ValueError("validated edit path escapes its root")
        path = parent / candidate.name
        if path.is_symlink() or not path.is_file():
            raise ValueError("validated edit file is unavailable")
        return path

    def _project_root(self, target_path: str) -> Path:
        project = self._root / Path(target_path).parts[0]
        if project.is_symlink() or not project.is_dir():
            raise ValueError("validated edit worktree is unavailable")
        return project.resolve(strict=True)

    def _worktree(self, project: Path, *, excluded: Path | None = None) -> str:
        entries = tuple(project.rglob("*"))
        if any(path.is_symlink() for path in entries):
            raise ValueError("validated edit worktree contains a symlink")
        files = tuple(sorted(path for path in entries if path.is_file()))
        if len(files) > 128:
            raise ValueError("validated edit worktree has too many files")
        manifest: list[dict[str, str]] = []
        total = 0
        for path in files:
            if excluded is not None and path == excluded:
                continue
            raw = path.read_bytes()
            total += len(raw)
            if total > 2_097_152:
                raise ValueError("validated edit worktree is too large")
            manifest.append(
                {
                    "path": path.relative_to(project).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        return canonical_sha256(manifest)

    @staticmethod
    def _read_text(path: Path, *, maximum_bytes: int) -> str:
        raw = path.read_bytes()
        if not raw or len(raw) > maximum_bytes:
            raise ValueError("validated edit file is empty or too large")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("validated edit file is not UTF-8") from error

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".eva-edit-", suffix=".tmp", dir=path.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            if path.read_text(encoding="utf-8") != content:
                raise OSError("validated edit readback differs")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
