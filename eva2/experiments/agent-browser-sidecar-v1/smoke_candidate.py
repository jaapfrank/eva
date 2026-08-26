#!/usr/bin/env python3
"""Exercise the agent-browser candidate in Eva's smallest closed test cell.

This is intentionally a provider evaluation, not a runtime adapter.  It proves
one allowlisted page, one deliberate form effect, independent server readback,
and the absence of ordinary egress from the sidecar network.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
FIXTURE_PATH = REPOSITORY / "experiments/playwright-mcp-action-v1/fixture_server.py"
CANDIDATE_IMAGE = "eva2/agent-browser-sidecar:0.35.1-eval"
FIXTURE_IMAGE = (
    "python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(
    command: list[str], *, timeout: float = 30, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, check=check, text=True, timeout=timeout
    )


def inspect(kind: str, name: str) -> dict[str, Any]:
    value = json.loads(run(["docker", kind, "inspect", name]).stdout)
    require(isinstance(value, list) and len(value) == 1, f"wrong {kind} inspection")
    return value[0]


def remove(name: str) -> None:
    run(["docker", "rm", "-f", name], timeout=10, check=False)


def main() -> None:
    require(FIXTURE_PATH.is_file(), "sealed action fixture is missing")
    suffix = uuid4().hex[:12]
    network_name = f"eva-agent-browser-{suffix}"
    fixture_name = f"eva-agent-browser-fixture-{suffix}"
    browser_name = f"eva-agent-browser-{suffix}"
    effect_key = f"agent-browser-{suffix}"
    request_sha256 = hashlib.sha256(effect_key.encode("ascii")).hexdigest()
    page_url = "http://fixture.eva.test:8080/index.html?" + urlencode(
        {"effect_key": effect_key, "request_sha256": request_sha256}
    )

    image = inspect("image", CANDIDATE_IMAGE)
    require(image["Architecture"] == "amd64", "candidate architecture changed")
    require(image["Config"]["User"] == "10001:10001", "candidate is not non-root")
    environment = set(image["Config"]["Env"] or [])
    require(
        "AGENT_BROWSER_EXECUTABLE_PATH="
        "/opt/agent-browser/browsers/chrome-152.0.7977.64/chrome" in environment,
        "candidate Chrome executable pin changed",
    )
    fixture_image = inspect("image", FIXTURE_IMAGE)
    require(fixture_image["Architecture"] == "amd64", "fixture architecture changed")

    run(
        [
            "docker",
            "network",
            "create",
            "--internal",
            "--driver",
            "bridge",
            "--opt",
            "com.docker.network.bridge.inhibit_ipv4=true",
            network_name,
        ]
    )
    try:
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                fixture_name,
                "--pull",
                "never",
                "--network",
                network_name,
                "--network-alias",
                "fixture.eva.test",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "64m",
                "--cpus",
                "0.25",
                "--user",
                "65532:65532",
                "--mount",
                f"type=bind,src={FIXTURE_PATH},dst=/fixture_server.py,readonly",
                "--entrypoint",
                "python",
                FIXTURE_IMAGE,
                "/fixture_server.py",
            ]
        )
        browser_command = [
            "docker",
            "create",
            "--name",
            browser_name,
            "--network",
            network_name,
            "--read-only",
            "--tmpfs",
            "/home/eva-browser:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
            "--tmpfs",
            "/dev/shm:rw,nosuid,nodev,size=256m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "192",
            "--memory",
            "1g",
            "--cpus",
            "1.0",
            "--entrypoint",
            "sh",
            CANDIDATE_IMAGE,
            "-ceu",
            "\n".join(
                [
                    f"agent-browser --allowed-domains fixture.eva.test --session eva-probe open '{page_url}'",
                    "agent-browser --allowed-domains fixture.eva.test --session eva-probe snapshot -i",
                    "agent-browser --allowed-domains fixture.eva.test --session eva-probe click @e2",
                    "agent-browser --allowed-domains fixture.eva.test --session eva-probe wait --text 'eenmaal geklikt'",
                    "agent-browser --allowed-domains fixture.eva.test --session eva-probe snapshot -i",
                    "agent-browser --allowed-domains fixture.eva.test --session eva-probe close --all",
                ]
            ),
        ]
        run(browser_command)
        browser = inspect("container", browser_name)
        require(browser["Config"]["User"] == "10001:10001", "runner user changed")
        require(browser["HostConfig"]["ReadonlyRootfs"] is True, "root is writable")
        require(browser["HostConfig"]["CapDrop"] == ["ALL"], "runner retained capability")
        require(
            browser["HostConfig"]["SecurityOpt"] == ["no-new-privileges"],
            "runner lost no-new-privileges",
        )
        require(browser["Mounts"] == [], "runner received a host mount")
        require(not (browser["NetworkSettings"]["Ports"] or {}), "runner publishes ports")
        network = inspect("network", network_name)
        require(network["Internal"] is True, "test network has external egress")
        require(
            network["Options"] == {"com.docker.network.bridge.inhibit_ipv4": "true"},
            "test network gateway changed",
        )
        require(network["IPAM"]["Config"][0]["Gateway"] == "", "test network has gateway")

        started = time.monotonic()
        result = run(["docker", "start", "-a", browser_name], timeout=25)
        stop_milliseconds = round((time.monotonic() - started) * 1000)
        require("Eva browserproef A02" in result.stdout, "page title was not seen")
        require("[disabled, ref=e2]" in result.stdout, "click did not change the page")

        oracle = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                network_name,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=8m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "64m",
                "--cpus",
                "0.25",
                "--user",
                "65532:65532",
                "--entrypoint",
                "python",
                FIXTURE_IMAGE,
                "-c",
                "import urllib.request; print(urllib.request.urlopen(" + repr(
                    f"http://fixture.eva.test:8080/oracle?effect_key={effect_key}"
                )
                + ", timeout=5).read().decode())",
            ]
        )
        state = json.loads(oracle.stdout)
        require(state["state"] == "committed", "fixture did not observe the action")
        require(state["post_requests"] == 1, "fixture saw duplicate browser POST")
        require(state["event"]["request_sha256"] == request_sha256, "wrong effect receipt")

        denied = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                network_name,
                "--read-only",
                "--tmpfs",
                "/home/eva-browser:rw,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                "--tmpfs",
                "/dev/shm:rw,nosuid,nodev,size=128m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "192",
                "--memory",
                "1g",
                "--cpus",
                "1.0",
                CANDIDATE_IMAGE,
                "--allowed-domains",
                "fixture.eva.test",
                "--session",
                "eva-denied",
                "open",
                "https://example.com",
            ],
            timeout=15,
            check=False,
        )
        require(denied.returncode != 0, "allowlist accepted a foreign destination")

        egress = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                network_name,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=8m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "64m",
                "--cpus",
                "0.25",
                "--entrypoint",
                "node",
                CANDIDATE_IMAGE,
                "-e",
                "const s=require('net').connect({host:'1.1.1.1',port:443});s.setTimeout(3000);s.on('connect',()=>process.exit(17));s.on('timeout',()=>process.exit(0));s.on('error',()=>process.exit(0));",
            ],
            timeout=8,
            check=False,
        )
        require(egress.returncode == 0, "candidate network reached public internet")
        print(
            json.dumps(
                {
                    "allowlistRejectedForeignDestination": True,
                    "browserImage": image["Id"],
                    "directEgressBlocked": True,
                    "effectPostRequests": state["post_requests"],
                    "effectState": state["state"],
                    "pageUrl": "http://fixture.eva.test:8080/index.html",
                    "publishedPorts": 0,
                    "rootFilesystemReadOnly": True,
                    "stopMilliseconds": stop_milliseconds,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    finally:
        remove(browser_name)
        remove(fixture_name)
        run(["docker", "network", "rm", network_name], timeout=10, check=False)


if __name__ == "__main__":
    main()
