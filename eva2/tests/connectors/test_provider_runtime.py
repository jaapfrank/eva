from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa

import eva.providers.runtime as provider_runtime
from eva.providers.runtime import (
    EmailProviderSettings,
    ExternalProviderSettings,
    HomeAutomationProviderSettings,
    build_external_provider_bundle,
    load_external_provider_settings,
)


def test_provider_settings_stay_outside_core_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(provider_runtime.os.environ):
        if name.startswith(("EVA_HOME_ASSISTANT_", "EVA_AGENTMAIL_")):
            monkeypatch.delenv(name, raising=False)
    assert load_external_provider_settings() is None

    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://provider:8123")
    monkeypatch.setattr(provider_runtime, "environment_secret", lambda *_: "token")
    settings = load_external_provider_settings()
    assert settings is not None
    assert settings.home_automation is not None
    assert settings.home_automation.endpoint_url == "http://provider:8123"
    assert settings.home_automation.effects_enabled is False

    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "true")
    with pytest.raises(RuntimeError, match="ALLOWLIST is required"):
        load_external_provider_settings()


def test_email_provider_settings_are_file_only_and_live_outside_core(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    for name in tuple(provider_runtime.os.environ):
        if name.startswith(("EVA_HOME_ASSISTANT_", "EVA_AGENTMAIL_")):
            monkeypatch.delenv(name, raising=False)
    key = private_tmp_path / "agentmail-key"
    key.write_text("private-test-key\n")
    key.chmod(0o600)
    monkeypatch.setenv("EVA_AGENTMAIL_INBOX_ID", "eva@example.invalid")
    monkeypatch.setenv("EVA_AGENTMAIL_API_KEY_FILE", str(key))

    settings = load_external_provider_settings()

    assert settings is not None
    assert settings.home_automation is None
    assert settings.email == EmailProviderSettings(
        inbox_id="eva@example.invalid",
        credential="private-test-key",
    )
    monkeypatch.setenv("EVA_AGENTMAIL_API_KEY", "forbidden-inline-key")
    with pytest.raises(RuntimeError, match="API_KEY is forbidden"):
        load_external_provider_settings()


def test_provider_bundle_registers_tools_without_core_branch() -> None:
    principal = hashlib.sha256(b"person:owner").hexdigest()
    settings = ExternalProviderSettings(
        home_automation=HomeAutomationProviderSettings(
            endpoint_url="http://provider:8123",
            credential="token",
            effects_enabled=True,
            effect_allowlist=("light.desk:turn_off",),
            effect_principal_sha256_allowlist=(principal,),
            event_domains=("light",),
        ),
        email=EmailProviderSettings(
            inbox_id="eva@example.invalid",
            credential="secret",
        ),
    )
    bundle = build_external_provider_bundle(settings, sa.create_engine("sqlite://"))

    assert bundle is not None
    assert "read_home_assistant_state" in {
        name for tools in bundle.external_tools for name in tools.action_names
    }
    assert "set_home_assistant_light" in {
        name for tools in bundle.external_tools for name in tools.action_names
    }
    assert "list_email_messages" in {
        descriptor.name for descriptor in bundle.conversation_tools
    }
    assert len(bundle.external_tools) == 6
    assert bundle.management_reader is not None
    assert bundle.management_writer is not None
    assert bundle.device_manager is not None
