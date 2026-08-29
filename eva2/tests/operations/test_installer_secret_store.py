from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_secret_store import (
    InstallerSecretInputError,
    InstallerSecretStore,
    InstallerSecretStoreError,
)


def _store(root: Path) -> InstallerSecretStore:
    workspace = root / "secret-workspace"
    workspace.mkdir(mode=0o700)
    return InstallerSecretStore(
        workspace=workspace,
        clock=lambda: "2026-08-29T04:00:00Z",
    )


def test_secret_is_written_once_and_only_reference_survives_restart(
    private_tmp_path: Path,
) -> None:
    store = _store(private_tmp_path)
    secret = "synthetic-token-value-123456789"
    assert {item.state for item in store.status()} == {"missing"}

    stored = store.store(slot_id="telegram", secret=secret)
    assert stored.state == "present"
    assert stored.reference == "installer-secret://telegram/v1"
    assert secret not in repr(stored)

    restarted = InstallerSecretStore(workspace=store.workspace)
    status = next(item for item in restarted.status() if item.slot_id == "telegram")
    assert status.state == "present"
    assert status.reference == "installer-secret://telegram/v1"
    assert restarted.read_secret(slot_id="telegram") == secret.encode()

    bundle = store.workspace / "slot-telegram"
    assert bundle.stat().st_mode & 0o077 == 0
    for path in bundle.iterdir():
        assert path.stat().st_mode & 0o077 == 0
        assert path.stat().st_nlink == 1
    metadata = json.loads((bundle / "metadata.json").read_text())
    assert "hash" not in json.dumps(metadata).casefold()
    assert "size" not in json.dumps(metadata).casefold()
    assert secret not in json.dumps(metadata)


@pytest.mark.parametrize(
    ("slot_id", "secret"),
    [
        ("unknown", "a" * 32),
        ("telegram", "short"),
        ("telegram", "has whitespace inside"),
        ("telegram", "é" * 32),
    ],
)
def test_secret_rejects_unknown_slot_or_invalid_opaque_value(
    private_tmp_path: Path, slot_id: str, secret: str
) -> None:
    store = _store(private_tmp_path)
    with pytest.raises(InstallerSecretInputError):
        store.store(slot_id=slot_id, secret=secret)
    assert list(store.workspace.iterdir()) == []


def test_secret_slot_refuses_overwrite_even_with_the_same_value(
    private_tmp_path: Path,
) -> None:
    store = _store(private_tmp_path)
    secret = "synthetic-token-value-123456789"
    store.store(slot_id="home_assistant", secret=secret)

    with pytest.raises(InstallerSecretStoreError, match="not empty"):
        store.store(slot_id="home_assistant", secret=secret)

    assert store.read_secret(slot_id="home_assistant") == secret.encode()


def test_existing_v1_bundle_is_read_as_first_generation(
    private_tmp_path: Path,
) -> None:
    store = _store(private_tmp_path)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")
    bundle = store.workspace / "slot-telegram"
    metadata_path = bundle / "metadata.json"
    metadata = json.loads(metadata_path.read_bytes())
    metadata.pop("generation")
    metadata["contract"] = "EvaInstallerSecretMetadata/v1"
    metadata_path.write_bytes(canonical_json_bytes(metadata))
    os.chmod(metadata_path, 0o600)
    (store.workspace / "slot-telegram.generation.json").unlink()

    restarted = InstallerSecretStore(workspace=store.workspace)
    status = next(item for item in restarted.status() if item.slot_id == "telegram")
    assert status.state == "present"
    assert status.generation == 1


def test_incomplete_bundle_is_needs_help_and_never_silently_reused(
    private_tmp_path: Path,
) -> None:
    store = _store(private_tmp_path)
    bundle = store.workspace / "slot-email"
    bundle.mkdir(mode=0o700)
    (bundle / "secret.bin").write_bytes(b"synthetic-token-value-123456789")
    os.chmod(bundle / "secret.bin", 0o600)

    status = next(item for item in store.status() if item.slot_id == "email")
    assert status.state == "needs_help"
    assert status.reference is None
    with pytest.raises(InstallerSecretStoreError, match="not empty"):
        store.store(slot_id="email", secret="another-synthetic-token-123456")
    with pytest.raises(InstallerSecretStoreError, match="not available"):
        store.read_secret(slot_id="email")


def test_secret_store_rejects_tampered_metadata_permissions_and_symlink_lock(
    private_tmp_path: Path,
) -> None:
    store = _store(private_tmp_path)
    store.store(slot_id="model_provider", secret="synthetic-model-token-123456")
    metadata = store.workspace / "slot-model_provider" / "metadata.json"
    payload = json.loads(metadata.read_text())
    payload["slot_id"] = "telegram"
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    os.chmod(metadata, 0o600)
    with pytest.raises(InstallerSecretStoreError, match="differs"):
        store.status()

    other = private_tmp_path / "other"
    other.mkdir(mode=0o700)
    second = _store(other)
    target = second.workspace / "outside"
    target.write_text("lock")
    (second.workspace / ".secret-store.lock").symlink_to(target)
    with pytest.raises(InstallerSecretStoreError, match="lock"):
        second.status()


def test_secret_store_rejects_unsafe_workspace_and_secret_file_mode(
    private_tmp_path: Path,
) -> None:
    unsafe = private_tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(InstallerSecretStoreError, match="owner-only"):
        InstallerSecretStore(workspace=unsafe)

    safe = private_tmp_path / "safe"
    safe.mkdir(mode=0o700)
    store = _store(safe)
    store.store(slot_id="telegram", secret="synthetic-token-value-123456789")
    secret_path = store.workspace / "slot-telegram" / "secret.bin"
    os.chmod(secret_path, 0o640)
    with pytest.raises(InstallerSecretStoreError, match="private regular"):
        store.status()
