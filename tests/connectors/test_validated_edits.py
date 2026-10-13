from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.adapters.connectors.validated_edits import LocalValidatedTextEditAdapter


def _workspace(root: Path, assertions: list[dict[str, str]]) -> None:
    project = root / "project"
    project.mkdir()
    (project / "settings.env").write_text("MODE=safe\nCOLOR=blue\n", encoding="utf-8")
    (project / "notes.txt").write_text("unrelated work\n", encoding="utf-8")
    (project / "focused-test.json").write_text(
        json.dumps(
            {
                "assertions": assertions,
                "contract": "EvaFocusedTextTest/v1",
                "target_path": "project/settings.env",
            }
        ),
        encoding="utf-8",
    )


def test_failed_focused_validation_rolls_back_only_the_target(tmp_path: Path) -> None:
    _workspace(tmp_path, [{"kind": "contains", "value": "MODE=safe"}])
    adapter = LocalValidatedTextEditAdapter(tmp_path)
    plan = adapter.prepare(
        target_path="project/settings.env",
        old_text="MODE=safe",
        new_text="MODE=turbo",
        test_manifest_path="project/focused-test.json",
    )

    result = adapter.execute(plan)

    assert result.status == "rolled_back"
    assert result.validation_after_edit.passed is False
    assert result.validation_after_repair is not None
    assert result.validation_after_repair.passed is True
    assert result.final_sha256 == result.original_sha256
    assert result.worktree_after_sha256 == result.worktree_before_sha256
    assert result.unrelated_after_sha256 == result.unrelated_before_sha256
    assert (tmp_path / "project/settings.env").read_text() == (
        "MODE=safe\nCOLOR=blue\n"
    )
    assert (tmp_path / "project/notes.txt").read_text() == "unrelated work\n"


def test_green_focused_validation_commits_one_edit(tmp_path: Path) -> None:
    _workspace(tmp_path, [{"kind": "contains", "value": "COLOR=green"}])
    (tmp_path / "project/settings.env").write_text(
        "MODE=safe\nCOLOR=green\n", encoding="utf-8"
    )
    adapter = LocalValidatedTextEditAdapter(tmp_path)
    plan = adapter.prepare(
        target_path="project/settings.env",
        old_text="MODE=safe",
        new_text="MODE=balanced",
        test_manifest_path="project/focused-test.json",
    )

    result = adapter.execute(plan)

    assert result.status == "committed"
    assert result.validation_after_edit.passed is True
    assert result.validation_after_repair is None
    assert result.final_sha256 == result.proposed_sha256
    assert result.unrelated_after_sha256 == result.unrelated_before_sha256
    assert "MODE=balanced" in (tmp_path / "project/settings.env").read_text()


def test_prepare_rejects_red_baseline_and_cross_worktree_test(tmp_path: Path) -> None:
    _workspace(tmp_path, [{"kind": "contains", "value": "MODE=missing"}])
    adapter = LocalValidatedTextEditAdapter(tmp_path)

    with pytest.raises(ValueError, match="not green"):
        adapter.prepare(
            target_path="project/settings.env",
            old_text="MODE=safe",
            new_text="MODE=balanced",
            test_manifest_path="project/focused-test.json",
        )

    other = tmp_path / "other"
    other.mkdir()
    (other / "test.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="share one worktree"):
        adapter.prepare(
            target_path="project/settings.env",
            old_text="MODE=safe",
            new_text="MODE=balanced",
            test_manifest_path="other/test.json",
        )


def test_execute_rejects_concurrent_unrelated_change(tmp_path: Path) -> None:
    _workspace(tmp_path, [{"kind": "contains", "value": "MODE=safe"}])
    adapter = LocalValidatedTextEditAdapter(tmp_path)
    plan = adapter.prepare(
        target_path="project/settings.env",
        old_text="MODE=safe",
        new_text="MODE=turbo",
        test_manifest_path="project/focused-test.json",
    )
    (tmp_path / "project/notes.txt").write_text("concurrent work\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unrelated worktree"):
        adapter.execute(plan)
