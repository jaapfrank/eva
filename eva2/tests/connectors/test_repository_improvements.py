from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from eva.adapters.connectors.repository_improvements import (
    LocalRepositoryImprovementAdapter,
)
from eva.application.repository_improvements import RepositoryImprovementProposal


NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def fixture_repository(root: Path) -> Path:
    project = root / "fixture-repo"
    project.mkdir()
    (project / "settings.toml").write_text(
        "retry_limit = 0\ntimeout_seconds = 10\n", encoding="utf-8"
    )
    (project / "README.md").write_text("# Fixture\n\nKleine service.\n", encoding="utf-8")
    (project / "unrelated.txt").write_text("bewaren\n", encoding="utf-8")
    (project / "eva-quality.json").write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "id": "bounded_retry",
                        "kind": "contains",
                        "target_path": "settings.toml",
                        "value": "retry_limit = 2",
                    },
                    {
                        "id": "timeout_preserved",
                        "kind": "contains",
                        "target_path": "settings.toml",
                        "value": "timeout_seconds = 10",
                    },
                    {
                        "id": "privacy_documented",
                        "kind": "contains",
                        "target_path": "README.md",
                        "value": "## Privacy",
                    },
                ],
                "contract": "EvaRepositoryQuality/v1",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return project


def test_self_chosen_repository_improvement_fixes_one_check_and_preserves_rest(
    tmp_path: Path,
) -> None:
    project = fixture_repository(tmp_path)
    original_mode = (project / "settings.toml").stat().st_mode & 0o777
    adapter = LocalRepositoryImprovementAdapter(tmp_path)
    snapshot = adapter.inspect(project_directory="fixture-repo", captured_at=NOW)

    assert {item.check_id for item in snapshot.failed_checks} == {
        "bounded_retry",
        "privacy_documented",
    }
    result = adapter.apply(
        snapshot,
        RepositoryImprovementProposal(
            check_id="bounded_retry",
            problem="Retries staan uit, waardoor een tijdelijke fout direct terminaal is.",
            evidence="settings.toml bevat retry_limit = 0 en de bounded_retry-check faalt.",
            target_path="settings.toml",
            old_text="retry_limit = 0",
            new_text="retry_limit = 2",
        ),
    )

    assert result.status == "committed"
    assert next(
        item for item in result.checks_after if item.check_id == "bounded_retry"
    ).passed
    assert next(
        item for item in result.checks_after if item.check_id == "timeout_preserved"
    ).passed
    assert (project / "unrelated.txt").read_text(encoding="utf-8") == "bewaren\n"
    assert (project / "settings.toml").stat().st_mode & 0o777 == original_mode
    assert result.unrelated_before_sha256 == result.unrelated_after_sha256

    replay = adapter.apply(
        snapshot,
        RepositoryImprovementProposal(
            check_id="bounded_retry",
            problem="Retries staan uit, waardoor een tijdelijke fout direct terminaal is.",
            evidence="settings.toml bevat retry_limit = 0 en de bounded_retry-check faalt.",
            target_path="settings.toml",
            old_text="retry_limit = 0",
            new_text="retry_limit = 2",
        ),
    )
    assert replay.patch_sha256 == result.patch_sha256
    assert replay.repository_after_sha256 == result.repository_after_sha256
