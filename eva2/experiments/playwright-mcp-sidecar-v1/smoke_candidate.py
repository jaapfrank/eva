#!/usr/bin/env python3
"""Run the exact browser-read candidate in a gatewayless test cell."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]


PROFILE_PATH = HERE / "profile.json"
SANDBOX_PATH = HERE / "sandbox-profile.json"
FIXTURE_PATH = HERE / "fixture"
PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright/mcp@"
    "sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9"
)
FIXTURE_IMAGE = (
    "python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134"
)
CLIENT_IMAGE = (
    "eva2-living-core@"
    "sha256:d1a861106ecaaa2230e5bcac63232808eced907bf2b1febeff4db2ab7cc161ae"
)
CLIENT_SCRIPT = HERE / "smoke_client.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(command: list[str], *, timeout: float = 20, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def inspect(kind: str, name: str) -> dict[str, Any]:
    value = json.loads(run(["docker", kind, "inspect", name]).stdout)
    require(isinstance(value, list) and len(value) == 1, f"wrong {kind} inspection")
    return value[0]


def main() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    sandbox = json.loads(SANDBOX_PATH.read_text(encoding="utf-8"))
    suffix = uuid4().hex[:12]
    network_name = f"eva-browser-r01-{suffix}"
    fixture_name = f"eva-browser-fixture-{suffix}"
    browser_name = f"eva-browser-sidecar-{suffix}"
    client_name = f"eva-browser-client-{suffix}"
    browser_stopped = False

    image = inspect("image", PLAYWRIGHT_IMAGE)
    require(image["Architecture"] == "amd64", "wrong browser image architecture")
    require(image["Config"]["User"] == "node", "browser image is not non-root")
    require(image["Size"] == profile["image"]["localAmd64ContentSizeBytes"], "browser image size drifted")
    fixture_image = inspect("image", FIXTURE_IMAGE)
    require(fixture_image["Architecture"] == "amd64", "wrong fixture architecture")
    client_image = inspect("image", CLIENT_IMAGE)
    require(client_image["Architecture"] == "amd64", "wrong client architecture")
    require(client_image["Config"]["User"] == "65532:65532", "client image is not non-root")
    require(profile["testCell"]["fixtureImage"] == FIXTURE_IMAGE, "fixture image drifted")
    require(profile["testCell"]["controllerImage"] == CLIENT_IMAGE, "client image drifted")
    require(profile["testCell"]["networkDriver"] == "bridge", "test network driver drifted")
    require(
        profile["testCell"]["gatewaylessBridgeOption"]
        == "com.docker.network.bridge.inhibit_ipv4=true",
        "gatewayless bridge option drifted",
    )

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
                f"type=bind,src={FIXTURE_PATH},dst=/srv/fixture,readonly",
                "--entrypoint",
                "python",
                FIXTURE_IMAGE,
                "-m",
                "http.server",
                "8080",
                "--bind",
                "0.0.0.0",
                "--directory",
                "/srv/fixture",
            ]
        )
        browser_command = [
            "docker",
            "run",
            "-d",
            "--name",
            browser_name,
            "--pull",
            "never",
            "--network",
            network_name,
            "--network-alias",
            "browser.eva.test",
            "--read-only",
        ]
        for path, options in sandbox["tmpfs"].items():
            browser_command.extend(["--tmpfs", f"{path}:{options}"])
        browser_command.extend(
            [
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                str(sandbox["pidsLimit"]),
                "--memory",
                str(sandbox["memoryBytes"]),
                "--cpus",
                "1.0",
                "--entrypoint",
                "node",
                PLAYWRIGHT_IMAGE,
                *sandbox["arguments"],
            ]
        )
        run(browser_command)

        browser = inspect("container", browser_name)
        require(browser["Config"]["User"] == "node", "browser container changed user")
        require(browser["HostConfig"]["ReadonlyRootfs"] is True, "browser root is writable")
        require(browser["HostConfig"]["CapDrop"] == ["ALL"], "browser retained capabilities")
        require(
            browser["HostConfig"]["SecurityOpt"] == ["no-new-privileges"],
            "browser lost no-new-privileges",
        )
        require(browser["HostConfig"]["NetworkMode"] == network_name, "wrong browser network")
        ports = browser["NetworkSettings"]["Ports"] or {}
        require(
            all(bindings is None for bindings in ports.values()),
            "browser port was published",
        )
        require(browser["Mounts"] == [], "browser received an unexpected mount")
        require(browser["Config"]["Entrypoint"] == ["node"], "browser entrypoint drifted")
        require(browser["Config"]["Cmd"] == sandbox["arguments"], "browser arguments drifted")
        require("--sandbox" in browser["Config"]["Cmd"], "Chromium sandbox was not requested")
        require("--no-sandbox" not in browser["Config"]["Cmd"], "Chromium sandbox was disabled")

        network = inspect("network", network_name)
        require(network["Internal"] is True, "browser test network has external egress")
        require(
            network["Options"]
            == {"com.docker.network.bridge.inhibit_ipv4": "true"},
            "browser bridge inhibition drifted",
        )
        require(
            network["IPAM"]["Config"][0]["Gateway"] == "",
            "browser test network gained a host gateway",
        )
        require(len(network["Containers"]) == 2, "browser test network has another peer")

        browser_ip = next(iter(browser["NetworkSettings"]["Networks"].values()))["IPAddress"]
        try:
            with socket.create_connection((browser_ip, 8931), timeout=2):
                raise RuntimeError("Docker host reached the gatewayless browser")
        except OSError:
            pass

        egress = run(
            [
                "docker",
                "exec",
                browser_name,
                "node",
                "-e",
                "fetch('https://example.com',{signal:AbortSignal.timeout(3000)}).then(()=>process.exit(17)).catch(()=>process.exit(0))",
            ],
            timeout=8,
            check=False,
        )
        require(egress.returncode == 0, "browser container reached the public internet")

        client = run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                client_name,
                "--pull",
                "never",
                "--network",
                network_name,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "64",
                "--memory",
                "128m",
                "--cpus",
                "0.5",
                "--user",
                "65532:65532",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                f"type=bind,src={REPOSITORY / 'src'},dst=/candidate/src,readonly",
                "--mount",
                f"type=bind,src={REPOSITORY / 'connector_sdk'},dst=/candidate/connector_sdk,readonly",
                "--mount",
                f"type=bind,src={PROFILE_PATH},dst=/candidate/profile.json,readonly",
                "--mount",
                f"type=bind,src={CLIENT_SCRIPT},dst=/candidate/smoke_client.py,readonly",
                "--entrypoint",
                "python3",
                CLIENT_IMAGE,
                "/candidate/smoke_client.py",
                "http://browser.eva.test:8931/mcp",
            ],
            timeout=25,
            check=False,
        )
        require(
            client.returncode == 0,
            f"gatewayless browser controller failed: {client.stderr[-2_000:]}",
        )
        result = json.loads(client.stdout)

        fixture_logs = run(["docker", "logs", fixture_name]).stderr
        require(
            fixture_logs.count('"GET /index.html HTTP/1.1" 200') == 1,
            "browser did not perform exactly one admitted page GET",
        )
        require('"GET /' not in fixture_logs.replace('"GET /index.html HTTP/1.1" 200', ""), "browser fetched another fixture path")

        started_stop = time.monotonic()
        run(["docker", "stop", "--time", "2", browser_name], timeout=6)
        stop_milliseconds = round((time.monotonic() - started_stop) * 1_000)
        browser_stopped = True
        require(stop_milliseconds <= 5_000, "browser sidecar did not stop within its bound")

        print(
            json.dumps(
                {
                    "browserImage": profile["image"]["amd64Digest"],
                    "browserUser": browser["Config"]["User"],
                    "externalEgressBlocked": True,
                    "finalUrl": result["finalUrl"],
                    "fixtureGets": 1,
                    "gatewaylessNetwork": True,
                    "hostNetworkBlocked": True,
                    "internalNetwork": True,
                    "matchedVisibleTexts": result["matchedVisibleTexts"],
                    "publishedPorts": 0,
                    "sandboxRequested": True,
                    "snapshotSha256": result["snapshotSha256"],
                    "stopMilliseconds": stop_milliseconds,
                    "toolSequence": result["toolSequence"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    finally:
        run(["docker", "rm", "-f", client_name], timeout=10, check=False)
        run(["docker", "rm", "-f", browser_name], timeout=10, check=False)
        run(["docker", "rm", "-f", fixture_name], timeout=10, check=False)
        run(["docker", "network", "rm", network_name], timeout=10, check=False)
        if not browser_stopped:
            # The result is deliberately not promoted when bounded shutdown did not run.
            pass


if __name__ == "__main__":
    main()
