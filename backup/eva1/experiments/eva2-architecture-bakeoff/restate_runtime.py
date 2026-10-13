from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common import canonical_json


HERE = Path(__file__).resolve().parent
RESTATE_IMAGE = "docker.restate.dev/restatedev/restate:1.7.2"
RESTATE_VERSION = "1.7.2"
SDK_VERSION = "restate-sdk-python/1.0.4"
RUNTIME_FILE = "restate-runtime.json"


def checked_state_dir(value: str | Path) -> Path:
    state_dir = Path(value).resolve()
    tmp = Path("/tmp").resolve()
    if state_dir == tmp or tmp not in state_dir.parents:
        raise ValueError("--state-dir moet een specifieke wegwerpmap onder /tmp zijn")
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def checked_fixture_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("--fixture moet een expliciete localhost-http-URL zijn")
    if parsed.port is None:
        raise ValueError("--fixture moet een expliciete poort bevatten")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("--fixture mag geen gebruikersnaam of wachtwoord bevatten")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("--fixture mag geen pad, query of fragment bevatten")
    return value.rstrip("/")


def free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def container_name(state_dir: Path) -> str:
    suffix = hashlib.sha256(str(state_dir).encode("utf-8")).hexdigest()[:16]
    return f"eva-restate-{suffix}"


def runtime_path(state_dir: Path) -> Path:
    return state_dir / RUNTIME_FILE


def write_runtime(state_dir: Path, runtime: dict[str, Any]) -> None:
    target = runtime_path(state_dir)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(canonical_json(runtime) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def load_runtime(state_dir: Path, fixture_url: str | None = None) -> dict[str, Any]:
    target = runtime_path(state_dir)
    if not target.exists():
        raise RuntimeError(
            f"runtime ontbreekt; start eerst met restate_runtime.py: {target}"
        )
    runtime = json.loads(target.read_text(encoding="utf-8"))
    if runtime.get("contract") != "EvaRestateRuntime/v1":
        raise RuntimeError("onbekend Restate-runtimecontract in state-dir")
    if runtime.get("state_dir") != str(state_dir):
        raise RuntimeError("runtimebestand hoort bij een andere state-dir")
    if runtime.get("container_name") != container_name(state_dir):
        raise RuntimeError("runtimebestand mist de kandidaat-eigen containerbinding")
    if runtime.get("restate_image") != RESTATE_IMAGE:
        raise RuntimeError("runtimebestand wijst naar een andere Restate-image")
    ports = [runtime.get(name) for name in ("sdk_port", "ingress_port", "admin_port")]
    if (
        not all(type(port) is int and 1_024 <= port <= 65_535 for port in ports)
        or len(set(ports)) != 3
    ):
        raise RuntimeError("runtimebestand bevat ongeldige of overlappende poorten")
    stored_fixture = runtime.get("fixture_url")
    if not isinstance(stored_fixture, str):
        raise RuntimeError("runtimebestand mist de fixture-URL")
    try:
        checked_fixture_url(stored_fixture)
    except ValueError as error:
        raise RuntimeError("runtimebestand bevat een onveilige fixture-URL") from error
    if fixture_url is not None and runtime.get("fixture_url") != fixture_url:
        raise RuntimeError(
            "--fixture wijkt af van de fixture die aan state-dir is gebonden"
        )
    return runtime


def initial_runtime(state_dir: Path, fixture_url: str) -> dict[str, Any]:
    return {
        "contract": "EvaRestateRuntime/v1",
        "state_dir": str(state_dir),
        "fixture_url": fixture_url,
        "sdk_port": free_port(),
        "ingress_port": free_port(),
        "admin_port": free_port(),
        "container_name": container_name(state_dir),
        "worker_pid": None,
        "worker_start_ticks": None,
        "restate_image": RESTATE_IMAGE,
    }


def process_start_ticks(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[21])
    except (FileNotFoundError, IndexError, ValueError, PermissionError):
        return None


def worker_is_running(runtime: dict[str, Any]) -> bool:
    pid = runtime.get("worker_pid")
    ticks = runtime.get("worker_start_ticks")
    if not isinstance(pid, int) or not isinstance(ticks, int):
        return False
    if process_start_ticks(pid) != ticks:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError):
        return False
    return b"restate_service.py" in cmdline


def container_is_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_for_tcp(port: int, *, timeout: float = 30, pid: int | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid is not None and process_start_ticks(pid) is None:
            raise RuntimeError(f"worker {pid} stopte tijdens opstarten")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"poort {port} kwam niet binnen {timeout:g}s beschikbaar")


def request_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    data = None if body is None else canonical_json(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} voor {url}: {detail}") from error


def wait_for_admin(runtime: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
    admin_url = f"http://127.0.0.1:{runtime['admin_port']}"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if not container_is_running(runtime["container_name"]):
            raise RuntimeError("Restate-container stopte tijdens opstarten")
        try:
            return request_json("GET", f"{admin_url}/version", timeout=1)
        except (OSError, RuntimeError) as error:
            last_error = error
            time.sleep(0.2)
    raise TimeoutError(f"Restate Admin API startte niet: {last_error}")


def ensure_fixture_is_reachable(fixture_url: str) -> None:
    parsed = urlsplit(fixture_url)
    assert parsed.port is not None
    host = parsed.hostname or ""
    with socket.create_connection((host, parsed.port), timeout=2):
        return


def start_worker(state_dir: Path, runtime: dict[str, Any]) -> None:
    if worker_is_running(runtime):
        return
    log = (state_dir / "restate-worker.log").open("ab", buffering=0)
    env = os.environ.copy()
    process = subprocess.Popen(
        [
            sys.executable,
            str(HERE / "restate_service.py"),
            "--fixture",
            runtime["fixture_url"],
            "--host",
            "0.0.0.0",
            "--port",
            str(runtime["sdk_port"]),
        ],
        cwd=HERE,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    runtime["worker_pid"] = process.pid
    runtime["worker_start_ticks"] = process_start_ticks(process.pid)
    write_runtime(state_dir, runtime)
    wait_for_tcp(runtime["sdk_port"], pid=process.pid)


def start_server(state_dir: Path, runtime: dict[str, Any]) -> None:
    name = runtime["container_name"]
    if container_is_running(name):
        return
    subprocess.run(
        ["docker", "rm", "--force", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    data = state_dir / "restate-data"
    data.mkdir(exist_ok=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--log-driver=none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--add-host=host.docker.internal:host-gateway",
            "-p",
            f"127.0.0.1:{runtime['ingress_port']}:8080",
            "-p",
            f"127.0.0.1:{runtime['admin_port']}:9070",
            "-v",
            f"{data}:/data",
            RESTATE_IMAGE,
            "--base-dir=/data",
            "--node-name=eva-noor",
            "--no-logo",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Restate-container startte niet: {result.stderr.strip()}")


def ensure_deployment(runtime: dict[str, Any]) -> None:
    admin_url = f"http://127.0.0.1:{runtime['admin_port']}"
    deployments = request_json("GET", f"{admin_url}/deployments", timeout=10)
    uri = f"http://host.docker.internal:{runtime['sdk_port']}"
    matching = [item for item in deployments["deployments"] if item.get("uri") == uri]
    if matching:
        if matching[-1].get("sdk_version") != SDK_VERSION:
            raise RuntimeError(f"verkeerde Restate SDK-discovery: {matching[-1]}")
        return
    deployment = request_json(
        "POST",
        f"{admin_url}/deployments",
        {"uri": uri, "force": True},
        timeout=30,
    )
    if deployment.get("sdk_version") != SDK_VERSION:
        raise RuntimeError(f"verkeerde Restate SDK-discovery: {deployment}")


def start(state_dir: Path, fixture_url: str) -> dict[str, Any]:
    target = runtime_path(state_dir)
    if target.exists():
        runtime = load_runtime(state_dir, fixture_url)
    else:
        runtime = initial_runtime(state_dir, fixture_url)
        write_runtime(state_dir, runtime)
    if runtime.get("restate_image") != RESTATE_IMAGE:
        raise RuntimeError("state-dir is aan een andere Restate-image gebonden")

    ensure_fixture_is_reachable(fixture_url)
    start_worker(state_dir, runtime)
    start_server(state_dir, runtime)
    version = wait_for_admin(runtime)
    if version.get("version") != RESTATE_VERSION:
        raise RuntimeError(f"verkeerde Restate Server-versie: {version}")
    ensure_deployment(runtime)
    return runtime


def stop_worker(state_dir: Path, runtime: dict[str, Any]) -> None:
    if not worker_is_running(runtime):
        runtime["worker_pid"] = None
        runtime["worker_start_ticks"] = None
        write_runtime(state_dir, runtime)
        return
    pid = runtime["worker_pid"]
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and worker_is_running(runtime):
        time.sleep(0.1)
    if worker_is_running(runtime):
        os.kill(pid, signal.SIGKILL)
    runtime["worker_pid"] = None
    runtime["worker_start_ticks"] = None
    write_runtime(state_dir, runtime)


def stop(state_dir: Path) -> None:
    runtime = load_runtime(state_dir)
    stop_worker(state_dir, runtime)
    subprocess.run(
        ["docker", "stop", "--time", "10", runtime["container_name"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--state-dir", required=True)
    start_parser.add_argument("--fixture", required=True)

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--state-dir", required=True)

    args = parser.parse_args()
    state_dir = checked_state_dir(args.state_dir)
    if args.command == "start":
        runtime = start(state_dir, checked_fixture_url(args.fixture))
        print(
            canonical_json(
                {
                    "contract": "EvaRestateRuntimeStarted/v1",
                    "state_dir": str(state_dir),
                    "ingress_url": f"http://127.0.0.1:{runtime['ingress_port']}",
                    "admin_url": f"http://127.0.0.1:{runtime['admin_port']}",
                    "worker_pid": runtime["worker_pid"],
                }
            )
        )
    else:
        stop(state_dir)


if __name__ == "__main__":
    main()
