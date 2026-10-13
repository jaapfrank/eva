from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.support.p256 import P256AckVerifier, P256KeyError
from eva.contracts import canonical_sha256
from eva.contracts.execution import ClientAckChallengeClaim, DeliveryReceiptV2
from eva.contracts.ingress import DeliveryOfferV2, IngressRequest


EVA2_ROOT = Path(__file__).resolve().parents[2]
ACK_KEY_PATH = EVA2_ROOT / "design/fixtures/noor-client-ack-key-v1.json"
CONNECTOR_FIXTURES = (
    EVA2_ROOT / "design/vertical/noor-4d-connector-provider-fixtures-v1.json"
)


def _fixture() -> dict[str, object]:
    return json.loads(ACK_KEY_PATH.read_bytes())


def _vectors() -> dict[str, dict[str, object]]:
    fixtures = json.loads(CONNECTOR_FIXTURES.read_bytes())
    return fixtures["hostEvidence"]["outcomeAckVectors"]


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_all_ten_sealed_p1363_ack_vectors_verify() -> None:
    fixture = _fixture()
    verifier = P256AckVerifier.from_public_jwk(
        fixture["publicJwk"], expected_sha256=fixture["publicJwkCanonicalSha256"]
    )
    vectors = _vectors()

    assert len(vectors) == 10
    for vector in vectors.values():
        receipt = DeliveryReceiptV2.model_validate(vector["deliveryReceipt"]["payload"])
        ack = receipt.proof.client_ack
        assert verifier(
            ack.signing_bytes(), ack.signature_bytes(), ack.public_key_sha256
        )


def test_real_contract_binder_uses_adapter_before_durable_claim() -> None:
    fixture = _fixture()
    verifier = P256AckVerifier.from_public_jwk(
        fixture["publicJwk"], expected_sha256=fixture["publicJwkCanonicalSha256"]
    )
    vector = _vectors()["evaSuccess"]
    ingress = IngressRequest.model_validate(vector["ingress"]["payload"])
    offer = DeliveryOfferV2.model_validate(vector["deliveryOffer"]["payload"])
    receipt = DeliveryReceiptV2.model_validate(vector["deliveryReceipt"]["payload"])
    calls: list[str] = []

    def claim(*_args):
        calls.append("claim")
        return ClientAckChallengeClaim.model_validate(
            {"status": "claimed", "receipt": receipt.model_dump(mode="json")}
        )

    assert (
        receipt.validate_against_offer(
            offer,
            ingress,
            signature_verifier=verifier,
            challenge_claimer=claim,
        )
        == receipt
    )
    assert calls == ["claim"]


def test_message_signature_and_key_hash_mutations_are_rejected() -> None:
    fixture = _fixture()
    verifier = P256AckVerifier.from_public_jwk(fixture["publicJwk"])
    vector = _vectors()["evaSuccess"]
    receipt = DeliveryReceiptV2.model_validate(vector["deliveryReceipt"]["payload"])
    ack = receipt.proof.client_ack
    signature = ack.signature_bytes()

    assert not verifier(ack.signing_bytes() + b"x", signature, ack.public_key_sha256)
    mutated = bytearray(signature)
    mutated[0] ^= 1
    assert not verifier(ack.signing_bytes(), bytes(mutated), ack.public_key_sha256)
    assert not verifier(ack.signing_bytes(), signature, "0" * 64)
    assert not verifier(ack.signing_bytes(), signature[:-1], ack.public_key_sha256)


def test_jwk_is_closed_canonical_p256_and_hash_bound() -> None:
    fixture = _fixture()
    public_jwk = fixture["publicJwk"]
    assert canonical_sha256(public_jwk) == fixture["publicJwkCanonicalSha256"]

    for mutate, message in (
        (lambda value: value.update({"kid": "attacker"}), "exactly"),
        (lambda value: value.update({"crv": "P-384"}), "P-256"),
        (lambda value: value.update({"x": value["x"] + "="}), "base64url"),
    ):
        altered = deepcopy(public_jwk)
        mutate(altered)
        with pytest.raises(P256KeyError, match=message):
            P256AckVerifier.from_public_jwk(altered)

    with pytest.raises(P256KeyError, match="hash"):
        P256AckVerifier.from_public_jwk(public_jwk, expected_sha256="0" * 64)

    assert len(_decode(public_jwk["x"])) == 32
    assert len(_decode(public_jwk["y"])) == 32
