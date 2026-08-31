from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_dependency_update_posture import validate_release_watch_receipt


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = ROOT.parent


def test_renovate_config_is_proposal_only_and_content_addressed() -> None:
    config = json.loads((REPOSITORY_ROOT / "renovate.json").read_bytes())

    assert config["dependencyDashboard"] is True
    assert config["dependencyDashboardApproval"] is True
    assert config["prConcurrentLimit"] == 2
    assert config["prHourlyLimit"] == 1
    assert config["minimumReleaseAge"] == "7 days"
    assert all(rule.get("automerge") is not True for rule in config["packageRules"])
    assert any(
        rule.get("matchDatasources") == ["docker"] and rule.get("pinDigests") is True
        for rule in config["packageRules"]
    )
    assert any(
        manager.get("datasourceTemplate") == "github-releases"
        for manager in config["customManagers"]
    )


def test_component_watch_includes_all_comparison_targets() -> None:
    watch = json.loads((REPOSITORY_ROOT / "component-watch.json").read_bytes())

    assert {row["repository"] for row in watch["competitors"]} == {
        "openclaw/openclaw",
        "NousResearch/hermes-agent",
        "agent0ai/agent-zero",
    }
    assert all(row["currentRelease"] for row in watch["components"])
    assert watch["checkedAt"] < watch["reviewValidUntil"]


def _watch() -> dict[str, object]:
    return {
        "contract": "Eva2UpstreamReleaseWatch/v1",
        "checkedAt": "2026-08-30T12:00:00+02:00",
        "reviewValidUntil": "2026-09-29T12:00:00+02:00",
        "components": [
            {"repository": "example/component", "currentRelease": "v2"},
        ],
        "competitors": [
            {"repository": "openclaw/openclaw", "currentRelease": "v1"},
            {"repository": "NousResearch/hermes-agent", "currentRelease": "v2"},
            {"repository": "agent0ai/agent-zero", "currentRelease": "v3"},
        ],
    }


def _receipt(watch_raw: bytes, watch: dict[str, object]) -> dict[str, object]:
    def rows(key: str) -> list[dict[str, str]]:
        return [
            {
                "repository": row["repository"],
                "expected": row["currentRelease"],
                "observed": row["currentRelease"],
                "status": "match",
            }
            for row in watch[key]  # type: ignore[index]
        ]

    return {
        "contract": "Eva2UpstreamReleaseWatchReceipt/v1",
        "checkedAt": watch["checkedAt"],
        "reviewValidUntil": watch["reviewValidUntil"],
        "watchManifestSha256": hashlib.sha256(watch_raw).hexdigest(),
        "components": rows("components"),
        "competitors": rows("competitors"),
        "summary": {"matched": 4, "mismatched": 0, "errors": 0},
    }


def _write_receipt(tmp_path: Path, receipt: dict[str, object]) -> Path:
    path = tmp_path / "upstream-release-watch-live-test.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def test_release_watch_receipt_binds_exact_manifest_and_complete_coverage(
    tmp_path: Path,
) -> None:
    watch = _watch()
    watch_raw = json.dumps(watch, separators=(",", ":")).encode()
    receipt_path = _write_receipt(tmp_path, _receipt(watch_raw, watch))

    validate_release_watch_receipt(watch_raw, watch, [receipt_path])


def test_release_watch_receipt_rejects_stale_manifest_hash(tmp_path: Path) -> None:
    old_watch = _watch()
    old_raw = json.dumps(old_watch, separators=(",", ":")).encode()
    receipt_path = _write_receipt(tmp_path, _receipt(old_raw, old_watch))
    current_watch = {
        **old_watch,
        "components": [
            *old_watch["components"],  # type: ignore[misc]
            {"repository": "example/new-component", "currentRelease": "v1"},
        ],
    }
    current_raw = json.dumps(current_watch, separators=(",", ":")).encode()

    with pytest.raises(SystemExit, match="no unique exact release-watch receipt"):
        validate_release_watch_receipt(current_raw, current_watch, [receipt_path])


def test_release_watch_receipt_rejects_missing_component(tmp_path: Path) -> None:
    watch = _watch()
    watch_raw = json.dumps(watch, separators=(",", ":")).encode()
    receipt = _receipt(watch_raw, watch)
    receipt["components"] = []
    receipt_path = _write_receipt(tmp_path, receipt)

    with pytest.raises(SystemExit, match="does not exactly cover components"):
        validate_release_watch_receipt(watch_raw, watch, [receipt_path])


def test_dependency_update_posture_checker_rejects_unbound_current_receipt() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/check_dependency_update_posture.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert completed.stderr.strip() == (
        "current component watch has no unique exact release-watch receipt"
    )
