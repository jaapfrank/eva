from datetime import UTC, datetime
import hashlib
import json
from types import SimpleNamespace

import pytest

from eva.bootstrap.household_voice import load_household_voice_authority
from eva.contracts.canonical_json import canonical_sha256


@pytest.fixture
def setup(tmp_path):
    identity = SimpleNamespace(state="current", model_dump=lambda **kw: {"state": "current"})
    token = tmp_path / "token"
    token.write_text("synthetic-integration-token")
    token.chmod(0o600)
    path = tmp_path / "policy.json"
    policy = dict(contract="HouseholdVoicePolicy/v1", schema_version=1,
                  policy_id="home-voice", policy_version=1, state="enabled",
                  tenant_id="tenant", installation_id="installation",
                  owner_principal_id="00000000-0000-0000-0000-000000000001",
                  principal_binding_id="00000000-0000-0000-0000-000000000002",
                  identity_sha256=canonical_sha256(identity.model_dump()),
                  token_sha256=hashlib.sha256(token.read_bytes()).hexdigest(),
                  household_id="home", consent_operation_id="household-consent:owner-1",
                  devices={"assist_satellite.room": "a" * 32},
                  effective_at="2026-09-01T00:00:00Z", expires_at="2026-10-01T00:00:00Z")
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    settings = SimpleNamespace(household_voice_policy_file=str(path),
                               household_voice_token_file=str(token),
                               background_tenant_id="tenant", background_installation_id="installation",
                               background_principal_id=policy["owner_principal_id"],
                               dashboard_principal_binding_id=policy["principal_binding_id"])
    clock = [datetime(2026, 9, 9, tzinfo=UTC)]
    def load():
        return load_household_voice_authority(settings, runtime_identity=identity,
                                              identity_loader=lambda: identity, clock=lambda: clock[0])
    return SimpleNamespace(load=load, path=path, token=token, policy=policy, settings=settings,
                           identity=identity, clock=clock)


def reference(authority, now, actor="household-device:" + "a" * 32, household="home"):
    return authority.active_reference(principal_id=actor, household_id=household, now=now)


def test_only_registered_device_audit_actor_gets_household_reference(setup):
    authority = setup.load()
    assert reference(authority, setup.clock[0]).operation_id == "household-consent:owner-1"
    assert reference(authority, setup.clock[0], actor=setup.settings.background_principal_id) is None
    assert reference(authority, setup.clock[0], household="other") is None
    assert reference(authority, setup.clock[0], actor="Jaap") is None


@pytest.mark.parametrize("change", ["remove", "revoke", "token", "identity", "expire", "mapping"])
def test_current_revocation_invalidates_existing_resolver(setup, change):
    authority = setup.load()
    original_now = setup.clock[0]
    if change == "remove":
        setup.path.unlink()
    elif change == "token":
        setup.token.write_text("rotated-token")
    elif change == "identity":
        setup.identity.state = "retired"
    elif change == "expire":
        setup.clock[0] = datetime(2026, 10, 1, tzinfo=UTC)
    else:
        if change == "revoke":
            setup.policy["state"] = "revoked"
        else:
            setup.policy["devices"] = {"assist_satellite.room": "b" * 32}
        setup.path.write_text(json.dumps(setup.policy))
    assert reference(authority, original_now) is None
    with pytest.raises((ValueError, RuntimeError, OSError)):
        authority.validate_current()


@pytest.mark.parametrize("field", ["tenant_id", "installation_id", "owner_principal_id", "principal_binding_id"])
def test_other_owner_or_installation_policy_rejected(setup, field):
    setup.policy[field] = ("00000000-0000-0000-0000-000000000003"
                           if field.endswith("principal_id") or field == "principal_binding_id" else "other")
    setup.path.write_text(json.dumps(setup.policy))
    with pytest.raises(ValueError, match="owner scope"):
        setup.load()


@pytest.mark.parametrize("state", ["unconfigured", "missing", "revoked", "expired"])
def test_inactive_channel_does_not_disable_core_startup(setup, state):
    if state == "unconfigured":
        setup.settings.household_voice_policy_file = None
    elif state == "missing":
        setup.path.unlink()
    elif state == "revoked":
        setup.policy["state"] = "revoked"
        setup.path.write_text(json.dumps(setup.policy))
    else:
        setup.clock[0] = datetime(2026, 10, 1, tzinfo=UTC)
    assert setup.load() is None


def test_world_readable_authority_rejected(setup):
    setup.path.chmod(0o644)
    with pytest.raises(ValueError):
        setup.load()


@pytest.mark.parametrize("household", [".home", "-home", "_home"])
def test_invalid_household_id_rejected_before_service_start(setup, household):
    setup.policy["household_id"] = household
    setup.path.write_text(json.dumps(setup.policy))
    with pytest.raises(ValueError):
        setup.load()
