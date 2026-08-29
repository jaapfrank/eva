from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from eva.adapters.api.installer_wizard import (
    InstallerBootstrap,
    create_installer_wizard,
)
from eva.adapters.api.installer_wizard_cli import run_installer_wizard
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_provider_configuration import (
    InstallerProviderConfigurationStore,
)
from eva.operations.installer_provider_verification import (
    InstallerProviderProbe,
    InstallerProviderProbeObservation,
    InstallerProviderVerifier,
)
from eva.operations.installer_secret_store import InstallerSecretStore


def _receipt(*, status: str = "ready") -> dict[str, object]:
    return {
        "checks": [
            {"detail": "Ondersteund", "id": "release_support", "state": "passed"}
        ],
        "contract": "EvaInstallerPreflight/v1",
        "host": {
            "architecture": "amd64",
            "docker_version": "29.1.3",
            "operating_system": "Ubuntu",
            "python_version": "3.12.3",
        },
        "next": "De lokale wizard mag verder.",
        "non_claims": ["geen installatie"],
        "platform_lock_claim_state": "selected_for_validation",
        "read_only": True,
        "status": status,
    }


class Probe:
    def __init__(
        self,
        result: InstallerProviderProbeObservation,
        *,
        entered: Event | None = None,
        release: Event | None = None,
    ) -> None:
        self.result = result
        self.entered = entered
        self.release = release
        self.calls = 0

    def read_only_probe(self, *, secret: memoryview, timeout_seconds: float):
        self.calls += 1
        assert bytes(secret).startswith(b"synthetic-")
        assert timeout_seconds == 10.0
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(timeout=2)
        return self.result


def _components(
    root: Path, *, slot_id: str, probe: Probe
) -> tuple[InstallerCapabilityWorkflow, InstallerSecretStore, InstallerProviderVerifier]:
    capability_workspace = root / "capabilities"
    secret_workspace = root / "secrets"
    evidence_workspace = root / "evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T06:00:00Z",
    )
    store = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T06:00:00Z",
    )
    workflow.select(step_id=slot_id, wanted=True)
    store.store(slot_id=slot_id, secret=f"synthetic-{slot_id}-secret-123456")
    moments = iter(["2026-08-29T06:01:00Z", "2026-08-29T06:01:01Z"])
    ticks = iter([10.0, 10.02])
    verifier = InstallerProviderVerifier(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        probes={slot_id: probe},
        clock=lambda: next(moments),
        monotonic=lambda: next(ticks),
    )
    return workflow, store, verifier


def _client(
    workflow: InstallerCapabilityWorkflow,
    store: InstallerSecretStore,
    verifier: InstallerProviderVerifier,
    *,
    token: str = "p" * 43,
    status: str = "ready",
    configuration_store: InstallerProviderConfigurationStore | None = None,
    configured_probe_factory: (
        Callable[[str, str], InstallerProviderProbe] | None
    ) = None,
) -> TestClient:
    bootstrap = InstallerBootstrap(token=token, created_at=1.0, clock=lambda: 1.0)
    client = TestClient(
        create_installer_wizard(
            _receipt(status=status),
            bootstrap,
            capability_workflow=workflow,
            secret_store=store,
            provider_verifier=verifier,
            provider_configuration_store=configuration_store,
            configured_probe_factory=configured_probe_factory,
        ),
        headers={"host": "127.0.0.1"},
        client=("127.0.0.1", 40000),
    )
    assert client.post("/internal/v1/bootstrap", json={"token": token}).status_code == 204
    return client


@pytest.mark.parametrize(
    ("slot_id", "configuration", "check_id"),
    [
        ("home_assistant", "http://127.0.0.1:18123", "home_assistant.api.read"),
        ("email", "eva-family@agentmail.to", "email.identity.read"),
    ],
)
def test_user_can_configure_and_verify_endpoint_provider_without_value_echo(
    private_tmp_path: Path,
    slot_id: str,
    configuration: str,
    check_id: str,
) -> None:
    probe = Probe(InstallerProviderProbeObservation(check_id, "ready", "verified"))
    workflow, store, verifier = _components(
        private_tmp_path, slot_id=slot_id, probe=probe
    )
    configuration_workspace = private_tmp_path / "provider-configuration"
    configuration_workspace.mkdir(mode=0o700)
    configuration_store = InstallerProviderConfigurationStore(
        workspace=configuration_workspace,
        clock=lambda: "2026-08-29T06:00:30Z",
    )
    factory_calls: list[tuple[str, str]] = []

    def factory(actual_slot: str, actual_value: str) -> InstallerProviderProbe:
        factory_calls.append((actual_slot, actual_value))
        assert actual_slot == slot_id
        assert actual_value == configuration
        return probe

    client = _client(
        workflow,
        store,
        verifier,
        token=("h" if slot_id == "home_assistant" else "e") * 43,
        configuration_store=configuration_store,
        configured_probe_factory=factory,
    )
    before = client.get("/internal/v1/onboarding/providers/status")
    row = next(item for item in before.json()["slots"] if item["slot_id"] == slot_id)
    assert row["can_configure"] is True
    assert row["configuration_present"] is False
    assert row["can_verify"] is False
    assert configuration not in before.text

    denied = client.post(
        f"/internal/v1/onboarding/providers/{slot_id}/configure",
        json={"configuration": configuration},
        headers={"origin": "http://evil.invalid"},
    )
    assert denied.status_code == 403
    configured = client.post(
        f"/internal/v1/onboarding/providers/{slot_id}/configure",
        json={"configuration": configuration},
        headers={"origin": "http://127.0.0.1"},
    )
    assert configured.status_code == 200
    assert configured.json()["configuration_returned"] is False
    assert len(configured.json()["configuration_sha256"]) == 64
    assert configuration not in configured.text

    after = client.get("/internal/v1/onboarding/providers/status")
    row = next(item for item in after.json()["slots"] if item["slot_id"] == slot_id)
    assert row["configuration_present"] is True
    assert row["can_verify"] is True
    assert configuration not in after.text
    verified = client.post(
        f"/internal/v1/onboarding/providers/{slot_id}/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert verified.status_code == 200
    assert verified.json()["outcome"] == "ready"
    assert probe.calls == 1
    assert factory_calls == [(slot_id, configuration), (slot_id, configuration)]
    receipt = next(verifier.evidence_workspace.iterdir()).read_text()
    assert configuration not in receipt
    assert configured.json()["configuration_sha256"] in receipt
    assert '"contract":"EvaInstallerProviderVerificationReceipt/v3"' in receipt

    resumed = _client(
        workflow,
        store,
        verifier,
        token=("j" if slot_id == "home_assistant" else "f") * 43,
        configuration_store=InstallerProviderConfigurationStore(
            workspace=configuration_workspace
        ),
        configured_probe_factory=factory,
    )
    resumed_status = resumed.get("/internal/v1/onboarding/providers/status")
    resumed_row = next(
        item for item in resumed_status.json()["slots"] if item["slot_id"] == slot_id
    )
    assert resumed_row["configuration_present"] is True
    assert resumed_row["state"] == "ready"
    replacement = resumed.post(
        f"/internal/v1/onboarding/providers/{slot_id}/configure",
        json={"configuration": configuration},
        headers={"origin": "http://127.0.0.1"},
    )
    assert replacement.status_code == 409
    assert probe.calls == 1


def test_default_home_assistant_configuration_rejects_public_literal(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation(
            "home_assistant.api.read", "ready", "verified"
        )
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="home_assistant", probe=probe
    )
    configuration_workspace = private_tmp_path / "provider-configuration"
    configuration_workspace.mkdir(mode=0o700)
    client = _client(
        workflow,
        store,
        verifier,
        token="k" * 43,
        configuration_store=InstallerProviderConfigurationStore(
            workspace=configuration_workspace
        ),
    )
    rejected = client.post(
        "/internal/v1/onboarding/providers/home_assistant/configure",
        json={"configuration": "https://8.8.8.8"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert rejected.status_code == 422
    assert configuration_store_is_empty(configuration_workspace)


def test_configuration_cannot_change_while_provider_verification_is_running(
    private_tmp_path: Path,
) -> None:
    entered = Event()
    release = Event()
    probe = Probe(
        InstallerProviderProbeObservation(
            "home_assistant.api.read", "ready", "verified"
        ),
        entered=entered,
        release=release,
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="home_assistant", probe=probe
    )
    configuration_workspace = private_tmp_path / "provider-configuration"
    configuration_workspace.mkdir(mode=0o700)
    configuration_store = InstallerProviderConfigurationStore(
        workspace=configuration_workspace,
        clock=lambda: "2026-08-29T06:00:30Z",
    )
    original = "http://127.0.0.1:18123"
    configuration_store.configure(slot_id="home_assistant", value=original)

    def factory(_slot_id: str, value: str) -> InstallerProviderProbe:
        assert value == original
        return probe

    client = _client(
        workflow,
        store,
        verifier,
        token="l" * 43,
        configuration_store=configuration_store,
        configured_probe_factory=factory,
    )
    response_codes: list[int] = []

    def verify() -> None:
        response = client.post(
            "/internal/v1/onboarding/providers/home_assistant/verify",
            json={"confirmation": "VERBINDING CONTROLEREN"},
            headers={"origin": "http://127.0.0.1"},
        )
        response_codes.append(response.status_code)

    thread = Thread(target=verify)
    thread.start()
    assert entered.wait(timeout=2)
    changed = client.post(
        "/internal/v1/onboarding/providers/home_assistant/configure",
        json={"configuration": "http://127.0.0.1:18124"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert changed.status_code == 409
    assert configuration_store.read(slot_id="home_assistant").value == original
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert response_codes == [200]
    assert probe.calls == 1


def configuration_store_is_empty(workspace: Path) -> bool:
    store = InstallerProviderConfigurationStore(workspace=workspace)
    return all(item.state == "missing" for item in store.status())


def test_user_can_verify_selected_telegram_connection_without_secret_echo(
    private_tmp_path: Path,
) -> None:
    secret = "synthetic-telegram-secret-123456"
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="telegram", probe=probe
    )
    client = _client(workflow, store, verifier)

    page = client.get("/")
    assert page.status_code == 200
    assert "Verbinding controleren" in page.text
    assert "window.__evaProviderVerificationEnabled=true" in page.text
    assert secret not in page.text
    status = client.get("/internal/v1/onboarding/providers/status")
    telegram = next(
        item for item in status.json()["slots"] if item["slot_id"] == "telegram"
    )
    assert telegram["can_verify"] is True
    assert telegram["secret_present"] is True
    assert telegram["confirmation"] == "VERBINDING CONTROLEREN"
    assert secret not in status.text

    wrong_origin = client.post(
        "/internal/v1/onboarding/providers/telegram/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://evil.invalid"},
    )
    assert wrong_origin.status_code == 403
    unconfirmed = client.post(
        "/internal/v1/onboarding/providers/telegram/verify",
        json={"confirmation": "ja"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert unconfirmed.status_code == 409
    assert probe.calls == 0

    verified = client.post(
        "/internal/v1/onboarding/providers/telegram/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert verified.status_code == 200
    assert verified.headers["cache-control"] == "no-store"
    assert verified.json()["outcome"] == "ready"
    assert verified.json()["secret_returned"] is False
    assert verified.json()["provider_payload_returned"] is False
    assert secret not in verified.text
    assert probe.calls == 1
    assert workflow.status().step("telegram").state == "ready"

    repeated = client.post(
        "/internal/v1/onboarding/providers/telegram/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert repeated.status_code == 409
    assert probe.calls == 1


def test_model_verification_requires_explicit_billable_confirmation(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation(
            "model.response.generate",
            "ready",
            "verified",
            provider_operation="bounded_generation",
            usage_measurement="measured",
            input_tokens=19,
            output_tokens=5,
            billable_request_attempted=True,
        )
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="model_provider", probe=probe
    )
    client = _client(workflow, store, verifier, token="q" * 43)
    status = client.get("/internal/v1/onboarding/providers/status").json()
    model = next(item for item in status["slots"] if item["slot_id"] == "model_provider")
    assert model["billable_request"] is True
    assert model["confirmation"] == "MODEL CONTROLEREN"

    generic = client.post(
        "/internal/v1/onboarding/providers/model_provider/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert generic.status_code == 409
    assert probe.calls == 0
    verified = client.post(
        "/internal/v1/onboarding/providers/model_provider/verify",
        json={"confirmation": "MODEL CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert verified.status_code == 200
    assert verified.json()["outcome"] == "ready"
    assert probe.calls == 1


def test_parallel_double_click_starts_only_one_provider_call(
    private_tmp_path: Path,
) -> None:
    entered = Event()
    release = Event()
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified"),
        entered=entered,
        release=release,
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="telegram", probe=probe
    )
    client = _client(workflow, store, verifier, token="r" * 43)
    responses: list[int] = []

    def first_request() -> None:
        response = client.post(
            "/internal/v1/onboarding/providers/telegram/verify",
            json={"confirmation": "VERBINDING CONTROLEREN"},
            headers={"origin": "http://127.0.0.1"},
        )
        responses.append(response.status_code)

    thread = Thread(target=first_request)
    thread.start()
    assert entered.wait(timeout=2)
    duplicate = client.post(
        "/internal/v1/onboarding/providers/telegram/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert duplicate.status_code == 409
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert responses == [200]
    assert probe.calls == 1


def test_provider_controls_are_disabled_by_platform_gate(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="telegram", probe=probe
    )
    client = _client(
        workflow, store, verifier, token="s" * 43, status="blocked"
    )
    status = client.get("/internal/v1/onboarding/providers/status")
    assert status.json()["enabled"] is False
    assert all(not item["can_verify"] for item in status.json()["slots"])
    denied = client.post(
        "/internal/v1/onboarding/providers/telegram/verify",
        json={"confirmation": "VERBINDING CONTROLEREN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 409
    assert probe.calls == 0


def test_wizard_rejects_provider_verifier_from_different_workspaces(
    private_tmp_path: Path,
) -> None:
    probe = Probe(
        InstallerProviderProbeObservation("telegram.get_me", "ready", "verified")
    )
    workflow, store, verifier = _components(
        private_tmp_path, slot_id="telegram", probe=probe
    )
    other = private_tmp_path / "other-capabilities"
    other.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="different workspaces"):
        create_installer_wizard(
            _receipt(),
            InstallerBootstrap(token="t" * 43, created_at=1.0, clock=lambda: 1.0),
            capability_workflow=InstallerCapabilityWorkflow(workspace=other),
            secret_store=store,
            provider_verifier=verifier,
        )
    assert workflow.status().step("telegram").state == "selected"
    assert probe.calls == 0


def test_launcher_opens_model_and_telegram_verification_without_manual_config(
    private_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_workspace = private_tmp_path / "launcher-capabilities"
    secret_workspace = private_tmp_path / "launcher-secrets"
    evidence_workspace = private_tmp_path / "launcher-evidence"
    lock_path = private_tmp_path / "platform-lock.json"
    lock_path.write_text("{}")
    fixed_bootstrap = InstallerBootstrap(
        token="u" * 43, created_at=1.0, clock=lambda: 1.0
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "eva.adapters.api.installer_wizard.InstallerBootstrap.create",
        lambda: fixed_bootstrap,
    )
    monkeypatch.setattr(
        "eva.adapters.api.installer_wizard_cli.load_platform_lock", lambda _path: object()
    )
    monkeypatch.setattr(
        "eva.adapters.api.installer_wizard_cli.inspect_host", lambda: object()
    )
    monkeypatch.setattr(
        "eva.adapters.api.installer_wizard_cli.preflight",
        lambda _lock, _host: _receipt(),
    )

    def capture(app, **kwargs: object) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr("eva.adapters.api.installer_wizard_cli.uvicorn.run", capture)
    assert (
        run_installer_wizard(
            [
                "--platform-lock",
                str(lock_path),
                "--capability-workspace",
                str(capability_workspace),
                "--secret-workspace",
                str(secret_workspace),
                "--provider-evidence-workspace",
                str(evidence_workspace),
            ]
        )
        == 0
    )
    app = captured["app"]
    client = TestClient(
        app,  # type: ignore[arg-type]
        headers={"host": "127.0.0.1"},
        client=("127.0.0.1", 40000),
    )
    assert (
        client.post(
            "/internal/v1/bootstrap", json={"token": "u" * 43}
        ).status_code
        == 204
    )
    status = client.get("/internal/v1/onboarding/providers/status").json()
    availability = {
        item["slot_id"]: item["available"] for item in status["slots"]
    }
    assert availability == {
        "model_provider": True,
        "telegram": True,
        "home_assistant": False,
        "email": False,
    }
    assert captured["kwargs"] == {
        "host": "127.0.0.1",
        "port": 8765,
        "log_level": "warning",
    }
