"""Crash-recoverable, target-local CAS materialization for a portable archive.

This is intentionally before and separate from PostgreSQL ObjectRef restore.
The journal is the durable authority for a temporary CAS file, which lets a
restart finish or prove the same object without blindly making another copy.
It never grants database selection authority and never writes an ObjectRef.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eva.adapters.objects.cas import CasError, FileSystemCAS
from eva.adapters.objects.envelope import ObjectAuthenticationError, XChaCha20ObjectCipher
from eva.application.objects import (
    CasInstallStatus,
    DurableCandidateBindingCommit,
    ObjectKeyResolver,
    PreparedCandidateBinding,
)
from eva.application.portable_archive import canonical_json_bytes
from eva.operations.portable_object_verifier import (
    PortableObjectVerificationError,
    VerifiedPortableObjectCandidate,
    verify_portable_object_candidates,
)


_JOURNAL_NAME = ".eva-portable-object-materialization-v1.jsonl"
_JOURNAL_CONTRACT = "EvaPortableObjectMaterialization/v1"
_JOURNAL_FIELDS = frozenset(
    {
        "binding",
        "candidate",
        "commit",
        "contract",
        "record_row_sha256",
    }
)


class PortableObjectMaterializationError(RuntimeError):
    """A target CAS cannot safely receive the verified portable object."""


@dataclass(frozen=True, slots=True)
class MaterializedPortableObject:
    """Non-secret result of one independently authenticated target CAS write."""

    object_id: str
    ciphertext_sha256: str
    record_row_sha256: str
    status: CasInstallStatus
    target_object_root: Path
    relative_path: Path


@dataclass(frozen=True, slots=True)
class _JournalEntry:
    candidate: VerifiedPortableObjectCandidate
    binding: PreparedCandidateBinding
    commit: DurableCandidateBindingCommit


def _private_directory(path: Path) -> None:
    if not isinstance(path, Path):
        raise TypeError("target object root must be a pathlib.Path")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise PortableObjectMaterializationError(
            "target object root must be a private owner directory"
        )


def _journal_path(target_root: Path) -> Path:
    return target_root / _JOURNAL_NAME


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def _journal_entry_value(
    candidate: VerifiedPortableObjectCandidate,
    binding: PreparedCandidateBinding,
    commit: DurableCandidateBindingCommit,
) -> dict[str, Any]:
    return {
        "binding": {
            "ciphertext_sha256": binding.ciphertext_sha256,
            "installation_id": binding.binding.installation_id,
            "key_ref": binding.binding.key_ref,
            "mime_type": binding.binding.mime_type,
            "object_id": binding.binding.object_id,
            "plaintext_byte_length": binding.plaintext_byte_length,
            "plaintext_sha256": binding.plaintext_sha256,
            "stored_envelope_byte_length": binding.stored_envelope_byte_length,
            "temp_name_sha256": binding.temp_name_sha256,
            "tenant_id": binding.binding.tenant_id,
        },
        "candidate": {
            "ciphertext_sha256": candidate.candidate.ciphertext_sha256,
            "plaintext_sha256": candidate.candidate.plaintext_sha256,
        },
        "commit": {
            "committed_at": commit.committed_at,
            "commit_receipt_sha256": commit.commit_receipt_sha256,
            "prepared_binding_sha256": commit.prepared_binding_sha256,
        },
        "contract": _JOURNAL_CONTRACT,
        "record_row_sha256": candidate.record_row_sha256,
    }


def _entry_commit_hash(
    candidate: VerifiedPortableObjectCandidate,
    binding: PreparedCandidateBinding,
) -> str:
    return _canonical_sha256(
        {
            "candidate_ciphertext_sha256": candidate.candidate.ciphertext_sha256,
            "prepared_binding_sha256": binding.canonical_sha256,
            "record_row_sha256": candidate.record_row_sha256,
        }
    )


def _append_journal(
    path: Path,
    candidate: VerifiedPortableObjectCandidate,
    binding: PreparedCandidateBinding,
) -> DurableCandidateBindingCommit:
    commit = DurableCandidateBindingCommit(
        prepared_binding_sha256=binding.canonical_sha256,
        committed_at=datetime.now(timezone.utc).replace(microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        commit_receipt_sha256=_entry_commit_hash(candidate, binding),
    )
    encoded = canonical_json_bytes(_journal_entry_value(candidate, binding, commit)) + b"\n"
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise PortableObjectMaterializationError("target CAS journal cannot be opened") from error
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or named.st_dev != opened.st_dev
            or named.st_ino != opened.st_ino
        ):
            raise PortableObjectMaterializationError("target CAS journal is not private")
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)
    return commit


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_journal(
    path: Path,
    candidates: Mapping[str, VerifiedPortableObjectCandidate],
) -> dict[str, _JournalEntry]:
    if not path.exists():
        return {}
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise PortableObjectMaterializationError("target CAS journal is not private")
    try:
        raw_lines = path.read_bytes().splitlines()
    except OSError as error:
        raise PortableObjectMaterializationError("target CAS journal cannot be read") from error
    result: dict[str, _JournalEntry] = {}
    for raw in raw_lines:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PortableObjectMaterializationError("target CAS journal is malformed") from error
        if not isinstance(value, Mapping) or set(value) != _JOURNAL_FIELDS:
            raise PortableObjectMaterializationError("target CAS journal is malformed")
        if value.get("contract") != _JOURNAL_CONTRACT:
            raise PortableObjectMaterializationError("target CAS journal contract differs")
        candidate_value = value.get("candidate")
        binding_value = value.get("binding")
        commit_value = value.get("commit")
        if not all(isinstance(item, Mapping) for item in (candidate_value, binding_value, commit_value)):
            raise PortableObjectMaterializationError("target CAS journal is malformed")
        digest = candidate_value.get("ciphertext_sha256")
        candidate = candidates.get(digest) if isinstance(digest, str) else None
        if candidate is None or value.get("record_row_sha256") != candidate.record_row_sha256:
            raise PortableObjectMaterializationError("target CAS journal names another archive")
        try:
            binding = PreparedCandidateBinding(
                binding=candidate.candidate.binding,
                ciphertext_sha256=binding_value["ciphertext_sha256"],
                plaintext_sha256=binding_value["plaintext_sha256"],
                plaintext_byte_length=binding_value["plaintext_byte_length"],
                stored_envelope_byte_length=binding_value["stored_envelope_byte_length"],
                temp_name_sha256=binding_value["temp_name_sha256"],
            )
            commit = DurableCandidateBindingCommit(
                prepared_binding_sha256=commit_value["prepared_binding_sha256"],
                committed_at=commit_value["committed_at"],
                commit_receipt_sha256=commit_value["commit_receipt_sha256"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PortableObjectMaterializationError("target CAS journal is malformed") from error
        expected = _journal_entry_value(candidate, binding, commit)
        if dict(value) != expected or commit.commit_receipt_sha256 != _entry_commit_hash(candidate, binding):
            raise PortableObjectMaterializationError("target CAS journal differs from object")
        if digest in result:
            raise PortableObjectMaterializationError("target CAS journal duplicates an object")
        result[digest] = _JournalEntry(candidate=candidate, binding=binding, commit=commit)
    return result


def _refuse_unknown_prepared_temps(target_root: Path, journal: Mapping[str, _JournalEntry]) -> None:
    known_names = {entry.binding.temp_name_sha256 for entry in journal.values()}
    for path in target_root.rglob(".eva-candidate-*"):
        digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()
        if digest not in known_names:
            raise PortableObjectMaterializationError(
                "target CAS contains an unjournaled prepared object"
            )


def _authenticate_target_readback(
    item: VerifiedPortableObjectCandidate,
    envelope: bytes,
    key_resolver: ObjectKeyResolver,
) -> None:
    try:
        plaintext = XChaCha20ObjectCipher().decrypt(
            envelope, item.candidate.binding, key_resolver=key_resolver
        )
    except ObjectAuthenticationError as error:
        raise PortableObjectMaterializationError("target CAS object cannot be authenticated") from error
    if plaintext != item.plaintext:
        raise PortableObjectMaterializationError("target CAS plaintext differs")


def materialize_verified_portable_objects(
    *,
    source_object_root: Path,
    target_object_root: Path,
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    key_resolver: ObjectKeyResolver,
) -> tuple[MaterializedPortableObject, ...]:
    """Copy only authenticated source envelopes into a private target CAS.

    The target-local journal is fsynced before the no-replace CAS link. A retry
    therefore recovers the same prepared temp or independently re-opens its
    final path. This function deliberately has no database connection: a later
    restore writer must accept only this completed materialization proof before
    inserting ObjectRefs.
    """

    try:
        candidates = verify_portable_object_candidates(
            object_root=source_object_root,
            records_by_table=records_by_table,
            key_resolver=key_resolver,
        )
    except PortableObjectVerificationError as error:
        raise PortableObjectMaterializationError("source portable object is invalid") from error
    candidate_by_digest = {item.candidate.ciphertext_sha256: item for item in candidates}
    if len(candidate_by_digest) != len(candidates):
        raise PortableObjectMaterializationError("portable archive repeats an object")
    _private_directory(target_object_root)
    journal_path = _journal_path(target_object_root)
    journal = _parse_journal(journal_path, candidate_by_digest)
    _refuse_unknown_prepared_temps(target_object_root, journal)
    store = FileSystemCAS(target_object_root)
    result: list[MaterializedPortableObject] = []
    for item in candidates:
        digest = item.candidate.ciphertext_sha256
        entry = journal.get(digest)
        try:
            if entry is None:
                prepared = store.prepare(item.candidate)
                try:
                    commit = _append_journal(journal_path, item, prepared.binding)
                except Exception:
                    # A failed or ambiguous journal fsync must retain the temp
                    # for explicit operator reconciliation rather than guess.
                    raise
            else:
                prepared = store.recover_prepared(
                    item.candidate, entry.binding, entry.commit
                )
                commit = entry.commit
            readback = store.install_prepared_and_readback(prepared, commit)
        except (CasError, OSError, ValueError) as error:
            raise PortableObjectMaterializationError("target CAS materialization failed") from error
        _authenticate_target_readback(item, readback.envelope, key_resolver)
        result.append(
            MaterializedPortableObject(
                object_id=item.candidate.binding.object_id,
                ciphertext_sha256=digest,
                record_row_sha256=item.record_row_sha256,
                status=readback.status,
                target_object_root=target_object_root,
                relative_path=readback.path,
            )
        )
    return tuple(result)


__all__ = [
    "MaterializedPortableObject",
    "PortableObjectMaterializationError",
    "materialize_verified_portable_objects",
]
