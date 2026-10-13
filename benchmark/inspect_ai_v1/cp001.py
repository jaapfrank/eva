#!/usr/bin/env python3
"""Execute CP-001's sealed offline pair in Inspect; no product/model launch mode.

Inspect owns samples, scores and .eval logs. This module only schedules the
fixed fixture driver and binds independent readbacks and measurement metadata.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import bridge_probe as proof
import cp001_native as native

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "cp001_fixtures.json"
WORKER = HERE / "cp001_fixture_worker.py"
SOURCES = {
    **proof.SOURCE_FILES,
    **{
        f"benchmark/inspect_ai_v1/{name}": HERE / name
        for name in (
            "cp001.py",
            "cp001_fixture_worker.py",
            "cp001_native.py",
            "cp001_fixtures.json",
        )
    },
}
FAULTS = ("none", "unavailable", "timeout", "failure")


def schedule(plan: dict) -> list[dict]:
    samples = []
    for task in plan["tasks"]:
        for repeat in range(plan["repetitions"]):
            participants = plan["participants"][:: 1 if repeat == 0 else -1]
            for participant in participants:
                samples.append(
                    {
                        "id": f"{task['id']}-{repeat + 1}-{participant}",
                        "participant": participant,
                        "repeat": repeat + 1,
                        "task": task,
                    }
                )
    return samples


def execute_sample(sample: dict, plan: dict, root: Path, fault: str = "none") -> dict:
    """Run a fresh fixed fixture subprocess per turn; failure is never retried."""
    if fault not in FAULTS:
        raise ValueError("unknown fault")
    root.mkdir(mode=0o700)
    task = sample["task"]
    budget = plan["budgets"]
    result: dict[str, Any] = {
        "mode": plan["mode"],
        "product_result": "unknown",
        "participant": sample["participant"],
        "status": "running",
        "usage": {
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_tokens": 0,
            "helper_calls": 0,
            "retry_calls": 0,
            "judge_calls": 0,
            "money": None,
            "basis": "no model route exists",
        },
        "latency_ms": {
            "acknowledgement": None,
            "first_content": None,
            "verified_end": None,
            "elapsed": None,
        },
        "assistance": {
            "hints": 0,
            "repairs": 0,
            "takeovers": 0,
            "human_minutes": 0,
            "login_minutes": None,
        },
        "human_review": {key: None for key in task["human_review"]},
        "phases": [],
        "answer": None,
        "readback": {},
    }
    started = time.monotonic()
    if fault == "unavailable":
        result["status"] = "admission_failed"
        result["reason"] = "injected unavailable route; no product loss"
        result["latency_ms"]["elapsed"] = (time.monotonic() - started) * 1000
        return result
    originals = task.get("files", {})
    for name, content in originals.items():
        (root / name).write_text(content, encoding="utf-8")
    for index, phase in enumerate(task["phases"]):
        remaining = budget["sample_timeout_seconds"] - (time.monotonic() - started)
        if index >= budget["phases"] or remaining <= 0:
            result["status"] = "budget_exceeded"
            break
        # Participant label, expected answers and scoring never enter the driver.
        request = json.dumps(
            {"phase": phase, "prompt": task["turns"][index], "fault": fault},
            ensure_ascii=False,
        ).encode()
        if len(request) > budget["input_bytes"]:
            result["status"] = "budget_exceeded"
            break
        event = {
            "phase": phase,
            "request_sha256": proof.bytes_sha256(request),
            "request_bytes": len(request),
            "timeout_seconds": budget["phase_timeout_seconds"],
        }
        result["phases"].append(event)
        try:
            with (root / f"{phase}.stdout").open("wb") as out:
                with (root / f"{phase}.stderr").open("wb") as err:
                    run = subprocess.run(
                        [sys.executable, "-I", str(WORKER)],
                        cwd=root,
                        input=request,
                        stdout=out,
                        stderr=err,
                        check=False,
                        timeout=min(budget["phase_timeout_seconds"], remaining),
                        env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
                    )
            event["exit_code"] = run.returncode
        except subprocess.TimeoutExpired:
            result["status"] = "timeout"
            event["terminated_and_reaped"] = True
            break
        event["elapsed_ms"] = (time.monotonic() - started) * 1000
        if run.returncode == 75 and phase == "save":
            event["interruption"] = "after-save-before-acknowledgement"
            result["readback"]["saved_sha256"] = proof.file_sha256(root / "advies.txt")
            continue
        if run.returncode != 0:
            result["status"] = "execution_failed"
            break
        output = root / f"{phase}.stdout"
        if output.stat().st_size > budget["output_bytes"]:
            result["status"] = "budget_exceeded"
            break
        try:
            response = json.loads(output.read_text(encoding="utf-8"))
            result["answer"] = response["answer"]
            result["response_status"] = response["status"]
        except (ValueError, KeyError):
            result["status"] = "execution_failed"
            break
        if phase in ("remember", "correct"):
            if result["latency_ms"]["acknowledgement"] is None:
                result["latency_ms"]["acknowledgement"] = event["elapsed_ms"]
        else:
            result["latency_ms"]["first_content"] = event["elapsed_ms"]
    else:
        result["status"] = "completed"
    if result["status"] == "completed":
        if task["id"] == "A":
            result["readback"]["memory"] = json.loads(
                (root / "memory.json").read_text()
            )
        else:
            result["readback"].update(
                {
                    "content": (root / "advies.txt").read_text(encoding="utf-8"),
                    "sha256": proof.file_sha256(root / "advies.txt"),
                    "mutations": json.loads((root / "mutations.json").read_text()),
                    "sources_unchanged": all(
                        (root / name).read_text() == content
                        for name, content in originals.items()
                    ),
                }
            )
        result["latency_ms"]["verified_end"] = (time.monotonic() - started) * 1000
    result["latency_ms"]["elapsed"] = (time.monotonic() - started) * 1000
    result["artifacts"] = {p.name: proof.file_sha256(p) for p in sorted(root.iterdir())}
    return result


def fixture_checks(task: dict, result: dict) -> dict:
    """Exact fixture oracle only; never claim this is semantic product grading."""
    if result["status"] != "completed":
        return {"fixture_contract": None}
    readback = result["readback"]
    checks = {"exact_fixture_answer": result["answer"] == task["expected"]}
    if task["id"] == "A":
        checks["durable_correction"] = readback.get("memory") == {"drink": "muntthee"}
        checks["new_process_conversation"] = [
            p["phase"] for p in result["phases"]
        ] == task["phases"]
    else:
        checks.update(
            {
                "independent_file_content": readback.get("content") == task["expected"],
                "same_saved_hash": readback.get("sha256")
                == readback.get("saved_sha256"),
                "single_mutation": readback.get("mutations") == ["save"],
                "sources_unchanged": readback.get("sources_unchanged") is True,
                "resumed_status": result.get("response_status") == "resumed",
                "interruption_observed": result["phases"][0].get("exit_code") == 75,
            }
        )
    return checks


def build_task(plan: dict, output: Path, fault: str):
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.model import ModelOutput
    from inspect_ai.scorer import Score, scorer
    from inspect_ai.solver import solver

    execution_order: list[str] = []

    @solver
    def paired_fixture():
        async def solve(state, generate):
            spec = state.metadata["spec"]
            execution_order.append(state.sample_id)
            result = execute_sample(spec, plan, output / state.sample_id, fault)
            result["execution_index"] = len(execution_order) - 1
            state.metadata["execution"] = result
            state.output = ModelOutput.from_content(
                model="mockllm/model", content=result["answer"] or result["status"]
            )
            return state

        return solve

    @scorer(metrics=[])
    def fixture_contract():
        async def score(state, target):
            result = state.metadata["execution"]
            checks = fixture_checks(state.metadata["spec"]["task"], result)
            value = (
                "unknown"
                if result["status"] != "completed"
                else ("C" if all(checks.values()) else "I")
            )
            return Score(
                value=value,
                metadata={"checks": checks, "product_result": "unknown"},
                explanation="Deterministic fixture plumbing only; human rubric remains unscored.",
            )

        return score

    return Task(
        name="cp001_offline_pair",
        dataset=[
            Sample(
                id=sample["id"],
                input=json.dumps(sample["task"]["turns"], ensure_ascii=False),
                metadata={
                    "spec": sample,
                    "budgets": plan["budgets"],
                    "rights": plan["rights"],
                },
            )
            for sample in schedule(plan)
        ],
        solver=paired_fixture(),
        scorer=fixture_contract(),
    )


def run(
    output: Path,
    source_commit: str,
    fault: str = "none",
    native_preflight: bool = False,
) -> dict:
    sources = dict(SOURCES)
    if native_preflight:
        sources.update(
            {
                p: native.ROOT / p
                for paths in native.NATIVE_SOURCES.values()
                for p in paths
            }
        )
    source = proof.verify_source_checkout(source_commit, sources)
    proof._verify_runtime()
    lock = proof.verify_lock()
    plan = json.loads(FIXTURES.read_text(encoding="utf-8"))
    if plan["mode"] != "offline-fixture-only" or fault not in FAULTS:
        raise ValueError("only sealed offline fixture execution is supported")
    if native_preflight:
        if fault != "none":
            raise ValueError("fault injection belongs only to fixture mode")
        plan["mode"] = "native-preflight-only"
        plan["budgets"] = {key: None for key in plan["budgets"]}
        plan["hardware"] = "unknown: no product execution"
        for task in plan["tasks"]:
            task.pop("expected", None)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {
        "source": source,
        "lock": lock,
        "plan": plan,
        "fault": fault,
        "ranking": None,
        "product_result": "unknown",
        "native_admission": {
            p: "unknown: native multi-turn/recovery route not admitted"
            for p in plan["participants"]
        },
    }
    report = native.preflight() if native_preflight else None
    if report is not None:
        manifest["native_admission"] = report
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    from inspect_ai import eval as inspect_eval
    from inspect_ai.log import list_eval_logs, read_eval_log

    logs = inspect_eval(
        native.build_preflight_task(schedule(plan), report)
        if native_preflight
        else build_task(plan, output, fault),
        model="mockllm/model",
        display="none",
        log_dir=str(output / "logs"),
        log_format="eval",
        max_samples=1,
        retry_on_error=0,
    )
    if len(logs) != 1 or logs[0].status != "success":
        raise RuntimeError("Inspect execution failed; retain logs, do not retry")
    infos = list_eval_logs(str(output / "logs"), formats=["eval"])
    if len(infos) != 1:
        raise RuntimeError("expected one native Inspect log")
    log_path = proof._local_log_path(infos[0].name)
    log = read_eval_log(log_path)
    if len(log.samples or []) != 8:
        raise RuntimeError("incomplete paired schedule")
    if not native_preflight:
        actual_order = sorted(
            log.samples, key=lambda s: s.metadata["execution"]["execution_index"]
        )
        if [s.id for s in actual_order] != [s["id"] for s in schedule(plan)]:
            raise RuntimeError("Inspect execution order differs from sealed schedule")
    summary = {
        "log": str(log_path),
        "sha256": proof.file_sha256(log_path),
        "manifest_sha256": proof.file_sha256(output / "manifest.json"),
        "samples": len(log.samples),
        "paid_model_calls": 0,
        "product_result": "unknown",
        "ranking": None,
    }
    (output / "receipt.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--native-preflight", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--fault", choices=FAULTS, default="none")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output.resolve(),
                args.source_commit,
                args.fault,
                args.native_preflight,
            )
        )
    )


if __name__ == "__main__":
    main()
