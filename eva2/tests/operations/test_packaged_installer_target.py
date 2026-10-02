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
