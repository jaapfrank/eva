"""Sealed Eva4DEncryptedObject/v1 XChaCha20-Poly1305 envelope adapter."""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass

from nacl.exceptions import CryptoError
from nacl.secret import Aead

from eva.application.objects import (
    EncryptedObjectCandidate,
    ObjectBinding,
    ObjectEnvelopeMetadata,
    ObjectKeyResolver,
)
from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes


MAGIC = b"EVA2XO01"
KEY_BYTES = 32
NONCE_BYTES = 24
TAG_BYTES = 16
MAXIMUM_PLAINTEXT_BYTES = 65_536
MAXIMUM_CANONICAL_AAD_BYTES = 1_024
MAXIMUM_STORED_ENVELOPE_BYTES = 66_612
_AAD_KEYS = {
    "contract",
    "installation_id",
    "key_ref",
    "mime_type",
    "object_id",
    "plaintext_byte_length",
    "plaintext_sha256",
    "tenant_id",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ObjectEnvelopeError(ValueError):
    """The encrypted object is structurally invalid or binding-incoherent."""


class ObjectAuthenticationError(ObjectEnvelopeError):
    """The XChaCha20-Poly1305 authentication or plaintext binding failed."""


@dataclass(frozen=True, slots=True)
class InspectedEnvelope:
    binding: ObjectBinding
    plaintext_sha256: str
    plaintext_byte_length: int
    canonical_aad: bytes
    header: bytes
    nonce: bytes
    encrypted_message: bytes


def _resolve_key(binding: ObjectBinding, key_resolver: ObjectKeyResolver) -> bytes:
    key = key_resolver(binding)
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise ObjectEnvelopeError("object key resolver must return exactly 32 bytes")
    return key


def _aad_bytes(
    binding: ObjectBinding, plaintext_sha256: str, plaintext_length: int
) -> bytes:
    aad = canonical_json_bytes(
        {
            "contract": "Eva4DObjectAAD/v1",
            "installation_id": binding.installation_id,
            "key_ref": binding.key_ref,
            "mime_type": binding.mime_type,
            "object_id": binding.object_id,
            "plaintext_byte_length": plaintext_length,
            "plaintext_sha256": plaintext_sha256,
            "tenant_id": binding.tenant_id,
        }
    )
    if not 1 <= len(aad) <= MAXIMUM_CANONICAL_AAD_BYTES:
        raise ObjectEnvelopeError("canonical object AAD exceeds 1024 bytes")
    return aad


def _candidate_fields(
    *,
    binding: ObjectBinding,
    plaintext: bytes,
    encrypted_message: bytes,
    canonical_aad: bytes,
) -> dict[str, object]:
    header = MAGIC + struct.pack(">I", len(canonical_aad)) + canonical_aad
    envelope = header + encrypted_message
    if len(envelope) > MAXIMUM_STORED_ENVELOPE_BYTES:
        raise ObjectEnvelopeError("stored object envelope exceeds 66612 bytes")
    return {
        "binding": binding,
        "envelope": envelope,
        "canonical_aad": canonical_aad,
        "ciphertext_sha256": hashlib.sha256(envelope).hexdigest(),
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        "plaintext_byte_length": len(plaintext),
        "stored_envelope_byte_length": len(envelope),
    }


def _prepare_encryption(
    binding: ObjectBinding,
    plaintext: bytes,
    *,
    key_resolver: ObjectKeyResolver,
) -> tuple[bytes, bytes, bytes]:
    if not isinstance(plaintext, bytes):
        raise TypeError("object plaintext must be bytes")
    if len(plaintext) > MAXIMUM_PLAINTEXT_BYTES:
        raise ObjectEnvelopeError("object plaintext exceeds 65536 bytes")

    plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
    canonical_aad = _aad_bytes(binding, plaintext_sha256, len(plaintext))
    header = MAGIC + struct.pack(">I", len(canonical_aad)) + canonical_aad
    key = _resolve_key(binding, key_resolver)
    return canonical_aad, header, key


def _encrypted_message_bytes(encrypted: object, plaintext_length: int) -> bytes:
    try:
        nonce = encrypted.nonce  # type: ignore[attr-defined]
        ciphertext = encrypted.ciphertext  # type: ignore[attr-defined]
        encrypted_message = bytes(encrypted)  # type: ignore[arg-type]
    except (AttributeError, TypeError) as exc:  # pragma: no cover - library guard
        raise ObjectEnvelopeError("PyNaCl returned an unexpected value") from exc
    if (
        len(nonce) != NONCE_BYTES
        or len(ciphertext) != plaintext_length + TAG_BYTES
        or encrypted_message != nonce + ciphertext
    ):
        raise ObjectEnvelopeError("PyNaCl returned an unexpected envelope layout")
    return encrypted_message


def encrypt_object(
    binding: ObjectBinding,
    plaintext: bytes,
    *,
    key_resolver: ObjectKeyResolver,
) -> EncryptedObjectCandidate:
    """Encrypt using fresh PyNaCl/libsodium entropy; nonce injection is absent."""

    canonical_aad, header, key = _prepare_encryption(
        binding, plaintext, key_resolver=key_resolver
    )
    # Deliberately omit the nonce argument. PyNaCl/libsodium owns runtime
    # entropy; there is no nonce injection branch in this adapter.
    encrypted = Aead(key).encrypt(plaintext, aad=header)
    fields = _candidate_fields(
        binding=binding,
        plaintext=plaintext,
        encrypted_message=_encrypted_message_bytes(encrypted, len(plaintext)),
        canonical_aad=canonical_aad,
    )
    return EncryptedObjectCandidate(**fields)  # type: ignore[arg-type]


def inspect_envelope(envelope: bytes) -> InspectedEnvelope:
    """Parse and validate the complete non-secret envelope structure."""

    if not isinstance(envelope, bytes):
        raise TypeError("stored object envelope must be bytes")
    minimum = 12 + 1 + NONCE_BYTES + TAG_BYTES
    if not minimum <= len(envelope) <= MAXIMUM_STORED_ENVELOPE_BYTES:
        raise ObjectEnvelopeError("stored object envelope length is out of range")
    if envelope[:8] != MAGIC:
        raise ObjectEnvelopeError("unknown encrypted object magic or version")
    aad_length = struct.unpack(">I", envelope[8:12])[0]
    if not 1 <= aad_length <= MAXIMUM_CANONICAL_AAD_BYTES:
        raise ObjectEnvelopeError("canonical object AAD length is out of range")
    aad_end = 12 + aad_length
    if aad_end > len(envelope):
        raise ObjectEnvelopeError("stored object envelope is truncated")
    canonical_aad = envelope[12:aad_end]
    try:
        aad = require_canonical_bytes(
            canonical_aad, maximum_bytes=MAXIMUM_CANONICAL_AAD_BYTES
        )
    except (TypeError, ValueError) as exc:
        raise ObjectEnvelopeError("object AAD is not exact canonical JSON") from exc
    if set(aad) != _AAD_KEYS:
        raise ObjectEnvelopeError("object AAD must contain exactly the sealed keys")
    if aad.get("contract") != "Eva4DObjectAAD/v1":
        raise ObjectEnvelopeError("object AAD contract is not supported")
    plaintext_length = aad.get("plaintext_byte_length")
    if (
        type(plaintext_length) is not int
        or not 0 <= plaintext_length <= MAXIMUM_PLAINTEXT_BYTES
    ):
        raise ObjectEnvelopeError("object AAD plaintext length is out of range")
    plaintext_sha256 = aad.get("plaintext_sha256")
    if (
        not isinstance(plaintext_sha256, str)
        or _SHA256_RE.fullmatch(plaintext_sha256) is None
    ):
        raise ObjectEnvelopeError("object AAD plaintext SHA-256 is malformed")
    try:
        binding = ObjectBinding(
            tenant_id=aad["tenant_id"],
            installation_id=aad["installation_id"],
            object_id=aad["object_id"],
            key_ref=aad["key_ref"],
            mime_type=aad["mime_type"],
        )
    except (TypeError, ValueError) as exc:
        raise ObjectEnvelopeError("object AAD binding is invalid") from exc

    expected_length = aad_end + NONCE_BYTES + plaintext_length + TAG_BYTES
    if len(envelope) != expected_length:
        raise ObjectEnvelopeError("stored object envelope length does not match AAD")
    header = envelope[:aad_end]
    encrypted_message = envelope[aad_end:]
    return InspectedEnvelope(
        binding=binding,
        plaintext_sha256=plaintext_sha256,
        plaintext_byte_length=plaintext_length,
        canonical_aad=canonical_aad,
        header=header,
        nonce=encrypted_message[:NONCE_BYTES],
        encrypted_message=encrypted_message,
    )


def envelope_metadata(envelope: bytes) -> ObjectEnvelopeMetadata:
    """Return structural metadata without claiming cryptographic authentication."""

    inspected = inspect_envelope(envelope)
    return ObjectEnvelopeMetadata(
        binding=inspected.binding,
        canonical_aad=inspected.canonical_aad,
        ciphertext_sha256=hashlib.sha256(envelope).hexdigest(),
        plaintext_sha256=inspected.plaintext_sha256,
        plaintext_byte_length=inspected.plaintext_byte_length,
        stored_envelope_byte_length=len(envelope),
    )


def _require_binding(actual: ObjectBinding, expected: ObjectBinding) -> None:
    for field in (
        "tenant_id",
        "installation_id",
        "object_id",
        "key_ref",
        "mime_type",
    ):
        if getattr(actual, field) != getattr(expected, field):
            raise ObjectEnvelopeError(f"object AAD {field} differs from authority")


def decrypt_object(
    envelope: bytes,
    binding: ObjectBinding,
    *,
    key_resolver: ObjectKeyResolver,
) -> bytes:
    """Bind first, then resolve the key, authenticate, decrypt and re-hash."""

    inspected = inspect_envelope(envelope)
    _require_binding(inspected.binding, binding)
    key = _resolve_key(binding, key_resolver)
    try:
        plaintext = Aead(key).decrypt(inspected.encrypted_message, aad=inspected.header)
    except CryptoError as exc:
        raise ObjectAuthenticationError(
            "object envelope XChaCha20-Poly1305 authentication failed"
        ) from exc
    if len(plaintext) != inspected.plaintext_byte_length:
        raise ObjectAuthenticationError("decrypted object length differs from AAD")
    if hashlib.sha256(plaintext).hexdigest() != inspected.plaintext_sha256:
        raise ObjectAuthenticationError("decrypted object SHA-256 differs from AAD")
    return plaintext


class XChaCha20ObjectCipher:
    """ObjectCipher implementation for the selected PyNaCl primitive."""

    def encrypt(
        self,
        binding: ObjectBinding,
        plaintext: bytes,
        *,
        key_resolver: ObjectKeyResolver,
    ) -> EncryptedObjectCandidate:
        return encrypt_object(binding, plaintext, key_resolver=key_resolver)

    def inspect(self, envelope: bytes) -> ObjectEnvelopeMetadata:
        return envelope_metadata(envelope)

    def decrypt(
        self,
        envelope: bytes,
        binding: ObjectBinding,
        *,
        key_resolver: ObjectKeyResolver,
    ) -> bytes:
        return decrypt_object(envelope, binding, key_resolver=key_resolver)


__all__ = [
    "KEY_BYTES",
    "MAGIC",
    "MAXIMUM_CANONICAL_AAD_BYTES",
    "MAXIMUM_PLAINTEXT_BYTES",
    "MAXIMUM_STORED_ENVELOPE_BYTES",
    "NONCE_BYTES",
    "ObjectAuthenticationError",
    "ObjectEnvelopeError",
    "TAG_BYTES",
    "XChaCha20ObjectCipher",
    "decrypt_object",
    "encrypt_object",
    "envelope_metadata",
    "inspect_envelope",
]
