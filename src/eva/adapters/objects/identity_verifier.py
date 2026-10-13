"""Ed25519 adapter and verified IdentityPack file loader."""

from __future__ import annotations

from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from eva.application.identity_verifier import (
    IdentityPackTrustBinding,
    load_default_identity_pack_trust_binding,
    verify_identity_pack_bytes as verify_identity_pack_payload,
)
from eva.contracts.identity import IdentityPack


def verify_ed25519_signature(
    *, public_key_bytes: bytes, signature: bytes, message: bytes
) -> None:
    """Verify one detached Ed25519 signature and expose a stable failure type."""

    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Ed25519 signature is invalid") from exc


def verify_identity_pack_bytes(
    raw: bytes, *, trust: IdentityPackTrustBinding
) -> IdentityPack:
    """Verify an IdentityPack with the concrete Ed25519 adapter."""

    return verify_identity_pack_payload(
        raw,
        trust=trust,
        signature_verifier=verify_ed25519_signature,
    )


def load_current_identity_pack(
    path: Path | None = None, *, trust: IdentityPackTrustBinding | None = None
) -> IdentityPack:
    """Load and fully verify the versioned IdentityPack used by the runtime."""

    selected = path or Path(__file__).parents[2] / "application/default_identity_pack.json"
    if selected.is_symlink() or not selected.is_file():
        raise ValueError("IdentityPack must be a regular non-symlink file")
    if selected.stat().st_size > 65_536:
        raise ValueError("IdentityPack is too large")
    try:
        raw = selected.read_bytes()
        pack = verify_identity_pack_bytes(
            raw, trust=trust or load_default_identity_pack_trust_binding()
        )
    except (OSError, ValueError) as error:
        raise ValueError("IdentityPack is invalid") from error
    return pack


__all__ = [
    "load_current_identity_pack",
    "verify_ed25519_signature",
    "verify_identity_pack_bytes",
]
