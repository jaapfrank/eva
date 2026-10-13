from __future__ import annotations

import base64
import hashlib
import inspect
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eva.adapters.objects.identity_verifier import (
    load_current_identity_pack,
    verify_ed25519_signature,
    verify_identity_pack_bytes,
)
from eva.application.identity_verifier import (
    IdentityPackTrustBinding,
    load_default_identity_pack_trust_binding,
    parse_identity_pack_trust_binding,
    verify_identity_pack_bytes as verify_identity_pack_payload,
)
from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes


DEFAULT_PACK = Path(__file__).parents[3] / "src/eva/application/default_identity_pack.json"


def _signed_pack(*, state: str = "current") -> tuple[bytes, IdentityPackTrustBinding]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    payload = json.loads(DEFAULT_PACK.read_text(encoding="utf-8"))
    payload.update(
        {
            "identity_id": "identity:verifier:test",
            "issuer": "issuer:verifier:test",
            "state": state,
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
    return canonical_json_bytes(payload), IdentityPackTrustBinding(
        expected_identity_id=payload["identity_id"],
        expected_issuer=payload["issuer"],
        public_key_bytes=public_key,
        public_key_sha256=hashlib.sha256(public_key).hexdigest(),
    )


def test_default_identity_pack_is_canonical_and_fully_verified() -> None:
    raw = DEFAULT_PACK.read_bytes()

    assert require_canonical_bytes(raw)
    assert verify_identity_pack_bytes(
        raw, trust=load_default_identity_pack_trust_binding()
    ).display_name == "Eva"
    assert load_current_identity_pack().display_name == "Eva"


@pytest.mark.parametrize(
    "raw",
    (
        DEFAULT_PACK.read_bytes() + b"\n",
        b'{"contract":"IdentityPack/v1","contract":"IdentityPack/v1"}',
        b'{"value":1.0}',
        b"\xef\xbb\xbf{}",
    ),
)
def test_identity_pack_verifier_rejects_noncanonical_wire_bytes(raw: bytes) -> None:
    with pytest.raises(ValueError, match="IdentityPack is invalid"):
        verify_identity_pack_bytes(raw, trust=load_default_identity_pack_trust_binding())


def test_identity_pack_verifier_rejects_source_hash_signature_and_trust_mutations() -> None:
    raw, trust = _signed_pack()
    payload = json.loads(raw)

    payload["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source SHA-256"):
        verify_identity_pack_bytes(canonical_json_bytes(payload), trust=trust)

    payload = json.loads(raw)
    payload["signature"] += "="
    with pytest.raises(ValueError, match="canonical base64url"):
        verify_identity_pack_bytes(canonical_json_bytes(payload), trust=trust)

    mismatched = IdentityPackTrustBinding(
        expected_identity_id="identity:another",
        expected_issuer=trust.expected_issuer,
        public_key_bytes=trust.public_key_bytes,
        public_key_sha256=trust.public_key_sha256,
    )
    with pytest.raises(ValueError, match="trust binding does not match"):
        verify_identity_pack_bytes(raw, trust=mismatched)


def test_identity_pack_verifier_requires_current_signed_pack_and_closed_schema() -> None:
    staged, trust = _signed_pack(state="staged")
    with pytest.raises(ValueError, match="not current"):
        verify_identity_pack_bytes(staged, trust=trust)

    raw, trust = _signed_pack()
    payload = json.loads(raw)
    payload["system_prompt"] = "forbidden"
    with pytest.raises(ValueError, match="IdentityPack is invalid"):
        verify_identity_pack_bytes(canonical_json_bytes(payload), trust=trust)


def test_trust_binding_is_closed_and_verifier_accepts_no_signing_authority() -> None:
    valid = (
        b'{"contract":"IdentityPackTrustBinding/v1","expected_identity_id"'
        b':"identity:eva:fixture","expected_issuer":"issuer:eva2:fixture",'
        b'"public_key_base64url":"A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",'
        b'"public_key_sha256":"56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c",'
        b'"schema_version":1}'
    )
    assert parse_identity_pack_trust_binding(valid).expected_issuer == "issuer:eva2:fixture"

    malformed = json.loads(valid)
    malformed["private_key"] = "forbidden"
    with pytest.raises(ValueError, match="shape is closed"):
        parse_identity_pack_trust_binding(canonical_json_bytes(malformed))
    malformed = json.loads(valid)
    malformed["expected_identity_id"] = "not an identifier"
    with pytest.raises(ValueError, match="trust binding is incomplete"):
        parse_identity_pack_trust_binding(canonical_json_bytes(malformed))
    with pytest.raises(ValueError, match="public key hash"):
        IdentityPackTrustBinding(
            expected_identity_id="identity:verifier:test",
            expected_issuer="issuer:verifier:test",
            public_key_bytes=b"x" * 32,
            public_key_sha256="not-a-hash",
        )
    signature = inspect.signature(verify_identity_pack_payload)
    assert "private" not in signature.parameters
    assert "signature_verifier" in signature.parameters


def test_application_verifier_delegates_only_detached_signature_material() -> None:
    raw, trust = _signed_pack()
    observed: dict[str, bytes] = {}

    def verifier(*, public_key_bytes: bytes, signature: bytes, message: bytes) -> None:
        observed.update(
            public_key_bytes=public_key_bytes,
            signature=signature,
            message=message,
        )
        verify_ed25519_signature(
            public_key_bytes=public_key_bytes,
            signature=signature,
            message=message,
        )

    pack = verify_identity_pack_payload(
        raw, trust=trust, signature_verifier=verifier
    )

    assert pack.identity_id == trust.expected_identity_id
    assert observed["public_key_bytes"] == trust.public_key_bytes
    assert len(observed["signature"]) == 64
    assert b'"signature"' not in observed["message"]
