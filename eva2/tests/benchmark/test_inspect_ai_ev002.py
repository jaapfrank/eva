from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark.inspect_ai_v1 import ev002_baseline


ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "eva2/benchmark/inspect_ai_v1/ev002_baseline_plan.json"
PROBE_PATH = ROOT / "eva2/experiments/managed-b-clean-graph-v1/probe.py"
SOURCE_ROOT = ROOT / "eva2/src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
PROBE_SPEC = importlib.util.spec_from_file_location(
    "ev002_clean_graph_probe", PROBE_PATH
)
assert PROBE_SPEC is not None and PROBE_SPEC.loader is not None
probe = importlib.util.module_from_spec(PROBE_SPEC)
PROBE_SPEC.loader.exec_module(probe)


def write_changed_plan(tmp_path: Path, change) -> Path:
    document = json.loads(PLAN.read_text(encoding="utf-8"))
    change(document)
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def result_fixture() -> dict:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    participants = []
    for source in plan["participants"]:
        completed = source["id"] in {"eva2", "hermes"}
        reply = "Ik spreek Lola niet streng toe; de sok ligt onder Eriks stoel."
        participants.append(
            {
                "id": source["id"],
                "sourceCommit": source["sourceCommit"],
                "status": "completed" if completed else "admission_failed",
                "promptDisposition": (
                    "submitted_to_native_route" if completed else "withheld_by_harness"
                ),
                "nativeRoute": "test",
                "modelCalls": 1 if completed else 0,
                "toolCalls": 0,
                "inputTokens": 10 if completed else 0,
                "outputTokens": 12 if completed else 0,
                "wallClockMs": 100 if completed else None,
                "reply": reply if completed else None,
                "replySha256": (
                    hashlib.sha256(reply.encode("utf-8")).hexdigest()
                    if completed
                    else None
                ),
                "semanticCriteria": {
                    "sceneSpecific": True if completed else None,
                    "independentDissentOrLightJoke": True if completed else None,
                    "noInternalLanguage": True if completed else None,
                },
                "failure": (
                    None
                    if completed
                    else {
                        "stage": "pre_model_admission",
                        "code": "constraint_not_enforceable",
                        "message": "Native route cannot prove the fixed constraint.",
                    }
                ),
                "evidenceQuality": (
                    "native_usage" if completed else "static_admission_check"
                ),
                "cost": {
                    "amountUsd": None if completed else 0,
                    "measurement": "unknown" if completed else "no_model_call",
                },
            }
        )
    return {
        "contract": "EvaInspectControlledBaselineResult/v1",
        "taskId": "EV-002",
        "createdAt": "2026-09-04T00:00:00+02:00",
        "sourcePlanSha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "claimLimit": plan["claimLimit"],
        "sharedRunContract": plan["sharedRunContract"],
        "participants": participants,
        "totals": {
            "modelCalls": 2,
            "inputTokens": 20,
            "outputTokens": 24,
            "toolCalls": 0,
            "paidJudgeCalls": 0,
            "knownCostUsd": 0.0,
            "unknownSuccessfulCostCount": 2,
        },
        "comparison": {
            "successfulParticipants": ["eva2", "hermes"],
            "admissionFailures": ["openclaw", "agent_zero"],
            "ranking": None,
            "generalWinner": None,
        },
        "nonClaims": [
            "Not a product ranking.",
            "Not a cross-domain result.",
            "Admission failure is not a quality score.",
        ],
    }


def write_result(tmp_path: Path, document: dict) -> Path:
    target = tmp_path / "result.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def test_plan_pins_only_required_participants_and_a_bounded_run() -> None:
    plan = ev002_baseline.load_plan(PLAN)
    assert [participant["id"] for participant in plan["participants"]] == [
        "eva2",
        "openclaw",
        "hermes",
        "agent_zero",
    ]
    assert plan["sharedRunContract"] == {
        "model": "gpt-5.4-mini",
        "reasoningEffort": "medium",
        "epochs": 1,
        "tools": "none",
        "freshConversation": True,
        "network": "model_endpoint_only",
        "retryLimit": 0,
        "maxModelCallsPerParticipant": 2,
        "maxProviderInputTokensPerParticipant": 24000,
        "maxProviderOutputTokensPerParticipant": 4096,
        "maxWallClockSecondsPerParticipant": 120,
        "maxTotalModelCalls": 8,
        "paidJudgeCalls": 0,
    }
    assert plan["status"] == "preflight_only"


@pytest.mark.parametrize(
    "change",
    [
        lambda plan: plan["participants"].append(dict(plan["participants"][0])),
        lambda plan: plan["sharedRunContract"].update({"model": "different"}),
        lambda plan: plan["sharedRunContract"].update({"maxTotalModelCalls": 9}),
        lambda plan: plan["sharedRunContract"].update({"retryLimit": 1}),
        lambda plan: plan["task"].update({"stimulus": "easier"}),
        lambda plan: plan["participants"][1].update({"sourceCommit": "0" * 40}),
        lambda plan: plan.update({"status": "run_enabled"}),
    ],
)
def test_plan_rejects_scope_or_cost_expansion(tmp_path: Path, change) -> None:
    with pytest.raises(RuntimeError):
        ev002_baseline.load_plan(write_changed_plan(tmp_path, change))


def test_preflight_only_reads_fixed_git_and_docker_state(
    monkeypatch, tmp_path: Path
) -> None:
    plan = ev002_baseline.load_plan(PLAN)
    roots = {"openclaw": tmp_path / "openclaw", "hermes": tmp_path / "hermes"}
    for root in roots.values():
        root.mkdir()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            expected_image = command[3].rsplit("@", 1)[1]
            return subprocess.CompletedProcess(command, 0, expected_image + "\n", "")
        if command[-2:] == ["rev-parse", "HEAD"]:
            participant = "openclaw" if "openclaw" in command[2] else "hermes"
            expected = next(p for p in plan["participants"] if p["id"] == participant)
            return subprocess.CompletedProcess(
                command, 0, expected["sourceCommit"] + "\n", ""
            )
        if command[-2:] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[-4:] == ["describe", "--tags", "--exact-match", "HEAD"]:
            participant = "openclaw" if "openclaw" in command[2] else "hermes"
            expected = next(p for p in plan["participants"] if p["id"] == participant)
            return subprocess.CompletedProcess(
                command, 0, expected["sourceTag"] + "\n", ""
            )
        raise AssertionError(command)

    monkeypatch.setattr(ev002_baseline.subprocess, "run", fake_run)
    result = ev002_baseline.local_preflight(plan, roots)
    assert result["status"] == "ready"
    assert result["paidModelCalls"] == 0
    assert result["networkCalls"] == 0
    assert all(command[0] in {"git", "docker"} for command in calls)


def test_missing_competitor_sources_fail_before_any_run(monkeypatch) -> None:
    plan = ev002_baseline.load_plan(PLAN)

    def fake_run(command, **kwargs):
        assert command[:3] == ["docker", "image", "inspect"]
        expected_image = command[3].rsplit("@", 1)[1]
        return subprocess.CompletedProcess(command, 0, expected_image + "\n", "")

    monkeypatch.setattr(ev002_baseline.subprocess, "run", fake_run)
    result = ev002_baseline.local_preflight(plan, {})
    assert result["status"] == "not_ready"
    assert result["paidModelCalls"] == 0
    assert result["participants"]["openclaw"]["status"] == "not_ready"
    assert result["participants"]["hermes"]["status"] == "not_ready"


def test_cli_has_no_run_mode(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["ev002_baseline.py", "--check-plan", "--run"])
    with pytest.raises(SystemExit) as error:
        ev002_baseline.main()
    assert error.value.code == 2
    assert "--run" in capsys.readouterr().err


def test_result_accepts_successes_and_honest_pre_model_failures(tmp_path: Path) -> None:
    result = ev002_baseline.validate_result(write_result(tmp_path, result_fixture()))
    assert result["totals"]["modelCalls"] == 2
    assert result["comparison"]["ranking"] is None


@pytest.mark.parametrize(
    "change",
    [
        lambda result: result["totals"].update({"modelCalls": 3}),
        lambda result: result["participants"][0].update({"toolCalls": 1}),
        lambda result: result["participants"][1].update({"replySha256": "0" * 64}),
        lambda result: result["participants"][1].update({"modelCalls": 1}),
        lambda result: result["comparison"].update({"ranking": ["eva2"]}),
    ],
)
def test_result_rejects_fabrication_scope_expansion_or_ranking(
    tmp_path: Path, change
) -> None:
    document = result_fixture()
    change(document)
    with pytest.raises(RuntimeError):
        ev002_baseline.validate_result(write_result(tmp_path, document))


def test_disposable_eva_probe_accepts_one_bounded_prompt_file(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(ev002_baseline.EXPECTED_TASK["stimulus"] + "\n", encoding="utf-8")

    assert probe._conversation_prompt(None) == probe.DEFAULT_CONVERSATION_PROMPT
    assert (
        probe._conversation_prompt(prompt) == ev002_baseline.EXPECTED_TASK["stimulus"]
    )

    linked = tmp_path / "linked.txt"
    linked.symlink_to(prompt)
    with pytest.raises(probe.ProbeError):
        probe._conversation_prompt(linked)


def test_disposable_eva_probe_requires_measured_tool_free_bounded_usage() -> None:
    reply = "Dat lijkt mij vooral een sok met gevoel voor slechte timing."
    durable = {
        "provider": "chatgpt-codex:gpt-5.4-mini",
        "text": reply,
        "model_usage": {
            "input_tokens": 2000,
            "measurement": "measured",
            "model_attempts": 1,
            "output_tokens": 40,
            "tool_calls": 0,
        },
    }

    assert probe._conversation_measurement(durable, expected_text=reply) == {
        "inputTokens": 2000,
        "measurement": "measured",
        "modelAttempts": 1,
        "outputTokens": 40,
        "provider": "chatgpt-codex:gpt-5.4-mini",
        "toolCalls": 0,
    }

    for field, value in (
        ("tool_calls", 1),
        ("model_attempts", 3),
        ("input_tokens", 24001),
    ):
        changed = json.loads(json.dumps(durable))
        changed["model_usage"][field] = value
        with pytest.raises(probe.ProbeError):
            probe._conversation_measurement(changed, expected_text=reply)


def test_disposable_eva_probe_rejects_unsafe_receipt_parent_before_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    receipt_parent = tmp_path / "not-owned"
    receipt_parent.mkdir(mode=0o700)
    receipt = receipt_parent / "receipt.json"
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)

    assert probe.main(receipt_path=receipt) == 2
    assert not receipt.exists()
    assert "parent must be private and owned" in capsys.readouterr().out
