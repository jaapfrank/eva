from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_secret_lifecycle import (
    InstallerSecretLifecycle,
    InstallerSecretLifecycleError,
)
from eva.operations.installer_secret_store import (
    InstallerSecretStore,
    InstallerSecretStoreError,
)


def _fixture(
    root: Path,
) -> tuple[InstallerCapabilityWorkflow, InstallerSecretStore, InstallerSecretLifecycle]:
    capability_workspace = root / "capabilities"
    secret_workspace = root / "secrets"
    evidence_workspace = root / "development/evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    workflow = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T06:00:00Z",
    )
    store = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T06:01:00Z",
    )
    lifecycle = InstallerSecretLifecycle(
        capability_workflow=workflow,
        secret_store=store,
        evidence_workspace=evidence_workspace,
        clock=lambda: "2026-08-29T06:02:00Z",
    )
    return workflow, store, lifecycle


def _ready(
    workflow: InstallerCapabilityWorkflow,
    store: InstallerSecretStore,
    *,
    slot_id: str,
    secret: str,
) -> None:
    workflow.select(step_id=slot_id, wanted=True)
    store.store(slot_id=slot_id, secret=secret)
    workflow.record_verification(
        step_id=slot_id,
        outcome="ready",
        evidence_sha256="a" * 64,
    )


def test_rotation_invalidates_old_receipt_and_advances_generation(
    private_tmp_path: Path,
) -> None:
    old_secret = "synthetic-old-telegram-token-123456"
    new_secret = "synthetic-new-telegram-token-654321"
    workflow, store, lifecycle = _fixture(private_tmp_path)
    _ready(workflow, store, slot_id="telegram", secret=old_secret)

    result = lifecycle.rotate(slot_id="telegram", secret=new_secret)

    assert result.operation == "rotate"
    assert result.state == "present"
    assert result.generation == 2
    capability = workflow.status().step("telegram")
    assert capability.state == "blocked"
    assert capability.evidence_sha256 == result.invalidation_evidence_sha256
    assert capability.evidence_sha256 != "a" * 64
    assert store.read_secret(slot_id="telegram") == new_secret.encode()
    evidence = b"".join(
        path.read_bytes() for path in lifecycle.evidence_workspace.iterdir()
    )
    assert old_secret.encode() not in evidence
    assert new_secret.encode() not in evidence
    assert result.completion_evidence_sha256.encode() not in evidence


def test_removal_keeps_generation_tombstone_and_new_input_cannot_reuse_epoch(
    private_tmp_path: Path,
) -> None:
    workflow, store, lifecycle = _fixture(private_tmp_path)
    _ready(
        workflow,
        store,
        slot_id="home_assistant",
        secret="synthetic-home-token-old-123456",
    )
    rotated = lifecycle.rotate(
        slot_id="home_assistant",
        secret="synthetic-home-token-new-654321",
    )
    assert rotated.generation == 2

    removed = lifecycle.remove(slot_id="home_assistant")

    assert removed.state == "missing"
    assert removed.generation is None
    assert workflow.status().step("home_assistant").state == "blocked"
    with pytest.raises(InstallerSecretStoreError, match="not available"):
        store.read_secret(slot_id="home_assistant")
    restored = store.store(
        slot_id="home_assistant",
        secret="synthetic-home-token-third-abcdef",
    )
    assert restored.generation == 3


def test_rotation_never_swaps_bytes_when_invalidation_fails(
    private_tmp_path: Path,
) -> None:
    workflow, store, _lifecycle = _fixture(private_tmp_path)
    old_secret = "synthetic-email-token-old-123456"
    _ready(workflow, store, slot_id="email", secret=old_secret)

    def refuse(_slot: str, _old: int, _new: int) -> None:
        raise RuntimeError("synthetic refusal")

    with pytest.raises(InstallerSecretStoreError, match="not authorized"):
        store.rotate(
            slot_id="email",
            secret="synthetic-email-token-new-654321",
            invalidate=refuse,
        )

    assert store.read_secret(slot_id="email") == old_secret.encode()
    assert not any("rotation" in path.name for path in store.workspace.iterdir())


def test_invalid_replacement_does_not_invalidate_working_capability(
    private_tmp_path: Path,
) -> None:
    workflow, store, lifecycle = _fixture(private_tmp_path)
    old_secret = "synthetic-valid-model-token-123456"
    _ready(workflow, store, slot_id="model_provider", secret=old_secret)

    with pytest.raises(InstallerSecretLifecycleError, match="invalid"):
        lifecycle.rotate(slot_id="model_provider", secret="too-short")

    assert workflow.status().step("model_provider").state == "ready"
    assert store.read_secret(slot_id="model_provider") == old_secret.encode()


def test_interrupted_rotation_is_blocked_and_recovered_without_new_secret(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store, lifecycle = _fixture(private_tmp_path)
    _ready(
        workflow,
        store,
        slot_id="model_provider",
        secret="synthetic-model-token-old-123456",
    )
    evidence, _revision, _head = lifecycle._invalidate(
        slot_id="model_provider",
        operation="rotate",
        from_generation=1,
        to_generation=2,
    )
    confirm = lifecycle._confirm_invalidation(
        slot_id="model_provider",
        evidence_sha256=evidence,
        from_generation=1,
        to_generation=2,
    )
    real_replace = os.replace

    def interrupt_second_rename(source: object, destination: object) -> None:
        if str(source).endswith(".rotation-next"):
            raise OSError("synthetic process boundary")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", interrupt_second_rename)
    with pytest.raises(InstallerSecretStoreError, match="needs recovery"):
        store.rotate(
            slot_id="model_provider",
            secret="synthetic-model-token-new-654321",
            invalidate=confirm,
        )
    monkeypatch.setattr(os, "replace", real_replace)

    assert workflow.status().step("model_provider").state == "blocked"
    assert next(
        item for item in store.status() if item.slot_id == "model_provider"
    ).state == "needs_help"
    recovered = lifecycle.recover(slot_id="model_provider")
    assert recovered.state == "present"
    assert recovered.generation == 2
    assert store.read_secret(slot_id="model_provider") == (
        b"synthetic-model-token-new-654321"
    )


def test_interrupted_removal_is_recovered_only_while_capability_is_blocked(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store, lifecycle = _fixture(private_tmp_path)
    _ready(
        workflow,
        store,
        slot_id="telegram",
        secret="synthetic-telegram-token-remove-123456",
    )
    evidence, _revision, _head = lifecycle._invalidate(
        slot_id="telegram",
        operation="remove",
        from_generation=1,
        to_generation=None,
    )
    confirm = lifecycle._confirm_invalidation(
        slot_id="telegram",
        evidence_sha256=evidence,
        from_generation=1,
        to_generation=None,
    )
    real_remove_bundle = InstallerSecretStore._remove_bundle

    def interrupt_cleanup(self: InstallerSecretStore, _bundle: Path) -> None:
        raise InstallerSecretStoreError("synthetic process boundary")

    monkeypatch.setattr(InstallerSecretStore, "_remove_bundle", interrupt_cleanup)
    with pytest.raises(InstallerSecretStoreError, match="needs recovery"):
        store.remove(slot_id="telegram", invalidate=confirm)
    monkeypatch.setattr(InstallerSecretStore, "_remove_bundle", real_remove_bundle)

    assert next(item for item in store.status() if item.slot_id == "telegram").state == (
        "needs_help"
    )
    recovered = lifecycle.recover(slot_id="telegram")
    assert recovered.state == "missing"
    assert workflow.status().step("telegram").state == "blocked"


def test_lifecycle_receipts_are_canonical_and_contain_no_secret_material(
    private_tmp_path: Path,
) -> None:
    workflow, store, lifecycle = _fixture(private_tmp_path)
    secret = "synthetic-private-token-123456789"
    _ready(workflow, store, slot_id="telegram", secret=secret)
    lifecycle.remove(slot_id="telegram")

    receipts = list(lifecycle.evidence_workspace.iterdir())
    assert len(receipts) == 2
    for path in receipts:
        raw = path.read_bytes()
        payload = json.loads(raw)
        assert secret.encode() not in raw
        assert "secret_hash" not in payload
        assert "secret_length" not in payload
        assert "host_path" not in payload
