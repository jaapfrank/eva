"""Verify encrypted portable-object bytes before a future restore may write rows.

This boundary deliberately does not copy a byte or write a database row.  It
only proves that every active ObjectRef has its exact CAS envelope and that a
separately provisioned target keyring can authenticate and decrypt it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.cas import CasError, FileSystemCAS
from eva.adapters.objects.envelope import ObjectAuthenticationError, XChaCha20ObjectCipher
from eva.application.objects import EncryptedObjectCandidate, ObjectBinding, ObjectKeyResolver
from eva.application.portable_archive import PortableArchiveError, canonical_json_bytes


_OBJECT_REF_TABLE = "eva_evidence.object_refs"
_OBJECT_REF_CONTRACT = "EvaCanonicalRecord/eva_evidence.object_refs/v1"
_RECORD_FIELDS = frozenset(
    {"contract", "primary_key", "row", "row_sha256", "schema_version", "source_table"}
)
_REQUIRED_ROW_FIELDS = frozenset(
    {
        "ciphertext_sha256",
        "encryption_algorithm",
        "envelope_byte_length",
        "installation_id",
        "mime_type",
        "object_id",
        "physical_cas_key",
        "plaintext_byte_length",
        "plaintext_sha256",
        "state",
        "tenant_id",
        "wrapped_key_ref",
    }
)


class PortableObjectVerificationError(RuntimeError):
    """A portable encrypted object is absent, mismatched or cannot be opened."""


@dataclass(frozen=True, slots=True)
class VerifiedPortableObject:
    """Non-secret proof that one archived object is readable on the target."""

    object_id: str
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_byte_length: int
    envelope_byte_length: int


@dataclass(frozen=True, slots=True)
class VerifiedPortableObjectCandidate:
    """Authenticated archive object, retained only for the restore hand-off.

    The envelope and plaintext are deliberately never rendered in a receipt or
    log. They remain in process memory only long enough for a target CAS write
    to independently re-open and authenticate the same bytes.
    """

    record_row_sha256: str
    candidate: EncryptedObjectCandidate
    plaintext: bytes


def _decode_archived_object_ref(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Check the self-authenticating archive record, independent of live DB schema.

    The complete archive reader has already verified table schemas before this
    object-only preflight is reached.  Requiring the historic *current* export
    column order here would make a valid sealed archive unrecoverable after an
    unrelated later schema extension.  We instead authenticate the immutable
    record envelope and the object fields this checker consumes.
    """

    if not isinstance(record, Mapping) or set(record) != _RECORD_FIELDS:
        raise PortableObjectVerificationError("portable ObjectRef record is invalid")
    if (
        record.get("contract") != _OBJECT_REF_CONTRACT
        or record.get("source_table") != _OBJECT_REF_TABLE
        or record.get("schema_version") != 1
    ):
        raise PortableObjectVerificationError("portable ObjectRef record is invalid")
    row = record.get("row")
    if not isinstance(row, Mapping) or not _REQUIRED_ROW_FIELDS.issubset(row):
        raise PortableObjectVerificationError("portable ObjectRef row is invalid")
    row_sha256 = record.get("row_sha256")
    if not isinstance(row_sha256, str) or len(row_sha256) != 64:
        raise PortableObjectVerificationError("portable ObjectRef row hash is invalid")
    try:
        actual_row_sha256 = hashlib.sha256(canonical_json_bytes(row)).hexdigest()
    except PortableArchiveError as error:
        raise PortableObjectVerificationError("portable ObjectRef row is invalid") from error
    if actual_row_sha256 != row_sha256:
        raise PortableObjectVerificationError("portable ObjectRef row hash differs")
    primary_key = record.get("primary_key")
    if not isinstance(primary_key, Mapping) or any(
        primary_key.get(field) != row.get(field)
        for field in ("tenant_id", "installation_id", "object_id")
    ):
        raise PortableObjectVerificationError("portable ObjectRef key differs")
    return row


def verify_portable_object_candidates(
    *,
    object_root: Path,
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    key_resolver: ObjectKeyResolver,
) -> tuple[VerifiedPortableObjectCandidate, ...]:
    """Authenticate every active ObjectRef against archive CAS bytes.

    ``object_root`` is the ``objects`` directory from an already validated
    portable archive.  A caller still needs a separate, atomic CAS/database
    materializer after this preflight; successful verification is never a
    restore claim by itself.
    """

    records = records_by_table.get(_OBJECT_REF_TABLE)
    if records is None or isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise PortableObjectVerificationError("portable ObjectRef record set is absent")
    if not isinstance(object_root, Path):
        raise TypeError("portable object root must be a pathlib.Path")
    store = FileSystemCAS(object_root)
    cipher = XChaCha20ObjectCipher()
    result: list[VerifiedPortableObjectCandidate] = []
    seen: set[str] = set()
    for record in records:
        row = _decode_archived_object_ref(record)
        if row["state"] == "crypto_shredded":
            continue
        if row["state"] != "active":
            raise PortableObjectVerificationError("portable ObjectRef state is invalid")
        digest = row["ciphertext_sha256"]
        if not isinstance(digest, str) or digest in seen:
            raise PortableObjectVerificationError("portable ObjectRef ciphertext is invalid")
        seen.add(digest)
        try:
            binding = ObjectBinding(
                tenant_id=row["tenant_id"],
                installation_id=row["installation_id"],
                object_id=row["object_id"],
                key_ref=row["wrapped_key_ref"],
                mime_type=row["mime_type"],
            )
            envelope = store.read(digest)
            metadata = cipher.inspect(envelope)
            plaintext = cipher.decrypt(envelope, binding, key_resolver=key_resolver)
        except (CasError, ObjectAuthenticationError, TypeError, ValueError) as error:
            raise PortableObjectVerificationError(
                "portable encrypted object cannot be authenticated on the target"
            ) from error
        if (
            row["encryption_algorithm"] != "XChaCha20-Poly1305"
            or row["physical_cas_key"] != str(FileSystemCAS.relative_path(digest))
            or metadata.binding != binding
            or metadata.ciphertext_sha256 != digest
            or metadata.plaintext_sha256 != row["plaintext_sha256"]
            or metadata.plaintext_byte_length != row["plaintext_byte_length"]
            or metadata.stored_envelope_byte_length != row["envelope_byte_length"]
            or len(plaintext) != row["plaintext_byte_length"]
        ):
            raise PortableObjectVerificationError("portable object metadata differs")
        row_sha256 = record["row_sha256"]
        if not isinstance(row_sha256, str):  # guaranteed by _decode, kept local.
            raise PortableObjectVerificationError("portable ObjectRef row hash is invalid")
        result.append(
            VerifiedPortableObjectCandidate(
                record_row_sha256=row_sha256,
                candidate=EncryptedObjectCandidate(
                    binding=binding,
                    envelope=envelope,
                    canonical_aad=metadata.canonical_aad,
                    ciphertext_sha256=digest,
                    plaintext_sha256=metadata.plaintext_sha256,
                    plaintext_byte_length=metadata.plaintext_byte_length,
                    stored_envelope_byte_length=metadata.stored_envelope_byte_length,
                ),
                plaintext=plaintext,
            )
        )
    return tuple(result)


def verify_portable_object_bytes(
    *,
    object_root: Path,
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    key_resolver: ObjectKeyResolver,
) -> tuple[VerifiedPortableObject, ...]:
    """Return only non-secret object summaries after the same authentication."""

    verified = verify_portable_object_candidates(
        object_root=object_root,
        records_by_table=records_by_table,
        key_resolver=key_resolver,
    )
    return tuple(
        VerifiedPortableObject(
            object_id=item.candidate.binding.object_id,
            ciphertext_sha256=item.candidate.ciphertext_sha256,
            plaintext_sha256=item.candidate.plaintext_sha256,
            plaintext_byte_length=item.candidate.plaintext_byte_length,
            envelope_byte_length=item.candidate.stored_envelope_byte_length,
        )
        for item in verified
    )


__all__ = [
    "PortableObjectVerificationError",
    "VerifiedPortableObject",
    "VerifiedPortableObjectCandidate",
    "verify_portable_object_candidates",
    "verify_portable_object_bytes",
]
