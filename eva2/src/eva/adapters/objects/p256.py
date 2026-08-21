"""P-256/SHA-256 verifier for exact 64-byte P1363 client ACKs."""

from __future__ import annotations

import base64
import binascii
import hmac
import re
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from eva.contracts.canonical_json import canonical_sha256


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BASE64URL_32_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_P256_ORDER = int(
    "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551",
    16,
)


class P256KeyError(ValueError):
    """A public JWK is not the exact closed P-256 key profile."""


def _decode_coordinate(value: object, label: str) -> bytes:
    if not isinstance(value, str) or _BASE64URL_32_RE.fullmatch(value) is None:
        raise P256KeyError(f"P-256 JWK {label} is not canonical base64url")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise P256KeyError(f"P-256 JWK {label} is not canonical base64url") from exc
    if (
        len(decoded) != 32
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        raise P256KeyError(f"P-256 JWK {label} is not canonical base64url")
    return decoded


@dataclass(frozen=True, slots=True)
class P256AckVerifier:
    """Callable matching the host contract's P256SignatureVerifier port."""

    public_key_sha256: str
    _public_key: ec.EllipticCurvePublicKey

    @classmethod
    def from_public_jwk(
        cls,
        public_jwk: Mapping[str, Any],
        *,
        expected_sha256: str | None = None,
    ) -> "P256AckVerifier":
        if not isinstance(public_jwk, Mapping):
            raise P256KeyError("P-256 public JWK must be an object")
        jwk = dict(public_jwk)
        if set(jwk) != {"kty", "crv", "x", "y"}:
            raise P256KeyError("P-256 public JWK must contain exactly kty, crv, x, y")
        if jwk["kty"] != "EC" or jwk["crv"] != "P-256":
            raise P256KeyError("public JWK must select EC P-256")
        x = int.from_bytes(_decode_coordinate(jwk["x"], "x"), "big")
        y = int.from_bytes(_decode_coordinate(jwk["y"], "y"), "big")
        try:
            public_key = ec.EllipticCurvePublicNumbers(
                x, y, ec.SECP256R1()
            ).public_key()
        except ValueError as exc:
            raise P256KeyError("P-256 public JWK point is not on the curve") from exc
        key_sha256 = canonical_sha256(jwk)
        if expected_sha256 is not None:
            if _SHA256_RE.fullmatch(expected_sha256) is None:
                raise P256KeyError("expected P-256 key hash is malformed")
            if not hmac.compare_digest(key_sha256, expected_sha256):
                raise P256KeyError("P-256 public JWK hash differs from authority")
        return cls(public_key_sha256=key_sha256, _public_key=public_key)

    def __call__(
        self, signing_bytes: bytes, signature: bytes, public_key_sha256: str
    ) -> bool:
        if (
            not isinstance(signing_bytes, bytes)
            or not isinstance(signature, bytes)
            or not isinstance(public_key_sha256, str)
            or len(signature) != 64
            or _SHA256_RE.fullmatch(public_key_sha256) is None
            or not hmac.compare_digest(public_key_sha256, self.public_key_sha256)
        ):
            return False
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        if not (1 <= r < _P256_ORDER and 1 <= s < _P256_ORDER):
            return False
        der_signature = encode_dss_signature(r, s)
        try:
            self._public_key.verify(
                der_signature, signing_bytes, ec.ECDSA(hashes.SHA256())
            )
        except (InvalidSignature, ValueError):
            return False
        return True


__all__ = ["P256AckVerifier", "P256KeyError"]
