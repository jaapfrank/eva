#!/usr/bin/env python3
"""Bind Eva's replaceable HA surface to the exact HA-MCP tool contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
CONTRACT = HERE / "parity-contract.json"
SMOKE = HERE.parent / "ha-mcp-sidecar-v1" / "smoke_candidate.py"
READ_TOOLS = {"ha_config_get_automation", "ha_get_history", "ha_get_state", "ha_search"}
WRITE_TOOLS = {
    "ha_call_service",
    "ha_config_remove_automation",
    "ha_config_set_automation",
}
EXPECTED_CANDIDATE = "DF02e-ha-mcp-parity-v1"
EXPECTED_CONTRACT = "EvaHomeAssistantMcpParity/v2"
EXPECTED_BASELINE = "1e780ca4ecd433d230ea18c3d58213fc6baebe26"
EXPECTED_IMAGE = (
    "ghcr.io/homeassistant-ai/ha-mcp@"
    "sha256:2d06fa32c06a935700c44fbce3998a2741edf20e698cc8a0231743f595bd653e"
)
EXPECTED_FILES = {
    "eva2/src/eva/adapters/connectors/home_assistant.py": {
        "lines": 763,
        "sha256": "be368877bf6b65a1930a1ab77504f52a6e4ebdab501c92dbfe583017c3856cb6",
    },
    "eva2/src/eva/adapters/connectors/home_assistant_automations.py": {
        "lines": 260,
        "sha256": "b5028928526f48fa90c713fa8f9f275836131ef699776b3664c5da43749066d7",
    },
}
EXPECTED_EXCLUSIONS = [
    "eva2/src/eva/adapters/connectors/home_assistant_admin.py",
    "eva2/src/eva/adapters/connectors/home_assistant_events.py",
    "eva2/src/eva/adapters/connectors/home_assistant_hacs.py",
    "eva2/src/eva/adapters/connectors/home_assistant_localtuya.py",
    "eva2/src/eva/application/home_automation.py",
]
EXPECTED_MAPPING = {
    "execute": ["ha_call_service"],
    "history": ["ha_get_history"],
    "history_group": ["ha_get_history"],
    "observe": ["ha_get_state"],
    "resolve_entities": ["ha_search"],
    "resolve_history_targets": ["ha_search"],
    "resolve_light": ["ha_search"],
    "resolve_switch": ["ha_search"],
    "state": ["ha_get_state"],
    "automation_delete_verified": [
        "ha_config_get_automation",
        "ha_config_remove_automation",
        "ha_config_get_automation",
    ],
    "automation_execute": [
        "ha_config_get_automation",
        "ha_config_set_automation",
    ],
    "automation_observe": ["ha_config_get_automation"],
}
EXPECTED_INPUT_HASHES = {
    "ha_call_service": "9104b318256ff10e0e3edce620fe43981c01a3c4405a93cefcc92b2ef98c9c88",
    "ha_config_get_automation": "bae748eb90c04a6f51aaa606d7b2b20afa76ede3bc0b40d4d8e0da847a581200",
    "ha_config_remove_automation": "239a10c59c69242458e3f8ef41adead2b58cb5d59e65d9ffc1e6efe9097ce4ed",
    "ha_config_set_automation": "269653db1d134f5d6a1010f0f702037803e55e2ad23a37c5f093e21f72b2c2b3",
    "ha_get_history": "bb8276887ba10132e7d59fd99b41f564c18ead1f42fd9bb466c1b56c22416d36",
    "ha_get_state": "bd1f2d5a94013f975b8aa5f0328cdcbe076dde11d99837876c03594580c5b78e",
    "ha_search": "9e75d75b39f277391dd3e2d70719268c8d0be0ae8fcab9e60eb29a5594aa7375",
}
EXPECTED_DESCRIPTOR_HASHES = {
    "ha_call_service": "82c1e34423c22989493b7e3d5c0b00a20854f9b5de29bb5853a9c5c388e9e213",
    "ha_config_get_automation": "d8b9e6397e1962b666bfe56c2d7d00b17897abe7776e625e79eca55f70af156f",
    "ha_config_remove_automation": "7ccae0fef719e4246729330324fbcb807f09867a056fdcf009c05b6ac64bdd5a",
    "ha_config_set_automation": "268cc55ca6914212531ea77e62be81a3407594e510390eb0511d97751cc96b3b",
    "ha_get_history": "a2e5abbdf4db6437c5f8e43d22438410969cd095817a11935d41ed516f8087ca",
    "ha_get_state": "77c25a48cc8f10c2e1851b9d8b2ccd8bc54861cea5dc82c02eaa309de02220fc",
    "ha_search": "d08832740a09c6846f8d6c12bc5cda8553bbbe943699a2876bbe836bb4cb6b1d",
}
EXPECTED_NONCLAIMS = {
    "connectedToRealHomeAssistant": False,
    "provedCodeRemoval": False,
    "provedCrashRecovery": False,
    "provedLatencyWin": False,
    "provedProductionPromotion": False,
}
EXPECTED_PROMOTION = {
    "requiresIndependentReadback": True,
    "requiresLeastPrivilegeTestToken": True,
    "requiresOldRouteRemoved": True,
    "requiresRollbackByExactDigest": True,
    "requiresSingleRuntimeOwner": True,
}
EXPECTED_EXCLUDED_METHODS = {
    "health": "Separate sidecar and Home Assistant readiness check has no admitted tool mapping yet."
}
EXPECTED_OUTPUT_SCHEMA = {"additionalProperties": True, "type": "object"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def load_smoke() -> Any:
    spec = importlib.util.spec_from_file_location("ha_mcp_candidate_smoke", SMOKE)
    require(spec is not None and spec.loader is not None, "cannot load sidecar smoke")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline_sha256(commit: str, relative: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def descriptor_sha256(tool: dict[str, Any]) -> str:
    return canonical_sha256(
        {
            "annotations": tool.get("annotations"),
            "inputSchema": tool.get("inputSchema"),
            "name": tool.get("name"),
            "outputSchema": tool.get("outputSchema"),
        }
    )


def validate_contract(contract: dict[str, Any]) -> None:
    require(contract["candidateId"] == EXPECTED_CANDIDATE, "candidate ID drifted")
    require(contract["contract"] == EXPECTED_CONTRACT, "contract name drifted")
    require(contract["status"] == "historical_superseded", "status drifted")
    require(contract["image"] == EXPECTED_IMAGE, "sidecar image binding drifted")
    current = contract["historicalDirectProductionBaseline"]
    require(current["baselineGitCommit"] == EXPECTED_BASELINE, "source baseline drifted")
    require(current["files"] == EXPECTED_FILES, "direct source binding drifted")
    require(contract["excludedFromReplacement"] == EXPECTED_EXCLUSIONS, "exclusions drifted")
    require(contract["excludedMethods"] == EXPECTED_EXCLUDED_METHODS, "method exclusions drifted")
    require(contract["mapping"] == EXPECTED_MAPPING, "Eva-to-MCP mapping drifted")
    require(contract["nonclaims"] == EXPECTED_NONCLAIMS, "nonclaims drifted")
    require(
        contract["historicalReplacementRequirements"] == EXPECTED_PROMOTION,
        "historical replacement requirements drifted",
    )
    require(contract["toolInputSchemaSha256"] == EXPECTED_INPUT_HASHES, "input bindings drifted")
    require(contract["toolDescriptorSha256"] == EXPECTED_DESCRIPTOR_HASHES, "descriptor bindings drifted")

    for relative, expected in EXPECTED_FILES.items():
        require(
            baseline_sha256(EXPECTED_BASELINE, relative) == expected["sha256"],
            f"Git baseline differs: {relative}",
        )
    require(
        sum(value["lines"] for value in EXPECTED_FILES.values()) == current["lines"],
        "direct source total is inconsistent",
    )
    require(current["lines"] == 1023, "direct source total drifted")


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    validate_contract(contract)
    smoke = load_smoke()
    require(smoke.IMAGE == EXPECTED_IMAGE, "sidecar smoke image binding drifted")
    current = contract["historicalDirectProductionBaseline"]

    command = [
        "docker", "run", "--rm", "-i", "--pull=never", "--network=none",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
        "-e", "HOMEASSISTANT_URL=http://127.0.0.1:9",
        "-e", "HOMEASSISTANT_TOKEN=not-a-real-token",
        "-e", "READ_ONLY_MODE=false",
        "-e", "ENABLE_BETA_FEATURES=false",
        "-e", "ENABLE_CODE_MODE=false",
        "-e", "HAMCP_ENABLE_FILESYSTEM_TOOLS=false",
        contract["image"],
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        smoke.send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": contract["protocolVersion"],
                    "capabilities": {},
                    "clientInfo": {"name": "eva-ha-parity-gate", "version": "1"},
                },
            },
        )
        initialized = smoke.response(process, 1)["result"]
        require(initialized["protocolVersion"] == contract["protocolVersion"], "MCP version drifted")
        require(initialized["serverInfo"]["version"] == contract["version"], "server version drifted")
        smoke.send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        smoke.send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = {tool["name"]: tool for tool in smoke.response(process, 2)["result"]["tools"]}
        required = READ_TOOLS | WRITE_TOOLS
        require(required == EXPECTED_INPUT_HASHES.keys(), "required tool set drifted")
        require(required <= tools.keys(), "required HA-MCP parity tool disappeared")
        observed_hashes = {
            name: canonical_sha256(tools[name]["inputSchema"])
            for name in EXPECTED_INPUT_HASHES
        }
        mismatches = {
            name: observed
            for name, observed in observed_hashes.items()
            if observed != EXPECTED_INPUT_HASHES[name]
        }
        require(not mismatches, f"input schemas drifted: {json.dumps(mismatches, sort_keys=True)}")
        observed_descriptors = {
            name: descriptor_sha256(tools[name]) for name in EXPECTED_DESCRIPTOR_HASHES
        }
        require(observed_descriptors == EXPECTED_DESCRIPTOR_HASHES, "tool descriptors drifted")
        for name, expected in EXPECTED_INPUT_HASHES.items():
            tool = tools[name]
            require(observed_hashes[name] == expected, f"input schema drifted: {name}")
            require(tool.get("outputSchema") == EXPECTED_OUTPUT_SCHEMA, f"output schema drifted: {name}")
            annotation = tool.get("annotations", {})
            if name in READ_TOOLS:
                require(annotation.get("readOnlyHint") is True, f"read annotation drifted: {name}")
            else:
                require(annotation.get("readOnlyHint") is not True, f"write became read-only: {name}")
        print(
            json.dumps(
                {
                    "candidateId": EXPECTED_CANDIDATE,
                    "decision": "historical_contract_only",
                    "historicalDirectProductionLines": current["lines"],
                    "requiresOldRouteRemoved": EXPECTED_PROMOTION[
                        "requiresOldRouteRemoved"
                    ],
                    "requiresSingleRuntimeOwner": EXPECTED_PROMOTION[
                        "requiresSingleRuntimeOwner"
                    ],
                    "requiredTools": sorted(required),
                },
                separators=(",", ":"),
            )
        )
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
