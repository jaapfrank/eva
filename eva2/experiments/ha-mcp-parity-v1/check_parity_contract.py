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


def line_count(relative: str) -> int:
    with (REPOSITORY / relative).open(encoding="utf-8") as source:
        return sum(1 for _line in source)


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    smoke = load_smoke()
    require(contract["image"] == smoke.IMAGE, "sidecar image binding drifted")

    for relative, expected in contract["currentDirectProduction"]["files"].items():
        require(line_count(relative) == expected, f"direct source size drifted: {relative}")
    require(
        sum(contract["currentDirectProduction"]["files"].values())
        == contract["currentDirectProduction"]["lines"],
        "direct source total is inconsistent",
    )
    require(
        contract["promotion"]["minimumRemovedProductionLines"] >= 300,
        "replacement no longer removes a meaningful amount of code",
    )
    require(
        contract["promotion"]["maximumNewIntegrationLines"] <= 180,
        "thin integration ceiling widened",
    )

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
        require(required <= tools.keys(), "required HA-MCP parity tool disappeared")
        observed_hashes = {
            name: canonical_sha256(tools[name]["inputSchema"])
            for name in contract["toolInputSchemaSha256"]
        }
        mismatches = {
            name: observed
            for name, observed in observed_hashes.items()
            if observed != contract["toolInputSchemaSha256"][name]
        }
        require(not mismatches, f"input schemas drifted: {json.dumps(mismatches, sort_keys=True)}")
        for name, expected in contract["toolInputSchemaSha256"].items():
            tool = tools[name]
            require(observed_hashes[name] == expected, f"input schema drifted: {name}")
            require(tool.get("outputSchema", {}).get("type") == "object", f"output schema drifted: {name}")
            annotation = tool.get("annotations", {})
            if name in READ_TOOLS:
                require(annotation.get("readOnlyHint") is True, f"read annotation drifted: {name}")
            else:
                require(annotation.get("readOnlyHint") is not True, f"write became read-only: {name}")
        print(
            json.dumps(
                {
                    "candidateId": contract["candidateId"],
                    "decision": "ready_for_thin_adapter_candidate",
                    "directProductionLines": contract["currentDirectProduction"]["lines"],
                    "maximumNewIntegrationLines": contract["promotion"]["maximumNewIntegrationLines"],
                    "minimumRemovedProductionLines": contract["promotion"]["minimumRemovedProductionLines"],
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
