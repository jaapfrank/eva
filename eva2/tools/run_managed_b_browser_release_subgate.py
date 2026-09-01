#!/usr/bin/env python3
"""Run the isolated Playwright action-chain release subgate.

This physical subgate can run alone and is also consumed by the broad managed-B
required-test runner. It executes the one browser module against product code
already present in an exact local candidate image. Repository product sources
are never mounted in the controller.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import tempfile
import time
from typing import Any
import xml.etree.ElementTree as ET


CONTRACT = "EvaManagedBBrowserReleaseSubgate/v1"
PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright/mcp@"
    "sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9"
)
FIXTURE_IMAGE = (
    "docker.io/library/python@"
    "sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134"
)
POSTGRES_IMAGE = (
    "docker.io/library/postgres@"
    "sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
)
TEST_MODULE = "tests/postgres/test_playwright_mcp_action_chain.py"
PROFILE_RELATIVE = "tests/fixtures/browser/worker-profile.json"
WORKER_RELATIVE = "tests/fixtures/browser/playwright_mcp_worker.py"
FIXTURE_RELATIVE = "experiments/playwright-mcp-action-v1/fixture_server.py"
SCHEDULE_RELATIVE = "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
EXPECTED_NODEIDS = (
    TEST_MODULE
    + "::test_owner_stop_after_browser_authorization_prevents_the_real_click",
    TEST_MODULE
    + "::test_real_browser_click_crosses_durable_ledger_and_server_readback",
    TEST_MODULE + "::test_browser_click_crash_never_starts_a_second_provider_call"
    "[before-before_effect_io-0-not_committed]",
    TEST_MODULE + "::test_browser_click_crash_never_starts_a_second_provider_call"
    "[after-after_effect_io-1-committed]",
)
BROWSER_ARGUMENTS = (
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
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_OBJECT_ID = re.compile(r"[0-9a-f]{64}\Z")


class BrowserSubgateError(RuntimeError):
    """A fail-closed admission, execution, or cleanup error."""


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], float], Completed]


def _command(argv: Sequence[str], timeout: float) -> Completed:
    result = subprocess.run(
        list(argv),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return Completed(result.returncode, result.stdout, result.stderr)


def _run(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    timeout: float = 30,
    label: str,
) -> Completed:
    result = runner(tuple(argv), timeout)
    if result.returncode != 0:
        raise BrowserSubgateError(f"{label} failed")
    return result


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_regular(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BrowserSubgateError(f"{label} is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
    ):
        raise BrowserSubgateError(f"{label} is not one immutable regular file")
    return path.read_bytes()


def directory_sha256(root: Path, *, ignore_python_cache: bool = False) -> str:
    """Hash one closed regular-file tree without following links."""

    if not root.is_absolute():
        raise BrowserSubgateError("hashed directory must be absolute")
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise BrowserSubgateError("hashed directory is inaccessible") from error
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise BrowserSubgateError("hashed directory is not a real directory")
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        relative_path = PurePosixPath(relative)
        if ignore_python_cache and (
            "__pycache__" in relative_path.parts
            or ".pytest_cache" in relative_path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BrowserSubgateError(f"unsafe tree entry: {relative}")
        raw = path.read_bytes()
        rows.append(
            {
                "mode": stat.S_IMODE(metadata.st_mode),
                "path": relative,
                "sha256": _sha256(raw),
                "size": len(raw),
            }
        )
    if not rows:
        raise BrowserSubgateError("hashed directory is empty")
    canonical = json.dumps(
        rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return _sha256(canonical)


def _one_inspect(result: Completed, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BrowserSubgateError(f"{label} inspect is invalid") from error
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise BrowserSubgateError(f"{label} inspect differs")
    return value[0]


def _inspect(
    runner: CommandRunner, kind: str, name: str, *, label: str
) -> dict[str, Any]:
    result = _run(
        runner,
        ("docker", kind, "inspect", name),
        timeout=15,
        label=f"{label} inspect",
    )
    return _one_inspect(result, label=label)


def _preflight_image(
    runner: CommandRunner,
    reference: str,
    *,
    candidate_id: str | None = None,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    image = _inspect(runner, "image", reference, label="image")
    actual_id = image.get("Id")
    if not isinstance(actual_id, str) or _IMAGE_ID.fullmatch(actual_id) is None:
        raise BrowserSubgateError("image ID is invalid")
    if candidate_id is not None:
        config = image.get("Config", {})
        labels = config.get("Labels") or {}
        environment = config.get("Env") or []
        if (
            actual_id != candidate_id
            or not isinstance(labels, dict)
            or labels.get("io.eva2.source-commit") != expected_source_commit
            or not isinstance(environment, list)
            or "LD_LIBRARY_PATH=/usr/local/lib" not in environment
        ):
            raise BrowserSubgateError("candidate image authority differs")
        if config.get("User") != "65532:65532":
            raise BrowserSubgateError("candidate runtime user differs")
    else:
        repo_digests = image.get("RepoDigests") or []
        digest = reference.split("@", 1)[1]
        if not any(
            isinstance(item, str) and item.endswith("@" + digest)
            for item in repo_digests
        ):
            raise BrowserSubgateError("pinned image digest is not locally resolved")
    return image


def _validate_profile(repository_root: Path) -> dict[str, str]:
    profile_path = repository_root / PROFILE_RELATIVE
    worker_path = repository_root / WORKER_RELATIVE
    fixture_path = repository_root / FIXTURE_RELATIVE
    schedule_path = repository_root / SCHEDULE_RELATIVE
    profile_raw = _safe_regular(profile_path, label="browser worker profile")
    worker_raw = _safe_regular(worker_path, label="browser worker")
    fixture_raw = _safe_regular(fixture_path, label="browser fixture server")
    schedule_raw = _safe_regular(schedule_path, label="browser schedule fixture")
    try:
        profile = json.loads(profile_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrowserSubgateError("browser worker profile is invalid") from error
    if not isinstance(profile, dict):
        raise BrowserSubgateError("browser worker profile is invalid")
    manifests = profile.get("connectorManifests")
    action = profile.get("actionProfile")
    worker_hash = _sha256(worker_raw)
    if (
        profile.get("contract") != "EvaPlaywrightMcpActionWorkerProfile/v1"
        or not isinstance(manifests, dict)
        or set(manifests) != {"browser.fixture.click", "effect.readback"}
        or any(
            not isinstance(item, dict) or item.get("executable_sha256") != worker_hash
            for item in manifests.values()
        )
        or not isinstance(action, dict)
        or action.get("server_image_digest") != PLAYWRIGHT_IMAGE.split("@", 1)[1]
    ):
        raise BrowserSubgateError("browser worker/profile authority differs")
    return {
        "fixtureServerSha256": _sha256(fixture_raw),
        "profileSha256": _sha256(profile_raw),
        "scheduleSha256": _sha256(schedule_raw),
        "workerSha256": worker_hash,
    }


def _network_is_closed(network: dict[str, Any]) -> bool:
    configs = network.get("IPAM", {}).get("Config") or []
    return (
        network.get("Internal") is True
        and network.get("Options", {}).get("com.docker.network.bridge.inhibit_ipv4")
        == "true"
        and all(not item.get("Gateway") for item in configs if isinstance(item, dict))
    )


def _object_id(value: dict[str, Any], *, label: str) -> str:
    identifier = value.get("Id")
    if not isinstance(identifier, str) or _OBJECT_ID.fullmatch(identifier) is None:
        raise BrowserSubgateError(f"{label} object ID differs")
    return identifier


def _container_networks(container: dict[str, Any]) -> set[str]:
    networks = container.get("NetworkSettings", {}).get("Networks") or {}
    if not isinstance(networks, dict):
        raise BrowserSubgateError("container networks are invalid")
    return set(networks)


def _mount_matches(
    mounts: dict[object, object],
    destination: str,
    *,
    source: Path,
    read_write: bool,
) -> bool:
    mount = mounts.get(destination)
    return (
        isinstance(mount, dict)
        and mount.get("Type") == "bind"
        and mount.get("Source") == str(source)
        and mount.get("Destination") == destination
        and mount.get("RW") is read_write
    )


def _assert_container(
    container: dict[str, Any],
    *,
    resource_label: str,
    image_id: str,
    user: str,
    networks: set[str],
    authority_label: str,
    read_only: bool = True,
) -> None:
    host = container.get("HostConfig") or {}
    config = container.get("Config") or {}
    declared_ports = container.get("NetworkSettings", {}).get("Ports") or {}
    checks = {
        "image": container.get("Image") == image_id,
        "user": config.get("User") == user,
        "authority_label": config.get("Labels", {}).get(
            "io.eva2.browser-release-subgate"
        )
        == authority_label,
        "read_only": host.get("ReadonlyRootfs") is read_only,
        "capabilities": set(host.get("CapDrop") or []) == {"ALL"},
        "not_privileged": host.get("Privileged") is False,
        "networks": _container_networks(container) == networks,
        "no_port_bindings": not (host.get("PortBindings") or {}),
        "no_published_ports": isinstance(declared_ports, dict)
        and all(not bindings for bindings in declared_ports.values()),
        "running": container.get("State", {}).get("Running") is True,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise BrowserSubgateError(
            f"{resource_label} container isolation differs: {','.join(failed)}"
        )


def _junit_counts(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise BrowserSubgateError("browser JUnit is invalid") from error
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise BrowserSubgateError("browser JUnit contains no suite")
    counts = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts != {"tests": 4, "failures": 0, "errors": 0, "skipped": 0}:
        raise BrowserSubgateError("browser JUnit counts differ")
    return counts


def _atomic_receipt(path: Path, payload: dict[str, object]) -> None:
    if not path.is_absolute() or path.exists() or not path.parent.is_dir():
        raise BrowserSubgateError("receipt path must be new, absolute, and parented")
    raw = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _cleanup(
    runner: CommandRunner,
    *,
    containers: Sequence[str],
    networks: Sequence[str],
) -> None:
    failures: list[str] = []
    for name in containers:
        runner(("docker", "rm", "--force", name), 15)
    for name in networks:
        runner(("docker", "network", "rm", name), 15)
    for kind, names in (("container", containers), ("network", networks)):
        for name in names:
            if runner(("docker", kind, "inspect", name), 10).returncode == 0:
                failures.append(f"{kind}:{name}")
    if failures:
        raise BrowserSubgateError("cleanup left resources: " + ",".join(failures))


def run_browser_subgate(
    *,
    repository_root: Path,
    candidate_image_id: str,
    expected_candidate_source_commit: str,
    gate_site_packages: Path,
    expected_gate_site_packages_sha256: str,
    expected_tests_sha256: str,
    expected_tools_sha256: str,
    expected_harness_sha256: str,
    receipt_path: Path,
    command_runner: CommandRunner = _command,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    if not repository_root.is_absolute() or not gate_site_packages.is_absolute():
        raise BrowserSubgateError("repository and gate site-packages must be absolute")
    repository_root = repository_root.resolve()
    gate_site_packages = gate_site_packages.resolve()
    harness_path = Path(__file__).resolve()
    tests_root = (repository_root / "tests").resolve()
    tools_root = (repository_root / "tools").resolve()
    if (
        _IMAGE_ID.fullmatch(candidate_image_id) is None
        or _COMMIT.fullmatch(expected_candidate_source_commit) is None
        or _SHA256.fullmatch(expected_gate_site_packages_sha256) is None
        or _SHA256.fullmatch(expected_tests_sha256) is None
        or _SHA256.fullmatch(expected_tools_sha256) is None
        or _SHA256.fullmatch(expected_harness_sha256) is None
    ):
        raise BrowserSubgateError("external authority argument is invalid")
    if (
        not gate_site_packages.is_absolute()
        or not tests_root.is_dir()
        or not tools_root.is_dir()
    ):
        raise BrowserSubgateError("test inputs are invalid")
    if (
        not receipt_path.is_absolute()
        or receipt_path.exists()
        or not receipt_path.parent.is_dir()
    ):
        raise BrowserSubgateError("receipt path must be new, absolute, and parented")
    actual_gate_hash = directory_sha256(gate_site_packages)
    actual_tests_hash = directory_sha256(tests_root, ignore_python_cache=True)
    actual_tools_hash = directory_sha256(tools_root, ignore_python_cache=True)
    actual_harness_hash = _sha256(_safe_regular(harness_path, label="browser harness"))
    if (
        actual_gate_hash != expected_gate_site_packages_sha256
        or actual_tests_hash != expected_tests_sha256
        or actual_tools_hash != expected_tools_sha256
        or actual_harness_hash != expected_harness_sha256
    ):
        raise BrowserSubgateError("external test authority differs")
    input_hashes = _validate_profile(repository_root)
    test_module_hash = _sha256(
        _safe_regular(repository_root / TEST_MODULE, label="browser test module")
    )

    image_authority = {
        candidate_image_id: _preflight_image(
            command_runner,
            candidate_image_id,
            candidate_id=candidate_image_id,
            expected_source_commit=expected_candidate_source_commit,
        ),
        PLAYWRIGHT_IMAGE: _preflight_image(command_runner, PLAYWRIGHT_IMAGE),
        FIXTURE_IMAGE: _preflight_image(command_runner, FIXTURE_IMAGE),
        POSTGRES_IMAGE: _preflight_image(command_runner, POSTGRES_IMAGE),
    }
    image_ids = {key: value["Id"] for key, value in image_authority.items()}

    nonce = secrets.token_hex(8)
    prefix = f"eva2-browser-gate-{nonce}"
    database = f"eva2_action_chain_disposable_{nonce}"
    db_network = prefix + "-db-net"
    browser_network = prefix + "-browser-net"
    db_name = prefix + "-db"
    fixture_name = prefix + "-fixture"
    browser_name = prefix + "-browser"
    controller_name = prefix + "-controller"
    containers = (controller_name, browser_name, fixture_name, db_name)
    networks = (browser_network, db_network)
    label = f"io.eva2.browser-release-subgate={nonce}"
    resources_started = False
    cleanup_complete = False
    receipt: dict[str, object] | None = None

    with tempfile.TemporaryDirectory(prefix="eva2-browser-subgate-") as temporary:
        output_directory = Path(temporary) / "output"
        output_directory.mkdir(mode=0o777)
        output_directory.chmod(0o777)
        junit_host = Path(temporary) / "junit.xml"
        try:
            resources_started = True
            for network in networks:
                _run(
                    command_runner,
                    (
                        "docker",
                        "network",
                        "create",
                        "--internal",
                        "--driver",
                        "bridge",
                        "--opt",
                        "com.docker.network.bridge.inhibit_ipv4=true",
                        "--label",
                        label,
                        network,
                    ),
                    label="internal network creation",
                )
            _run(
                command_runner,
                (
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    db_name,
                    "--pull",
                    "never",
                    "--network",
                    db_network,
                    "--network-alias",
                    "db.eva.test",
                    "--label",
                    label,
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "999:999",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=512m,uid=999,gid=999,mode=0700",
                    "--tmpfs",
                    "/var/run/postgresql:rw,nosuid,nodev,noexec,size=16m,uid=999,gid=999,mode=0700",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "--env",
                    f"POSTGRES_DB={database}",
                    POSTGRES_IMAGE,
                ),
                label="database start",
            )
            for attempt in range(40):
                ready = command_runner(
                    (
                        "docker",
                        "exec",
                        db_name,
                        "pg_isready",
                        "-U",
                        "postgres",
                        "-d",
                        database,
                    ),
                    5,
                )
                if ready.returncode == 0:
                    break
                if attempt == 39:
                    raise BrowserSubgateError("disposable PostgreSQL was not ready")
                pause(0.25)
            _run(
                command_runner,
                (
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    fixture_name,
                    "--pull",
                    "never",
                    "--network",
                    browser_network,
                    "--network-alias",
                    "fixture.eva.test",
                    "--label",
                    label,
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
                    f"type=bind,src={repository_root / FIXTURE_RELATIVE},dst=/fixture_server.py,readonly",
                    "--entrypoint",
                    "/usr/local/bin/python",
                    FIXTURE_IMAGE,
                    "/fixture_server.py",
                ),
                label="fixture start",
            )
            _run(
                command_runner,
                (
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    browser_name,
                    "--pull",
                    "never",
                    "--network",
                    browser_network,
                    "--network-alias",
                    "browser.eva.test",
                    "--label",
                    label,
                    "--read-only",
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
                    "--user",
                    "1000:1000",
                    "--tmpfs",
                    "/dev/shm:rw,nosuid,nodev,size=256m,mode=1777",
                    "--tmpfs",
                    "/home/node:rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=0700",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=384m,mode=1777",
                    "--entrypoint",
                    "node",
                    PLAYWRIGHT_IMAGE,
                    *BROWSER_ARGUMENTS,
                ),
                label="browser start",
            )
            _run(
                command_runner,
                (
                    "docker",
                    "create",
                    "--name",
                    controller_name,
                    "--pull",
                    "never",
                    "--network",
                    db_network,
                    "--label",
                    label,
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "160",
                    "--memory",
                    "768m",
                    "--cpus",
                    "1.0",
                    "--user",
                    "65532:65532",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,noexec,size=256m,uid=65532,gid=65532,mode=0700",
                    "--tmpfs",
                    "/gate:rw,nosuid,nodev,noexec,size=16m,uid=65532,gid=65532,mode=0700",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--env",
                    "PYTHONPYCACHEPREFIX=/tmp/pycache",
                    "--env",
                    "PYTHONPATH=/opt/eva/src:/opt/eva:/gate:/gate/tests/postgres:/gate/site-packages",
                    "--env",
                    f"EVA2_TEST_POSTGRES_DSN=postgresql+psycopg://postgres@db.eva.test:5432/{database}",
                    "--mount",
                    f"type=bind,src={tests_root},dst=/gate/tests,readonly",
                    "--mount",
                    f"type=bind,src={tools_root},dst=/gate/tools,readonly",
                    "--mount",
                    f"type=bind,src={repository_root / SCHEDULE_RELATIVE},dst=/gate/{SCHEDULE_RELATIVE},readonly",
                    "--mount",
                    f"type=bind,src={gate_site_packages},dst=/gate/site-packages,readonly",
                    "--mount",
                    f"type=bind,src={output_directory},dst=/gate-output",
                    "--workdir",
                    "/opt/eva",
                    "--entrypoint",
                    "/usr/local/bin/python",
                    candidate_image_id,
                    "-c",
                    "import os,time; "
                    "os.symlink('/opt/eva/alembic.ini','/gate/alembic.ini'); "
                    "os.symlink('/opt/eva/migrations','/gate/migrations'); "
                    "time.sleep(600)",
                ),
                label="controller creation",
            )
            _run(
                command_runner,
                ("docker", "network", "connect", browser_network, controller_name),
                label="controller browser-network connection",
            )
            _run(
                command_runner,
                ("docker", "start", controller_name),
                label="controller start",
            )

            inspected = {
                db_name: _inspect(
                    command_runner, "container", db_name, label="database"
                ),
                fixture_name: _inspect(
                    command_runner, "container", fixture_name, label="fixture"
                ),
                browser_name: _inspect(
                    command_runner, "container", browser_name, label="browser"
                ),
                controller_name: _inspect(
                    command_runner, "container", controller_name, label="controller"
                ),
            }
            _assert_container(
                inspected[db_name],
                resource_label="database",
                image_id=image_ids[POSTGRES_IMAGE],
                user="999:999",
                networks={db_network},
                authority_label=nonce,
            )
            controller_tmpfs = (
                inspected[controller_name].get("HostConfig", {}).get("Tmpfs") or {}
            )
            if set(controller_tmpfs) != {"/gate", "/tmp"}:
                raise BrowserSubgateError("controller candidate-path bridge differs")
            _assert_container(
                inspected[fixture_name],
                resource_label="fixture",
                image_id=image_ids[FIXTURE_IMAGE],
                user="65532:65532",
                networks={browser_network},
                authority_label=nonce,
            )
            _assert_container(
                inspected[browser_name],
                resource_label="browser",
                image_id=image_ids[PLAYWRIGHT_IMAGE],
                user="1000:1000",
                networks={browser_network},
                authority_label=nonce,
            )
            _assert_container(
                inspected[controller_name],
                resource_label="controller",
                image_id=candidate_image_id,
                user="65532:65532",
                networks={db_network, browser_network},
                authority_label=nonce,
            )
            controller_mounts = inspected[controller_name].get("Mounts") or []
            destinations = {item.get("Destination"): item for item in controller_mounts}
            if (
                any(
                    PurePosixPath(str(destination)).is_relative_to("/opt/eva")
                    for destination in destinations
                )
                or not _mount_matches(
                    destinations, "/gate/tests", source=tests_root, read_write=False
                )
                or not _mount_matches(
                    destinations, "/gate/tools", source=tools_root, read_write=False
                )
                or not _mount_matches(
                    destinations,
                    "/gate/site-packages",
                    source=gate_site_packages,
                    read_write=False,
                )
                or not _mount_matches(
                    destinations,
                    "/gate/" + SCHEDULE_RELATIVE,
                    source=repository_root / SCHEDULE_RELATIVE,
                    read_write=False,
                )
                or not _mount_matches(
                    destinations,
                    "/gate-output",
                    source=output_directory,
                    read_write=True,
                )
            ):
                raise BrowserSubgateError("controller mounts differ")
            fixture_mounts = inspected[fixture_name].get("Mounts") or []
            fixture_destinations = {
                item.get("Destination"): item for item in fixture_mounts
            }
            if len(fixture_mounts) != 1 or not _mount_matches(
                fixture_destinations,
                "/fixture_server.py",
                source=repository_root / FIXTURE_RELATIVE,
                read_write=False,
            ):
                raise BrowserSubgateError("fixture mount differs")
            inspected_networks = {
                network_name: _inspect(
                    command_runner, "network", network_name, label="test network"
                )
                for network_name in networks
            }
            for network in inspected_networks.values():
                if (
                    not _network_is_closed(network)
                    or network.get("Labels", {}).get("io.eva2.browser-release-subgate")
                    != nonce
                ):
                    raise BrowserSubgateError("test network gained a gateway")

            for attempt in range(40):
                ready = command_runner(
                    (
                        "docker",
                        "exec",
                        "--workdir",
                        "/opt/eva",
                        controller_name,
                        "/usr/local/bin/python",
                        "-c",
                        "import socket; "
                        "socket.create_connection(('fixture.eva.test',8080),2).close(); "
                        "socket.create_connection(('browser.eva.test',8931),2).close()",
                    ),
                    6,
                )
                if ready.returncode == 0:
                    break
                if attempt == 39:
                    raise BrowserSubgateError("browser providers were not ready")
                pause(0.25)
            egress = command_runner(
                (
                    "docker",
                    "exec",
                    browser_name,
                    "node",
                    "-e",
                    "fetch('https://example.com',{signal:AbortSignal.timeout(3000)})"
                    ".then(()=>process.exit(17)).catch(()=>process.exit(0))",
                ),
                8,
            )
            if egress.returncode != 0:
                raise BrowserSubgateError("browser external egress was not blocked")

            collect = command_runner(
                (
                    "docker",
                    "exec",
                    "--workdir",
                    "/opt/eva",
                    controller_name,
                    "/usr/local/bin/python",
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "/gate/" + TEST_MODULE,
                ),
                60,
            )
            if collect.returncode != 0:
                diagnostic = " ".join((collect.stdout + "\n" + collect.stderr).split())[
                    -2_000:
                ]
                raise BrowserSubgateError(
                    "browser nodeid collection failed: " + diagnostic
                )
            nodeid_marker = TEST_MODULE + "::"
            short_nodeid_marker = Path(TEST_MODULE).name + "::"
            collected_rows: list[str] = []
            for line in collect.stdout.splitlines():
                if nodeid_marker in line:
                    collected_rows.append(line[line.index(nodeid_marker) :])
                elif line.startswith(short_nodeid_marker):
                    collected_rows.append(TEST_MODULE + "::" + line.split("::", 1)[1])
            collected = tuple(collected_rows)
            if collected != EXPECTED_NODEIDS:
                raise BrowserSubgateError(
                    "browser nodeid collection differs: "
                    + json.dumps(collected, separators=(",", ":"))
                    + " output="
                    + " ".join(collect.stdout.split())[-2_000:]
                )

            execution = command_runner(
                (
                    "docker",
                    "exec",
                    "--workdir",
                    "/opt/eva",
                    controller_name,
                    "/usr/local/bin/python",
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "--tb=short",
                    "-p",
                    "no:cacheprovider",
                    "--junitxml=/gate-output/junit.xml",
                    *("/gate/" + nodeid for nodeid in EXPECTED_NODEIDS),
                ),
                120,
            )
            if execution.returncode != 0:
                combined = " ".join(
                    (execution.stdout + "\n" + execution.stderr).split()
                )
                browser_logs = command_runner(
                    ("docker", "logs", "--tail", "200", browser_name), 10
                )
                fixture_logs = command_runner(
                    ("docker", "logs", "--tail", "200", fixture_name), 10
                )
                diagnostic = combined[:6_000] + " ... " + combined[-4_000:]
                provider_diagnostic = " ".join(
                    (
                        browser_logs.stdout
                        + "\n"
                        + browser_logs.stderr
                        + "\n"
                        + fixture_logs.stdout
                        + "\n"
                        + fixture_logs.stderr
                    ).split()
                )[-4_000:]
                raise BrowserSubgateError(
                    "browser scenarios failed: "
                    + diagnostic
                    + " provider-logs="
                    + provider_diagnostic
                )
            _run(
                command_runner,
                ("docker", "stop", "--time", "2", controller_name),
                timeout=8,
                label="controller stop",
            )
            stopped = _inspect(
                command_runner, "container", controller_name, label="stopped controller"
            )
            if stopped.get("State", {}).get("Running") is not False:
                raise BrowserSubgateError("controller did not stop before JUnit copy")
            _run(
                command_runner,
                (
                    "docker",
                    "cp",
                    f"{controller_name}:/gate-output/junit.xml",
                    str(junit_host),
                ),
                label="JUnit copy",
            )
            junit = _junit_counts(junit_host)
            if (
                directory_sha256(gate_site_packages)
                != expected_gate_site_packages_sha256
                or directory_sha256(tests_root, ignore_python_cache=True)
                != expected_tests_sha256
                or directory_sha256(tools_root, ignore_python_cache=True)
                != expected_tools_sha256
                or _sha256(_safe_regular(harness_path, label="browser harness"))
                != expected_harness_sha256
                or _validate_profile(repository_root) != input_hashes
            ):
                raise BrowserSubgateError("test authority changed during execution")
            receipt = {
                "candidate": {
                    "imageId": candidate_image_id,
                    "sourceCommit": expected_candidate_source_commit,
                },
                "contract": CONTRACT,
                "execution": {
                    "collectedNodeids": list(collected),
                    "externalBrowserEgressBlocked": True,
                    "junit": junit,
                    "junitSha256": _sha256(junit_host.read_bytes()),
                    "stderrSha256": _sha256(execution.stderr.encode("utf-8")),
                    "stdoutSha256": _sha256(execution.stdout.encode("utf-8")),
                },
                "images": {
                    "browser": {
                        "id": image_ids[PLAYWRIGHT_IMAGE],
                        "reference": PLAYWRIGHT_IMAGE,
                    },
                    "fixture": {
                        "id": image_ids[FIXTURE_IMAGE],
                        "reference": FIXTURE_IMAGE,
                    },
                    "postgres": {
                        "id": image_ids[POSTGRES_IMAGE],
                        "reference": POSTGRES_IMAGE,
                    },
                },
                "inputs": {
                    **input_hashes,
                    "gateSitePackagesSha256": actual_gate_hash,
                    "harnessSha256": actual_harness_hash,
                    "testModuleSha256": test_module_hash,
                    "testsTreeSha256": actual_tests_hash,
                    "toolsTreeSha256": actual_tools_hash,
                },
                "isolation": {
                    "containerCount": 4,
                    "controllerUsesCandidateInternalProductCode": True,
                    "gatewaylessInternalNetworkCount": 2,
                    "publishedPortCount": 0,
                    "repositoryProductSourceMounted": False,
                },
                "nonClaims": [
                    "This isolated test does not promote or start a live Eva route.",
                    "This receipt does not replace the broad managed required-test receipt.",
                ],
                "resources": {
                    "containers": {
                        name: _object_id(value, label="container")
                        for name, value in inspected.items()
                    },
                    "database": database,
                    "networks": {
                        name: _object_id(value, label="network")
                        for name, value in inspected_networks.items()
                    },
                    "nonce": nonce,
                },
                "schemaVersion": 1,
                "status": "passed",
            }
        finally:
            if resources_started:
                _cleanup(
                    command_runner,
                    containers=containers,
                    networks=networks,
                )
                cleanup_complete = True
    if receipt is None or not cleanup_complete:
        raise BrowserSubgateError("browser subgate did not complete")
    receipt["cleanup"] = {"allNonceResourcesRemoved": True}
    _atomic_receipt(receipt_path, receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--candidate-image-id", required=True)
    parser.add_argument("--expected-candidate-source-commit", required=True)
    parser.add_argument("--gate-site-packages", required=True, type=Path)
    parser.add_argument("--expected-gate-site-packages-sha256", required=True)
    parser.add_argument("--expected-tests-sha256", required=True)
    parser.add_argument("--expected-tools-sha256", required=True)
    parser.add_argument("--expected-harness-sha256", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        run_browser_subgate(
            repository_root=arguments.repository_root,
            candidate_image_id=arguments.candidate_image_id,
            expected_candidate_source_commit=arguments.expected_candidate_source_commit,
            gate_site_packages=arguments.gate_site_packages,
            expected_gate_site_packages_sha256=(
                arguments.expected_gate_site_packages_sha256
            ),
            expected_tests_sha256=arguments.expected_tests_sha256,
            expected_tools_sha256=arguments.expected_tools_sha256,
            expected_harness_sha256=arguments.expected_harness_sha256,
            receipt_path=arguments.receipt,
        )
    except BrowserSubgateError as error:
        print(str(error), file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
