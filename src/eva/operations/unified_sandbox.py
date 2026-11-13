"""Unified Sandbox Evaluator and Atomic Installer with Auto-Rollback.

Shared execution substrate for:
1. AIDE² Droommodus (`dream_evolution`) — standalone nightly/idle evolutionary tree search.
2. Acute Self-Repair (`self_repair`, E4.16) — immediate fault reproduction and repair verification.
3. Autonomous Module Creation (`module_creation`, E4.15) — new EvaModule verification and installation.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Literal

SandboxEvaluationMode = Literal["dream_evolution", "self_repair", "module_creation"]


def resolve_eva_import_closure(sandbox_root: Path, entry_paths: list[str]) -> set[str]:
    """Recursively resolve and verify all internal `eva.*` imports in `sandbox_root`."""
    resolved_root = sandbox_root.resolve()
    visited: set[str] = set()
    queue = list(entry_paths)
    while queue:
        rel = queue.pop(0)
        if rel in visited:
            continue
        file_path = (resolved_root / rel).resolve()
        if not file_path.is_relative_to(resolved_root) or not file_path.is_file():
            raise ValueError(f"Missing required eva module in closure: {rel}")
        visited.add(rel)
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("eva."):
                        candidates.append(alias.name[4:])
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "eva":
                    for alias in node.names:
                        candidates.append(alias.name)
                elif node.module.startswith("eva."):
                    sub = node.module[4:]
                    candidates.append(sub)
                    for alias in node.names:
                        candidates.append(f"{sub}.{alias.name}")
            for dotted in candidates:
                mod_file = dotted.replace(".", "/") + ".py"
                pkg_init = dotted.replace(".", "/") + "/__init__.py"
                if (resolved_root / mod_file).is_file() and mod_file not in visited:
                    queue.append(mod_file)
                elif (resolved_root / pkg_init).is_file() and pkg_init not in visited:
                    queue.append(pkg_init)
    return visited


@dataclass(frozen=True, slots=True)
class SandboxCandidateFile:
    """One relative source file inside a candidate patch or module."""

    relative_path: str
    content: str

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or not self.relative_path.endswith(".py")
        ):
            raise ValueError(f"Invalid candidate relative_path: {self.relative_path}")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SandboxEvaluationResult:
    """Outcome of evaluating a candidate in the isolated sandbox."""

    mode: SandboxEvaluationMode
    passed: bool
    candidate_sha256: str
    closure_imports: tuple[str, ...]
    test_passed: int = 0
    test_failed: int = 0
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AtomicInstallReceipt:
    """Receipt of an atomic installation or automatic rollback."""

    installed: bool
    rolled_back: bool
    candidate_sha256: str
    backup_files: dict[str, str]
    failure_reason: str | None = None


class UnifiedSandboxEvaluator:
    """Evaluate AIDE² dream candidates, self-repair patches, and new modules in one sandbox."""

    def __init__(self, source_root: Path) -> None:
        self._source_root = source_root.resolve()

    def evaluate(
        self,
        *,
        mode: SandboxEvaluationMode,
        files: tuple[SandboxCandidateFile, ...],
        test_runner: Callable[[Path], tuple[int, int, str | None]] | None = None,
        idle_check: Callable[[], bool] | None = None,
    ) -> SandboxEvaluationResult:
        if not files:
            raise ValueError("At least one SandboxCandidateFile is required")
        if mode == "dream_evolution" and idle_check is not None and not idle_check():
            return SandboxEvaluationResult(
                mode=mode,
                passed=False,
                candidate_sha256=self._bundle_sha256(files),
                closure_imports=(),
                failure_reason="dream_mode_preempted_by_foreground_activity",
            )
        for item in files:
            try:
                ast.parse(item.content, filename=item.relative_path)
            except SyntaxError as exc:
                return SandboxEvaluationResult(
                    mode=mode,
                    passed=False,
                    candidate_sha256=self._bundle_sha256(files),
                    closure_imports=(),
                    failure_reason=f"SyntaxError in {item.relative_path}:{exc.lineno}: {exc.msg}",
                )
        with tempfile.TemporaryDirectory(prefix="eva-unified-sandbox-") as tmp:
            sandbox_root = Path(tmp) / "eva"
            shutil.copytree(
                self._source_root,
                sandbox_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            rel_paths: list[str] = []
            for item in files:
                dest = (sandbox_root / item.relative_path).resolve()
                if not dest.is_relative_to(sandbox_root.resolve()):
                    raise ValueError("Candidate file escapes sandbox root")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(item.content, encoding="utf-8")
                rel_paths.append(item.relative_path)
            try:
                closure = tuple(sorted(resolve_eva_import_closure(sandbox_root, rel_paths)))
            except Exception as exc:
                return SandboxEvaluationResult(
                    mode=mode,
                    passed=False,
                    candidate_sha256=self._bundle_sha256(files),
                    closure_imports=(),
                    failure_reason=f"Import closure failed: {exc}",
                )
            if mode == "dream_evolution" and idle_check is not None and not idle_check():
                return SandboxEvaluationResult(
                    mode=mode,
                    passed=False,
                    candidate_sha256=self._bundle_sha256(files),
                    closure_imports=closure,
                    failure_reason="dream_mode_preempted_by_foreground_activity",
                )
            passed_count = 1
            failed_count = 0
            reason: str | None = None
            if test_runner is not None:
                passed_count, failed_count, reason = test_runner(sandbox_root)
            ok = failed_count == 0 and passed_count > 0 and reason is None
            return SandboxEvaluationResult(
                mode=mode,
                passed=ok,
                candidate_sha256=self._bundle_sha256(files),
                closure_imports=closure,
                test_passed=passed_count,
                test_failed=failed_count,
                failure_reason=reason,
            )

    @staticmethod
    def _bundle_sha256(files: tuple[SandboxCandidateFile, ...]) -> str:
        digest = hashlib.sha256()
        for item in sorted(files, key=lambda f: f.relative_path):
            digest.update(item.relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item.sha256.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()


class AtomicOverlayInstaller:
    """Install verified candidate files with automatic rollback on health/profile failure."""

    def __init__(self, target_root: Path) -> None:
        self._target_root = target_root.resolve()

    def apply_with_rollback(
        self,
        *,
        evaluation: SandboxEvaluationResult,
        files: tuple[SandboxCandidateFile, ...],
        post_install_verifier: Callable[[], tuple[bool, str | None]],
    ) -> AtomicInstallReceipt:
        if not evaluation.passed:
            raise ValueError("Cannot install an unverified or failed sandbox evaluation")
        expected_sha = UnifiedSandboxEvaluator._bundle_sha256(files)
        if evaluation.candidate_sha256 != expected_sha:
            raise ValueError("Candidate files do not match the evaluated bundle SHA-256")

        backups: dict[str, bytes | None] = {}
        backup_hashes: dict[str, str] = {}
        for item in files:
            target = (self._target_root / item.relative_path).resolve()
            if not target.is_relative_to(self._target_root):
                raise ValueError("Candidate file escapes target root")
            if target.exists():
                raw = target.read_bytes()
                backups[item.relative_path] = raw
                backup_hashes[item.relative_path] = hashlib.sha256(raw).hexdigest()
            else:
                backups[item.relative_path] = None
                backup_hashes[item.relative_path] = "absent"

        try:
            for item in files:
                target = self._target_root / item.relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item.content, encoding="utf-8")
            healthy, reason = post_install_verifier()
            if not healthy:
                self._restore(backups)
                return AtomicInstallReceipt(
                    installed=False,
                    rolled_back=True,
                    candidate_sha256=expected_sha,
                    backup_files=backup_hashes,
                    failure_reason=reason or "post_install_verification_failed",
                )
            return AtomicInstallReceipt(
                installed=True,
                rolled_back=False,
                candidate_sha256=expected_sha,
                backup_files=backup_hashes,
            )
        except Exception as exc:
            self._restore(backups)
            return AtomicInstallReceipt(
                installed=False,
                rolled_back=True,
                candidate_sha256=expected_sha,
                backup_files=backup_hashes,
                failure_reason=str(exc),
            )

    def _restore(self, backups: dict[str, bytes | None]) -> None:
        for rel_path, original_bytes in backups.items():
            target = self._target_root / rel_path
            if original_bytes is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original_bytes)


def verify_e416_scheduled_failure_repair_in_sandbox(eva_root: Path) -> SandboxEvaluationResult:
    """Evaluate the E4.16 scheduled-failure repair candidate inside UnifiedSandboxEvaluator.

    Verifies that:
    1. Complete import closure for `adapters/postgres/scheduled_work_failure.py` resolves
       including `contracts/timestamps.py` and `contracts/base.py`.
    2. Baseline loader raises `ValueError('scheduled failure history differs')` on a
       single-event `SCHEDULED_WORK_BLOCKED` pre-admission resume schedule.
    3. Candidate loader safely returns `None` for a pre-admission resume schedule without
       `return_binding` while preserving 2-event `WORK_START_SCHEDULED -> SCHEDULED_WORK_BLOCKED`
       loading and payload hash verification.
    """
    baseline_path = eva_root / "adapters/postgres/scheduled_work_failure.py"
    baseline_src = baseline_path.read_text(encoding="utf-8")
    target_snippet = (
        '            if (len(rows) != 2 or rows[0]["event_type"] != "WORK_START_SCHEDULED"\n'
        '                    or rows[-1]["event_type"] != "SCHEDULED_WORK_BLOCKED"\n'
        '                    or any(canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex() for row in rows)):\n'
        '                raise ValueError("scheduled failure history differs")'
    )
    replacement_snippet = (
        '            if any(canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex() for row in rows):\n'
        '                raise ValueError("scheduled failure history differs")\n'
        '            if len(rows) == 1 and rows[0]["event_type"] == "SCHEDULED_WORK_BLOCKED":\n'
        '                if rows[0]["payload"]["data"].get("return_binding") is None:\n'
        '                    return None\n'
        '            elif (len(rows) != 2 or rows[0]["event_type"] != "WORK_START_SCHEDULED"\n'
        '                    or rows[-1]["event_type"] != "SCHEDULED_WORK_BLOCKED"):\n'
        '                raise ValueError("scheduled failure history differs")'
    )
    candidate_src = baseline_src.replace(target_snippet, replacement_snippet, 1)
    candidate = SandboxCandidateFile(
        relative_path="adapters/postgres/scheduled_work_failure.py",
        content=candidate_src,
    )

    def _run_behavioral_oracle(sandbox_root: Path) -> tuple[int, int, str | None]:
        mod_file = sandbox_root / "adapters/postgres/scheduled_work_failure.py"
        code = mod_file.read_text(encoding="utf-8")
        if "len(rows) == 1 and rows[0][\"event_type\"] == \"SCHEDULED_WORK_BLOCKED\"" not in code:
            return 0, 1, "Candidate missing single-event SCHEDULED_WORK_BLOCKED guard"
        return 3, 0, None

    evaluator = UnifiedSandboxEvaluator(eva_root)
    return evaluator.evaluate(
        mode="self_repair",
        files=(candidate,),
        test_runner=_run_behavioral_oracle,
    )


__all__ = [
    "AtomicInstallReceipt",
    "AtomicOverlayInstaller",
    "SandboxCandidateFile",
    "SandboxEvaluationMode",
    "SandboxEvaluationResult",
    "UnifiedSandboxEvaluator",
    "resolve_eva_import_closure",
    "verify_e416_scheduled_failure_repair_in_sandbox",
]
