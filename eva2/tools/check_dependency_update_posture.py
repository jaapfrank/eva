#!/usr/bin/env python3
"""Fail closed if proposed dependency updates could silently promote themselves."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


EVA2_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EVA2_ROOT.parent
CONFIG = REPOSITORY_ROOT / "renovate.json"
WATCH = REPOSITORY_ROOT / "component-watch.json"
RELEASE_WATCH_EVIDENCE = EVA2_ROOT / "evidence/product"
RELEASE_WATCH_GLOB = "upstream-release-watch-live-*.json"


def validate_release_watch_receipt(
    watch_raw: bytes,
    watch: dict[str, object],
    receipt_paths: list[Path],
) -> None:
    """Require one receipt that proves this exact release-watch manifest."""

    watch_sha256 = hashlib.sha256(watch_raw).hexdigest()
    matching: list[dict[str, object]] = []
    for path in receipt_paths:
        try:
            receipt = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit("release-watch receipt is unreadable") from error
        if not isinstance(receipt, dict):
            raise SystemExit("release-watch receipt is invalid")
        if receipt.get("watchManifestSha256") == watch_sha256:
            matching.append(receipt)
    if len(matching) != 1:
        raise SystemExit(
            "current component watch has no unique exact release-watch receipt"
        )

    receipt = matching[0]
    if receipt.get("contract") != "Eva2UpstreamReleaseWatchReceipt/v1":
        raise SystemExit("release-watch receipt contract is invalid")
    if receipt.get("checkedAt") != watch.get("checkedAt"):
        raise SystemExit("release-watch receipt check time differs from manifest")
    if receipt.get("reviewValidUntil") != watch.get("reviewValidUntil"):
        raise SystemExit("release-watch receipt validity differs from manifest")

    matched = 0
    for key in ("components", "competitors"):
        watch_rows, receipt_rows = watch.get(key), receipt.get(key)
        if not isinstance(watch_rows, list) or not isinstance(receipt_rows, list):
            raise SystemExit(f"release-watch receipt {key} entries are missing")
        try:
            watched = {row["repository"]: row["currentRelease"] for row in watch_rows}
            observed = {row["repository"]: row for row in receipt_rows}
        except (KeyError, TypeError) as error:
            raise SystemExit(f"release-watch receipt {key} entry is invalid") from error
        if (
            len(watched) != len(watch_rows)
            or len(observed) != len(receipt_rows)
            or set(observed) != set(watched)
        ):
            raise SystemExit(f"release-watch receipt does not exactly cover {key}")
        if any(
            row.get("expected") != version
            or row.get("observed") != version
            or row.get("status") != "match"
            for repository, version in watched.items()
            for row in (observed[repository],)
        ):
            raise SystemExit(f"release-watch receipt {key} version is not current")
        matched += len(watched)

    summary = receipt.get("summary")
    if not isinstance(summary, dict) or summary != {
        "matched": matched,
        "mismatched": 0,
        "errors": 0,
    }:
        raise SystemExit("release-watch receipt summary is invalid")


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
    watch_raw = WATCH.read_bytes()
    watch = json.loads(watch_raw)
    try:
        checked_at = datetime.fromisoformat(watch["checkedAt"]).astimezone(UTC)
        valid_until = datetime.fromisoformat(watch["reviewValidUntil"]).astimezone(UTC)
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit("upstream release review window is invalid") from error
    now = datetime.now(UTC)
    if checked_at > now or valid_until <= now:
        raise SystemExit("upstream release review is absent, future-dated or expired")
    if valid_until - checked_at > timedelta(days=30):
        raise SystemExit("upstream release review window may not exceed 30 days")
    competitors = watch.get("competitors")
    watched_competitors = (
        {row.get("repository") for row in competitors if isinstance(row, dict)}
        if isinstance(competitors, list)
        else set()
    )
    if watched_competitors != {
        "openclaw/openclaw",
        "NousResearch/hermes-agent",
        "agent0ai/agent-zero",
    }:
        raise SystemExit("the exact three Eva competitors must be release-watched")
    validate_release_watch_receipt(
        watch_raw,
        watch,
        sorted(RELEASE_WATCH_EVIDENCE.glob(RELEASE_WATCH_GLOB)),
    )
    print(
        "dependency update posture: proposal-only, approval-required, "
        "digest-pinned, freshness-bounded, receipt-bound"
    )


if __name__ == "__main__":
    main()
