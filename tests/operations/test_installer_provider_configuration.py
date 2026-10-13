from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_provider_configuration import (
    InstallerProviderConfigurationError,
    InstallerProviderConfigurationInputError,
    InstallerProviderConfigurationStore,
)


def _store(root: Path) -> InstallerProviderConfigurationStore:
    root.mkdir(mode=0o700)
    return InstallerProviderConfigurationStore(
        workspace=root, clock=lambda: "2026-08-29T07:00:00Z"
    )


def test_configuration_is_private_hashed_restart_safe_and_idempotent(
    private_tmp_path: Path,
) -> None:
    root = private_tmp_path / "provider-configuration"
    store = _store(root)
    assert [item.state for item in store.status()] == ["missing", "missing", "missing"]

    retained = store.configure(
        slot_id="home_assistant", value="http://homeassistant.local:8123"
    )
    assert retained.revision == 1
    assert len(retained.configuration_sha256) == 64
    assert store.configure(
        slot_id="home_assistant", value="http://homeassistant.local:8123"
    ) == retained

    resumed = InstallerProviderConfigurationStore(workspace=root)
    status = resumed.status()
    home = next(item for item in status if item.slot_id == "home_assistant")
    assert home.state == "configured"
    assert home.configuration_sha256 == retained.configuration_sha256
    assert home.revision == 1
    assert "homeassistant.local" not in repr(status)
    assert resumed.read(slot_id="home_assistant").value == (
        "http://homeassistant.local:8123"
    )
    assert store.state_path.stat().st_mode & 0o077 == 0


def test_configuration_replacement_advances_revision_and_detects_tampering(
    private_tmp_path: Path,
) -> None:
    root = private_tmp_path / "provider-configuration"
    store = _store(root)
    first = store.configure(slot_id="email", value="first@agentmail.to")
    second = store.configure(slot_id="email", value="second@agentmail.to")
    assert second.revision == 2
    assert second.configuration_sha256 != first.configuration_sha256

    payload = json.loads(store.state_path.read_bytes())
    payload["slots"]["email"]["value"] = "tampered@agentmail.to"
    store.state_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(InstallerProviderConfigurationError, match="invalid"):
        store.status()


@pytest.mark.parametrize(
    ("slot_id", "value"),
    [
        ("telegram", "not-supported"),
        ("email", ""),
        ("email", " leading"),
        ("email", "line\nbreak"),
        ("email", "x" * 1_025),
    ],
)
def test_configuration_rejects_unknown_or_unsafe_input_without_writing(
    private_tmp_path: Path, slot_id: str, value: str
) -> None:
    root = private_tmp_path / "provider-configuration"
    store = _store(root)
    with pytest.raises(InstallerProviderConfigurationInputError):
        store.configure(slot_id=slot_id, value=value)
    assert not store.state_path.exists()


def test_configuration_refuses_non_private_workspace(private_tmp_path: Path) -> None:
    root = private_tmp_path / "provider-configuration"
    root.mkdir(mode=0o755)
    with pytest.raises(InstallerProviderConfigurationError, match="owner-only"):
        InstallerProviderConfigurationStore(workspace=root)
