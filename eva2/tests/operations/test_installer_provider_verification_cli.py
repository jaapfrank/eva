from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_verification import (
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
)
from eva.operations.installer_provider_verification_cli import (
    run_installer_provider_verification,
)
from eva.operations.installer_secret_store import InstallerSecretStore


class CliProbe:
    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.calls = 0

    def read_only_probe(self, *, secret: memoryview, timeout_seconds: float):
        self.calls += 1
        assert bytes(secret) == b"7000000001:Eva2SyntheticTokenValue_1234567890ABCDE"
        assert timeout_seconds == 3.0
        if not self.ready:
            raise InstallerProviderProbeError("unauthorized")
        return InstallerProviderProbeObservation(
            "telegram.get_me", "ready", "verified"
        )


def _workspaces(root: Path) -> tuple[Path, Path, Path]:
    capability = root / "capabilities"
    secrets = root / "secrets"
    evidence = root / "evidence"
    for path in (capability, secrets, evidence):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(workspace=capability)
    workflow.select(step_id="telegram", wanted=True)
    InstallerSecretStore(workspace=secrets).store(
        slot_id="telegram",
        secret="7000000001:Eva2SyntheticTokenValue_1234567890ABCDE",
    )
    return capability, secrets, evidence


def _arguments(paths: tuple[Path, Path, Path]) -> list[str]:
    capability, secrets, evidence = paths
    return [
        "--slot",
        "telegram",
        "--capability-workspace",
        str(capability),
        "--secret-workspace",
        str(secrets),
        "--evidence-workspace",
        str(evidence),
        "--timeout-seconds",
        "3",
    ]


def test_cli_marks_ready_and_outputs_only_closed_receipt_fields(
    private_tmp_path: Path, capsys
) -> None:
    paths = _workspaces(private_tmp_path)
    probe = CliProbe(ready=True)

    exit_code = run_installer_provider_verification(
        _arguments(paths), probes={"telegram": probe}
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "passed"
    assert payload["outcome"] == "ready"
    assert payload["reason_code"] == "verified"
    assert payload["secret_returned"] is False
    assert payload["provider_payload_retained"] is False
    assert set(payload) == {
        "capability_revision",
        "contract",
        "evidence_sha256",
        "outcome",
        "provider_payload_retained",
        "reason_code",
        "secret_returned",
        "slot_id",
        "status",
    }
    assert "synthetic_token" not in json.dumps(payload)
    assert probe.calls == 1


def test_cli_blocked_result_is_sanitized_and_nonzero(
    private_tmp_path: Path, capsys
) -> None:
    paths = _workspaces(private_tmp_path)
    probe = CliProbe(ready=False)

    exit_code = run_installer_provider_verification(
        _arguments(paths), probes={"telegram": probe}
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "unauthorized"
    assert payload["secret_returned"] is False
    assert "synthetic_token" not in json.dumps(payload)


def test_cli_preflight_failure_does_not_call_probe_or_echo_paths(
    private_tmp_path: Path, capsys
) -> None:
    capability = private_tmp_path / "missing-capability"
    secrets = private_tmp_path / "missing-secrets"
    evidence = private_tmp_path / "missing-evidence"
    probe = CliProbe(ready=True)

    exit_code = run_installer_provider_verification(
        _arguments((capability, secrets, evidence)), probes={"telegram": probe}
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert payload == {
        "contract": "EvaInstallerProviderVerificationCliResult/v1",
        "error": "provider verification could not be completed",
        "provider_payload_retained": False,
        "secret_returned": False,
        "status": "failed",
    }
    assert str(private_tmp_path) not in output
    assert probe.calls == 0


@pytest.mark.parametrize(
    ("slot_id", "extra_arguments"),
    [
        ("home_assistant", []),
        ("telegram", ["--home-assistant-url", "http://127.0.0.1:8123"]),
    ],
)
def test_cli_rejects_missing_or_irrelevant_home_assistant_url_without_echo(
    private_tmp_path: Path,
    capsys,
    slot_id: str,
    extra_arguments: list[str],
) -> None:
    arguments = _arguments(_workspaces(private_tmp_path))
    arguments[1] = slot_id
    arguments.extend(extra_arguments)

    exit_code = run_installer_provider_verification(arguments)

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output)["status"] == "failed"
    assert "127.0.0.1" not in output
