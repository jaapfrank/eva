from __future__ import annotations

from pathlib import Path
import pytest

from eva.operations.unified_sandbox import (
    AtomicOverlayInstaller,
    SandboxCandidateFile,
    UnifiedSandboxEvaluator,
    resolve_eva_import_closure,
    verify_e416_scheduled_failure_repair_in_sandbox,
)


def test_unified_sandbox_evaluates_closure_and_preempts_dream_mode_when_busy(tmp_path: Path) -> None:
    eva_root = tmp_path / "eva"
    (eva_root / "contracts").mkdir(parents=True)
    (eva_root / "contracts" / "__init__.py").write_text("", encoding="utf-8")
    (eva_root / "contracts" / "base.py").write_text("VALUE = 1\n", encoding="utf-8")
    (eva_root / "application").mkdir(parents=True)
    (eva_root / "application" / "__init__.py").write_text("", encoding="utf-8")

    evaluator = UnifiedSandboxEvaluator(eva_root)
    candidate = SandboxCandidateFile(
        relative_path="application/sample_feature.py",
        content="from eva.contracts.base import VALUE\nRESULT = VALUE + 1\n",
    )

    # Dream mode yields immediately when foreground activity is detected
    preempted = evaluator.evaluate(
        mode="dream_evolution",
        files=(candidate,),
        idle_check=lambda: False,
    )
    assert preempted.passed is False
    assert preempted.failure_reason == "dream_mode_preempted_by_foreground_activity"

    # Self-repair and module creation evaluate closure and run sandbox tests regardless of idle state
    res = evaluator.evaluate(
        mode="self_repair",
        files=(candidate,),
        test_runner=lambda root: (3, 0, None),
    )
    assert res.passed is True
    assert "contracts/base.py" in res.closure_imports
    assert res.test_passed == 3


def test_atomic_overlay_installer_rolls_back_automatically_on_failed_post_install_check(tmp_path: Path) -> None:
    eva_root = tmp_path / "eva"
    (eva_root / "application").mkdir(parents=True)
    target_file = eva_root / "application" / "existing.py"
    target_file.write_text("STATE = 'original'\n", encoding="utf-8")

    evaluator = UnifiedSandboxEvaluator(eva_root)
    candidate = SandboxCandidateFile(
        relative_path="application/existing.py",
        content="STATE = 'candidate'\n",
    )
    evaluation = evaluator.evaluate(mode="self_repair", files=(candidate,))
    assert evaluation.passed is True

    installer = AtomicOverlayInstaller(eva_root)
    failed_receipt = installer.apply_with_rollback(
        evaluation=evaluation,
        files=(candidate,),
        post_install_verifier=lambda: (False, "profile_mismatch"),
    )
    assert failed_receipt.installed is False
    assert failed_receipt.rolled_back is True
    assert failed_receipt.failure_reason == "profile_mismatch"
    assert target_file.read_text(encoding="utf-8") == "STATE = 'original'\n"

    ok_receipt = installer.apply_with_rollback(
        evaluation=evaluation,
        files=(candidate,),
        post_install_verifier=lambda: (True, None),
    )
    assert ok_receipt.installed is True
    assert ok_receipt.rolled_back is False
    assert target_file.read_text(encoding="utf-8") == "STATE = 'candidate'\n"


def test_resolve_eva_import_closure_covers_e416_scheduled_work_failure_dependencies() -> None:
    repo_eva_root = Path(__file__).resolve().parents[3] / "src" / "eva"
    closure = resolve_eva_import_closure(
        repo_eva_root,
        ("adapters/postgres/scheduled_work_failure.py",),
    )
    assert "adapters/postgres/scheduled_work_failure.py" in closure
    assert "adapters/postgres/operational_timeline.py" in closure
    assert "contracts/timestamps.py" in closure
    assert "contracts/canonical_json.py" in closure
    assert "contracts/base.py" in closure

    res = verify_e416_scheduled_failure_repair_in_sandbox(repo_eva_root)
    assert res.passed is True
    assert res.test_passed == 3
    assert "contracts/timestamps.py" in res.closure_imports
    assert "contracts/base.py" in res.closure_imports
