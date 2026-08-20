from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import selectors
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import (
    EpisodeFailureOutcome,
    EpisodeOutcome,
    FixtureClient,
    canonical_json,
    load_contract,
    sha256,
)
from oracle import OracleFailure, validate_definitive_failure, validate_episode


ROOT = Path(__file__).resolve().parent


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.exists()
    )


@dataclass(frozen=True)
class Scenario:
    name: str
    crash_at: str | None = None
    fault: tuple[str, str] | None = None
    concurrent: bool = False
    definitive_reject: str | None = None


SCENARIOS = (
    Scenario("happy"),
    Scenario("crash-before-model", crash_at="model.plan:before"),
    Scenario("crash-after-plan", crash_at="plan:after_persist"),
    Scenario("crash-after-calendar-commit", crash_at="calendar.create:after_remote_accept"),
    Scenario("crash-after-calendar-receipt", crash_at="calendar.create:after_local_receipt"),
    Scenario("crash-after-document-commit", crash_at="document.create:after_remote_accept"),
    Scenario("crash-after-document-receipt", crash_at="document.create:after_local_receipt"),
    Scenario("crash-after-delivery-commit", crash_at="delivery.send:after_remote_accept"),
    Scenario("crash-after-delivery-receipt", crash_at="delivery.send:after_local_receipt"),
    Scenario("crash-before-closure", crash_at="episode:before_close"),
    Scenario("disconnect-after-calendar-commit", fault=("calendar.create", "disconnect_after_commit")),
    Scenario("rate-limit-calendar-once", fault=("calendar.create", "rate_limit")),
    Scenario("concurrent-duplicate-ingress", concurrent=True),
    Scenario(
        "definitive-reject-document",
        fault=("document.create", "reject"),
        definitive_reject="document.create",
    ),
)


class FixtureProcess:
    def __init__(self, db_path: Path):
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "fixture_server.py"),
                "--db",
                str(db_path),
                "--port",
                "0",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        ready = selector.select(timeout=10)
        selector.close()
        if not ready:
            self.stop()
            raise RuntimeError("fixture startte niet binnen tien seconden")
        line = self.process.stdout.readline()
        started = json.loads(line)
        if started.get("contract") != "FixtureStarted/v1":
            self.stop()
            raise RuntimeError("fixture gaf geen exact startbewijs")
        self.url = str(started["url"])

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def __enter__(self) -> "FixtureProcess":
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


def _invoke(db_path: Path, fixture_url: str, contract_path: Path, crash_at: str | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if crash_at is None:
        env.pop("EVA_BAKEOFF_CRASH_AT", None)
    else:
        env["EVA_BAKEOFF_CRASH_AT"] = crash_at
    started = time.monotonic_ns()
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "sql_baseline.py"),
            "--db",
            str(db_path),
            "--fixture",
            fixture_url,
            "--contract",
            str(contract_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=130,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "duration_ms": (time.monotonic_ns() - started) // 1_000_000,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _parse_outcome(invocation: dict[str, Any]) -> EpisodeOutcome:
    lines = [line for line in str(invocation["stdout"]).splitlines() if line.strip()]
    if invocation["exit_code"] != 0 or len(lines) != 1:
        raise OracleFailure("kandidaat gaf geen exact enkel outcome-object")
    return EpisodeOutcome.model_validate_json(lines[0])


def _parse_failure_outcome(invocation: dict[str, Any]) -> EpisodeFailureOutcome:
    lines = [line for line in str(invocation["stdout"]).splitlines() if line.strip()]
    if invocation["exit_code"] != 0 or len(lines) != 1:
        raise OracleFailure("kandidaat gaf geen exact terminal deelresultaat")
    return EpisodeFailureOutcome.model_validate_json(lines[0])


def _definitive_scenario(
    scenario: Scenario,
    output_dir: Path,
    contract_path: Path,
) -> dict[str, Any]:
    assert scenario.fault is not None and scenario.definitive_reject is not None
    scenario_dir = output_dir / scenario.name
    scenario_dir.mkdir(parents=True, exist_ok=False)
    candidate_db = scenario_dir / "candidate.sqlite"
    fixture_db = scenario_dir / "fixture.sqlite"
    contract = load_contract(contract_path)
    invocations: list[dict[str, Any]] = []
    failure: str | None = None
    oracle: dict[str, Any] | None = None
    outcome: EpisodeFailureOutcome | None = None

    with FixtureProcess(fixture_db) as fixture_process:
        client = FixtureClient(fixture_process.url)
        client.set_fault(scenario.fault[0], scenario.fault[1], remaining=10)
        first = _invoke(candidate_db, fixture_process.url, contract_path)
        invocations.append(first)
        try:
            outcome = _parse_failure_outcome(first)
        except (OracleFailure, ValueError) as error:
            failure = f"{type(error).__name__}: {error}"
        if outcome is not None:
            replay = _invoke(candidate_db, fixture_process.url, contract_path)
            invocations.append(replay)
            try:
                replay_outcome = _parse_failure_outcome(replay)
                if replay_outcome != outcome:
                    raise OracleFailure("terminale failure-replay wijkt af")
            except (OracleFailure, ValueError) as error:
                failure = f"{type(error).__name__}: {error}"
        evidence = client.evidence()
        (scenario_dir / "fixture-evidence.json").write_text(
            canonical_json(evidence) + "\n", encoding="utf-8",
        )
        if outcome is not None:
            (scenario_dir / "outcome.json").write_text(
                canonical_json(outcome.model_dump(mode="json")) + "\n", encoding="utf-8",
            )
            if failure is None:
                try:
                    oracle = validate_definitive_failure(
                        contract, outcome, evidence, scenario.definitive_reject,
                    )
                except OracleFailure as error:
                    failure = f"{type(error).__name__}: {error}"

    (scenario_dir / "invocations.json").write_text(
        canonical_json(invocations) + "\n", encoding="utf-8",
    )
    return {
        "contract": "EvaArchitectureScenarioRun/v1",
        "scenario": scenario.name,
        "status": "passed" if failure is None else "failed",
        "fault": scenario.fault,
        "invocations": [
            {
                "exit_code": item["exit_code"],
                "duration_ms": item["duration_ms"],
                "stdout_sha256": sha256(str(item["stdout"]).encode("utf-8")),
                "stderr_sha256": sha256(str(item["stderr"]).encode("utf-8")),
            }
            for item in invocations
        ],
        "oracle": oracle,
        "failure": failure,
        "storage_bytes": {
            "candidate": _sqlite_bytes(candidate_db),
            "fixture": _sqlite_bytes(fixture_db),
        },
    }


def _scenario(
    scenario: Scenario,
    output_dir: Path,
    contract_path: Path,
) -> dict[str, Any]:
    scenario_dir = output_dir / scenario.name
    scenario_dir.mkdir(parents=True, exist_ok=False)
    candidate_db = scenario_dir / "candidate.sqlite"
    fixture_db = scenario_dir / "fixture.sqlite"
    contract = load_contract(contract_path)
    invocations: list[dict[str, Any]] = []
    outcome: EpisodeOutcome | None = None
    oracle: dict[str, Any] | None = None
    failure: str | None = None

    with FixtureProcess(fixture_db) as fixture_process:
        client = FixtureClient(fixture_process.url)
        if scenario.fault is not None:
            client.set_fault(*scenario.fault)

        if scenario.concurrent:
            command = [
                sys.executable,
                str(ROOT / "sql_baseline.py"),
                "--db",
                str(candidate_db),
                "--fixture",
                fixture_process.url,
                "--contract",
                str(contract_path),
            ]
            concurrent_env = os.environ.copy()
            concurrent_env["EVA_BAKEOFF_MODEL_DELAY_MS"] = "750"
            started = time.monotonic_ns()
            processes = [
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=concurrent_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            for process in processes:
                stdout, stderr = process.communicate(timeout=130)
                invocations.append({
                    "exit_code": process.returncode,
                    "duration_ms": (time.monotonic_ns() - started) // 1_000_000,
                    "stdout": stdout,
                    "stderr": stderr,
                })
            first = _parse_outcome(invocations[0])
            second = _parse_outcome(invocations[1])
            if first != second:
                raise OracleFailure("concurrente replay gaf verschillende outcomes")
            outcome = first
        else:
            first = _invoke(candidate_db, fixture_process.url, contract_path, scenario.crash_at)
            invocations.append(first)
            if scenario.crash_at is not None and first["exit_code"] != 86:
                raise OracleFailure("crashinjectie stopte niet exact op de gekozen grens")
            if scenario.crash_at is not None or scenario.fault is not None:
                if scenario.fault is not None and first["exit_code"] == 0:
                    outcome = _parse_outcome(first)
                else:
                    recovered = _invoke(candidate_db, fixture_process.url, contract_path)
                    invocations.append(recovered)
                    outcome = _parse_outcome(recovered)
            else:
                outcome = _parse_outcome(first)

        replay = _invoke(candidate_db, fixture_process.url, contract_path)
        invocations.append(replay)
        replay_outcome = _parse_outcome(replay)
        if outcome != replay_outcome:
            raise OracleFailure("terminale replay is niet byte-semantisch gelijk")
        evidence = client.evidence()
        (scenario_dir / "outcome.json").write_text(
            canonical_json(outcome.model_dump(mode="json")) + "\n", encoding="utf-8",
        )
        (scenario_dir / "fixture-evidence.json").write_text(
            canonical_json(evidence) + "\n", encoding="utf-8",
        )
        try:
            oracle = validate_episode(contract, outcome, evidence)
        except OracleFailure as error:
            failure = f"{type(error).__name__}: {error}"

    (scenario_dir / "invocations.json").write_text(
        canonical_json(invocations) + "\n", encoding="utf-8",
    )

    compact_invocations = [
        {
            "exit_code": item["exit_code"],
            "duration_ms": item["duration_ms"],
            "stdout_sha256": sha256(str(item["stdout"]).encode("utf-8")),
            "stderr_sha256": sha256(str(item["stderr"]).encode("utf-8")),
        }
        for item in invocations
    ]
    return {
        "contract": "EvaArchitectureScenarioRun/v1",
        "scenario": scenario.name,
        "status": "passed" if failure is None else "failed",
        "crash_at": scenario.crash_at,
        "fault": scenario.fault,
        "invocations": compact_invocations,
        "oracle": oracle,
        "failure": failure,
        "storage_bytes": {
            "candidate": _sqlite_bytes(candidate_db),
            "fixture": _sqlite_bytes(fixture_db),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--contract", default=str(ROOT / "contract" / "episode-noor-v1.json"),
    )
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    contract_path = Path(args.contract).resolve()
    selected = set(args.scenario or ())
    scenarios = [scenario for scenario in SCENARIOS if not selected or scenario.name in selected]
    if selected - {scenario.name for scenario in scenarios}:
        raise SystemExit("onbekend scenario")

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        try:
            results.append(
                _definitive_scenario(scenario, output_dir, contract_path)
                if scenario.definitive_reject is not None
                else _scenario(scenario, output_dir, contract_path)
            )
        except Exception as error:  # bewijsrunner moet de eerste echte fout bewaren
            results.append({
                "contract": "EvaArchitectureScenarioRun/v1",
                "scenario": scenario.name,
                "status": "failed",
                "failure": f"{type(error).__name__}: {error}",
            })
            break

    passed = sum(result["status"] == "passed" for result in results)
    by_name = {result["scenario"]: result for result in results}
    recovery_names = {
        scenario.name
        for scenario in SCENARIOS
        if not scenario.concurrent and scenario.definitive_reject is None
    }
    recovery_attempted = recovery_names.issubset(by_name)
    recovery_passed = recovery_attempted and all(
        by_name[name]["status"] == "passed" for name in recovery_names
    )
    concurrent_status = by_name.get("concurrent-duplicate-ingress", {}).get("status")
    reject_status = by_name.get("definitive-reject-document", {}).get("status")
    report = {
        "contract": "EvaArchitectureBakeoffRun/v1",
        "candidate": "python-sql-state-machine",
        "candidate_version": "prototype-1",
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "os": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": {
            "pydantic": importlib.metadata.version("pydantic"),
            "pydantic-ai-slim": importlib.metadata.version("pydantic-ai-slim"),
        },
        "sources": {
            path.name: _file_sha256(path)
            for path in (
                ROOT / "common.py",
                ROOT / "fixture_server.py",
                ROOT / "oracle.py",
                ROOT / "sql_baseline.py",
                ROOT / "run_sql_matrix.py",
                contract_path,
                ROOT / "requirements-common.txt",
            )
        },
        "contract_sha256": sha256(load_contract(contract_path).model_dump(mode="json")),
        "scenarios": results,
        "summary": {"passed": passed, "attempted": len(results)},
        "gates": {
            "effect_crash_recovery": (
                "passed" if recovery_passed else "failed" if recovery_attempted else "not_proven"
            ),
            "duplicate_ingress_budget": (
                concurrent_status if concurrent_status in {"passed", "failed"} else "not_proven"
            ),
            "definitive_reject": (
                reject_status if reject_status in {"passed", "failed"} else "not_proven"
            ),
            "provider_circuit_breaker": "not_proven",
            "durable_supersession": "not_proven",
            "install_restore_export": "not_proven",
            "memory": "not_proven",
            "real_model": "not_proven",
            "live_canary": "not_proven",
        },
        "winner_eligible": False,
    }
    report_json = canonical_json(report) + "\n"
    (output_dir / "run.json").write_text(report_json, encoding="utf-8")
    print(report_json, end="")
    raise SystemExit(0 if passed == len(scenarios) else 1)


if __name__ == "__main__":
    main()
