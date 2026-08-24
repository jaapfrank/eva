from __future__ import annotations

import subprocess

import pytest

from eva.operations.managed_b_candidate_image import CandidateImage
from eva.operations.managed_b_runtime_smoke import run_candidate_runtime_smoke


IMAGE = "sha256:" + "a" * 64


def _candidate() -> CandidateImage:
    return CandidateImage(IMAGE, {})


def test_runs_only_an_ephemeral_networkless_nonroot_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "eva.operations.managed_b_runtime_smoke.validate_offline_candidate_image",
        lambda _candidate, *, runner: {"status": "built_local_candidate"},
    )
    commands: list[list[str]] = []

    def runner(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="runtime-import-ok\n", stderr="")

    receipt = run_candidate_runtime_smoke(_candidate(), runner=runner).receipt

    assert receipt["status"] == "runtime_imported_nonroot"
    command = commands[0]
    assert command[:4] == ["docker", "run", "--pull=never", "--rm"]
    assert "--network=none" in command and "--read-only" in command
    assert "--user=65532:65532" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--mount" not in command and "--env" not in command


def test_refuses_unexpected_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "eva.operations.managed_b_runtime_smoke.validate_offline_candidate_image",
        lambda _candidate, *, runner: {},
    )

    def runner(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="wrong\n", stderr="")

    with pytest.raises(RuntimeError, match="runtime smoke differs"):
        run_candidate_runtime_smoke(_candidate(), runner=runner)
