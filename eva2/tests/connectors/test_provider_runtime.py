from __future__ import annotations

import hashlib

import pytest
import sqlalchemy as sa

import eva.providers.runtime as provider_runtime
from eva.providers.runtime import (
    ExternalProviderSettings,
    build_external_provider_bundle,
    load_external_provider_settings,
)


def test_provider_settings_stay_outside_core_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(provider_runtime.os.environ):
        if name.startswith("EVA_HOME_ASSISTANT_"):
            monkeypatch.delenv(name, raising=False)
    assert load_external_provider_settings() is None

    monkeypatch.setenv("EVA_HOME_ASSISTANT_URL", "http://provider:8123")
    monkeypatch.setattr(provider_runtime, "environment_secret", lambda *_: "token")
    settings = load_external_provider_settings()
    assert settings is not None
    assert settings.endpoint_url == "http://provider:8123"
    assert settings.effects_enabled is False

    monkeypatch.setenv("EVA_HOME_ASSISTANT_EFFECTS_ENABLED", "true")
    with pytest.raises(RuntimeError, match="ALLOWLIST is required"):
        load_external_provider_settings()


def test_provider_bundle_registers_tools_without_core_branch() -> None:
    principal = hashlib.sha256(b"person:owner").hexdigest()
    settings = ExternalProviderSettings(
        endpoint_url="http://provider:8123",
        credential="token",
        effects_enabled=True,
        effect_allowlist=("light.desk:turn_off",),
        effect_principal_sha256_allowlist=(principal,),
        event_domains=("light",),
    )
    bundle = build_external_provider_bundle(settings, sa.create_engine("sqlite://"))

    assert bundle is not None
    assert "read_home_assistant_state" in {
        name for tools in bundle.external_tools for name in tools.action_names
    }
    assert "set_home_assistant_light" in {
        name for tools in bundle.external_tools for name in tools.action_names
    }
    assert len(bundle.external_tools) == 5
    assert bundle.management_reader is not None
    assert bundle.management_writer is not None
    assert bundle.device_manager is not None
