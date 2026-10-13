from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any, BinaryIO

from common import (
    EpisodeContract,
    EpisodeOutcome,
    FixtureClient,
    canonical_json,
    load_contract,
)
from oracle import validate_episode


HERE = Path(__file__).resolve().parent
RESTATE_IMAGE = "docker.restate.dev/restatedev/restate:1.7.2"
WORKFLOW_NAME = "EvaNoorWorkflow"
CRASH_POINT = "calendar.create:after_remote_accept"


def free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def wait_for_tcp(
    port: int, process: subprocess.Popen[bytes], timeout: float = 30
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"proces stopte tijdens opstarten met code {process.returncode}"
            )
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


def wait_for_admin(admin_url: str, restate: subprocess.Popen[bytes]) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if restate.poll() is not None:
            raise RuntimeError(
                f"Restate Server stopte tijdens opstarten met code {restate.returncode}"
            )
        try:
            return request_json("GET", f"{admin_url}/version", timeout=1)
        except (OSError, RuntimeError) as error:
            last_error = error
            time.sleep(0.2)
    raise TimeoutError(f"Restate Admin API startte niet: {last_error}")


def open_log(stack: ExitStack, path: Path) -> BinaryIO:
    return stack.enter_context(path.open("wb"))


def start_fixture(
    stack: ExitStack,
    root: Path,
    port: int,
) -> subprocess.Popen[bytes]:
    log = open_log(stack, root / "fixture.log")
    process = subprocess.Popen(
        [
            sys.executable,
            str(HERE / "fixture_server.py"),
            "--db",
            str(root / "fixture.sqlite3"),
            "--port",
            str(port),
        ],
        cwd=HERE,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    wait_for_tcp(port, process)
    return process


def start_sdk(
    stack: ExitStack,
    root: Path,
    sdk_port: int,
    fixture_url: str,
    *,
    crash: bool,
) -> subprocess.Popen[bytes]:
    suffix = "crashing" if crash else f"steady-{uuid.uuid4().hex[:6]}"
    log = open_log(stack, root / f"sdk-{suffix}.log")
    env = os.environ.copy()
    if crash:
        env["EVA_BAKEOFF_CRASH_AT"] = CRASH_POINT
    else:
        env.pop("EVA_BAKEOFF_CRASH_AT", None)
    process = subprocess.Popen(
        [
            sys.executable,
            str(HERE / "restate_service.py"),
            "--fixture",
            fixture_url,
            "--host",
            "0.0.0.0",
            "--port",
            str(sdk_port),
        ],
        cwd=HERE,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    wait_for_tcp(sdk_port, process)
    return process


def start_restate(
    stack: ExitStack,
    root: Path,
    ingress_port: int,
    admin_port: int,
) -> tuple[subprocess.Popen[bytes], str]:
    name = f"eva-restate-{uuid.uuid4().hex[:12]}"
    data = root / "restate-data"
    data.mkdir()
    log = open_log(stack, root / "restate.log")
    process = subprocess.Popen(
        [
            "docker",
            "run",
            "--name",
            name,
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--add-host=host.docker.internal:host-gateway",
            "-p",
            f"127.0.0.1:{ingress_port}:8080",
            "-p",
            f"127.0.0.1:{admin_port}:9070",
            "-v",
            f"{data}:/data",
            RESTATE_IMAGE,
            "--base-dir=/data",
            "--no-logo",
        ],
        cwd=HERE,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return process, name


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def stop_restate(process: subprocess.Popen[bytes] | None, name: str | None) -> None:
    if name is not None:
        subprocess.run(
            ["docker", "stop", "--time", "10", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    stop_process(process)


def register_deployment(admin_url: str, sdk_port: int) -> dict[str, Any]:
    return request_json(
        "POST",
        f"{admin_url}/deployments",
        {"uri": f"http://host.docker.internal:{sdk_port}", "force": True},
        timeout=30,
    )


def invocation_url(ingress_url: str, contract: EpisodeContract) -> str:
    key = urllib.parse.quote(contract.episode_id, safe="")
    return f"{ingress_url}/{WORKFLOW_NAME}/{key}/run"


def start_invocation(
    stack: ExitStack,
    root: Path,
    ingress_url: str,
    contract_path: Path,
    contract: EpisodeContract,
) -> subprocess.Popen[bytes]:
    error_log = open_log(stack, root / "invoke.stderr.log")
    return subprocess.Popen(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--max-time",
            "120",
            "--request",
            "POST",
            "--header",
            "Content-Type: application/json",
            "--data-binary",
            f"@{contract_path}",
            invocation_url(ingress_url, contract),
        ],
        cwd=HERE,
        stdout=subprocess.PIPE,
        stderr=error_log,
    )


def finish_invocation(process: subprocess.Popen[bytes]) -> EpisodeOutcome:
    stdout, _ = process.communicate(timeout=125)
    if process.returncode != 0:
        raise RuntimeError(f"invocation-curl stopte met code {process.returncode}")
    return EpisodeOutcome.model_validate_json(stdout)


def validate_evidence(
    fixture: FixtureClient,
    contract: EpisodeContract,
    outcome: EpisodeOutcome,
    *,
    recovered_operation: str | None,
) -> None:
    evidence = fixture.evidence()
    model_calls = evidence["model_calls"]
    if len(model_calls) != 1 or model_calls[0]["episode_id"] != outcome.episode_id:
        raise AssertionError(f"verwachtte exact een modelcall: {model_calls}")
    effects = evidence["effects"]
    if len(effects) != 3:
        raise AssertionError(f"verwachtte 3 fixture-effecten, kreeg {len(effects)}")

    by_operation = {effect["operation"]: effect for effect in effects}
    expected_operations = {"calendar.create", "document.create", "delivery.send"}
    if set(by_operation) != expected_operations:
        raise AssertionError(f"onverwachte effectset: {set(by_operation)}")

    post_counts = {operation: 0 for operation in expected_operations}
    for call in evidence["calls"]:
        if call["method"] != "POST" or call["path"] != "/v1/effects":
            continue
        key = call["idempotency_key"]
        for operation in expected_operations:
            if key == f"{outcome.episode_id}:{operation}":
                post_counts[operation] += 1
    if post_counts != {operation: 1 for operation in expected_operations}:
        raise AssertionError(f"effect-POST-aantallen wijken af: {post_counts}")

    receipts = {receipt.operation: receipt for receipt in outcome.receipts}
    for operation in expected_operations:
        expected_reconciled = operation == recovered_operation
        if receipts[operation].reconciled is not expected_reconciled:
            raise AssertionError(
                f"reconciled-vlag voor {operation} wijkt af: "
                f"{receipts[operation].reconciled}"
            )
    validate_episode(contract, outcome, evidence)


def validate_outcome(contract: EpisodeContract, outcome: EpisodeOutcome) -> None:
    if outcome.episode_id != contract.episode_id:
        raise AssertionError("outcome episode_id wijkt af")
    if outcome.mail_id != contract.expected.mail_id:
        raise AssertionError("outcome mail_id wijkt af")
    if outcome.customer_id != contract.expected.customer_id:
        raise AssertionError("outcome customer_id wijkt af")
    if len(outcome.receipts) != 3:
        raise AssertionError("outcome bevat niet exact drie receipts")


def run_scenario(
    scenario: str,
    contract_path: Path,
    *,
    keep_tmp: bool,
) -> EpisodeOutcome:
    contract = load_contract(contract_path)
    root = Path(tempfile.mkdtemp(prefix=f"eva-restate-{scenario}-", dir="/tmp"))
    fixture_process: subprocess.Popen[bytes] | None = None
    sdk_process: subprocess.Popen[bytes] | None = None
    restate_process: subprocess.Popen[bytes] | None = None
    restate_name: str | None = None

    try:
        with ExitStack() as stack:
            fixture_port, sdk_port, ingress_port, admin_port = (
                free_port(),
                free_port(),
                free_port(),
                free_port(),
            )
            fixture_url = f"http://127.0.0.1:{fixture_port}"
            ingress_url = f"http://127.0.0.1:{ingress_port}"
            admin_url = f"http://127.0.0.1:{admin_port}"

            fixture_process = start_fixture(stack, root, fixture_port)
            sdk_process = start_sdk(
                stack,
                root,
                sdk_port,
                fixture_url,
                crash=scenario == "crash-restart",
            )
            restate_process, restate_name = start_restate(
                stack,
                root,
                ingress_port,
                admin_port,
            )
            version = wait_for_admin(admin_url, restate_process)
            if version.get("version") != "1.7.2":
                raise AssertionError(f"verkeerde Restate Server: {version}")
            deployment = register_deployment(admin_url, sdk_port)
            if deployment.get("sdk_version") != "restate-sdk-python/1.0.4":
                raise AssertionError(f"verkeerde Restate SDK: {deployment}")

            invocation = start_invocation(
                stack,
                root,
                ingress_url,
                contract_path,
                contract,
            )
            if scenario == "crash-restart":
                exit_code = sdk_process.wait(timeout=30)
                if exit_code != 86:
                    raise AssertionError(
                        f"SDK hard-crashte niet op {CRASH_POINT}; exit={exit_code}"
                    )
                sdk_process = start_sdk(
                    stack,
                    root,
                    sdk_port,
                    fixture_url,
                    crash=False,
                )

            outcome = finish_invocation(invocation)
            validate_outcome(contract, outcome)
            validate_evidence(
                FixtureClient(fixture_url),
                contract,
                outcome,
                recovered_operation=(
                    "calendar.create" if scenario == "crash-restart" else None
                ),
            )
            return outcome
    except Exception as error:
        print(f"scenario-data: {root}", file=sys.stderr)
        for log_path in sorted(root.glob("*.log")):
            print(f"--- {log_path.name} ---", file=sys.stderr)
            print(
                log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr
            )
        raise error
    finally:
        stop_process(sdk_process)
        stop_process(fixture_process)
        stop_restate(restate_process, restate_name)
        if keep_tmp:
            print(f"scenario-data bewaard: {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("happy", "crash-restart", "all"))
    parser.add_argument(
        "--contract",
        type=Path,
        default=HERE / "contract" / "episode-noor-v1.json",
    )
    parser.add_argument("--keep-tmp", action="store_true")
    args = parser.parse_args()

    if shutil.which("docker") is None or shutil.which("curl") is None:
        raise RuntimeError("docker en curl zijn vereist voor de lokale Restate-proef")
    scenarios = (
        ("happy", "crash-restart") if args.scenario == "all" else (args.scenario,)
    )
    for scenario in scenarios:
        outcome = run_scenario(
            scenario,
            args.contract.resolve(),
            keep_tmp=args.keep_tmp,
        )
        print(canonical_json(outcome.model_dump(mode="json")), flush=True)


if __name__ == "__main__":
    main()
