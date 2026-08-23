from __future__ import annotations

import base64
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.release_verifier import (
    ReleaseVerificationError,
    signature_message,
    unsigned_manifest,
    verify_release_manifest,
)


NOW = "2026-08-23T12:00:00Z"
HASH = "1" * 64


def _signed_release() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    root = {
        "contract": "Eva2TrustRoot/v1",
        "rootVersion": 1,
        "validFrom": "2026-08-01T00:00:00Z",
        "threshold": 1,
        "keys": [
            {
                "keyId": "release-key:test:001",
                "algorithm": "Ed25519",
                "publicKeyBase64": base64.b64encode(public).decode(),
                "roles": ["release_signer"],
                "validFrom": "2026-08-01T00:00:00Z",
                "validUntil": "2027-08-01T00:00:00Z",
            }
        ],
        "previousRootSignatures": [],
    }
    manifest = {
        "contract": "Eva2ReleaseManifest/v1",
        "releaseId": "release:eva2:test:001",
        "releaseVersion": "0.1.0-test",
        "minimumSupportedRelease": "0.1.0-test",
        "sourceCommit": "a" * 40,
        "platformLockSha256": HASH,
        "imageDigests": {
            "living_core": f"sha256:{'2' * 64}",
            "postgresql": f"sha256:{'3' * 64}",
        },
        "sbomBindings": ["4" * 64],
        "licenseInventorySha256": "5" * 64,
        "provenanceSha256": "6" * 64,
        "schemaSetSha256": "7" * 64,
        "migrationRevision": "4e_0001",
        "compatibleSchemaRevisions": ["4d_0007", "4e_0001"],
        "revocationSequence": 1,
        "signatures": [],
    }
    import hashlib

    subject_sha = hashlib.sha256(
        canonical_json_bytes(unsigned_manifest(manifest))
    ).hexdigest()
    envelope = {
        "contract": "Eva2SignatureEnvelope/v1",
        "scope": "release",
        "subjectId": manifest["releaseId"],
        "subjectSha256": subject_sha,
        "algorithm": "Ed25519",
        "domainSeparationPrefix": r"Eva2ReleaseSignature/v1\u0000",
        "keyId": "release-key:test:001",
        "trustRootVersion": 1,
        "signedAt": NOW,
        "expiresAt": "2026-09-23T12:00:00Z",
        "provenanceSha256": manifest["provenanceSha256"],
        "signatureBase64": "",
    }
    envelope["signatureBase64"] = base64.b64encode(
        private.sign(signature_message(envelope))
    ).decode()
    manifest["signatures"] = [envelope]
    return canonical_json_bytes(manifest), canonical_json_bytes(root)


def test_signed_release_becomes_exact_ab_staging_authority() -> None:
    manifest, root = _signed_release()
    verified = verify_release_manifest(manifest, root, verified_at=NOW)

    assert verified.release_id == "release:eva2:test:001"
    assert verified.signer_key_ids == ("release-key:test:001",)
    assert verified.compatible_schema_revisions == ("4d_0007", "4e_0001")
    assert verified.for_slot().verification_receipt_sha256 == (
        verified.verification_receipt_sha256
    )


def test_release_tamper_expiry_and_revocation_fail_before_staging() -> None:
    manifest_bytes, root = _signed_release()
    import json

    tampered = json.loads(manifest_bytes)
    tampered["imageDigests"]["living_core"] = f"sha256:{'9' * 64}"
    with pytest.raises(ReleaseVerificationError, match="authority differs"):
        verify_release_manifest(canonical_json_bytes(tampered), root, verified_at=NOW)

    with pytest.raises(ReleaseVerificationError, match="outside validity"):
        verify_release_manifest(
            manifest_bytes, root, verified_at="2027-09-23T12:00:00Z"
        )

    manifest = deepcopy(json.loads(manifest_bytes))
    with pytest.raises(ReleaseVerificationError, match="revoked"):
        verify_release_manifest(
            canonical_json_bytes(manifest),
            root,
            verified_at=NOW,
            revoked_subject_ids=("release:eva2:test:001",),
        )
