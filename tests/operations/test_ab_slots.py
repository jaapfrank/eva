from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from collections.abc import Iterator

import pytest

from eva.operations.ab_slots import (
    ABSlotError,
    ABSlotStore,
    VerifiedReleaseSlot,
    canonical_state_bytes,
    state_sha256,
)


NOW = "2026-08-23T12:00:00Z"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _release(manifest: str, *schemas: str) -> VerifiedReleaseSlot:
    return VerifiedReleaseSlot(manifest, HASH_B, HASH_C, schemas)


@pytest.fixture
def private_tmp() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="eva2-ab-slots-", dir="/tmp") as raw:
        root = Path(raw)
        root.chmod(0o700)
        yield root


def test_real_ab_cutover_and_rollback_keep_the_current_database(
    private_tmp: Path,
) -> None:
    root = private_tmp / "installation"
    store = ABSlotStore(root)
    state = store.initialize(
        installation_id="installation:test:001",
        database_lineage_id="database-lineage:test:001",
        database_schema_revision="4d_0007",
        initialized_at=NOW,
    )
    assert state.revision == 1
    assert (
        store.initialize(
            installation_id="installation:test:001",
            database_lineage_id="database-lineage:test:001",
            database_schema_revision="4d_0007",
            initialized_at=NOW,
        )
        == state
    )

    state = store.stage_release(
        "a", _release(HASH_A, "4d_0007", "4e_0001"), expected_revision=1, updated_at=NOW
    )
    state = store.activate(
        "a", activation_receipt_sha256=HASH_D, expected_revision=2, updated_at=NOW
    )
    state = store.set_traffic("open", expected_revision=3, updated_at=NOW)
    assert state.active_slot == "a"

    state = store.stage_release(
        "b", _release(HASH_B, "4e_0001"), expected_revision=4, updated_at=NOW
    )
    state = store.set_traffic("draining", expected_revision=5, updated_at=NOW)
    state = store.set_traffic("closed", expected_revision=6, updated_at=NOW)
    state = store.record_expand_migration(
        "4e_0001",
        migration_receipt_sha256=HASH_D,
        expected_revision=7,
        updated_at=NOW,
    )
    state = store.activate(
        "b", activation_receipt_sha256=HASH_C, expected_revision=8, updated_at=NOW
    )
    state = store.set_traffic("open", expected_revision=9, updated_at=NOW)
    assert state.active_slot == "b"
    assert state.slots["a"].status == "rollback_ready"

    state = store.set_traffic("closed", expected_revision=10, updated_at=NOW)
    state = store.activate(
        "a", activation_receipt_sha256=HASH_D, expected_revision=11, updated_at=NOW
    )
    state = store.set_traffic("open", expected_revision=12, updated_at=NOW)

    assert state.active_slot == "a"
    assert state.database_lineage_id == "database-lineage:test:001"
    assert state.database_schema_revision == "4e_0001"
    assert state.revision == 13
    assert canonical_state_bytes(store.load()) == canonical_state_bytes(state)


def test_cutover_is_revisioned_closed_and_schema_compatible(
    private_tmp: Path,
) -> None:
    store = ABSlotStore(private_tmp / "installation")
    state = store.initialize(
        installation_id="installation:test:002",
        database_lineage_id="database-lineage:test:002",
        database_schema_revision="4d_0007",
        initialized_at=NOW,
    )
    state = store.stage_release(
        "a", _release(HASH_A, "other_revision"), expected_revision=1, updated_at=NOW
    )
    with pytest.raises(ABSlotError, match="current canonical database"):
        store.activate(
            "a", activation_receipt_sha256=HASH_D, expected_revision=2, updated_at=NOW
        )
    with pytest.raises(ABSlotError, match="changed"):
        store.stage_release(
            "b", _release(HASH_B, "4d_0007"), expected_revision=1, updated_at=NOW
        )
    assert store.load() == state


def test_cli_status_and_diagnose_read_exact_durable_state(
    private_tmp: Path,
) -> None:
    root = private_tmp / "installation"
    state = ABSlotStore(root).initialize(
        installation_id="installation:test:003",
        database_lineage_id="database-lineage:test:003",
        database_schema_revision="4d_0007",
        initialized_at=NOW,
    )
    command = [
        sys.executable,
        "-m",
        "eva.operations.cli",
        "status",
        "--root",
        str(root),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "available"
    assert receipt["state_sha256"] == state_sha256(state)

    diagnosis = subprocess.run(
        [*command[:3], "diagnose", "--root", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(diagnosis.stdout) == {
        "errors": [],
        "state_sha256": state_sha256(state),
        "status": "passed",
    }


def test_diagnose_fails_closed_on_noncanonical_or_public_state(
    private_tmp: Path,
) -> None:
    root = private_tmp / "installation"
    store = ABSlotStore(root)
    original = store.initialize(
        installation_id="installation:test:004",
        database_lineage_id="database-lineage:test:004",
        database_schema_revision="4d_0007",
        initialized_at=NOW,
    )
    store.state_path.write_text("{}", encoding="utf-8")
    assert not store.diagnose().healthy

    store.state_path.write_bytes(canonical_state_bytes(original))
    store.state_path.chmod(0o644)
    assert not store.diagnose().healthy


def test_init_refuses_symlink_and_nonempty_unowned_root(private_tmp: Path) -> None:
    occupied = private_tmp / "occupied"
    occupied.mkdir(mode=0o700)
    (occupied / "unrelated").write_text("owner data", encoding="utf-8")
    with pytest.raises(ABSlotError, match="unrelated"):
        ABSlotStore(occupied).initialize(
            installation_id="installation:test:005",
            database_lineage_id="database-lineage:test:005",
            database_schema_revision="4d_0007",
            initialized_at=NOW,
        )

    target = private_tmp / "target"
    target.mkdir(mode=0o700)
    link = private_tmp / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        ABSlotStore(link)
