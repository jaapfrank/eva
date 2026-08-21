from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import struct

import pytest

from eva.adapters.objects.envelope import (
    MAGIC,
    MAXIMUM_PLAINTEXT_BYTES,
    ObjectAuthenticationError,
    ObjectEnvelopeError,
    XChaCha20ObjectCipher,
    decrypt_object,
    encrypt_object,
    inspect_envelope,
)
from eva.adapters.objects.fixture_keys import (
    FixtureObjectKeyResolver,
    verify_known_answer_vector,
)
from eva.application.objects import ObjectBinding
from eva.contracts import canonical_json_bytes


EVA2_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = EVA2_ROOT / "design/vertical/noor-4d-execution-profile-v1.json"
PLAINTEXT = (
    "Bevestiging afspraak Noor BV\n\n"
    "De afspraak met Noor BV staat gepland op woensdag 26 augustus 2026 "
    "van 15.00 tot 15.30 uur.\n"
).encode()


def _profile() -> dict[str, object]:
    return json.loads(PROFILE_PATH.read_bytes())


def _vector(identity: str) -> dict[str, object]:
    encrypted_format = _profile()["encryptedObjectFormat"]
    assert isinstance(encrypted_format, dict)
    vectors = encrypted_format["knownAnswerVectors"]["vectors"]
    return next(item for item in vectors if item["identity"] == identity)


def _binding(vector: dict[str, object]) -> ObjectBinding:
    return ObjectBinding(
        tenant_id=str(vector["tenantId"]),
        installation_id=str(vector["installationId"]),
        object_id=str(vector["objectId"]),
        key_ref=str(vector["keyRef"]),
        mime_type="text/plain",
    )


def test_both_sealed_pynacl_known_answer_vectors_match_exact_envelopes() -> None:
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)

    for identity in ("Eva", "Mira"):
        vector = _vector(identity)
        binding = _binding(vector)
        assert verify_known_answer_vector(
            binding=binding,
            plaintext=PLAINTEXT,
            key_resolver=resolver,
            nonce=bytes.fromhex(str(vector["nonceHex"])),
            expected_envelope=bytes.fromhex(str(vector["completeEnvelopeHex"])),
        )


def test_normal_encryption_has_no_nonce_injection_api_and_uses_fresh_entropy() -> None:
    vector = _vector("Eva")
    binding = _binding(vector)
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)

    assert "nonce" not in inspect.signature(encrypt_object).parameters
    assert "nonce_source" not in inspect.signature(encrypt_object).parameters

    first = encrypt_object(binding, PLAINTEXT, key_resolver=resolver)
    second = encrypt_object(binding, PLAINTEXT, key_resolver=resolver)

    assert first.envelope != second.envelope
    assert first.ciphertext_sha256 != second.ciphertext_sha256
    assert (
        inspect_envelope(first.envelope).nonce
        != inspect_envelope(second.envelope).nonce
    )
    assert decrypt_object(first.envelope, binding, key_resolver=resolver) == PLAINTEXT
    assert decrypt_object(second.envelope, binding, key_resolver=resolver) == PLAINTEXT


def test_plaintext_limit_is_rejected_before_key_or_entropy_acquisition() -> None:
    vector = _vector("Eva")
    binding = _binding(vector)
    resolver_calls = 0

    def resolver(_binding: ObjectBinding) -> bytes:
        nonlocal resolver_calls
        resolver_calls += 1
        return bytes(32)

    with pytest.raises(ObjectEnvelopeError, match="65536"):
        encrypt_object(
            binding,
            b"x" * (MAXIMUM_PLAINTEXT_BYTES + 1),
            key_resolver=resolver,
        )

    assert resolver_calls == 0


def test_binding_is_checked_before_decryption_key_resolution() -> None:
    vector = _vector("Eva")
    binding = _binding(vector)
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    candidate = encrypt_object(binding, PLAINTEXT, key_resolver=resolver)
    resolver_calls = 0

    def should_not_resolve(_binding: ObjectBinding) -> bytes:
        nonlocal resolver_calls
        resolver_calls += 1
        return bytes(32)

    wrong_binding = ObjectBinding(
        tenant_id=binding.tenant_id,
        installation_id=binding.installation_id,
        object_id="00000000-0000-4000-8000-000000000001",
        key_ref=binding.key_ref,
        mime_type=binding.mime_type,
    )
    with pytest.raises(ObjectEnvelopeError, match="object_id"):
        decrypt_object(
            candidate.envelope,
            wrong_binding,
            key_resolver=should_not_resolve,
        )

    assert resolver_calls == 0


def test_envelope_mutation_truncation_and_trailing_bytes_fail_closed() -> None:
    vector = _vector("Eva")
    binding = _binding(vector)
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    envelope = bytearray(
        encrypt_object(binding, PLAINTEXT, key_resolver=resolver).envelope
    )
    envelope[-1] ^= 1

    with pytest.raises(ObjectAuthenticationError):
        decrypt_object(bytes(envelope), binding, key_resolver=resolver)
    with pytest.raises(ObjectEnvelopeError, match="length"):
        inspect_envelope(bytes(envelope[:-1]))
    with pytest.raises(ObjectEnvelopeError, match="length"):
        inspect_envelope(bytes(envelope) + b"x")


def test_canonical_aad_shape_and_encoding_mutations_fail_before_key_resolution() -> (
    None
):
    vector = _vector("Eva")
    binding = _binding(vector)
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    inspected = inspect_envelope(
        encrypt_object(binding, PLAINTEXT, key_resolver=resolver).envelope
    )
    aad = json.loads(inspected.canonical_aad)
    aad["unexpected"] = True
    unknown_aad = canonical_json_bytes(aad)
    unknown_envelope = (
        MAGIC
        + struct.pack(">I", len(unknown_aad))
        + unknown_aad
        + inspected.encrypted_message
    )
    resolver_calls = 0

    def should_not_resolve(_binding: ObjectBinding) -> bytes:
        nonlocal resolver_calls
        resolver_calls += 1
        return bytes(32)

    with pytest.raises(ObjectEnvelopeError, match="exactly the sealed keys"):
        decrypt_object(unknown_envelope, binding, key_resolver=should_not_resolve)

    aad.pop("unexpected")
    reverse_order = dict(reversed(list(aad.items())))
    noncanonical_aad = json.dumps(
        reverse_order, ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert noncanonical_aad != canonical_json_bytes(aad)
    noncanonical_envelope = (
        MAGIC
        + struct.pack(">I", len(noncanonical_aad))
        + noncanonical_aad
        + inspected.encrypted_message
    )
    with pytest.raises(ObjectEnvelopeError, match="exact canonical JSON"):
        decrypt_object(noncanonical_envelope, binding, key_resolver=should_not_resolve)
    assert resolver_calls == 0


def test_cipher_adapter_exposes_the_framework_neutral_port() -> None:
    vector = _vector("Mira")
    binding = _binding(vector)
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    cipher = XChaCha20ObjectCipher()

    candidate = cipher.encrypt(binding, PLAINTEXT, key_resolver=resolver)
    assert candidate.plaintext_sha256 == hashlib.sha256(PLAINTEXT).hexdigest()
    assert (
        cipher.decrypt(candidate.envelope, binding, key_resolver=resolver) == PLAINTEXT
    )
