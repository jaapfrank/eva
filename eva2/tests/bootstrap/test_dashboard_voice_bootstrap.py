from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.bootstrap.dashboard_voice import build_dashboard_voice
from eva.bootstrap.runtime import RuntimeSettings
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.contracts import ExternalProviderSettings, LoadedProvider
from eva.providers.home_assistant import HomeAssistantProviderFactory, HomeAssistantProviderSettings


TENANT = "00000000-0000-4000-8000-000000000001"
INSTALL = "00000000-0000-4000-8000-000000000002"
OWNER = "00000000-0000-4000-8000-000000000003"
BINDING = "00000000-0000-4000-8000-000000000004"
NOW = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)


def setup(tmp_path):
    identity = load_current_identity_pack()
    policy = {
        "contract": "DashboardVoicePolicy/v1", "schema_version": 1,
        "policy_id": "voice-policy:owner", "policy_version": 1, "state": "enabled",
        "tenant_id": TENANT, "installation_id": INSTALL, "owner_principal_id": OWNER,
        "dashboard_tenant_id": TENANT, "dashboard_installation_id": INSTALL,
        "principal_binding_id": BINDING,
        "identity_sha256": canonical_sha256(identity.model_dump(mode="json")),
        "allowed_origin": "http://localhost:44711", "provider": "home_assistant",
        "endpoint_url": "http://ha.test", "pipeline_id": "pipeline:voice",
        "pipeline_language": "nl", "stt_engine": "stt.test", "tts_engine": "tts.test",
        "tts_language": "nl-NL", "privacy": {
            "contract": "VoicePrivacy/v1", "schema_version": 1,
            "partial_transcript_retention": "ephemeral", "raw_audio_retention": "none",
            "external_provider_audio_retention": "disabled",
            "final_transcript_admission": "conversation_admission_required",
        }, "privacy_evidence_ref": "test:local-media-fixture-only",
        "effective_at": "2026-09-08T09:00:00Z", "expires_at": "2026-09-08T11:00:00Z",
    }
    path = tmp_path / "voice-policy.json"
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    provider = HomeAssistantProviderSettings("http://ha.test", "synthetic", False, (), (), ())
    settings = RuntimeSettings(
        database_dsn="postgresql://test", hindsight_url="http://memory.test",
        background_tenant_id=TENANT, background_installation_id=INSTALL,
        background_principal_id=OWNER, dashboard_tenant_id=TENANT,
        dashboard_installation_id=INSTALL, dashboard_principal_binding_id=BINDING,
        dashboard_access_token="x"*40, dashboard_voice_policy_file=str(path),
        external_provider=ExternalProviderSettings((LoadedProvider(
            "home_assistant", HomeAssistantProviderFactory(), provider),)),
    )
    return path, policy, settings, identity


def build(settings, identity, **kwargs):
    return build_dashboard_voice(settings, runtime_identity=identity,
                                 identity_loader=kwargs.pop("identity_loader", lambda: identity),
                                 clock=kwargs.pop("clock", lambda: NOW), **kwargs)


def test_absent_configuration_disables_voice_without_loading_identity(tmp_path):
    _, _, settings, identity = setup(tmp_path)
    def never():
        raise AssertionError("no voice loader when disabled")
    assert build(replace(settings, dashboard_voice_policy_file=None), identity,
                 identity_loader=never) is None


def test_existing_owner_identity_and_policy_bound_into_voice_session(tmp_path):
    _, _, settings, identity = setup(tmp_path)
    config = build(settings, identity)
    first, second = config.issue_session(), config.issue_session()
    assert first.session_id != second.session_id
    assert first.principal_id == OWNER
    assert first.conversation_id == f"dashboard:{BINDING}"
    assert first.deadline == "2026-09-08T10:30:00Z"
    assert first.identity_pack_sha256 == canonical_sha256(identity.model_dump(mode="json"))
    config.check(first)
    assert config.speech_input._expected_stt_engine == "stt.test"
    assert config.speech_output._tts_engine == "tts.test"


@pytest.mark.parametrize("field,value", [
    ("owner_principal_id", TENANT), ("tenant_id", OWNER), ("principal_binding_id", OWNER),
    ("identity_sha256", "0"*64), ("endpoint_url", "http://other.test"),
    ("allowed_origin", "http://lan.test"), ("allowed_origin", "https://example.test/path"),
    ("allowed_origin", "null"),
])
def test_invalid_present_policy_refuses_startup(tmp_path, field, value):
    path, policy, settings, identity = setup(tmp_path)
    policy[field] = value
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        build(settings, identity)


@pytest.mark.parametrize("mutation", ["delete", "revoked", "owner", "privacy", "identity", "permissions"])
def test_grant_revocation_is_rechecked_for_existing_session(tmp_path, mutation):
    path, policy, settings, identity = setup(tmp_path)
    current_identity = [identity]
    config = build(settings, identity, identity_loader=lambda: current_identity[0])
    session = config.issue_session()
    if mutation == "delete":
        path.unlink()
    elif mutation == "permissions":
        path.chmod(0o644)
    elif mutation == "identity":
        current_identity[0] = identity.model_copy(update={"display_name": "Changed"})
    else:
        if mutation == "revoked":
            policy["state"] = "revoked"
        if mutation == "owner":
            policy["owner_principal_id"] = TENANT
        if mutation == "privacy":
            policy["privacy"]["external_provider_audio_retention"] = "explicit_consent"
        path.write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        config.check(session)


def test_expired_policy_and_changed_session_scope_fail(tmp_path):
    _, _, settings, identity = setup(tmp_path)
    now = [NOW]
    config = build(settings, identity, clock=lambda: now[0])
    session = config.issue_session()
    with pytest.raises(ValueError):
        config.check(session.model_copy(update={"principal_id": TENANT}))
    now[0] = datetime(2026, 9, 8, 11, tzinfo=UTC)
    with pytest.raises(ValueError):
        config.issue_session()


@pytest.mark.parametrize("settings_change", [
    {"dashboard_access_token": None}, {"dashboard_principal_binding_id": None},
    {"dashboard_tenant_id": OWNER}, {"external_provider": None},
])
def test_missing_runtime_authority_never_falls_back(tmp_path, settings_change):
    _, _, settings, identity = setup(tmp_path)
    with pytest.raises(ValueError):
        build(replace(settings, **settings_change), identity)


def test_separate_administration_scope_preserves_existing_conversation_scope(tmp_path):
    path, policy, settings, identity = setup(tmp_path)
    policy["dashboard_tenant_id"] = OWNER
    policy["dashboard_installation_id"] = BINDING
    path.write_text(json.dumps(policy))
    config = build(replace(settings, dashboard_tenant_id=OWNER,
                           dashboard_installation_id=BINDING), identity)
    session = config.issue_session()
    config.check(session)
    assert (session.tenant_id, session.installation_id) == (TENANT, INSTALL)
    assert session.conversation_id == f"dashboard:{BINDING}"


def test_runtime_scope_identifiers_need_not_be_administration_uuids(tmp_path):
    path, policy, settings, identity = setup(tmp_path)
    policy["tenant_id"] = "runtime:tenant"
    policy["installation_id"] = "runtime:installation"
    path.write_text(json.dumps(policy))
    config = build(replace(settings, background_tenant_id="runtime:tenant",
                           background_installation_id="runtime:installation"), identity)
    session = config.issue_session()
    assert (session.tenant_id, session.installation_id) == ("runtime:tenant", "runtime:installation")
    config.check(session)


def test_deleted_policy_disables_voice_on_restart_and_keeps_old_session_revoked(tmp_path):
    path, _, settings, identity = setup(tmp_path)
    config = build(settings, identity)
    session = config.issue_session()
    path.unlink()
    with pytest.raises(ValueError):
        config.check(session)
    assert build(settings, identity) is None


def test_dangling_policy_symlink_is_invalid_not_missing_configuration(tmp_path):
    path, _, settings, identity = setup(tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        build(settings, identity)


@pytest.mark.parametrize("field,value", [
    ("state", "revoked"), ("expires_at", "2026-09-08T10:00:00Z"),
    ("effective_at", "2026-09-08T10:30:00Z"),
])
def test_inactive_valid_policy_disables_voice_on_restart(tmp_path, field, value):
    path, policy, settings, identity = setup(tmp_path)
    config = build(settings, identity)
    session = config.issue_session()
    policy[field] = value
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        config.check(session)
    assert build(settings, identity) is None


@pytest.mark.parametrize("hour,active", [(9, True), (11, False)])
def test_policy_window_exact_boundaries(tmp_path, hour, active):
    _, _, settings, identity = setup(tmp_path)
    config = build(settings, identity, clock=lambda: datetime(2026, 9, 8, hour, tzinfo=UTC))
    assert (config is not None) is active
    if config is not None:
        config.check(config.issue_session())


@pytest.mark.parametrize("mutation", ["permissions", "malformed", "scope", "provider", "identity"])
def test_inactive_policy_still_validates_safety_and_bindings(tmp_path, mutation):
    path, policy, settings, identity = setup(tmp_path)
    policy["state"] = "revoked"
    if mutation == "scope":
        policy["owner_principal_id"] = TENANT
    elif mutation == "provider":
        policy["endpoint_url"] = "http://other.test"
    elif mutation == "identity":
        policy["identity_sha256"] = "0" * 64
    path.write_text("{" if mutation == "malformed" else json.dumps(policy))
    if mutation == "permissions":
        path.chmod(0o644)
    with pytest.raises(ValueError):
        build(settings, identity)


def test_expiry_rejects_old_session_but_allows_voiceless_restart(tmp_path):
    _, _, settings, identity = setup(tmp_path)
    now = [NOW]
    config = build(settings, identity, clock=lambda: now[0])
    session = config.issue_session()
    now[0] = datetime(2026, 9, 8, 11, tzinfo=UTC)
    with pytest.raises(ValueError):
        config.check(session)
    assert build(settings, identity, clock=lambda: now[0]) is None
