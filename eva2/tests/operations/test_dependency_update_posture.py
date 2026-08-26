from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_renovate_config_is_proposal_only_and_content_addressed() -> None:
    config = json.loads((ROOT / "renovate.json").read_bytes())

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
