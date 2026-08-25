#!/usr/bin/env python3
"""Replay the exact offline, no-secret HA-MCP read-only smoke."""

from __future__ import annotations

import json
import selectors
import subprocess
import time
from typing import Any


IMAGE = (
    "ghcr.io/homeassistant-ai/ha-mcp@"
    "sha256:2d06fa32c06a935700c44fbce3998a2741edf20e698cc8a0231743f595bd653e"
)
REQUIRED_READ_TOOLS = {
    "ha_config_get_automation",
    "ha_get_history",
    "ha_get_state",
    "ha_search",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def send(process: subprocess.Popen[str], value: dict[str, Any]) -> None:
    require(process.stdin is not None, "candidate stdin is unavailable")
    process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
    process.stdin.flush()


def response(process: subprocess.Popen[str], request_id: int) -> dict[str, Any]:
    require(process.stdout is not None, "candidate stdout is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            events = selector.select(max(0.0, deadline - time.monotonic()))
            if not events:
                break
            line = process.stdout.readline()
            require(line != "", "candidate stopped before its MCP response")
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("id") == request_id:
                return value
    finally:
        selector.close()
    raise RuntimeError(f"candidate did not answer request {request_id}")


def main() -> None:
    inspect = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    image = json.loads(inspect.stdout)[0]
    require(image["Id"].endswith(IMAGE.rsplit(":", 1)[1]), "wrong local image")
    require(image["Architecture"] == "amd64", "wrong local architecture")
    require(image["Config"]["User"] == "mcpuser", "candidate is not non-root")
    require(
        image["Config"]["Labels"]["org.opencontainers.image.version"] == "8.3.0",
        "wrong image version",
    )

    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
        "-e",
        "HOMEASSISTANT_URL=http://127.0.0.1:9",
        "-e",
        "HOMEASSISTANT_TOKEN=not-a-real-token",
        "-e",
        "READ_ONLY_MODE=true",
        "-e",
        "ENABLE_BETA_FEATURES=false",
        "-e",
        "ENABLE_CODE_MODE=false",
        "-e",
        "HAMCP_ENABLE_FILESYSTEM_TOOLS=false",
        IMAGE,
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
        send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "eva-dependency-bakeoff", "version": "1"},
                },
            },
        )
        initialized = response(process, 1)["result"]
        require(initialized["protocolVersion"] == "2025-06-18", "wrong MCP version")
        require(initialized["serverInfo"]["version"] == "8.3.0", "wrong server version")
        send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = response(process, 2)["result"]["tools"]
        names = {tool["name"] for tool in tools}
        mixed = [
            tool
            for tool in tools
            if not tool.get("annotations", {}).get("readOnlyHint", False)
        ]
        require(len(tools) == 39, "read-only catalog count drifted")
        require(len(mixed) == 6, "mixed-tool count drifted")
        require(REQUIRED_READ_TOOLS <= names, "required read tools disappeared")
        require("ha_config_set_automation" not in names, "write tool stayed visible")

        send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "ha_config_set_automation",
                    "arguments": {
                        "config": {"alias": "must-not-run", "triggers": [], "actions": []}
                    },
                },
            },
        )
        hidden_write = response(process, 3)["result"]
        require(hidden_write["isError"] is True, "hidden write was not rejected")
        require("READ_ONLY_MODE" in hidden_write["content"][0]["text"], "wrong write error")

        send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "ha_manage_backup",
                    "arguments": {"scope": "snapshot", "action": "create", "name": "must-not-run"},
                },
            },
        )
        mixed_write = response(process, 4)["result"]
        require(mixed_write["isError"] is True, "mixed write was not rejected")
        require("READ_ONLY_MODE" in mixed_write["content"][0]["text"], "wrong mixed error")
        print(
            json.dumps(
                {
                    "image": IMAGE,
                    "listedTools": len(tools),
                    "mixedTools": len(mixed),
                    "requiredReadTools": sorted(REQUIRED_READ_TOOLS),
                    "writeBlocks": 2,
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
