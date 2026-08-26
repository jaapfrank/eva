#!/usr/bin/env python3
"""Fail closed when the sealed Playwright MCP candidate evidence drifts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

PROFILE_PATH = HERE / "profile.json"
SANDBOX_PATH = HERE / "sandbox-profile.json"
RECEIPT_PATH = (
    REPOSITORY
    / "evidence"
    / "cockpit"
    / "isolated-browser-read-6b4cc87-test-receipt.json"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    from eva.adapters.connectors.playwright_mcp import (
        PLAYWRIGHT_MCP_AMD64_DIGEST,
        PLAYWRIGHT_MCP_VERSION,
        PLAYWRIGHT_NAVIGATE_TOOL_SHA256,
        PLAYWRIGHT_SERVER_VERSION,
        PLAYWRIGHT_SNAPSHOT_TOOL_SHA256,
    )
    from eva.contracts.canonical_json import canonical_sha256

    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    sandbox = json.loads(SANDBOX_PATH.read_text(encoding="utf-8"))
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))

    require(
        profile["contract"] == "EvaPlaywrightMcpCandidateProfile/v1",
        "wrong browser profile contract",
    )
    require(
        profile["decision"] == "admit_disposable_isolated_page_read",
        "wrong browser candidate decision",
    )
    require(profile["package"]["version"] == PLAYWRIGHT_MCP_VERSION, "wrong package version")
    require(
        profile["package"]["playwrightVersion"] == PLAYWRIGHT_SERVER_VERSION,
        "wrong Playwright runtime version",
    )
    require(
        profile["package"]["playwrightCoreVersion"] == PLAYWRIGHT_SERVER_VERSION,
        "wrong Playwright Core version",
    )
    require(
        profile["image"]["amd64Digest"] == PLAYWRIGHT_MCP_AMD64_DIGEST,
        "wrong browser image digest",
    )
    require(
        profile["tools"]["browserNavigateSha256"]
        == PLAYWRIGHT_NAVIGATE_TOOL_SHA256,
        "navigate tool drifted",
    )
    require(
        profile["tools"]["browserSnapshotSha256"]
        == PLAYWRIGHT_SNAPSHOT_TOOL_SHA256,
        "snapshot tool drifted",
    )
    require(
        profile["tools"]["sequence"] == ["browser_navigate", "browser_snapshot"],
        "browser tool sequence widened",
    )
    require(profile["tools"]["maximumCalls"] == 2, "browser call budget widened")
    require(
        canonical_sha256(sandbox) == profile["sandboxProfileSha256"],
        "sandbox profile hash drifted",
    )
    require(
        profile["testCell"]
        == {
            "controllerImage": "eva2-living-core@sha256:d1a861106ecaaa2230e5bcac63232808eced907bf2b1febeff4db2ab7cc161ae",
            "fixtureImage": "python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134",
            "gatewaylessBridgeOption": "com.docker.network.bridge.inhibit_ipv4=true",
            "networkDriver": "bridge",
        },
        "browser test cell drifted",
    )
    require(sandbox["filesystemReadOnly"] is True, "sandbox filesystem became writable")
    require(sandbox["capabilitiesDropped"] is True, "sandbox retained capabilities")
    require(sandbox["noNewPrivileges"] is True, "sandbox lost no-new-privileges")
    require(sandbox["network"]["internalDockerNetwork"] is True, "sandbox network widened")
    require(sandbox["network"]["gatewayless"] is True, "sandbox gained a gateway")
    require(sandbox["network"]["hostAccess"] is False, "sandbox gained host access")
    require(
        sandbox["network"]["inhibitBridgeIpv4"] is True,
        "sandbox bridge inhibition disappeared",
    )
    require(sandbox["network"]["externalAccess"] is False, "sandbox gained egress")
    require(sandbox["network"]["publishedPorts"] is False, "sandbox published a port")
    require(sandbox["secrets"] == [], "sandbox gained a secret")
    require(sandbox["workspaceMounts"] == [], "sandbox gained a workspace mount")
    require("--sandbox" in sandbox["arguments"], "Chromium sandbox is not requested")
    require("--no-sandbox" not in sandbox["arguments"], "Chromium sandbox is disabled")
    require(
        file_sha256(HERE / "fixture" / "index.html")
        == profile["fixture"]["fileSha256"],
        "browser fixture drifted",
    )

    require(
        receipt["contract"] == "EvaPlaywrightMcpTestReceipt/v1",
        "wrong browser receipt contract",
    )
    require(receipt["candidateId"] == "BROWSER-R01", "wrong browser candidate ID")
    require(
        receipt["sourceCommit"] == "a7913a8992e0f0a0fde19d37b0ead11d4c46b785",
        "wrong browser source commit",
    )
    require(
        receipt["status"] == "tested_isolated_browser_read_not_live",
        "browser status widened",
    )
    inputs = receipt["inputs"]
    paths = [entry["path"] for entry in inputs]
    require(paths == sorted(paths) and len(paths) == len(set(paths)), "browser inputs are not closed")
    require(
        "experiments/playwright-mcp-sidecar-v1/smoke_client.py" in paths,
        "gatewayless controller source is not bound",
    )
    require("tests/connectors/test_mcp.py" in paths, "MCP regression source is not bound")
    for entry in inputs:
        path = REPOSITORY / entry["path"]
        require(path.is_file(), f"browser input disappeared: {entry['path']}")
        require(file_sha256(path) == entry["sha256"], f"browser input drifted: {entry['path']}")

    runtime = receipt["runtime"]
    require(runtime["browserImage"] == PLAYWRIGHT_MCP_AMD64_DIGEST, "runtime image drifted")
    require(runtime["browserUser"] == "node", "runtime browser user drifted")
    require(
        runtime["controllerImage"] == profile["testCell"]["controllerImage"],
        "runtime controller image drifted",
    )
    require(runtime["externalEgressBlocked"] is True, "runtime egress was not blocked")
    require(runtime["gatewaylessNetwork"] is True, "runtime network retained a gateway")
    require(runtime["hostNetworkBlocked"] is True, "runtime host access was not blocked")
    require(runtime["networkGateway"] is None, "runtime recorded a network gateway")
    require(runtime["internalNetwork"] is True, "runtime network was not internal")
    require(runtime["publishedPorts"] == 0, "runtime browser published a port")
    require(runtime["fixtureGets"] == 1, "runtime browser made another page request")
    require(runtime["matchedVisibleTexts"] == 2, "runtime page proof is incomplete")
    require(runtime["sandboxRequested"] is True, "runtime omitted Chromium sandbox")
    require(
        runtime["snapshotSha256"] == profile["fixture"]["expectedSnapshotSha256"],
        "runtime snapshot drifted",
    )
    require(runtime["toolSequence"] == profile["tools"]["sequence"], "runtime tools widened")
    require(0 < runtime["stopMilliseconds"] <= 5_000, "runtime stop missed its bound")
    require(receipt["tests"]["playwrightAdapterTests"] == 19, "browser tests incomplete")
    require(receipt["tests"]["existingMcpRegressionTests"] == 10, "MCP regressions incomplete")
    require(receipt["tests"]["ruff"] == "green", "browser lint is not green")
    require(receipt["tests"]["smokeRuns"] == 1, "browser smoke is missing")
    require(
        profile["nonclaims"] == receipt["nonclaims"],
        "profile and receipt nonclaims differ",
    )
    require(all(value is False for value in receipt["nonclaims"].values()), "browser nonclaim widened")

    print(
        json.dumps(
            {
                "candidateId": receipt["candidateId"],
                "image": runtime["browserImage"],
                "sourceCommit": receipt["sourceCommit"],
                "status": receipt["status"],
                "tests": 29,
                "toolSequence": runtime["toolSequence"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
