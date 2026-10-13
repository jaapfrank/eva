from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from pydantic import ValidationError

from eva.contracts import canonical_json_bytes, canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInitializeSuccess,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
    classify_connector_protocol_failure,
    decode_connector_rpc_exchange,
    decode_connector_rpc_frame,
    decode_connector_rpc_frame_prefix,
    encode_connector_rpc_frame,
)
from eva.contracts.evidence import EvidenceEnvelope, EvidenceEnvelopeV2
from eva.contracts.execution import (
    ACTIVE_CLIENT_ACK_RECEIPT_CONTRACT,
    ClientAckChallengeClaim,
    DeliveryReceipt,
    DeliveryReceiptV2,
)
from eva.contracts.ingress import DeliveryOffer, DeliveryOfferV2, IngressRequest

EVA2_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = EVA2_ROOT / "schemas" / "contracts" / "contract-corpus-v1.json"
ACK_KEY_PATH = EVA2_ROOT / "design" / "fixtures" / "noor-client-ack-key-v1.json"


def _corpus() -> dict[str, object]:
    return json.loads(CORPUS_PATH.read_bytes())


def _payload(case_id: str) -> dict[str, object]:
    corpus = _corpus()
    cases = corpus["cases"]
    assert isinstance(cases, list)
    case = next(item for item in cases if item["caseId"] == case_id)
    return json.loads(case["wireUtf8"])


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_ack_contract() -> tuple[
    IngressRequest,
    DeliveryOfferV2,
    DeliveryReceiptV2,
    ec.EllipticCurvePublicKey,
]:
    fixture = json.loads(ACK_KEY_PATH.read_bytes())
    public_jwk = fixture["publicJwk"]
    assert canonical_sha256(public_jwk) == fixture["publicJwkCanonicalSha256"]
    private_value = int.from_bytes(_base64url_decode(fixture["privateJwk"]["d"]))
    private_key = ec.derive_private_key(private_value, ec.SECP256R1())
    public_numbers = private_key.public_key().public_numbers()
    assert public_numbers.x.to_bytes(32) == _base64url_decode(public_jwk["x"])
    assert public_numbers.y.to_bytes(32) == _base64url_decode(public_jwk["y"])

    offer_payload = _payload("valid-delivery-offer-v2-client")
    offer_payload["proof"]["ack_key_sha256"] = fixture["publicJwkCanonicalSha256"]
    offer = DeliveryOfferV2.model_validate(offer_payload)
    ingress_payload = _payload("valid-ingress-request")
    ingress_payload["client_ack_key_sha256"] = fixture["publicJwkCanonicalSha256"]
    ingress = IngressRequest.model_validate(ingress_payload)

    receipt_payload = _payload("valid-delivery-receipt-v2-client")
    receipt_payload["delivery_offer_sha256"] = canonical_sha256(offer)
    ack_payload = receipt_payload["proof"]["client_ack"]
    ack_payload["public_key_sha256"] = fixture["publicJwkCanonicalSha256"]
    unsigned_receipt = DeliveryReceiptV2.model_validate(receipt_payload)
    signing_bytes = unsigned_receipt.proof.client_ack.signing_bytes()
    der_signature = private_key.sign(signing_bytes, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    p1363 = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    ack_payload["signature"] = base64.urlsafe_b64encode(p1363).rstrip(b"=").decode()
    return (
        ingress,
        offer,
        DeliveryReceiptV2.model_validate(receipt_payload),
        private_key.public_key(),
    )


def test_p256_ack_signs_only_the_sealed_canonical_fields() -> None:
    _, _, receipt, _ = _signed_ack_contract()
    ack = receipt.proof.client_ack
    expected = canonical_json_bytes(
        {
            "requestRef": ack.request_ref,
            "deliveryId": ack.delivery_id,
            "presenceReplySha256": ack.presence_reply_sha256,
            "wireSha256": ack.wire_sha256,
            "challenge": ack.challenge,
        }
    )
    assert ack.signing_bytes() == expected
    assert len(ack.signature_bytes()) == 64
    assert len(ack.signature) == 86
    assert "=" not in ack.signature


def test_p256_ack_host_binding_verifies_before_transaction_bound_claim() -> None:
    ingress, offer, receipt, public_key = _signed_ack_contract()
    calls: list[tuple[str, str]] = []

    def verifier(data: bytes, signature: bytes, key_sha256: str) -> bool:
        calls.append(("verify", key_sha256))
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        try:
            public_key.verify(
                encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256())
            )
        except InvalidSignature:
            return False
        return True

    def claimer(
        request_ref: str,
        delivery_id: str,
        challenge: str,
        ack_proof_sha256: str,
        proposed: DeliveryReceiptV2,
    ) -> ClientAckChallengeClaim:
        assert (request_ref, delivery_id, challenge) == (
            receipt.proof.client_ack.request_ref,
            receipt.delivery_id,
            receipt.proof.client_ack.challenge,
        )
        assert ack_proof_sha256 == canonical_sha256(receipt.proof.client_ack)
        calls.append(("claim", ack_proof_sha256))
        return ClientAckChallengeClaim.model_validate(
            {"status": "claimed", "receipt": proposed.model_dump(mode="json")}
        )

    persisted = receipt.validate_against_offer(
        offer,
        ingress,
        signature_verifier=verifier,
        challenge_claimer=claimer,
    )
    assert persisted == receipt
    assert [name for name, _ in calls] == ["verify", "claim"]


def test_ack_offer_key_and_route_are_bound_to_authenticated_ingress() -> None:
    ingress, offer, _, _ = _signed_ack_contract()
    offer.validate_against_ingress(ingress)

    for field, other in (
        ("installation_id", "installation:other:001"),
        ("channel_id", "channel:other:001"),
        ("request_id", "request:other:001"),
        ("conversation_id", "conversation:other:001"),
    ):
        altered_ingress = ingress.model_copy(update={field: other})
        with pytest.raises(ValueError, match=field.removesuffix("_id")):
            offer.validate_against_ingress(altered_ingress)

    without_key = ingress.model_copy(update={"client_ack_key_sha256": None})
    with pytest.raises(ValueError, match="did not bind"):
        offer.validate_against_ingress(without_key)


def test_self_selected_attacker_key_fails_before_valid_signature_can_close() -> None:
    ingress, _, _, _ = _signed_ack_contract()
    attacker_private = ec.generate_private_key(ec.SECP256R1())
    attacker_public = attacker_private.public_key()
    numbers = attacker_public.public_numbers()
    attacker_jwk = {
        "crv": "P-256",
        "kty": "EC",
        "x": _base64url_encode(numbers.x.to_bytes(32, "big")),
        "y": _base64url_encode(numbers.y.to_bytes(32, "big")),
    }
    attacker_key_sha256 = canonical_sha256(attacker_jwk)
    assert attacker_key_sha256 != ingress.client_ack_key_sha256

    offer_payload = _payload("valid-delivery-offer-v2-client")
    offer_payload["proof"]["ack_key_sha256"] = attacker_key_sha256
    attacker_offer = DeliveryOfferV2.model_validate(offer_payload)
    receipt_payload = _payload("valid-delivery-receipt-v2-client")
    receipt_payload["delivery_offer_sha256"] = canonical_sha256(attacker_offer)
    receipt_payload["proof"]["client_ack"]["public_key_sha256"] = attacker_key_sha256
    unsigned = DeliveryReceiptV2.model_validate(receipt_payload)
    der_signature = attacker_private.sign(
        unsigned.proof.client_ack.signing_bytes(), ec.ECDSA(hashes.SHA256())
    )
    r, s = decode_dss_signature(der_signature)
    p1363 = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    receipt_payload["proof"]["client_ack"]["signature"] = _base64url_encode(p1363)
    attacker_receipt = DeliveryReceiptV2.model_validate(receipt_payload)
    attacker_public.verify(
        encode_dss_signature(r, s),
        attacker_receipt.proof.client_ack.signing_bytes(),
        ec.ECDSA(hashes.SHA256()),
    )

    verifier_called = False

    def verifier(*_args: object) -> bool:
        nonlocal verifier_called
        verifier_called = True
        return True

    with pytest.raises(ValueError, match="authenticated ingress"):
        attacker_receipt.validate_against_offer(
            attacker_offer,
            ingress,
            signature_verifier=verifier,
            challenge_claimer=lambda *_: ClientAckChallengeClaim(
                status="conflict", receipt=None
            ),
        )
    assert verifier_called is False


def test_p256_ack_replay_returns_exact_existing_receipt_and_rejects_conflict() -> None:
    ingress, offer, receipt, _ = _signed_ack_contract()
    stored = receipt.model_copy(
        update={
            "delivery_receipt_id": "delivery-receipt:stored:001",
            "accepted_at": "2026-08-20T12:00:01Z",
        }
    )

    def valid_signature(_data: bytes, _signature: bytes, _key_sha256: str) -> bool:
        return True

    replayed = receipt.validate_against_offer(
        offer,
        ingress,
        signature_verifier=valid_signature,
        challenge_claimer=lambda *_: ClientAckChallengeClaim.model_validate(
            {"status": "already_same", "receipt": stored.model_dump(mode="json")}
        ),
    )
    assert replayed == stored

    with pytest.raises(ValueError, match="conflict"):
        receipt.validate_against_offer(
            offer,
            ingress,
            signature_verifier=valid_signature,
            challenge_claimer=lambda *_: ClientAckChallengeClaim(
                status="conflict", receipt=None
            ),
        )


def test_p256_ack_never_claims_before_signature_and_v1_is_not_runtime_fallback() -> (
    None
):
    ingress, offer, receipt, _ = _signed_ack_contract()
    claimed = False

    def claimer(*_args: object) -> ClientAckChallengeClaim:
        nonlocal claimed
        claimed = True
        return ClientAckChallengeClaim(status="claimed", receipt=receipt)

    with pytest.raises(ValueError, match="signature is invalid"):
        receipt.validate_against_offer(
            offer,
            ingress,
            signature_verifier=lambda *_: False,
            challenge_claimer=claimer,
        )
    assert claimed is False

    assert ACTIVE_CLIENT_ACK_RECEIPT_CONTRACT == "DeliveryReceipt/v2"
    legacy_offer = DeliveryOffer.model_validate(_payload("valid-delivery-offer-client"))
    with pytest.raises(TypeError, match="requires DeliveryOffer/v2"):
        receipt.validate_against_offer(
            legacy_offer,  # type: ignore[arg-type]
            ingress,
            signature_verifier=lambda *_: True,
            challenge_claimer=claimer,
        )

    legacy_receipt = DeliveryReceipt.model_validate(
        _payload("valid-delivery-receipt-client")
    )
    legacy_fields = type(legacy_receipt.proof.client_ack).model_fields
    assert "signature_format" not in legacy_fields
    assert "presence_reply_sha256" not in legacy_fields

    downgraded_v2_offer = offer.model_dump(mode="json")
    downgraded_v2_offer["proof"] = {"mode": "provider"}
    with pytest.raises(ValidationError):
        DeliveryOfferV2.model_validate(downgraded_v2_offer)


def test_connector_initialize_invoke_and_success_bind_full_authority() -> None:
    manifest = ConnectorManifest.model_validate(_payload("valid-connector-manifest"))
    initialize = ConnectorInitializeRequest.model_validate(
        _payload("valid-connector-initialize-request")
    )
    initialize_success = ConnectorInitializeSuccess.model_validate(
        _payload("valid-connector-initialize-success")
    )
    invoke = ConnectorInvokeRequest.model_validate(
        _payload("valid-connector-invoke-request")
    )
    invoke_success = ConnectorInvokeSuccess.model_validate(
        _payload("valid-connector-invoke-success")
    )

    initialize.validate_against_manifest(manifest)
    initialize_success.validate_against_request(initialize)
    invoke.validate_against_initialize(initialize, manifest)
    invoke_success.validate_against_request(invoke, manifest)

    for field, lower_value in (
        ("maximum_protocol_frame_bytes", 524_288),
        ("maximum_protocol_frames_per_invocation", 1),
        ("maximum_stdout_bytes", 2_097_152),
    ):
        lower_budget = manifest.model_copy(update={field: lower_value})
        lower_initialize_payload = initialize.model_dump(mode="json")
        lower_initialize_payload["params"]["connector_manifest_sha256"] = (
            canonical_sha256(lower_budget)
        )
        lower_initialize = ConnectorInitializeRequest.model_validate(
            lower_initialize_payload
        )
        with pytest.raises(ValueError, match="sealed profile"):
            lower_initialize.validate_against_manifest(lower_budget)

    wrong_scope = invoke.model_dump(mode="json")
    wrong_scope["params"]["resource_scope"]["resource_id"] = "calendar:other"
    altered = ConnectorInvokeRequest.model_validate(wrong_scope)
    with pytest.raises(ValueError, match="resource differs"):
        altered.validate_against_initialize(initialize, manifest)

    wrong_method = invoke.model_copy(update={"method": "resource.read"})
    with pytest.raises(ValueError, match="effect class"):
        wrong_method.validate_against_initialize(initialize, manifest)

    for scopes in ([], ["calendar.read"], ["calendar.write", "mail.read"]):
        wrong_scopes = initialize.model_dump(mode="json")
        wrong_scopes["params"]["credential_grant"]["provider_scopes"] = scopes
        with pytest.raises(ValidationError, match="exactly cover"):
            ConnectorInitializeRequest.model_validate(wrong_scopes)

    unconfirmed_scope = initialize.model_dump(mode="json")
    unconfirmed_scope["params"]["connector_binding"]["confirmed_scopes"] = [
        "calendar.read"
    ]
    with pytest.raises(ValidationError, match="verified connector binding"):
        ConnectorInitializeRequest.model_validate(unconfirmed_scope)


def test_connector_success_closes_deadline_outcome_kind_and_output_schema() -> None:
    manifest = ConnectorManifest.model_validate(_payload("valid-connector-manifest"))
    invoke = ConnectorInvokeRequest.model_validate(
        _payload("valid-connector-invoke-request")
    )
    success_payload = _payload("valid-connector-invoke-success")

    late = deepcopy(success_payload)
    late["result"]["completed_at"] = "2026-08-20T12:05:01Z"
    with pytest.raises(ValueError, match="deadline"):
        ConnectorInvokeSuccess.model_validate(late).validate_against_request(
            invoke, manifest
        )

    read_kind_for_write = deepcopy(success_payload)
    read_kind_for_write["result"]["outcome"] = {
        "kind": "read_ok",
        "provider_resource_id": "calendar:event:001",
        "retrieved_at": "2026-08-20T12:00:00Z",
        "response_sha256": "0" * 64,
        "payload": {
            "representation": "inline",
            "output_schema_sha256": "1" * 64,
            "normalized_payload": {"exists": True},
        },
    }
    with pytest.raises(ValueError, match="outcome kind"):
        ConnectorInvokeSuccess.model_validate(
            read_kind_for_write
        ).validate_against_request(invoke, manifest)

    read_manifest_payload = manifest.model_dump(mode="json")
    read_manifest_payload["operations"][0]["writes_provider_state"] = False
    read_manifest = ConnectorManifest.model_validate(read_manifest_payload)
    read_manifest_sha256 = canonical_sha256(read_manifest)
    read_request_payload = invoke.model_dump(mode="json")
    read_request_payload["method"] = "resource.read"
    read_request_payload["params"]["connector_manifest_sha256"] = read_manifest_sha256
    read_request = ConnectorInvokeRequest.model_validate(read_request_payload)

    effect_kind_for_read = deepcopy(success_payload)
    effect_kind_for_read["result"]["connector_manifest_sha256"] = read_manifest_sha256
    effect_kind_for_read["result"]["request_sha256"] = canonical_sha256(
        read_request.params
    )
    with pytest.raises(ValueError, match="outcome kind"):
        ConnectorInvokeSuccess.model_validate(
            effect_kind_for_read
        ).validate_against_request(read_request, read_manifest)

    wrong_output_schema = deepcopy(effect_kind_for_read)
    wrong_output_schema["result"]["outcome"] = {
        "kind": "read_ok",
        "provider_resource_id": "calendar:event:001",
        "retrieved_at": "2026-08-20T12:00:00Z",
        "response_sha256": "0" * 64,
        "payload": {
            "representation": "inline",
            "output_schema_sha256": "0" * 64,
            "normalized_payload": {"exists": True},
        },
    }
    with pytest.raises(ValueError, match="payload schema"):
        ConnectorInvokeSuccess.model_validate(
            wrong_output_schema
        ).validate_against_request(read_request, read_manifest)

    wrong_output_schema["result"]["outcome"]["payload"]["output_schema_sha256"] = (
        "1" * 64
    )
    ConnectorInvokeSuccess.model_validate(wrong_output_schema).validate_against_request(
        read_request, read_manifest
    )


def test_connector_ids_request_hashes_and_error_phase_are_not_replayable() -> None:
    manifest = ConnectorManifest.model_validate(_payload("valid-connector-manifest"))
    initialize = ConnectorInitializeRequest.model_validate(
        _payload("valid-connector-initialize-request")
    )
    invoke = ConnectorInvokeRequest.model_validate(
        _payload("valid-connector-invoke-request")
    )
    invoke_success = ConnectorInvokeSuccess.model_validate(
        _payload("valid-connector-invoke-success")
    )
    error = ConnectorErrorResponse.model_validate(
        _payload("valid-connector-error-response")
    )
    error.validate_against_request(invoke, external_io_may_have_started=False)

    same_phase_id = invoke.model_dump(mode="json")
    same_phase_id["id"] = initialize.id
    same_phase_id["params"]["request_id"] = initialize.id
    same_id_request = ConnectorInvokeRequest.model_validate(same_phase_id)
    with pytest.raises(ValueError, match="phase-unique"):
        same_id_request.validate_against_initialize(initialize, manifest)

    wrong_hash = invoke_success.model_dump(mode="json")
    wrong_hash["result"]["request_sha256"] = "0" * 64
    structurally_valid = ConnectorInvokeSuccess.model_validate(wrong_hash)
    with pytest.raises(ValueError, match="request_sha256"):
        structurally_valid.validate_against_request(invoke, manifest)

    wrong_phase = error.model_copy(update={"id": initialize.id})
    with pytest.raises(ValueError, match="request contract"):
        wrong_phase.validate_against_request(
            initialize, external_io_may_have_started=False
        )
    with pytest.raises(ValueError, match="supervisor I/O phase"):
        error.validate_against_request(invoke, external_io_may_have_started=True)


def test_provider_outcomes_remain_typed_success_not_json_rpc_errors() -> None:
    manifest = ConnectorManifest.model_validate(_payload("valid-connector-manifest"))
    invoke = ConnectorInvokeRequest.model_validate(
        _payload("valid-connector-invoke-request")
    )
    success_payload = _payload("valid-connector-invoke-success")
    success_payload["result"]["outcome"] = {
        "kind": "rate_limited",
        "retry_after_ms": 4000,
        "provider_code": "http_429",
        "evidence_sha256": "0" * 64,
    }
    success = ConnectorInvokeSuccess.model_validate(success_payload)
    success.validate_against_request(invoke, manifest)
    assert success.result.outcome.kind == "rate_limited"


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("initialize", "internal_failure"),
        ("invoke_before_external_io", "internal_failure"),
        ("invoke_external_io_possible", "ambiguous_transport"),
    ],
)
def test_protocol_fatal_classifier_totally_closes_attempt_outcome(
    phase: str, expected: str
) -> None:
    violations = {
        "framing_error",
        "unexpected_eof",
        "frame_too_large",
        "extra_frame",
        "invalid_json",
        "invalid_message",
        "timeout",
        "manifest_mismatch",
        "executable_mismatch",
        "response_id_mismatch",
    }
    for violation in violations:
        classified = classify_connector_protocol_failure(
            request_id="connector-request:001",
            request_sha256="0" * 64,
            phase=phase,  # type: ignore[arg-type]
            violation=violation,  # type: ignore[arg-type]
            evidence_sha256="1" * 64,
            observed_at="2026-08-20T12:00:00Z",
        )
        assert isinstance(classified, ConnectorProtocolFailure)
        assert classified.failure_class == expected
        assert classified.automatic_resend_permitted is False

        invalid = classified.model_dump(mode="json")
        invalid["failure_class"] = (
            "ambiguous_transport"
            if expected == "internal_failure"
            else "internal_failure"
        )
        with pytest.raises(ValidationError):
            ConnectorProtocolFailure.model_validate(invalid)


def test_content_length_corpus_closes_frames_and_stdout_sequences() -> None:
    corpus = _corpus()
    cases = corpus["connectorFrameCases"]
    assert isinstance(cases, list)
    for case in cases:
        wire = bytes.fromhex(case["wireHex"])
        decoder = (
            decode_connector_rpc_frame
            if case["profile"] == "single_frame"
            else decode_connector_rpc_exchange
        )
        if case["expect"] == "accept":
            assert decoder(wire)
        else:
            with pytest.raises((TypeError, ValueError), match=".+"):
                decoder(wire)


def test_content_length_prefix_and_single_frame_have_distinct_trailing_rules() -> None:
    first = ConnectorInitializeRequest.model_validate(
        _payload("valid-connector-initialize-request")
    )
    second = ConnectorInvokeRequest.model_validate(
        _payload("valid-connector-invoke-request")
    )
    first_frame = encode_connector_rpc_frame(first)
    second_frame = encode_connector_rpc_frame(second)
    parsed, remainder = decode_connector_rpc_frame_prefix(first_frame + second_frame)
    assert parsed == first
    assert remainder == second_frame
    with pytest.raises(ValueError, match="trailing bytes"):
        decode_connector_rpc_frame(first_frame + second_frame)
    assert decode_connector_rpc_frame(first_frame) == first


def test_evidence_v2_adds_sequence_authority_without_relaxing_v1() -> None:
    legacy = EvidenceEnvelope.model_validate(_payload("valid-evidence-envelope"))
    sequenced = EvidenceEnvelopeV2.model_validate(
        _payload("valid-evidence-envelope-v2")
    )
    assert sequenced.schema_version == 2
    assert sequenced.sequence == 1
    assert sequenced.tenant_id
    assert sequenced.aggregate_id
    assert sequenced.idempotency_key

    legacy_payload = legacy.model_dump(mode="json")
    legacy_payload["tenant_id"] = sequenced.tenant_id
    with pytest.raises(ValidationError):
        EvidenceEnvelope.model_validate(legacy_payload)

    v2_payload = sequenced.model_dump(mode="json")
    v2_payload["sequence"] = 0
    with pytest.raises(ValidationError):
        EvidenceEnvelopeV2.model_validate(v2_payload)

    later = sequenced.model_dump(mode="json")
    later["sequence"] = 2
    with pytest.raises(ValidationError, match="predecessor"):
        EvidenceEnvelopeV2.model_validate(later)

    genesis_with_predecessor = sequenced.model_dump(mode="json")
    genesis_with_predecessor["predecessor_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="starts a chain"):
        EvidenceEnvelopeV2.model_validate(genesis_with_predecessor)


def test_public_ack_fixture_hash_remains_content_addressed() -> None:
    fixture_bytes = ACK_KEY_PATH.read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == (
        "330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93"
    )
