from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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


def test_dependency_update_posture_checker_is_green() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/check_dependency_update_posture.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == (
        "dependency update posture: proposal-only, approval-required, digest-pinned"
    )
