from __future__ import annotations

import hashlib
import json

from eva.application.portable_archive import canonical_json_bytes


ARCHIVE_MANIFEST_RAW = canonical_json_bytes(
    {
        "archive_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "contract": "EvaPortableArchive/v1",
        "created_at": "2026-08-29T07:30:00Z",
        "schema_version": 1,
        "source_installation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    }
)


def restore_authority_for_archive(
    archive_manifest_raw: bytes, *, admit: bool = True, generation: int = 1
) -> bytes:
    archive = json.loads(archive_manifest_raw)
    return canonical_json_bytes(
        {
            "authorized_archives": (
                [
                    {
                        "archive_id": archive["archive_id"],
                        "archive_json_sha256": hashlib.sha256(
                            archive_manifest_raw
                        ).hexdigest(),
                        "created_at": archive["created_at"],
                    }
                ]
                if admit
                else []
            ),
            "contract": "EvaPortableRestoreAuthority/v1",
            "erasure_generation": generation,
            "issued_at": "2026-08-29T07:29:00Z",
            "latest_erasure_operation_sha256": (
                hashlib.sha256(b"synthetic-current-erasure-generation").hexdigest()
                if generation
                else None
            ),
            "schema_version": 1,
            "source_installation_id": archive["source_installation_id"],
        }
    )


RESTORE_AUTHORITY_RAW = restore_authority_for_archive(ARCHIVE_MANIFEST_RAW)


RESTORE_AUTHORITY_ARGUMENTS = {
    "archive_manifest_raw": ARCHIVE_MANIFEST_RAW,
    "restore_authority_raw": RESTORE_AUTHORITY_RAW,
}
