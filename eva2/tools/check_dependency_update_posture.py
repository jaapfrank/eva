#!/usr/bin/env python3
"""Fail closed if proposed dependency updates could silently promote themselves."""

from __future__ import annotations

import json
from pathlib import Path


EVA2_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EVA2_ROOT.parent
CONFIG = REPOSITORY_ROOT / "renovate.json"
WATCH = REPOSITORY_ROOT / "component-watch.json"


def main() -> None:
    config = json.loads(CONFIG.read_bytes())
    if config.get("dependencyDashboard") is not True:
        raise SystemExit("Renovate must expose a dependency dashboard")
    if config.get("dependencyDashboardApproval") is not True:
        raise SystemExit("dependency proposals must require explicit approval")
    if config.get("prConcurrentLimit") != 2 or config.get("prHourlyLimit") != 1:
        raise SystemExit("dependency proposals must stay small and reviewable")
    if config.get("minimumReleaseAge") != "7 days":
        raise SystemExit("new releases must age before proposal")
    rules = config.get("packageRules")
    if not isinstance(rules, list) or not rules:
        raise SystemExit("Renovate package rules are missing")
    if any(rule.get("automerge") is True for rule in rules if isinstance(rule, dict)):
        raise SystemExit("Eva must never automatically merge a dependency update")
    if not any(
        isinstance(rule, dict)
        and rule.get("matchDatasources") == ["docker"]
        and rule.get("pinDigests") is True
        for rule in rules
    ):
        raise SystemExit("Docker update proposals must pin an immutable digest")
    managers = config.get("customManagers")
    if not isinstance(managers, list) or not any(
        isinstance(manager, dict)
        and manager.get("datasourceTemplate") == "github-releases"
        and manager.get("versioningTemplate") == "loose"
        for manager in managers
    ):
        raise SystemExit("upstream component and competitor releases must be watched")
    watch = json.loads(WATCH.read_bytes())
    competitors = watch.get("competitors")
    watched_competitors = {
        row.get("repository") for row in competitors if isinstance(row, dict)
    } if isinstance(competitors, list) else set()
    if watched_competitors != {
        "openclaw/openclaw",
        "NousResearch/hermes-agent",
        "agent0ai/agent-zero",
    }:
        raise SystemExit("the exact three Eva competitors must be release-watched")
    print("dependency update posture: proposal-only, approval-required, digest-pinned")


if __name__ == "__main__":
    main()
