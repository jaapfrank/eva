"""Protect selected module bindings against stale provider verification."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from tools.packaged_installer_target import verified_provider_configuration


def configured_selection(tmp_path):
    receipt = {
        "contract": "EvaInstallerProviderVerificationReceipt/v4",
        "slot_id": "home_assistant", "outcome": "ready", "reason_code": "verified",
        "configuration_sha256": "a" * 64,
        "secret_reference": "installer-secret:home_assistant", "secret_generation": 2,
    }
    raw = json.dumps(receipt, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / (digest + ".json")
    path.write_bytes(raw)
    path.chmod(0o600)
    plan = {
        "capabilities": [{"step_id": "home_assistant", "state": "ready",
                          "evidence_sha256": digest}],
        "inputs": {"secret_bindings": [{"slot_id": "home_assistant",
                    "reference": "installer-secret:home_assistant", "generation": 2}]},
    }
    configuration = SimpleNamespace(configuration_sha256="a" * 64,
                                    value="http://192.0.2.8:8123")
    store = SimpleNamespace(read=lambda **kwargs: configuration)
    verifier = SimpleNamespace(evidence_workspace=tmp_path)
    return plan, store, verifier, configuration, path


def test_selected_endpoint_uses_its_existing_verified_binding(tmp_path):
    plan, store, verifier, configuration, _ = configured_selection(tmp_path)
    assert verified_provider_configuration(plan, "home_assistant", store, verifier) == configuration.value


@pytest.mark.parametrize("changed", ["endpoint", "credential_generation", "evidence", "selection"])
def test_changed_provider_inputs_cannot_reuse_green_verification(tmp_path, changed):
    plan, store, verifier, configuration, path = configured_selection(tmp_path)
    if changed == "endpoint":
        configuration.configuration_sha256 = "b" * 64
    elif changed == "credential_generation":
        plan["inputs"]["secret_bindings"][0]["generation"] = 3
    elif changed == "evidence":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        plan["capabilities"][0]["state"] = "skipped"
    with pytest.raises(ValueError):
        verified_provider_configuration(plan, "home_assistant", store, verifier)


def test_explicit_early_continuation_preserves_owner_access_and_rejects_later_state(tmp_path):
    import socket
    from tools.packaged_installer_target import retained_initial_target_inputs
    target = tmp_path / "target"
    inputs = target / "inputs"
    inputs.mkdir(parents=True, mode=0o700)
    profile = {"installation_name": "Retained Eva", "language": "nl"}
    trust = tmp_path / "trust.json"
    trust.write_bytes(b'{"trusted": true}')
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    files = {
        target / "owner-access.json": json.dumps({"namespace": "eva-install-123456789abc",
            "url": f"http://127.0.0.1:{port}", "development_only": True,
            "platform_support_claim": False}).encode(),
        inputs / "installation-profile.json": json.dumps(profile).encode(),
        inputs / "identity-trust.json": trust.read_bytes(),
        inputs / "dashboard-token": b'a' * 64,
    }
    for path, data in files.items():
        path.write_bytes(data)
        path.chmod(0o600)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    assert retained_initial_target_inputs(target, profile=profile, identity_trust_file=trust) == (
        "eva-install-123456789abc", port, "a" * 64)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files} == before
    with pytest.raises(ValueError, match="choices differ"):
        retained_initial_target_inputs(target, profile={**profile, "language": "en"}, identity_trust_file=trust)
    (inputs / "browser").mkdir()
    with pytest.raises(ValueError, match="initial target inputs"):
        retained_initial_target_inputs(target, profile=profile, identity_trust_file=trust)
