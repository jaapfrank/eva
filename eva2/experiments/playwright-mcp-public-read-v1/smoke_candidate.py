#!/usr/bin/env python3
"""Prove one real public page read without giving the browser internet access."""

from __future__ import annotations

import hashlib
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
SQUID_CONFIG = HERE / "squid.conf"
CLIENT_SCRIPT = HERE / "smoke_client.py"
PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright/mcp@sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9"
SQUID_IMAGE = "ubuntu/squid@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029"
CLIENT_IMAGE = "eva2-living-core@sha256:d1a861106ecaaa2230e5bcac63232808eced907bf2b1febeff4db2ab7cc161ae"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(command: list[str], *, timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True, timeout=timeout)


def inspect(kind: str, name: str) -> dict[str, Any]:
    value = json.loads(run(["docker", kind, "inspect", name]).stdout)
    require(isinstance(value, list) and len(value) == 1, f"wrong {kind} inspection")
    return value[0]


def append_tmpfs(command: list[str], path: str, options: str) -> None:
    command.extend(["--tmpfs", f"{path}:{options}"])


def main() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    suffix = uuid4().hex[:12]
    browser_network = f"eva-public-read-internal-{suffix}"
    proxy_network = f"eva-public-read-egress-{suffix}"
    proxy_name = f"eva-public-read-proxy-{suffix}"
    browser_name = f"eva-public-read-browser-{suffix}"
    client_name = f"eva-public-read-client-{suffix}"
    browser_stopped = False
    squid_hash = hashlib.sha256(SQUID_CONFIG.read_bytes()).hexdigest()

    require(profile["proxy"]["configSha256"] == squid_hash, "proxy configuration drifted")
    browser_image = inspect("image", PLAYWRIGHT_IMAGE)
    require(browser_image["Architecture"] == "amd64", "wrong browser architecture")
    require(browser_image["Config"]["User"] == profile["image"]["user"], "browser user drifted")
    proxy_image = inspect("image", SQUID_IMAGE)
    require(proxy_image["Architecture"] == "amd64", "wrong proxy architecture")
    require(proxy_image["Size"] == profile["proxy"]["imageSizeBytes"], "proxy image drifted")
    client_image = inspect("image", CLIENT_IMAGE)
    require(client_image["Config"]["User"] == "65532:65532", "controller is not non-root")

    run(["docker", "network", "create", "--internal", "--driver", "bridge", "--opt", "com.docker.network.bridge.inhibit_ipv4=true", browser_network])
    run(["docker", "network", "create", "--driver", "bridge", proxy_network])
    try:
        proxy_command = [
            "docker", "run", "-d", "--name", proxy_name, "--pull", "never", "--network", proxy_network,
            "--read-only", "--user", profile["proxy"]["user"], "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "128m", "--cpus", "0.5",
        ]
        append_tmpfs(proxy_command, "/tmp", "rw,nosuid,nodev,noexec,size=16m,uid=13,gid=13")
        append_tmpfs(proxy_command, "/run/squid", "rw,nosuid,nodev,noexec,size=4m,uid=13,gid=13")
        append_tmpfs(proxy_command, "/var/log/squid", "rw,nosuid,nodev,noexec,size=16m,uid=13,gid=13")
        append_tmpfs(proxy_command, "/var/spool/squid", "rw,nosuid,nodev,noexec,size=16m,uid=13,gid=13")
        proxy_command.extend([
            "--mount", f"type=bind,src={SQUID_CONFIG},dst=/etc/squid/squid.conf,readonly",
            "--entrypoint", "squid", SQUID_IMAGE, "-N", "-f", "/etc/squid/squid.conf",
        ])
        run(proxy_command)
        run(["docker", "network", "connect", "--alias", "proxy.eva.test", browser_network, proxy_name])

        browser_command = ["docker", "run", "-d", "--name", browser_name, "--pull", "never", "--network", browser_network, "--network-alias", "browser.eva.test", "--read-only"]
        for path, options in profile["sandbox"]["tmpfs"].items():
            append_tmpfs(browser_command, path, options)
        browser_command.extend([
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", str(profile["sandbox"]["pidsLimit"]),
            "--memory", str(profile["sandbox"]["memoryBytes"]), "--cpus", "1.0", "--entrypoint", "node", PLAYWRIGHT_IMAGE,
            *profile["sandbox"]["arguments"],
        ])
        run(browser_command)

        browser = inspect("container", browser_name)
        proxy = inspect("container", proxy_name)
        internal = inspect("network", browser_network)
        require(browser["Config"]["User"] == "node", "browser user changed")
        require(browser["HostConfig"]["ReadonlyRootfs"] is True, "browser root is writable")
        require(browser["HostConfig"]["CapDrop"] == ["ALL"], "browser retained capabilities")
        require(browser["HostConfig"]["SecurityOpt"] == ["no-new-privileges"], "browser lost no-new-privileges")
        require(set(browser["NetworkSettings"]["Networks"]) == {browser_network}, "browser has an egress network")
        require(internal["Internal"] is True, "browser network is not internal")
        require(internal["Options"] == {"com.docker.network.bridge.inhibit_ipv4": "true"}, "browser network gateway changed")
        require(set(proxy["NetworkSettings"]["Networks"]) == {browser_network, proxy_network}, "proxy network shape drifted")
        require(proxy["Config"]["User"] == "13:13", "proxy is not non-root")
        require(proxy["HostConfig"]["ReadonlyRootfs"] is True, "proxy root is writable")
        require(proxy["HostConfig"]["CapDrop"] == ["ALL"], "proxy retained capabilities")
        require(not browser["Mounts"], "browser received an unexpected mount")
        require(all(value is None for value in (browser["NetworkSettings"]["Ports"] or {}).values()), "browser port was published")

        browser_ip = next(iter(browser["NetworkSettings"]["Networks"].values()))["IPAddress"]
        try:
            with socket.create_connection((browser_ip, 8931), timeout=2):
                raise RuntimeError("Docker host reached the private browser")
        except OSError:
            pass
        direct_egress = run([
            "docker", "exec", browser_name, "node", "-e",
            "const s=require('net').connect({host:'1.1.1.1',port:443});s.setTimeout(3000);s.on('connect',()=>process.exit(17));s.on('timeout',()=>process.exit(0));s.on('error',()=>process.exit(0));",
        ], timeout=8, check=False)
        require(direct_egress.returncode == 0, "browser process reached the public internet directly")

        client = run([
            "docker", "run", "--rm", "--name", client_name, "--pull", "never", "--network", browser_network,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "128m", "--cpus", "0.5", "--user", "65532:65532", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,src={REPOSITORY / 'src'},dst=/candidate/src,readonly",
            "--mount", f"type=bind,src={REPOSITORY / 'connector_sdk'},dst=/candidate/connector_sdk,readonly",
            "--mount", f"type=bind,src={PROFILE_PATH},dst=/candidate/profile.json,readonly",
            "--mount", f"type=bind,src={CLIENT_SCRIPT},dst=/candidate/smoke_client.py,readonly",
            "--entrypoint", "python3", CLIENT_IMAGE, "/candidate/smoke_client.py", "http://browser.eva.test:8931/mcp",
        ], timeout=30, check=False)
        require(client.returncode == 0, f"public browser read failed: {client.stderr[-2000:]}")
        result = json.loads(client.stdout)

        denial = run([
            "docker", "run", "--rm", "--pull", "never", "--network", browser_network, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "128m", "--cpus", "0.5", "--user", "65532:65532",
            "--entrypoint", "python3", CLIENT_IMAGE, "-c",
            "import sys,urllib.request; op=urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://proxy.eva.test:3128'}));\ntry: op.open('https://www.iana.org/',timeout=8); sys.exit(17)\nexcept Exception as error: sys.exit(0 if '403 Forbidden' in str(error) else 3)",
        ], timeout=12, check=False)
        require(
            denial.returncode == 0,
            f"proxy did not return its expected 403 denial: rc={denial.returncode} stderr={denial.stderr[-500:]}",
        )

        started_stop = time.monotonic()
        run(["docker", "stop", "--time", "2", browser_name], timeout=6)
        stop_milliseconds = round((time.monotonic() - started_stop) * 1000)
        browser_stopped = True
        require(stop_milliseconds <= 5000, "browser did not stop within bound")
        print(json.dumps({
            "allowlistedDestination": profile["proxy"]["allowedDestination"], "browserImage": profile["image"]["amd64Digest"],
            "browserDirectEgressBlocked": True, "finalUrl": result["finalUrl"], "proxyDeniedIana": True,
            "publishedPorts": 0, "snapshotSha256": result["snapshotSha256"], "stopMilliseconds": stop_milliseconds,
            "title": result["title"], "toolSequence": result["toolSequence"],
        }, sort_keys=True, separators=(",", ":")))
    finally:
        run(["docker", "rm", "-f", client_name], timeout=10, check=False)
        run(["docker", "rm", "-f", browser_name], timeout=10, check=False)
        run(["docker", "rm", "-f", proxy_name], timeout=10, check=False)
        run(["docker", "network", "rm", browser_network], timeout=10, check=False)
        run(["docker", "network", "rm", proxy_network], timeout=10, check=False)
        if not browser_stopped:
            pass


if __name__ == "__main__":
    main()
