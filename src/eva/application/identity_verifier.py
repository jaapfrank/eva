"""Fail-closed verification of a signed procedural IdentityPack."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import re
from typing import Protocol

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.contracts.identity import IdentityPack


_PUBLIC_KEY_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_SIGNATURE_RE = re.compile(r"[A-Za-z0-9_-]{86}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/@+\-=]{0,191}")
_TRUST_KEYS = {
    "contract",
    "expected_identity_id",
    "expected_issuer",
    "public_key_base64url",
    "public_key_sha256",
    "schema_version",
}


class IdentitySignatureVerifier(Protocol):
    """Verify one detached identity signature without owning crypto details."""

    def __call__(
        self, *, public_key_bytes: bytes, signature: bytes, message: bytes
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class IdentityPackTrustBinding:
    """Non-secret, image-bound authority for exactly one procedural identity."""

    expected_identity_id: str
    expected_issuer: str
    public_key_bytes: bytes
    public_key_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.expected_identity_id, str)
            or _IDENTIFIER_RE.fullmatch(self.expected_identity_id) is None
            or not isinstance(self.expected_issuer, str)
            or _IDENTIFIER_RE.fullmatch(self.expected_issuer) is None
        ):
            raise ValueError("IdentityPack trust binding is incomplete")
        if not isinstance(self.public_key_bytes, bytes) or len(self.public_key_bytes) != 32:
            raise ValueError("IdentityPack public key must be 32 bytes")
        if (
            not isinstance(self.public_key_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.public_key_sha256) is None
        ):
            raise ValueError("IdentityPack public key hash is invalid")
        actual = hashlib.sha256(self.public_key_bytes).hexdigest()
        if not hmac.compare_digest(actual, self.public_key_sha256):
            raise ValueError("IdentityPack public key hash is invalid")


def _decode_unpadded_base64url(value: object, *, pattern: re.Pattern[str], label: str) -> bytes:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"IdentityPack {label} is not canonical base64url")
    try:
        return base64.urlsafe_b64decode(value + "==")
    except ValueError as exc:
        raise ValueError(f"IdentityPack {label} is invalid") from exc


def parse_identity_pack_trust_binding(raw: bytes) -> IdentityPackTrustBinding:
    """Read one canonical non-secret trust binding with a closed shape."""

    try:
        payload = require_canonical_bytes(raw)
    except ValueError as exc:
        raise ValueError("IdentityPack trust binding is invalid") from exc
    if (
        set(payload) != _TRUST_KEYS
        or payload.get("contract") != "IdentityPackTrustBinding/v1"
        or payload.get("schema_version") != 1
    ):
        raise ValueError("IdentityPack trust binding shape is closed")
    public_key = _decode_unpadded_base64url(
        payload["public_key_base64url"], pattern=_PUBLIC_KEY_RE, label="public key"
    )
    return IdentityPackTrustBinding(
        expected_identity_id=payload["expected_identity_id"],
        expected_issuer=payload["expected_issuer"],
        public_key_bytes=public_key,
        public_key_sha256=payload["public_key_sha256"],
    )


def load_identity_pack_trust_binding(path: Path) -> IdentityPackTrustBinding:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError("IdentityPack trust binding must be a regular bounded file")
    try:
        return parse_identity_pack_trust_binding(path.read_bytes())
    except OSError as exc:
        raise ValueError("IdentityPack trust binding is invalid") from exc


def load_default_identity_pack_trust_binding() -> IdentityPackTrustBinding:
    return load_identity_pack_trust_binding(
        Path(__file__).with_name("default_identity_pack_trust.json")
    )


def verify_identity_pack_bytes(
    raw: bytes,
    *,
    trust: IdentityPackTrustBinding,
    signature_verifier: IdentitySignatureVerifier,
) -> IdentityPack:
    """Verify canonical bytes, source hash, trust binding and Ed25519 signature."""

    try:
        payload = require_canonical_bytes(raw)
        pack = IdentityPack.model_validate(payload)
    except ValueError as exc:
        raise ValueError("IdentityPack is invalid") from exc
    if (
        not hmac.compare_digest(pack.identity_id, trust.expected_identity_id)
        or not hmac.compare_digest(pack.issuer, trust.expected_issuer)
    ):
        raise ValueError("IdentityPack trust binding does not match")

    source_payload = dict(payload)
    source_payload.pop("source_sha256")
    source_payload.pop("signature")
    source_sha256 = hashlib.sha256(canonical_json_bytes(source_payload)).hexdigest()
    if not hmac.compare_digest(source_sha256, pack.source_sha256):
        raise ValueError("IdentityPack source SHA-256 is invalid")

    signature = _decode_unpadded_base64url(
        pack.signature, pattern=_SIGNATURE_RE, label="signature"
    )
    if len(signature) != 64:
        raise ValueError("IdentityPack signature must be 64 bytes")
    signing_payload = dict(payload)
    signing_payload.pop("signature")
    try:
        signature_verifier(
            public_key_bytes=trust.public_key_bytes,
            signature=signature,
            message=canonical_json_bytes(signing_payload),
        )
    except ValueError as exc:
        raise ValueError("IdentityPack signature is invalid") from exc
    if pack.state != "current":
        raise ValueError("IdentityPack is not current")
    return pack
