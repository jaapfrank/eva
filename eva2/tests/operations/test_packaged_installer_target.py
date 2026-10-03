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


@pytest.mark.parametrize("value", ["0", "-1", "001", "1.2", "١٢", "9007199254740992", "True"])
def test_telegram_owner_requires_explicit_numeric_account(value):
    from eva.adapters.connectors.installer_configured_probe_factory import configured_installer_probe
    with pytest.raises(ValueError):
        configured_installer_probe("telegram", value)


def test_telegram_owner_files_load_as_same_private_person(tmp_path):
    from uuid import uuid4
    from tools.packaged_installer_target import prepare_telegram_owner_files
    from eva.adapters.credentials.telegram_bindings import load_principal_bindings
    identifiers = {key: str(uuid4()) for key in ("principal", "binding")}
    paths = prepare_telegram_owner_files(tmp_path, 123456789, identifiers)
    decoded = json.loads(paths["bindings"].read_text())
    assert decoded == {"contract": "EvaTelegramPrincipalBindings/v2", "bindings": [{
        "telegram_user_id": 123456789, "principal_id": identifiers["principal"],
        "principal_binding_id": identifiers["binding"], "allowed_chat_ids": ["123456789"]}]}
    bindings = load_principal_bindings(str(paths["bindings"]))
    assert bindings.contains_binding(principal_id=identifiers["principal"],
                                     principal_binding_id=__import__('uuid').UUID(identifiers["binding"]))
    assert bindings.resolve(123456789, "123456789") == identifiers["principal"]
    assert bindings.resolve(987654321, "987654321") is None
    assert bindings.resolve(123456789, "-123456789") is None
    assert len(paths["pairing"].read_bytes()) >= 32
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths.values())
    with pytest.raises(Exception):
        prepare_telegram_owner_files(tmp_path, 987654321, identifiers)


def test_selected_telegram_binds_owner_to_verification(tmp_path):
    plan, store, verifier, configuration, path = configured_selection(tmp_path)
    receipt = json.loads(path.read_bytes())
    receipt["slot_id"] = "telegram"
    receipt["secret_reference"] = "installer-secret:telegram"
    raw = json.dumps(receipt, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / (digest + ".json")).write_bytes(raw)
    (tmp_path / (digest + ".json")).chmod(0o600)
    plan["capabilities"][0].update(step_id="telegram", evidence_sha256=digest)
    plan["inputs"]["secret_bindings"][0].update(slot_id="telegram", reference="installer-secret:telegram")
    configuration.value = "123456789"
    assert verified_provider_configuration(plan, "telegram", store, verifier) == "123456789"
    configuration.configuration_sha256 = "f" * 64
    with pytest.raises(ValueError):
        verified_provider_configuration(plan, "telegram", store, verifier)


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("capability", ["telegram", "email"])
def test_ordinary_telegram_selection_wires_existing_consumer(tmp_path, monkeypatch, selected, capability):
    from pathlib import Path
    from dataclasses import dataclass
    import re
    import yaml
    import tools.packaged_installer_target as target
    from eva.adapters.objects.identity_verifier import load_current_identity_pack
    from eva.contracts.canonical_json import canonical_sha256
    from tools.installer_optional_capabilities import project_optional_capabilities
    identity = load_current_identity_pack()
    monkeypatch.setattr("eva.adapters.objects.identity_verifier.load_current_identity_pack", lambda *a, **k: identity)
    monkeypatch.setattr("eva.application.identity_verifier.load_identity_pack_trust_binding", lambda *a: None)
    monkeypatch.setattr(target, "_new_subnet", lambda *a: "10.254.200.0/24")
    monkeypatch.setattr(target, "verified_provider_configuration", lambda plan, slot, *a: "123456789" if slot == "telegram" else "owner@example.invalid")
    @dataclass(frozen=True)
    class Render:
        rendered_sha256: str = "a" * 64
    def render(plan):
        values = dict(plan.configuration)
        text = (Path(target.__file__).parents[1] / "deploy/living-core/managed-b.compose.yml").read_text()
        text = re.sub(r"\$\{(\w+)(?::[-?]([^}]*))?\}",
                      lambda m: values.get(m[1], m[2] or ""), text)
        def compose_numbers(value):
            if isinstance(value, float): return str(value)
            if isinstance(value, dict): return {k: compose_numbers(v) for k, v in value.items()}
            if isinstance(value, list): return [compose_numbers(v) for v in value]
            return value
        return Render(), compose_numbers(yaml.safe_load(text))
    monkeypatch.setattr(target, "render_managed_b_configuration", render)
    prepared = object.__new__(target.PackagedInstallerPreparation)
    prepared.root = Path(target.__file__).parents[1]
    prepared.workspace = tmp_path / "target"
    prepared.workspace.mkdir(mode=0o700)
    prepared.identity_file = tmp_path / "identity.json"
    prepared.identity_trust_file = tmp_path / "trust.json"
    prepared.identity_trust_file.write_text('{}')
    prepared.model_authority = tmp_path / "auth.json"
    prepared.manifest = {"imageDigests": {"living-core": "sha256:"+'a'*64,
        "postgres": "sha256:"+'b'*64, "hindsight": "sha256:"+'c'*64}}
    prepared.verification = prepared.secret_store = prepared.budget_workflow = object()
    prepared.provider_configuration_store = prepared.provider_verifier = object()
    plan = {"contract": "EvaInstallerInstallationPlan/v2", "status": "ready_for_release_preparation",
        "blockers": [], "capabilities": [
            {"step_id": "telegram", "state": "ready" if selected and capability == "telegram" else "skipped"},
            {"step_id": "email", "state": "ready" if selected and capability == "email" else "skipped"},
            {"step_id": "home_assistant", "state": "skipped"},
            {"step_id": "browser", "state": "skipped"}],
        "inputs": {"identity_pack_sha256": canonical_sha256(identity),
                   "profile": {"profile": {"language": identity.language, "time_zone": "Europe/Amsterdam"}}}}
    plan["plan_sha256"] = canonical_sha256(plan)
    settings = prepared._settings(plan)
    graph, _ = project_optional_capabilities(settings.rendered_graph, plan, plan['plan_sha256'])
    assert ('telegram-ingress' in graph['services']) is (selected and capability == 'telegram')
    assert ('telegram-ingress' in settings.fresh_start.writer_services) is (selected and capability == 'telegram')
    if selected and capability == "telegram":
        from eva.adapters.credentials.telegram_bindings import load_principal_bindings
        path = settings.additional_bindings[('telegram-ingress', '/run/secrets/telegram_principal_bindings')]
        assert path == settings.additional_bindings[('core', '/run/secrets/telegram_principal_bindings')]
        binding = load_principal_bindings(path)
        from uuid import UUID
        assert binding.contains_binding(principal_id=settings.fresh_start.authority['principal_id'],
            principal_binding_id=UUID(settings.fresh_start.authority['binding_id']))
        assert settings.fresh_start.writer_services[-1] == 'telegram-ingress'
        assert graph['services']['telegram-ingress']['healthcheck'] == {'disable': True}
        core = graph['services']['core']
        assert core['environment']['EVA_TELEGRAM_BOT_TOKEN_FILE'] == '/run/secrets/telegram_bot_token'
        assert 'EVA_TELEGRAM_PROACTIVE_CHAT_ID' not in core['environment']
        assert any(m['target'] == '/run/secrets/telegram_bot_token' and m['read_only']
                   for m in core['volumes'])
    else:
        assert not list((prepared.workspace / 'inputs').glob('telegram-*'))
    core = graph['services']['core']
    email_mounts = [m for m in core['volumes'] if m['target'] == '/run/secrets/agentmail_api_key']
    if selected and capability == 'email':
        assert core['environment']['EVA_AGENTMAIL_INBOX_ID'] == 'owner@example.invalid'
        assert core['environment']['EVA_AGENTMAIL_API_KEY_FILE'] == '/run/secrets/agentmail_api_key'
        assert len(email_mounts) == 1 and email_mounts[0]['read_only'] is True
        assert core['environment']['EVA_RUNTIME_PRINCIPAL_ID'] == settings.fresh_start.authority['principal_id']
    else:
        assert not email_mounts
        assert not any(key.startswith('EVA_AGENTMAIL_') for key in core['environment'])
