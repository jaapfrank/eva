"""Offline, owner-operated IdentityPack issuance and reversible file cutover.

The runtime only verifies public signatures.  This module deliberately has no
runtime import: an operator runs it in a separate process and supplies a
private Ed25519 key through a private file or HSM integration.  It never reads
an environment variable and it never touches the default fixture identity.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eva.application.identity_drafts import (
    identity_presentation_diff,
    validate_identity_draft_base,
)
from eva.application.identity_verifier import (
    IdentityPackTrustBinding,
    verify_identity_pack_bytes,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.identity import IdentityPack, IdentityPackDraft


class IdentityPackLifecycleError(RuntimeError):
    """A candidate was unsafe, stale, invalid, or could not be cut over."""


@dataclass(frozen=True, slots=True)
class IdentityPackCutover:
    """The hashes an operator must retain to explain a reversible identity swap."""

    previous_sha256: str
    current_sha256: str
    rollback_sha256: str


def issue_identity_pack(
    draft: IdentityPackDraft,
    base: IdentityPack,
    *,
    private_key_bytes: bytes,
    issued_at: str,
) -> IdentityPack:
    """Apply one closed presentation draft and sign the next current version.

    The caller owns the private key.  The key is accepted only as a 32-byte
    Ed25519 seed, is never returned, and cannot change policy, permissions,
    memory, values, or any other non-presentation field.
    """

    validate_identity_draft_base(draft, base)
    if not identity_presentation_diff(draft, base):
        raise IdentityPackLifecycleError("identity draft has no effective change")
    if not isinstance(private_key_bytes, bytes) or len(private_key_bytes) != 32:
        raise IdentityPackLifecycleError("identity signing key must be 32 bytes")
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    except ValueError as exc:
        raise IdentityPackLifecycleError("identity signing key is invalid") from exc

    payload = base.model_dump(mode="json")
    payload.update(draft.presentation.model_dump(mode="json", exclude_none=True))
    payload.update(
        {
            "pack_version": base.pack_version + 1,
            "state": "current",
            "issued_at": issued_at,
            "signature": "A" * 86,
            "source_sha256": "0" * 64,
        }
    )
    source_payload = dict(payload)
    source_payload.pop("source_sha256")
    source_payload.pop("signature")
    payload["source_sha256"] = hashlib.sha256(
        canonical_json_bytes(source_payload)
    ).hexdigest()
    signing_payload = dict(payload)
    signing_payload.pop("signature")
    payload["signature"] = base64.urlsafe_b64encode(
        private_key.sign(canonical_json_bytes(signing_payload))
    ).rstrip(b"=").decode("ascii")
    try:
        return IdentityPack.model_validate(payload)
    except ValueError as exc:
        raise IdentityPackLifecycleError("issued identity pack is invalid") from exc


def validate_identity_successor(
    candidate: IdentityPack,
    base: IdentityPack,
    *,
    trust: IdentityPackTrustBinding,
) -> None:
    """Verify a signed candidate may replace exactly this verified current pack."""

    if candidate.identity_id != base.identity_id:
        raise IdentityPackLifecycleError("identity successor changes identity ID")
    if candidate.issuer != base.issuer:
        raise IdentityPackLifecycleError("identity successor changes issuer")
    if candidate.pack_version != base.pack_version + 1:
        raise IdentityPackLifecycleError("identity successor version is not next")
    if candidate.state != "current":
        raise IdentityPackLifecycleError("identity successor is not current")
    try:
        verified = verify_identity_pack_bytes(canonical_json_bytes(candidate), trust=trust)
    except ValueError as exc:
        raise IdentityPackLifecycleError("identity successor signature is invalid") from exc
    if canonical_sha256(verified) != canonical_sha256(candidate):
        raise IdentityPackLifecycleError("identity successor bytes differ after verification")


def _read_regular_bounded(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IdentityPackLifecycleError("identity pack file is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise IdentityPackLifecycleError("identity pack file must be a regular non-symlink")
    if metadata.st_size > 65_536:
        raise IdentityPackLifecycleError("identity pack file is too large")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            return handle.read(65_537)
    except OSError as exc:
        raise IdentityPackLifecycleError("identity pack file is unavailable") from exc


def _load_verified(path: Path, trust: IdentityPackTrustBinding) -> tuple[IdentityPack, bytes]:
    raw = _read_regular_bounded(path)
    if len(raw) > 65_536:
        raise IdentityPackLifecycleError("identity pack file is too large")
    try:
        return verify_identity_pack_bytes(raw, trust=trust), raw
    except ValueError as exc:
        raise IdentityPackLifecycleError("identity pack file is invalid") from exc


def _atomic_write(path: Path, data: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise IdentityPackLifecycleError("identity target directory is invalid")
    if path.exists() and path.is_symlink():
        raise IdentityPackLifecycleError("identity target file must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".identity-pack-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise IdentityPackLifecycleError("identity pack write failed") from exc


def activate_identity_successor(
    *,
    current_path: Path,
    candidate_path: Path,
    rollback_path: Path,
    trust: IdentityPackTrustBinding,
) -> IdentityPackCutover:
    """Save the exact old pack first, then atomically replace the current file.

    This only changes operator-provided candidate files.  It is not called by
    the Eva runtime and must never be pointed at Eva1's files.
    """

    if current_path == rollback_path or candidate_path in {current_path, rollback_path}:
        raise IdentityPackLifecycleError("identity lifecycle paths must be distinct")
    base, base_raw = _load_verified(current_path, trust)
    candidate, candidate_raw = _load_verified(candidate_path, trust)
    validate_identity_successor(candidate, base, trust=trust)
    _atomic_write(rollback_path, base_raw)
    _atomic_write(current_path, candidate_raw)
    current, current_raw = _load_verified(current_path, trust)
    rollback, rollback_raw = _load_verified(rollback_path, trust)
    if canonical_sha256(current) != canonical_sha256(candidate):
        raise IdentityPackLifecycleError("identity cutover verification failed")
    if canonical_sha256(rollback) != canonical_sha256(base):
        raise IdentityPackLifecycleError("identity rollback copy verification failed")
    return IdentityPackCutover(
        previous_sha256=hashlib.sha256(base_raw).hexdigest(),
        current_sha256=hashlib.sha256(current_raw).hexdigest(),
        rollback_sha256=hashlib.sha256(rollback_raw).hexdigest(),
    )


def rollback_identity_successor(
    *,
    current_path: Path,
    rollback_path: Path,
    trust: IdentityPackTrustBinding,
) -> IdentityPackCutover:
    """Restore the previously verified identity; no unsigned fallback exists."""

    current, current_raw = _load_verified(current_path, trust)
    rollback, rollback_raw = _load_verified(rollback_path, trust)
    if (
        rollback.identity_id != current.identity_id
        or rollback.issuer != current.issuer
        or rollback.pack_version + 1 != current.pack_version
    ):
        raise IdentityPackLifecycleError("identity rollback is not the direct predecessor")
    _atomic_write(current_path, rollback_raw)
    restored, restored_raw = _load_verified(current_path, trust)
    if canonical_sha256(restored) != canonical_sha256(rollback):
        raise IdentityPackLifecycleError("identity rollback verification failed")
    return IdentityPackCutover(
        previous_sha256=hashlib.sha256(current_raw).hexdigest(),
        current_sha256=hashlib.sha256(restored_raw).hexdigest(),
        rollback_sha256=hashlib.sha256(rollback_raw).hexdigest(),
    )


__all__ = [
    "IdentityPackCutover",
    "IdentityPackLifecycleError",
    "activate_identity_successor",
    "issue_identity_pack",
    "rollback_identity_successor",
    "validate_identity_successor",
]
