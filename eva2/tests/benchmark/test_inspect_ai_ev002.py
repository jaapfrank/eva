from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark.inspect_ai_v1 import ev002_baseline


ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "eva2/benchmark/inspect_ai_v1/ev002_baseline_plan.json"


def write_changed_plan(tmp_path: Path, change) -> Path:
    document = json.loads(PLAN.read_text(encoding="utf-8"))
    change(document)
    target = tmp_path / "plan.json"
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


def test_preflight_only_reads_fixed_git_and_docker_state(monkeypatch, tmp_path: Path) -> None:
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
            return subprocess.CompletedProcess(command, 0, expected["sourceCommit"] + "\n", "")
        if command[-2:] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[-4:] == ["describe", "--tags", "--exact-match", "HEAD"]:
            participant = "openclaw" if "openclaw" in command[2] else "hermes"
            expected = next(p for p in plan["participants"] if p["id"] == participant)
            return subprocess.CompletedProcess(command, 0, expected["sourceTag"] + "\n", "")
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
