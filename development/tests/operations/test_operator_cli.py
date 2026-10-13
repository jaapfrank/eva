from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.release_verifier import signature_message, unsigned_manifest


NOW = "2026-08-23T17:30:00Z"


def _authority() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    root = {
        "contract": "Eva2TrustRoot/v1",
        "keys": [
            {
                "algorithm": "Ed25519",
                "keyId": "release-key:operator:001",
                "publicKeyBase64": base64.b64encode(public).decode(),
                "roles": ["release_signer"],
                "validFrom": "2026-08-01T00:00:00Z",
                "validUntil": "2027-08-01T00:00:00Z",
            }
        ],
        "previousRootSignatures": [],
        "rootVersion": 1,
        "threshold": 1,
        "validFrom": "2026-08-01T00:00:00Z",
    }
    manifest = {
        "compatibleSchemaRevisions": ["4e_0001_operational_timeline"],
        "contract": "Eva2ReleaseManifest/v1",
        "imageDigests": {"living_core": f"sha256:{'2' * 64}"},
        "licenseInventorySha256": "3" * 64,
        "migrationRevision": "4e_0001_operational_timeline",
        "minimumSupportedRelease": "0.1.0",
        "platformLockSha256": "4" * 64,
        "provenanceSha256": "5" * 64,
        "releaseId": "release:eva2:operator:001",
        "releaseVersion": "0.1.0",
        "revocationSequence": 1,
        "sbomBindings": ["6" * 64],
        "schemaSetSha256": "7" * 64,
        "signatures": [],
        "sourceCommit": "8" * 40,
    }
    subject = hashlib.sha256(
        canonical_json_bytes(unsigned_manifest(manifest))
    ).hexdigest()
    envelope = {
        "algorithm": "Ed25519",
        "contract": "Eva2SignatureEnvelope/v1",
        "domainSeparationPrefix": r"Eva2ReleaseSignature/v1\u0000",
        "expiresAt": "2026-09-23T17:30:00Z",
        "keyId": "release-key:operator:001",
        "provenanceSha256": "5" * 64,
        "scope": "release",
        "signatureBase64": "",
        "signedAt": NOW,
        "subjectId": "release:eva2:operator:001",
        "subjectSha256": subject,
        "trustRootVersion": 1,
    }
    envelope["signatureBase64"] = base64.b64encode(
        private.sign(signature_message(envelope))
    ).decode()
    manifest["signatures"] = [envelope]
    return canonical_json_bytes(manifest), canonical_json_bytes(root)


def _run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "eva.operations.cli", *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def test_real_cli_verifies_signature_before_staging_and_opens_only_after_activation() -> (
    None
):
    with tempfile.TemporaryDirectory(prefix="eva2-operator-", dir="/tmp") as raw:
        root = Path(raw) / "installation"
        manifest, trust_root = _authority()
        manifest_path = Path(raw) / "release.json"
        trust_path = Path(raw) / "trust-root.json"
        manifest_path.write_bytes(manifest)
        trust_path.write_bytes(trust_root)

        initialized = json.loads(
            _run(
                "init",
                "--root",
                str(root),
                "--installation-id",
                "installation:operator:001",
                "--database-lineage-id",
                "database:operator:001",
                "--database-schema-revision",
                "4e_0001_operational_timeline",
                "--at",
                NOW,
            ).stdout
        )
        assert initialized["state"]["traffic_state"] == "closed"

        staged = json.loads(
            _run(
                "verify-stage",
                "--root",
                str(root),
                "--slot",
                "b",
                "--manifest",
                str(manifest_path),
                "--trust-root",
                str(trust_path),
                "--verified-at",
                NOW,
                "--expected-revision",
                "1",
            ).stdout
        )
        assert staged["action"] == "signature_verified_and_staged"
        assert staged["release"]["signer_key_ids"] == ["release-key:operator:001"]
        assert staged["state"]["slots"]["b"]["status"] == "staged"

        activated = json.loads(
            _run(
                "activate",
                "--root",
                str(root),
                "--slot",
                "b",
                "--activation-receipt-sha256",
                "9" * 64,
                "--expected-revision",
                "2",
                "--at",
                NOW,
            ).stdout
        )
        assert activated["state"]["active_slot"] == "b"
        assert activated["state"]["traffic_state"] == "closed"

        opened = json.loads(
            _run(
                "traffic",
                "--root",
                str(root),
                "--state",
                "open",
                "--expected-revision",
                "3",
                "--at",
                NOW,
            ).stdout
        )
        assert opened["state"]["traffic_state"] == "open"


def test_cli_rejects_tampered_release_without_changing_slot_state() -> None:
    with tempfile.TemporaryDirectory(prefix="eva2-operator-tamper-", dir="/tmp") as raw:
        root = Path(raw) / "installation"
        manifest, trust_root = _authority()
        tampered = json.loads(manifest)
        tampered["imageDigests"]["living_core"] = f"sha256:{'a' * 64}"
        manifest_path = Path(raw) / "release.json"
        trust_path = Path(raw) / "trust-root.json"
        manifest_path.write_bytes(canonical_json_bytes(tampered))
        trust_path.write_bytes(trust_root)
        _run(
            "init",
            "--root",
            str(root),
            "--installation-id",
            "installation:operator:002",
            "--database-lineage-id",
            "database:operator:002",
            "--database-schema-revision",
            "4e_0001_operational_timeline",
            "--at",
            NOW,
        )

        rejected = _run(
            "verify-stage",
            "--root",
            str(root),
            "--slot",
            "b",
            "--manifest",
            str(manifest_path),
            "--trust-root",
            str(trust_path),
            "--verified-at",
            NOW,
            "--expected-revision",
            "1",
            check=False,
        )
        assert rejected.returncode == 2
        assert json.loads(rejected.stdout)["status"] == "failed"

        status = json.loads(_run("status", "--root", str(root)).stdout)
        assert status["state"]["revision"] == 1
        assert status["state"]["slots"]["b"]["status"] == "empty"
