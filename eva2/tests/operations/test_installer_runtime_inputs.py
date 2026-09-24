from dataclasses import FrozenInstanceError
import json

import pytest

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.installer_installation_plan import build_installer_installation_plan
from eva.operations.installer_runtime_inputs import bind_installer_runtime_inputs
from eva.operations.installer_secret_store import InstallerSecretStore
from eva.operations.release_verifier import verify_release_manifest
from tests.operations.test_installer_installation_plan import _preflight, _profile, _budget, _capabilities
from tests.operations.test_release_verifier import _signed_release, NOW


@pytest.fixture
def inputs(tmp_path):
    (tmp_path / "secrets").mkdir(mode=0o700)
    store = InstallerSecretStore(tmp_path / "secrets")
    store.store(slot_id="model_provider", secret='{"access_token":"opaque-private-test"}')
    budget = _budget()
    plan = build_installer_installation_plan(
        preflight=_preflight(), identity=load_current_identity_pack(),
        profile=_profile(), capabilities=_capabilities(budget_sha256=budget.budget_sha256),
        secrets=store.status(), budgets=budget,
    )
    manifest, root = _signed_release()
    return dict(plan=plan, expected_plan_sha256=plan["plan_sha256"],
                release_manifest=json.loads(manifest),
                release_verification=verify_release_manifest(manifest, root, verified_at=NOW),
                secret_store=store)


def test_exact_release_and_opaque_secret_are_bound_without_exposure(inputs):
    result = bind_installer_runtime_inputs(**inputs)
    assert result.secrets[0].content == b'{"access_token":"opaque-private-test"}'
    assert "opaque-private-test" not in repr(result)
    assert "opaque-private-test" not in repr(result.secrets[0])
    assert "opaque-private-test" not in repr(result.receipt)
    assert dict(result.receipt.images) == inputs["release_manifest"]["imageDigests"]
    inputs["release_manifest"]["imageDigests"].clear()
    inputs["plan"]["inputs"]["secret_bindings"].clear()
    assert len(result.receipt.images) == 2
    assert len(result.receipt.secret_bindings) == 1
    with pytest.raises(FrozenInstanceError):
        result.receipt.plan_sha256 = "changed"


@pytest.mark.parametrize("change", ["stale_hash", "plan_body", "blocked", "release", "images", "unverified"])
def test_changed_confirmation_or_release_is_rejected(inputs, change):
    if change == "stale_hash":
        inputs["expected_plan_sha256"] = "0" * 64
    elif change == "plan_body":
        inputs["plan"]["inputs"]["profile"]["revision"] += 1
    elif change == "blocked":
        inputs["plan"]["status"] = "blocked"
        body = {k: v for k, v in inputs["plan"].items() if k != "plan_sha256"}
        inputs["plan"]["plan_sha256"] = inputs["expected_plan_sha256"] = canonical_sha256(body)
    elif change == "release":
        inputs["release_manifest"]["releaseVersion"] = "different"
    elif change == "images":
        inputs["release_manifest"]["imageDigests"]["living_core"] = "sha256:" + "0" * 64
    else:
        inputs["release_verification"] = object()
    with pytest.raises(ValueError):
        bind_installer_runtime_inputs(**inputs)


def test_actual_rotated_secret_generation_is_rejected(inputs):
    store = inputs["secret_store"]
    invalidations = []
    store.rotate(slot_id="model_provider", secret="new-private-test",
                 invalidate=lambda *args: invalidations.append(args))
    assert invalidations == [("model_provider", 1, 2)]
    with pytest.raises(ValueError, match="secret changed"):
        bind_installer_runtime_inputs(**inputs)
