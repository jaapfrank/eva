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
from eva.operations.cli import main
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    activate_identity_successor,
    issue_identity_pack,
    load_private_ed25519_seed,
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


def test_owner_only_key_loader_rejects_world_readable_key(private_tmp_path: Path) -> None:
    key_path = private_tmp_path / "key.bin"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o644)
    with pytest.raises(IdentityPackLifecycleError, match="owner-only"):
        load_private_ed25519_seed(key_path)


def test_cli_issues_activates_and_rolls_back_without_printing_identity_data(
    private_tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, key, trust = _base_and_key()
    base_path = private_tmp_path / "current.json"
    draft_path = private_tmp_path / "draft.json"
    key_path = private_tmp_path / "key.bin"
    trust_path = private_tmp_path / "trust.json"
    candidate_path = private_tmp_path / "candidate.json"
    rollback_path = private_tmp_path / "rollback.json"
    base_path.write_bytes(canonical_json_bytes(base))
    draft_path.write_bytes(canonical_json_bytes(_draft(base)))
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    trust_path.write_bytes(
        canonical_json_bytes(
            {
                "contract": "IdentityPackTrustBinding/v1",
                "expected_identity_id": trust.expected_identity_id,
                "expected_issuer": trust.expected_issuer,
                "public_key_base64url": base64.urlsafe_b64encode(trust.public_key_bytes)
                .rstrip(b"=")
                .decode("ascii"),
                "public_key_sha256": trust.public_key_sha256,
                "schema_version": 1,
            }
        )
    )

    assert main(
        [
            "identity-issue",
            "--base",
            str(base_path),
            "--draft",
            str(draft_path),
            "--private-key-file",
            str(key_path),
            "--issued-at",
            "2026-08-27T12:00:00Z",
            "--output",
            str(candidate_path),
            "--trust-file",
            str(trust_path),
        ]
    ) == 0
    issued_output = capsys.readouterr().out
    assert json.loads(issued_output)["status"] == "issued"
    assert "Evi" not in issued_output
    assert main(
        [
            "identity-activate",
            "--current",
            str(base_path),
            "--candidate",
            str(candidate_path),
            "--rollback",
            str(rollback_path),
            "--trust-file",
            str(trust_path),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "activated"
    assert main(
        [
            "identity-rollback",
            "--current",
            str(base_path),
            "--rollback",
            str(rollback_path),
            "--trust-file",
            str(trust_path),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rolled_back"
    assert json.loads(base_path.read_bytes())["display_name"] == "Eva"
