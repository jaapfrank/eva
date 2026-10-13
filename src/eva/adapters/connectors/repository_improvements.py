"""Safe local adapter for one evidence-selected repository improvement."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from eva.application.repository_improvements import (
    RepositoryCheck,
    RepositoryFile,
    RepositoryImprovementProposal,
    RepositoryImprovementResult,
    RepositorySnapshot,
)
from eva.contracts.canonical_json import canonical_sha256


_RELATIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,239}")
_MANIFEST = "eva-quality.json"
_MANIFEST_KEYS = {"checks", "contract"}
_CHECK_KEYS = {"id", "kind", "target_path", "value"}


class LocalRepositoryImprovementAdapter:
    """Inspect bounded text repositories and preserve only proven patches."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("repository improvement root must be an existing directory")
        self._root = root.resolve(strict=True)

    def inspect(
        self, *, project_directory: str, captured_at: datetime
    ) -> RepositorySnapshot:
        project = self._project(project_directory)
        paths = tuple(sorted(path for path in project.rglob("*") if path.is_file()))
        if not paths or len(paths) > 64 or any(path.is_symlink() for path in project.rglob("*")):
            raise ValueError("repository improvement worktree is invalid")
        files: list[RepositoryFile] = []
        total = 0
        for path in paths:
            raw = path.read_bytes()
            total += len(raw)
            if not raw or len(raw) > 65_536 or total > 524_288:
                raise ValueError("repository improvement worktree is too large")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("repository improvement worktree is not UTF-8") from error
            files.append(
                RepositoryFile(
                    path=path.relative_to(project).as_posix(),
                    content=content,
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            )
        by_path = {item.path: item for item in files}
        manifest_file = by_path.get(_MANIFEST)
        if manifest_file is None:
            raise ValueError("repository quality manifest is absent")
        manifest = self._manifest(manifest_file.content, set(by_path))
        checks = tuple(
            self._check(item, by_path[str(item["target_path"])].content)
            for item in manifest["checks"]
        )
        repository_sha256 = canonical_sha256(
            [{"path": item.path, "sha256": item.sha256} for item in files]
        )
        return RepositorySnapshot(
            project_directory=project_directory,
            repository_sha256=repository_sha256,
            checks_manifest_sha256=manifest_file.sha256,
            files=tuple(files),
            checks=checks,
            captured_at=captured_at,
        )

    def apply(
        self,
        snapshot: RepositorySnapshot,
        proposal: RepositoryImprovementProposal,
    ) -> RepositoryImprovementResult:
        selected = next(
            (item for item in snapshot.checks if item.check_id == proposal.check_id),
            None,
        )
        if selected is None or selected.passed or selected.target_path != proposal.target_path:
            raise ValueError("repository improvement did not select a failed check")
        project = self._project(snapshot.project_directory)
        target = self._file(project, proposal.target_path)
        original_file = next(
            (item for item in snapshot.files if item.path == proposal.target_path), None
        )
        if original_file is None or original_file.content.count(proposal.old_text) != 1:
            raise ValueError("repository improvement replacement is not unique")
        proposed_content = original_file.content.replace(
            proposal.old_text, proposal.new_text, 1
        )
        if not proposed_content or len(proposed_content.encode("utf-8")) > 65_536:
            raise ValueError("repository improvement proposed content is invalid")
        current = target.read_text(encoding="utf-8")
        current_snapshot = self.inspect(
            project_directory=snapshot.project_directory,
            captured_at=snapshot.captured_at,
        )
        if current == original_file.content:
            if current_snapshot.repository_sha256 != snapshot.repository_sha256:
                raise ValueError("repository changed after improvement snapshot")
            self._atomic_write(target, proposed_content)
        elif current != proposed_content:
            raise ValueError("repository target changed after improvement snapshot")

        after = self.inspect(
            project_directory=snapshot.project_directory,
            captured_at=snapshot.captured_at,
        )
        after_by_id = {item.check_id: item for item in after.checks}
        selected_fixed = after_by_id[proposal.check_id].passed
        green_preserved = all(
            after_by_id[item.check_id].passed for item in snapshot.checks if item.passed
        )
        status = "committed"
        final_snapshot = after
        if not selected_fixed or not green_preserved:
            self._atomic_write(target, original_file.content)
            restored = self.inspect(
                project_directory=snapshot.project_directory,
                captured_at=snapshot.captured_at,
            )
            if restored.repository_sha256 != snapshot.repository_sha256:
                raise RuntimeError("repository improvement rollback did not restore snapshot")
            status = "rolled_back"
            final_snapshot = restored

        final_file = next(
            item for item in final_snapshot.files if item.path == proposal.target_path
        )
        unrelated_before = self._unrelated_sha256(snapshot, proposal.target_path)
        unrelated_after = self._unrelated_sha256(final_snapshot, proposal.target_path)
        if unrelated_before != unrelated_after:
            raise RuntimeError("repository improvement changed unrelated work")
        proposed_sha256 = hashlib.sha256(proposed_content.encode("utf-8")).hexdigest()
        patch_sha256 = canonical_sha256(
            {
                "new_sha256": proposed_sha256,
                "old_sha256": original_file.sha256,
                "selected_check_id": proposal.check_id,
                "target_path": proposal.target_path,
            }
        )
        return RepositoryImprovementResult(
            status=status,
            selected_check_id=proposal.check_id,
            target_path=proposal.target_path,
            repository_before_sha256=snapshot.repository_sha256,
            repository_after_sha256=final_snapshot.repository_sha256,
            unrelated_before_sha256=unrelated_before,
            unrelated_after_sha256=unrelated_after,
            original_sha256=original_file.sha256,
            proposed_sha256=proposed_sha256,
            final_sha256=final_file.sha256,
            patch_sha256=patch_sha256,
            checks_before=snapshot.checks,
            checks_after=final_snapshot.checks,
        )

    def _project(self, relative: str) -> Path:
        if (
            _RELATIVE.fullmatch(relative) is None
            or "//" in relative
            or any(part in {".", ".."} or part.startswith(".") for part in Path(relative).parts)
        ):
            raise ValueError("repository improvement project path is invalid")
        candidate = self._root.joinpath(*Path(relative).parts)
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("repository improvement project is unavailable")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self._root):
            raise ValueError("repository improvement project escapes root")
        return resolved

    @staticmethod
    def _file(project: Path, relative: str) -> Path:
        candidate = project.joinpath(*Path(relative).parts)
        parent = candidate.parent.resolve(strict=True)
        if not parent.is_relative_to(project):
            raise ValueError("repository improvement target escapes project")
        target = parent / candidate.name
        if target.is_symlink() or not target.is_file():
            raise ValueError("repository improvement target is unavailable")
        return target

    @staticmethod
    def _manifest(content: str, paths: set[str]) -> dict[str, Any]:
        try:
            manifest = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError("repository quality manifest is invalid JSON") from error
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
            raise ValueError("repository quality manifest shape is invalid")
        checks = manifest.get("checks")
        if (
            manifest.get("contract") != "EvaRepositoryQuality/v1"
            or not isinstance(checks, list)
            or not 1 <= len(checks) <= 20
        ):
            raise ValueError("repository quality manifest scope is invalid")
        ids: set[str] = set()
        for check in checks:
            if not isinstance(check, dict) or set(check) != _CHECK_KEYS:
                raise ValueError("repository quality check shape is invalid")
            check_id = check.get("id")
            target_path = check.get("target_path")
            kind = check.get("kind")
            value = check.get("value")
            if (
                not isinstance(check_id, str)
                or not check_id
                or check_id in ids
                or not isinstance(target_path, str)
                or target_path not in paths
                or target_path == _MANIFEST
                or kind not in {"contains", "equals", "not_contains"}
                or not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 8_192
            ):
                raise ValueError("repository quality check is invalid")
            ids.add(check_id)
        return manifest

    @staticmethod
    def _check(raw: dict[str, Any], content: str) -> RepositoryCheck:
        kind = str(raw["kind"])
        value = str(raw["value"])
        passed = (
            content == value
            if kind == "equals"
            else value in content if kind == "contains" else value not in content
        )
        return RepositoryCheck(
            check_id=str(raw["id"]),
            target_path=str(raw["target_path"]),
            kind=kind,
            value=value,
            passed=passed,
            evidence_sha256=canonical_sha256(
                {
                    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "kind": kind,
                    "passed": passed,
                    "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                }
            ),
        )

    @staticmethod
    def _unrelated_sha256(snapshot: RepositorySnapshot, target_path: str) -> str:
        return canonical_sha256(
            [
                {"path": item.path, "sha256": item.sha256}
                for item in snapshot.files
                if item.path != target_path
            ]
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary: str | None = None
        original_mode = path.stat().st_mode & 0o777
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".eva-repo-improvement-", suffix=".tmp", dir=path.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), original_mode)
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
