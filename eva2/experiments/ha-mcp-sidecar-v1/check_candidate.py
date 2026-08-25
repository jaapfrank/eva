#!/usr/bin/env python3
"""Fail closed when the recorded HA-MCP candidate evidence drifts."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RECEIPT = ROOT / "receipt.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    value = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(value["contract"] == "EvaDependencySidecarReceipt/v1", "wrong contract")
    require(
        value["decision"] == "promote_to_isolated_live_parity_trial",
        "wrong decision",
    )
    require(value["project"]["version"] == "8.3.0", "wrong release")
    require(value["project"]["releaseImmutable"] is True, "release is mutable")
    require(value["project"]["license"] == "MIT", "wrong license")
    require(value["community"]["fixedMaintainers"] >= 4, "too few maintainers")
    require(value["community"]["githubListedContributors"] >= 60, "too few contributors")
    require(value["source"]["tag"] == "v8.3.0", "wrong source tag")
    require(
        value["source"]["runtimeSourceTree"]
        == "b6f80bafd162c4e9ce99d66a25f3ea043a85894d",
        "runtime source tree drifted",
    )
    require(
        value["image"]["amd64Digest"]
        == "sha256:2d06fa32c06a935700c44fbce3998a2741edf20e698cc8a0231743f595bd653e",
        "amd64 image drifted",
    )
    require(
        value["image"]["arm64Digest"]
        == "sha256:6e60aea9bb01f7e4b0cc20dc874d3043599dc3df77892b87e56ab9cbf18258c3",
        "arm64 image drifted",
    )
    smoke = value["disposableSmoke"]
    require(smoke["network"] == "none", "smoke used a network")
    require(smoke["realCredentialsUsed"] is False, "smoke used credentials")
    require(smoke["filesystemReadOnly"] is True, "filesystem was writable")
    require(smoke["capabilitiesDropped"] is True, "capabilities were retained")
    require(smoke["listedTools"] == 39, "read-only catalog drifted")
    require(smoke["listedNonReadOnlyMixedTools"] == 6, "mixed-tool count drifted")
    require(
        set(smoke["requiredReadToolsPresent"])
        == {"ha_config_get_automation", "ha_get_history", "ha_get_state", "ha_search"},
        "required read tools drifted",
    )
    require(len(smoke["writeCallsBlocked"]) == 2, "write block evidence incomplete")
    require(all(claim is False for claim in value["nonclaims"].values()), "nonclaim widened")
    print(
        json.dumps(
            {
                "candidateId": value["candidateId"],
                "decision": value["decision"],
                "image": value["image"]["amd64Digest"],
                "listedTools": smoke["listedTools"],
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
