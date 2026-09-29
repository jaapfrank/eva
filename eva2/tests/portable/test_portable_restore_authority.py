from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eva.application.portable_archive import canonical_json_bytes
from eva.operations.portable_restore_authority import (
    PrivateRestoreAuthorityStore,
    PortableRestoreAuthorityError,
    advance_restore_authority_for_erasure,
    authorize_portable_restore,
    read_private_restore_authority,
)


INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
ARCHIVE_ID = "22222222-2222-4222-8222-222222222222"


def _archive(*, archive_id: str = ARCHIVE_ID, created_at: str) -> bytes:
    return canonical_json_bytes(
        {
            "archive_id": archive_id,
            "contract": "EvaPortableArchive/v1",
            "created_at": created_at,
            "schema_version": 1,
            "source_installation_id": INSTALLATION_ID,
        }
    )


def _authority(
    archive_raw: bytes,
    *,
    generation: int,
    admit: bool,
) -> bytes:
    archive = __import__("json").loads(archive_raw)
    return canonical_json_bytes(
        {
            "authorized_archives": (
                [
                    {
                        "archive_id": archive["archive_id"],
                        "archive_json_sha256": hashlib.sha256(archive_raw).hexdigest(),
                        "created_at": archive["created_at"],
                    }
                ]
                if admit
                else []
            ),
            "contract": "EvaPortableRestoreAuthority/v1",
            "erasure_generation": generation,
            "issued_at": "2026-08-29T07:00:00Z",
            "latest_erasure_operation_sha256": (
                hashlib.sha256(b"synthetic-erasure-operation").hexdigest()
                if generation
                else None
            ),
            "schema_version": 1,
            "source_installation_id": INSTALLATION_ID,
        }
    )


def test_exact_archive_is_authorized_in_current_generation() -> None:
    archive = _archive(created_at="2026-08-29T06:00:00Z")

    receipt = authorize_portable_restore(
        archive_manifest_raw=archive,
        restore_authority_raw=_authority(archive, generation=0, admit=True),
    )

    assert receipt.archive_id == ARCHIVE_ID
    assert receipt.erasure_generation == 0
    assert receipt.latest_erasure_operation_sha256 is None


def test_erasure_generation_clears_old_archive_authority() -> None:
    old_archive = _archive(created_at="2026-08-29T06:00:00Z")

    with pytest.raises(
        PortableRestoreAuthorityError,
        match="not authorized after the latest erasure generation",
    ):
        authorize_portable_restore(
            archive_manifest_raw=old_archive,
            restore_authority_raw=_authority(old_archive, generation=1, admit=False),
        )


def test_new_post_erasure_archive_can_be_explicitly_admitted() -> None:
    new_archive = _archive(created_at="2026-08-29T07:01:00Z")

    receipt = authorize_portable_restore(
        archive_manifest_raw=new_archive,
        restore_authority_raw=_authority(new_archive, generation=1, admit=True),
    )

    assert receipt.erasure_generation == 1
    assert (
        receipt.latest_erasure_operation_sha256
        == hashlib.sha256(b"synthetic-erasure-operation").hexdigest()
    )


def test_changed_archive_bytes_do_not_match_admitted_digest() -> None:
    admitted = _archive(created_at="2026-08-29T07:01:00Z")
    changed = _archive(created_at="2026-08-29T07:01:01Z")

    with pytest.raises(PortableRestoreAuthorityError, match="not authorized"):
        authorize_portable_restore(
            archive_manifest_raw=changed,
            restore_authority_raw=_authority(admitted, generation=1, admit=True),
        )


def test_even_allowlisted_archive_must_be_newer_than_erasure_generation() -> None:
    old_archive = _archive(created_at="2026-08-29T06:00:00Z")

    with pytest.raises(PortableRestoreAuthorityError, match="predates"):
        authorize_portable_restore(
            archive_manifest_raw=old_archive,
            restore_authority_raw=_authority(old_archive, generation=1, admit=True),
        )


def test_wrong_installation_and_noncanonical_files_are_rejected() -> None:
    archive = _archive(created_at="2026-08-29T07:01:00Z")
    authority = __import__("json").loads(_authority(archive, generation=1, admit=True))
    authority["source_installation_id"] = "33333333-3333-4333-8333-333333333333"
    with pytest.raises(PortableRestoreAuthorityError, match="scope differs"):
        authorize_portable_restore(
            archive_manifest_raw=archive,
            restore_authority_raw=canonical_json_bytes(authority),
        )

    with pytest.raises(PortableRestoreAuthorityError, match="not canonical JSON"):
        authorize_portable_restore(
            archive_manifest_raw=archive + b"\n",
            restore_authority_raw=_authority(archive, generation=1, admit=True),
        )


def test_restore_authority_file_must_be_private_regular_and_unlinked(
    private_tmp_path: Path,
) -> None:
    archive = _archive(created_at="2026-08-29T07:01:00Z")
    raw = _authority(archive, generation=1, admit=True)
    authority = private_tmp_path / "restore-authority.json"
    authority.write_bytes(raw)
    authority.chmod(0o600)
    assert read_private_restore_authority(authority) == raw

    authority.chmod(0o640)
    with pytest.raises(PortableRestoreAuthorityError, match="unavailable"):
        read_private_restore_authority(authority)
    authority.chmod(0o600)

    linked = private_tmp_path / "linked-authority.json"
    linked.symlink_to(authority)
    with pytest.raises(PortableRestoreAuthorityError, match="unavailable"):
        read_private_restore_authority(linked)

    with pytest.raises(PortableRestoreAuthorityError, match="unavailable"):
        read_private_restore_authority(authority, effective_uid=os.geteuid() + 1)


def test_erasure_advance_clears_archives_and_replays_same_operation() -> None:
    archive = _archive(created_at="2026-08-29T07:01:00Z")
    operation = hashlib.sha256(b"erase-dashboard-exchange").hexdigest()
    updated, receipt = advance_restore_authority_for_erasure(
        _authority(archive, generation=1, admit=True),
        source_installation_id=INSTALLATION_ID,
        operation_sha256=operation,
        issued_at=datetime(2026, 8, 29, 8, 0, tzinfo=UTC),
    )
    payload = __import__("json").loads(updated)
    assert payload["authorized_archives"] == []
    assert payload["erasure_generation"] == 2
    assert payload["latest_erasure_operation_sha256"] == operation
    assert receipt.erasure_generation == 2
    assert not receipt.replayed

    replay_raw, replay = advance_restore_authority_for_erasure(
        updated,
        source_installation_id=INSTALLATION_ID,
        operation_sha256=operation,
        issued_at=datetime(2026, 8, 29, 8, 1, tzinfo=UTC),
    )
    assert replay_raw == updated
    assert replay.erasure_generation == 2
    assert replay.replayed


def test_private_store_initializes_advances_and_reads_back_atomically(
    private_tmp_path: Path,
) -> None:
    private_tmp_path.chmod(0o700)
    path = private_tmp_path / "restore-authority.json"
    first_operation = hashlib.sha256(b"first-erasure").hexdigest()
    store = PrivateRestoreAuthorityStore(
        path, source_installation_id=INSTALLATION_ID
    )

    first = store.advance_erasure_generation(
        operation_sha256=first_operation,
        erased_at=datetime(2026, 8, 29, 8, 0, tzinfo=UTC),
    )
    assert first.erasure_generation == 1
    assert not first.replayed
    assert path.stat().st_mode & 0o777 == 0o600
    persisted = read_private_restore_authority(path)
    assert hashlib.sha256(persisted).hexdigest() == first.authority_sha256

    replay = store.advance_erasure_generation(
        operation_sha256=first_operation,
        erased_at=datetime(2026, 8, 29, 8, 1, tzinfo=UTC),
    )
    assert replay.erasure_generation == 1
    assert replay.replayed

    second = store.advance_erasure_generation(
        operation_sha256=hashlib.sha256(b"second-erasure").hexdigest(),
        erased_at=datetime(2026, 8, 29, 8, 2, tzinfo=UTC),
    )
    assert second.erasure_generation == 2
    assert not second.replayed


def test_private_store_refuses_a_shared_or_linked_directory(
    private_tmp_path: Path,
) -> None:
    shared = private_tmp_path / "shared"
    shared.mkdir(mode=0o770)
    with pytest.raises(PortableRestoreAuthorityError, match="directory is unavailable"):
        PrivateRestoreAuthorityStore(
            shared / "restore-authority.json",
            source_installation_id=INSTALLATION_ID,
        )

    real = private_tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = private_tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(PortableRestoreAuthorityError, match="directory is unavailable"):
        PrivateRestoreAuthorityStore(
            linked / "restore-authority.json",
            source_installation_id=INSTALLATION_ID,
        )


def test_concurrent_erasure_advances_cannot_overwrite_one_generation(
    private_tmp_path: Path,
) -> None:
    private_tmp_path.chmod(0o700)
    path = private_tmp_path / "restore-authority.json"
    operations = (
        hashlib.sha256(b"parallel-erasure-a").hexdigest(),
        hashlib.sha256(b"parallel-erasure-b").hexdigest(),
    )

    def advance(operation: str) -> int:
        return PrivateRestoreAuthorityStore(
            path, source_installation_id=INSTALLATION_ID
        ).advance_erasure_generation(
            operation_sha256=operation,
            erased_at=datetime(2026, 8, 29, 8, 0, tzinfo=UTC),
        ).erasure_generation

    with ThreadPoolExecutor(max_workers=2) as executor:
        generations = tuple(executor.map(advance, operations))

    assert sorted(generations) == [1, 2]
    authority = __import__("json").loads(read_private_restore_authority(path))
    assert authority["erasure_generation"] == 2
    assert authority["latest_erasure_operation_sha256"] in operations


def test_runtime_v3_still_requires_exact_current_erasure_authority():
    import json
    manifest = json.loads(_archive(created_at="2026-09-29T12:00:00Z"))
    manifest.update(contract="EvaPortableArchive/v3", schema_version=3)
    raw = canonical_json_bytes(manifest)
    assert authorize_portable_restore(archive_manifest_raw=raw,
        restore_authority_raw=_authority(raw, generation=0, admit=True)).archive_id == ARCHIVE_ID
    with pytest.raises(PortableRestoreAuthorityError, match="not authorized"):
        authorize_portable_restore(archive_manifest_raw=raw,
            restore_authority_raw=_authority(raw, generation=1, admit=False))
