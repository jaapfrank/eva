from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from eva.application.portable_archive import canonical_json_bytes
from eva.operations.portable_restore_authority import (
    PortableRestoreAuthorityError,
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
