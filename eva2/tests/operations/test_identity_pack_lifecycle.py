from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eva.application.identity_verifier import IdentityPackTrustBinding
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.identity import IdentityPack, IdentityPackDraft
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    activate_identity_successor,
    issue_identity_pack,
    rollback_identity_successor,
    validate_identity_successor,
)


def _base_and_key() -> tuple[IdentityPack, bytes, IdentityPackTrustBinding]:
    private = Ed25519PrivateKey.generate()
    raw_key = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    payload = json.loads(
        (Path(__file__).parents[2] / "src/eva/application/default_identity_pack.json").read_bytes()
    )
    payload.update(
        {
            "identity_id": "identity:lifecycle:test",
            "issuer": "issuer:lifecycle:test",
            "state": "current",
        }
    )
    source = dict(payload)
    source.pop("source_sha256")
    source.pop("signature")
    payload["source_sha256"] = hashlib.sha256(canonical_json_bytes(source)).hexdigest()
    signing = dict(payload)
    signing.pop("signature")
    payload["signature"] = base64.urlsafe_b64encode(
        private.sign(canonical_json_bytes(signing))
    ).rstrip(b"=").decode("ascii")
    return (
        IdentityPack.model_validate(payload),
        raw_key,
        IdentityPackTrustBinding(
            expected_identity_id=payload["identity_id"],
            expected_issuer=payload["issuer"],
            public_key_bytes=public,
            public_key_sha256=hashlib.sha256(public).hexdigest(),
        ),
    )


def _draft(base: IdentityPack) -> IdentityPackDraft:
    return IdentityPackDraft.model_validate(
        {
            "contract": "IdentityPackDraft/v1",
            "schema_version": 1,
            "draft_id": "identity-draft:lifecycle:test",
            "base_identity_id": base.identity_id,
            "base_pack_version": base.pack_version,
            "base_pack_sha256": canonical_sha256(base),
            "parent_draft_sha256": None,
            "state": "staged",
            "presentation": {
                "display_name": "Evi",
                "pronouns": None,
                "language": None,
                "voice_style": None,
                "temperament": None,
                "humour": None,
                "channel_presentations": None,
            },
        }
    )


def test_offline_issuance_changes_only_the_closed_presentation_and_verifies() -> None:
    base, key, trust = _base_and_key()
    candidate = issue_identity_pack(
        _draft(base), base, private_key_bytes=key, issued_at="2026-08-27T12:00:00Z"
    )

    assert candidate.display_name == "Evi"
    assert candidate.pack_version == base.pack_version + 1
    assert candidate.values == base.values
    assert candidate.conversation_boundaries == base.conversation_boundaries
    validate_identity_successor(candidate, base, trust=trust)


def test_issuance_rejects_stale_draft_and_wrong_key_size() -> None:
    base, key, _trust = _base_and_key()
    stale = _draft(base).model_copy(update={"base_pack_sha256": "0" * 64})
    with pytest.raises(ValueError, match="base pack hash differs"):
        issue_identity_pack(stale, base, private_key_bytes=key, issued_at="2026-08-27T12:00:00Z")
    with pytest.raises(IdentityPackLifecycleError, match="32 bytes"):
        issue_identity_pack(_draft(base), base, private_key_bytes=b"x", issued_at="2026-08-27T12:00:00Z")


def test_cutover_saves_verified_predecessor_and_rollback_restores_it(private_tmp_path: Path) -> None:
    base, key, trust = _base_and_key()
    candidate = issue_identity_pack(
        _draft(base), base, private_key_bytes=key, issued_at="2026-08-27T12:00:00Z"
    )
    current_path = private_tmp_path / "current.json"
    candidate_path = private_tmp_path / "candidate.json"
    rollback_path = private_tmp_path / "rollback.json"
    current_path.write_bytes(canonical_json_bytes(base))
    candidate_path.write_bytes(canonical_json_bytes(candidate))

    cutover = activate_identity_successor(
        current_path=current_path,
        candidate_path=candidate_path,
        rollback_path=rollback_path,
        trust=trust,
    )
    assert cutover.previous_sha256 == hashlib.sha256(canonical_json_bytes(base)).hexdigest()
    assert json.loads(current_path.read_bytes())["display_name"] == "Evi"
    assert current_path.stat().st_mode & 0o777 == 0o600
    assert rollback_path.stat().st_mode & 0o777 == 0o600

    restored = rollback_identity_successor(
        current_path=current_path, rollback_path=rollback_path, trust=trust
    )
    assert restored.current_sha256 == hashlib.sha256(canonical_json_bytes(base)).hexdigest()
    assert json.loads(current_path.read_bytes())["display_name"] == "Eva"


def test_cutover_refuses_unsigned_or_non_successor_candidate(private_tmp_path: Path) -> None:
    base, key, trust = _base_and_key()
    candidate = issue_identity_pack(
        _draft(base), base, private_key_bytes=key, issued_at="2026-08-27T12:00:00Z"
    )
    current_path = private_tmp_path / "current.json"
    candidate_path = private_tmp_path / "candidate.json"
    rollback_path = private_tmp_path / "rollback.json"
    current_path.write_bytes(canonical_json_bytes(base))
    damaged = candidate.model_copy(update={"signature": "A" * 86})
    candidate_path.write_bytes(canonical_json_bytes(damaged))
    with pytest.raises(IdentityPackLifecycleError, match="identity pack file is invalid"):
        activate_identity_successor(
            current_path=current_path,
            candidate_path=candidate_path,
            rollback_path=rollback_path,
            trust=trust,
        )
    assert not rollback_path.exists()
