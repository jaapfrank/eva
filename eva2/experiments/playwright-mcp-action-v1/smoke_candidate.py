#!/usr/bin/env python3
"""Run the exact BROWSER-A02 ledger, click, readback, and crash proof."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any
from uuid import uuid4


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PROFILE_PATH = HERE / "worker-profile.json"
FIXTURE_PATH = HERE / "fixture_server.py"
TEST_PATH = REPOSITORY / "tests/postgres/test_playwright_mcp_action_chain.py"
LOCAL_SITE_PACKAGES = Path("/home/administrator/.local/lib/python3.12/site-packages")
SYSTEM_PYGMENTS = Path("/usr/lib/python3/dist-packages/pygments")
PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright/mcp@"
    "sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9"
)
FIXTURE_IMAGE = (
    "python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134"
)
POSTGRES_IMAGE = (
    "postgres@sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
)
CONTROLLER_IMAGE = (
    "eva2-living-core@"
    "sha256:d1a861106ecaaa2230e5bcac63232808eced907bf2b1febeff4db2ab7cc161ae"
)
BROWSER_ARGUMENTS = [
    "/app/cli.js",
    "--headless",
    "--browser",
    "chromium",
    "--sandbox",
    "--isolated",
    "--block-service-workers",
    "--image-responses",
    "omit",
    "--codegen",
    "none",
    "--timeout-action",
    "3000",
    "--timeout-navigation",
    "10000",
    "--timeout-settle",
    "0",
    "--allowed-origins",
    "http://fixture.eva.test:8080",
    "--port",
    "8931",
    "--host",
    "0.0.0.0",
    "--allowed-hosts",
    "browser.eva.test:8931",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(
    command: list[str],
    *,
    timeout: float = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=check,
        text=True,
        timeout=timeout,
    )


def inspect(kind: str, name: str) -> dict[str, Any]:
    result = json.loads(run(["docker", kind, "inspect", name]).stdout)
    require(isinstance(result, list) and len(result) == 1, "Docker inspect differs")
    return result[0]


def stop_and_remove(names: list[str]) -> None:
    for name in names:
        run(["docker", "stop", "--time", "2", name], timeout=6, check=False)
    for name in names:
        run(["docker", "rm", name], timeout=6, check=False)


def main() -> None:
    require(PROFILE_PATH.is_file(), "sealed worker profile is missing")
    require(FIXTURE_PATH.is_file(), "fixture server is missing")
    require(TEST_PATH.is_file(), "PostgreSQL browser proof is missing")
    require(LOCAL_SITE_PACKAGES.is_dir(), "local test toolchain is missing")
    require(SYSTEM_PYGMENTS.is_dir(), "system Pygments package is missing")
    suffix = uuid4().hex[:12]
    database = "eva2_action_chain_disposable_browser_a02"
    db_network = f"eva-a02-db-{suffix}"
    browser_network = f"eva-a02-browser-{suffix}"
    db_name = f"eva-a02-db-{suffix}"
    fixture_name = f"eva-a02-fixture-{suffix}"
    browser_name = f"eva-a02-browser-{suffix}"
    controller_name = f"eva-a02-controller-{suffix}"
    containers = [controller_name, browser_name, fixture_name, db_name]
    networks = [browser_network, db_network]

    with tempfile.TemporaryDirectory(prefix="eva-a02-") as temporary:
        extra_packages = Path(temporary) / "extra"
        shutil.copytree(SYSTEM_PYGMENTS, extra_packages / "pygments")
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
                db_network,
            ]
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
                browser_network,
            ]
        )
        try:
            run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    db_name,
                    "--pull",
                    "never",
                    "--network",
                    db_network,
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,nosuid,nodev,size=512m",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "--env",
                    f"POSTGRES_DB={database}",
                    POSTGRES_IMAGE,
                ]
            )
            for attempt in range(40):
                ready = run(
                    [
                        "docker",
                        "exec",
                        db_name,
                        "pg_isready",
                        "-U",
                        "postgres",
                        "-d",
                        database,
                    ],
                    timeout=3,
                    check=False,
                )
                if ready.returncode == 0:
                    break
                if attempt == 39:
                    raise RuntimeError("disposable PostgreSQL did not become ready")
                time.sleep(0.25)
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
                    browser_network,
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
            run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    browser_name,
                    "--pull",
                    "never",
                    "--network",
                    browser_network,
                    "--network-alias",
                    "browser.eva.test",
                    "--read-only",
                    "--tmpfs",
                    "/dev/shm:rw,nosuid,size=256m,mode=1777",
                    "--tmpfs",
                    "/home/node:rw,nosuid,size=128m,uid=1000,gid=1000,mode=0700",
                    "--tmpfs",
                    "/tmp:rw,nosuid,size=384m,mode=1777",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "192",
                    "--memory",
                    "1073741824",
                    "--cpus",
                    "1.0",
                    "--entrypoint",
                    "node",
                    PLAYWRIGHT_IMAGE,
                    *BROWSER_ARGUMENTS,
                ]
            )
            db = inspect("container", db_name)
            db_ip = db["NetworkSettings"]["Networks"][db_network]["IPAddress"]
            require(bool(db_ip), "database address is missing")
            run(
                [
                    "docker",
                    "create",
                    "--name",
                    controller_name,
                    "--pull",
                    "never",
                    "--network",
                    db_network,
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "128",
                    "--memory",
                    "512m",
                    "--cpus",
                    "1.0",
                    "--user",
                    "65532:65532",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--env",
                    f"EVA2_TEST_POSTGRES_DSN=postgresql+psycopg://postgres@{db_ip}:5432/{database}",
                    "--env",
                    "PYTHONPATH=/candidate/src:/candidate/tests/postgres:/test-site:/test-extra",
                    "--mount",
                    f"type=bind,src={REPOSITORY},dst=/candidate,readonly",
                    "--mount",
                    f"type=bind,src={LOCAL_SITE_PACKAGES},dst=/test-site,readonly",
                    "--mount",
                    f"type=bind,src={extra_packages},dst=/test-extra,readonly",
                    "--entrypoint",
                    "sleep",
                    CONTROLLER_IMAGE,
                    "180",
                ]
            )
            run(["docker", "network", "connect", browser_network, controller_name])
            run(["docker", "start", controller_name])

            browser = inspect("container", browser_name)
            controller = inspect("container", controller_name)
            require(browser["Config"]["User"] == "node", "browser user drifted")
            require(
                browser["HostConfig"]["ReadonlyRootfs"] is True,
                "browser root is writable",
            )
            require(
                browser["HostConfig"]["CapDrop"] == ["ALL"],
                "browser retained capabilities",
            )
            require(
                browser["NetworkSettings"]["Ports"] in ({}, None),
                "browser published a port",
            )
            require(
                set(browser["NetworkSettings"]["Networks"]) == {browser_network},
                "browser reached another network",
            )
            require(
                set(db["NetworkSettings"]["Networks"]) == {db_network},
                "database reached the browser network",
            )
            require(
                set(controller["NetworkSettings"]["Networks"])
                == {db_network, browser_network},
                "controller networks differ",
            )
            for network_name in networks:
                network = inspect("network", network_name)
                require(network["Internal"] is True, "test network gained egress")
                require(
                    network["IPAM"]["Config"][0]["Gateway"] == "",
                    "test network gained a host gateway",
                )
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
            require(egress.returncode == 0, "browser reached the public internet")

            test = run(
                [
                    "docker",
                    "exec",
                    "--workdir",
                    "/candidate",
                    controller_name,
                    "python3",
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "--tb=short",
                    "-p",
                    "no:cacheprovider",
                    "/candidate/tests/postgres/test_playwright_mcp_action_chain.py",
                ],
                timeout=70,
                check=False,
            )
            require(
                test.returncode == 0,
                f"A02 proof failed: {test.stdout[-4_000:]}{test.stderr[-2_000:]}",
            )
            require(
                "3 passed" in test.stdout, "A02 did not execute all three scenarios"
            )
            print(
                json.dumps(
                    {
                        "browserImage": PLAYWRIGHT_IMAGE,
                        "browserNetworkCount": 1,
                        "crashAfterClickNoSecondCall": True,
                        "crashBeforeClickNoSecondCall": True,
                        "externalEgressBlocked": True,
                        "gatewaylessNetworks": 2,
                        "normalClickStateVerified": True,
                        "postgresImage": POSTGRES_IMAGE,
                        "publishedBrowserPorts": 0,
                        "scenariosPassed": 3,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        finally:
            stop_and_remove(containers)
            for network in networks:
                run(["docker", "network", "rm", network], timeout=6, check=False)


if __name__ == "__main__":
    main()
