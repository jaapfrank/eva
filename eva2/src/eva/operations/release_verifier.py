"""Offline Ed25519 verification for an Eva release manifest.

The verifier produces the only receipt shape accepted by the A/B staging
boundary.  It downloads nothing, extracts nothing and starts no process.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.ab_slots import VerifiedReleaseSlot


_PREFIX_FIELD = r"Eva2ReleaseSignature/v1\u0000"
_PREFIX_BYTES = b"Eva2ReleaseSignature/v1\x00"
_RELEASE_VERIFICATION_SEAL = object()
_MANIFEST_KEYS = {
    "compatibleSchemaRevisions",
    "contract",
    "imageDigests",
    "licenseInventorySha256",
    "migrationRevision",
    "minimumSupportedRelease",
    "platformLockSha256",
    "provenanceSha256",
    "releaseId",
    "releaseVersion",
    "revocationSequence",
    "sbomBindings",
    "schemaSetSha256",
    "signatures",
    "sourceCommit",
}
_ENVELOPE_KEYS = {
    "algorithm",
    "contract",
    "domainSeparationPrefix",
    "expiresAt",
    "keyId",
    "provenanceSha256",
    "scope",
    "signatureBase64",
    "signedAt",
    "subjectId",
    "subjectSha256",
    "trustRootVersion",
}
_ROOT_KEYS = {
    "contract",
    "keys",
    "previousRootSignatures",
    "rootVersion",
    "threshold",
    "validFrom",
}
_KEY_KEYS = {
    "algorithm",
    "keyId",
    "publicKeyBase64",
    "roles",
    "validFrom",
    "validUntil",
}


class ReleaseVerificationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReleaseVerification:
    release_id: str
    release_version: str
    manifest_sha256: str
    unsigned_manifest_sha256: str
    image_set_sha256: str
    verification_receipt_sha256: str
    signer_key_ids: tuple[str, ...]
    revocation_sequence: int
    migration_revision: str
    compatible_schema_revisions: tuple[str, ...]
    provenance_sha256: str
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _RELEASE_VERIFICATION_SEAL:
            raise ValueError("release verification must come from the trust verifier")

    def require_verified(self) -> None:
        """Fail closed when an object did not originate at the trust verifier."""

        if self._seal is not _RELEASE_VERIFICATION_SEAL:
            raise ValueError("release verification authority is invalid")

    def for_slot(self) -> VerifiedReleaseSlot:
        return VerifiedReleaseSlot(
            self.manifest_sha256,
            self.image_set_sha256,
            self.verification_receipt_sha256,
            self.compatible_schema_revisions,
        )


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReleaseVerificationError(
            "release_invalid", f"{label} is not canonical UTC"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReleaseVerificationError(
            "release_invalid", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise ReleaseVerificationError("release_invalid", f"{label} lacks timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_hash(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReleaseVerificationError("release_invalid", f"{label} is not SHA-256")
    return value


def unsigned_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "signatures"}


def signature_message(envelope: dict[str, Any]) -> bytes:
    unsigned = {
        key: value for key, value in envelope.items() if key != "signatureBase64"
    }
    return _PREFIX_BYTES + canonical_json_bytes(unsigned)


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if (
        set(manifest) != _MANIFEST_KEYS
        or manifest.get("contract") != "Eva2ReleaseManifest/v1"
    ):
        raise ReleaseVerificationError(
            "release_invalid", "release manifest shape is closed"
        )
    for field in (
        "releaseId",
        "releaseVersion",
        "minimumSupportedRelease",
        "migrationRevision",
    ):
        if not isinstance(manifest[field], str) or not manifest[field]:
            raise ReleaseVerificationError("release_invalid", f"{field} is required")
    if (
        not isinstance(manifest["sourceCommit"], str)
        or len(manifest["sourceCommit"]) != 40
    ):
        raise ReleaseVerificationError("release_invalid", "source commit is invalid")
    for field in (
        "platformLockSha256",
        "licenseInventorySha256",
        "provenanceSha256",
        "schemaSetSha256",
    ):
        _require_hash(manifest[field], field)
    images = manifest["imageDigests"]
    if not isinstance(images, dict) or not images:
        raise ReleaseVerificationError("release_invalid", "release has no image set")
    for component, digest in images.items():
        if (
            not isinstance(component, str)
            or not component
            or not isinstance(digest, str)
        ):
            raise ReleaseVerificationError(
                "release_invalid", "image binding is invalid"
            )
        if not digest.startswith("sha256:"):
            raise ReleaseVerificationError(
                "release_invalid", "image is not digest pinned"
            )
        _require_hash(digest.removeprefix("sha256:"), "image digest")
    sboms = manifest["sbomBindings"]
    if not isinstance(sboms, list) or not sboms:
        raise ReleaseVerificationError("release_invalid", "release has no SBOM binding")
    for value in sboms:
        _require_hash(value, "SBOM binding")
    compatible = manifest["compatibleSchemaRevisions"]
    if (
        not isinstance(compatible, list)
        or not compatible
        or any(not isinstance(value, str) or not value for value in compatible)
    ):
        raise ReleaseVerificationError(
            "release_invalid", "schema compatibility is absent"
        )
    if (
        len(set(compatible)) != len(compatible)
        or manifest["migrationRevision"] not in compatible
    ):
        raise ReleaseVerificationError(
            "release_invalid", "schema compatibility is incoherent"
        )
    if (
        not isinstance(manifest["revocationSequence"], int)
        or isinstance(manifest["revocationSequence"], bool)
        or manifest["revocationSequence"] < 1
    ):
        raise ReleaseVerificationError(
            "release_invalid", "revocation sequence is invalid"
        )
    if not isinstance(manifest["signatures"], list) or not manifest["signatures"]:
        raise ReleaseVerificationError("release_unsigned", "release has no signature")


def verify_release_manifest(
    manifest_bytes: bytes,
    trust_root_bytes: bytes,
    *,
    verified_at: str,
    revoked_subject_ids: tuple[str, ...] = (),
) -> ReleaseVerification:
    try:
        manifest = require_canonical_bytes(manifest_bytes)
        root = require_canonical_bytes(trust_root_bytes)
    except ValueError as exc:
        raise ReleaseVerificationError("release_invalid", str(exc)) from exc
    _validate_manifest(manifest)
    if set(root) != _ROOT_KEYS or root.get("contract") != "Eva2TrustRoot/v1":
        raise ReleaseVerificationError(
            "trust_root_invalid", "trust root shape is closed"
        )
    now = _timestamp(verified_at, "verification time")
    if _timestamp(root["validFrom"], "trust-root validFrom") > now:
        raise ReleaseVerificationError("trust_root_invalid", "trust root is not active")
    root_version = root["rootVersion"]
    threshold = root["threshold"]
    if not isinstance(root_version, int) or root_version < 1:
        raise ReleaseVerificationError("trust_root_invalid", "root version is invalid")
    if not isinstance(threshold, int) or threshold < 1:
        raise ReleaseVerificationError(
            "trust_root_invalid", "root threshold is invalid"
        )
    keys = root["keys"]
    if not isinstance(keys, list) or len(keys) < threshold:
        raise ReleaseVerificationError("trust_root_invalid", "trust root lacks keys")
    key_by_id: dict[str, dict[str, Any]] = {}
    for key in keys:
        if not isinstance(key, dict) or set(key) != _KEY_KEYS:
            raise ReleaseVerificationError(
                "trust_root_invalid", "trust key shape is closed"
            )
        key_id = key["keyId"]
        if not isinstance(key_id, str) or not key_id or key_id in key_by_id:
            raise ReleaseVerificationError(
                "trust_root_invalid", "trust key ID is invalid"
            )
        key_by_id[key_id] = key
    release_id = manifest["releaseId"]
    if release_id in revoked_subject_ids:
        raise ReleaseVerificationError("release_revoked", "release is revoked")
    unsigned_bytes = canonical_json_bytes(unsigned_manifest(manifest))
    subject_sha256 = _sha256(unsigned_bytes)
    valid_signers: set[str] = set()
    for raw_envelope in manifest["signatures"]:
        if not isinstance(raw_envelope, dict) or set(raw_envelope) != _ENVELOPE_KEYS:
            raise ReleaseVerificationError(
                "release_invalid", "signature envelope shape is closed"
            )
        envelope = raw_envelope
        if (
            envelope["contract"] != "Eva2SignatureEnvelope/v1"
            or envelope["scope"] != "release"
            or envelope["subjectId"] != release_id
            or envelope["subjectSha256"] != subject_sha256
            or envelope["algorithm"] != "Ed25519"
            or envelope["domainSeparationPrefix"] != _PREFIX_FIELD
            or envelope["trustRootVersion"] != root_version
            or envelope["provenanceSha256"] != manifest["provenanceSha256"]
        ):
            raise ReleaseVerificationError(
                "release_invalid", "signature authority differs"
            )
        signed_at = _timestamp(envelope["signedAt"], "signature signedAt")
        expires_at = _timestamp(envelope["expiresAt"], "signature expiresAt")
        if signed_at > now or expires_at < now:
            raise ReleaseVerificationError(
                "release_expired", "signature is outside validity"
            )
        key_id = envelope["keyId"]
        key = key_by_id.get(key_id)
        if (
            key is None
            or key["algorithm"] != "Ed25519"
            or "release_signer" not in key["roles"]
        ):
            continue
        if not (
            _timestamp(key["validFrom"], "key validFrom")
            <= now
            <= _timestamp(key["validUntil"], "key validUntil")
        ):
            continue
        try:
            public_bytes = base64.b64decode(key["publicKeyBase64"], validate=True)
            signature = base64.b64decode(envelope["signatureBase64"], validate=True)
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                signature, signature_message(envelope)
            )
        except (ValueError, InvalidSignature) as exc:
            raise ReleaseVerificationError(
                "signature_invalid", "release signature is invalid"
            ) from exc
        valid_signers.add(key_id)
    if len(valid_signers) < threshold:
        raise ReleaseVerificationError(
            "signature_threshold", "release signature threshold is unmet"
        )
    manifest_sha256 = _sha256(manifest_bytes)
    image_set_sha256 = _sha256(canonical_json_bytes(manifest["imageDigests"]))
    receipt_payload = {
        "contract": "EvaReleaseVerificationReceipt/v1",
        "image_set_sha256": image_set_sha256,
        "manifest_sha256": manifest_sha256,
        "release_id": release_id,
        "revocation_sequence": manifest["revocationSequence"],
        "signer_key_ids": sorted(valid_signers),
        "trust_root_version": root_version,
        "unsigned_manifest_sha256": subject_sha256,
        "verified_at": verified_at,
    }
    return ReleaseVerification(
        release_id=release_id,
        release_version=manifest["releaseVersion"],
        manifest_sha256=manifest_sha256,
        unsigned_manifest_sha256=subject_sha256,
        image_set_sha256=image_set_sha256,
        verification_receipt_sha256=_sha256(canonical_json_bytes(receipt_payload)),
        signer_key_ids=tuple(sorted(valid_signers)),
        revocation_sequence=manifest["revocationSequence"],
        migration_revision=manifest["migrationRevision"],
        compatible_schema_revisions=tuple(manifest["compatibleSchemaRevisions"]),
        provenance_sha256=manifest["provenanceSha256"],
        _seal=_RELEASE_VERIFICATION_SEAL,
    )


__all__ = [
    "ReleaseVerification",
    "ReleaseVerificationError",
    "signature_message",
    "unsigned_manifest",
    "verify_release_manifest",
]
