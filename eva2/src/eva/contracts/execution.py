"""Authorization, read, effect, delivery, receipt, and readback contracts."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from .base import (
    ContractModel,
    Identifier,
    JsonValue,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
    Version2Contract,
    validate_json_value,
)
from .canonical_json import canonical_json_bytes, canonical_sha256
from .ingress import DeliveryOffer, DeliveryOfferV2, IngressRequest

Operation: TypeAlias = Identifier


class ResourceScope(ContractModel):
    resource_type: Identifier
    resource_id: ShortText
    scope_sha256: Sha256


class ConsentGrant(Version1Contract):
    contract: Literal["ConsentGrant/v1"]
    consent_grant_id: Identifier
    tenant_id: Identifier
    principal_id: Identifier
    principal_binding_id: Identifier
    authentication_evidence_ref: Identifier
    source_message_ref: Identifier
    operation: Operation
    resource_scope: ResourceScope
    scoped_payload_sha256: Sha256 | None
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    reuse_policy: Literal["single_use", "until_expiry"]
    state: Literal["active", "consumed", "expired", "revoked", "superseded"]
    granted_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _expiry_follows_grant(self) -> "ConsentGrant":
        if self.expires_at <= self.granted_at:
            raise ValueError("consent expiry must follow its grant time")
        return self


class ConnectorBinding(Version1Contract):
    contract: Literal["ConnectorBinding/v1"]
    binding_id: Identifier
    installation_id: Identifier
    tenant_id: Identifier
    owner_principal_id: Identifier
    provider_id: Identifier
    provider_account_id: Identifier
    provider_resource_id: ShortText
    confirmed_operations: list[Operation] = Field(min_length=1, max_length=64)
    confirmed_scopes: list[ShortText] = Field(min_length=1, max_length=64)
    credential_reference: Identifier
    verification_evidence_ref: Identifier
    verified_at: Timestamp
    status: Literal["verified", "disabled", "reauthorization_required", "revoked"]


class CapabilityManifest(Version1Contract):
    contract: Literal["CapabilityManifest/v1"]
    capability_id: Identifier
    connector_id: Identifier
    operation: Operation
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256
    effect_class: Literal[
        "read",
        "effect",
        "confirmation_delivery_effect",
        "outcome_delivery_effect",
        "recovery_read",
        "verification_read",
        "breaker_probe_read",
    ]
    required_provider_scopes: list[ShortText] = Field(max_length=64)
    proof_floor: ShortText
    maximum_freshness_ms: int | None = Field(ge=0, le=86_400_000)
    executable_sha256: Sha256
    sandbox_profile_sha256: Sha256


class CapabilityGrant(Version1Contract):
    contract: Literal["CapabilityGrant/v1"]
    grant_id: Identifier
    tenant_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    capability_id: Identifier
    connector_binding_id: Identifier
    operation: Operation
    resource_scope: ResourceScope
    authorized_payload_sha256: Sha256
    policy_sha256: Sha256
    nonce: Identifier
    issued_at: Timestamp
    deadline: Timestamp

    @model_validator(mode="after")
    def _deadline_follows_issue(self) -> "CapabilityGrant":
        if self.deadline <= self.issued_at:
            raise ValueError("capability deadline must follow issue time")
        return self


class CredentialGrant(Version1Contract):
    contract: Literal["CredentialGrant/v1"]
    credential_grant_id: Identifier
    connector_binding_id: Identifier | None
    model_provider_profile_id: Identifier | None
    worker_id: Identifier
    executable_sha256: Sha256
    provider_scopes: list[ShortText] = Field(max_length=64)
    secret_descriptor_id: Identifier
    nonce: Identifier
    issued_at: Timestamp
    deadline: Timestamp

    @model_validator(mode="after")
    def _one_authority_target(self) -> "CredentialGrant":
        if (self.connector_binding_id is None) == (
            self.model_provider_profile_id is None
        ):
            raise ValueError(
                "exactly one connector binding or model provider profile is required"
            )
        if self.deadline <= self.issued_at:
            raise ValueError("credential deadline must follow issue time")
        return self


class ReadIntent(Version1Contract):
    contract: Literal["ReadIntent/v1"]
    read_intent_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    purpose: Literal[
        "business_read", "effect_reconcile", "effect_verify", "breaker_probe"
    ]
    effect_intent_id: Identifier | None
    operation: Operation
    resource_scope: ResourceScope
    query_sha256: Sha256
    maximum_freshness_ms: int = Field(ge=0, le=86_400_000)
    maximum_attempts: int = Field(ge=1, le=2)
    capability_id: Identifier
    deadline: Timestamp

    @model_validator(mode="after")
    def _bind_recovery_read(self) -> "ReadIntent":
        requires_effect = self.purpose in {"effect_reconcile", "effect_verify"}
        if requires_effect != (self.effect_intent_id is not None):
            raise ValueError(
                "reconcile and verify reads bind an effect; other reads do not"
            )
        expected = {
            "effect_reconcile": "effect.reconcile",
            "effect_verify": "effect.readback",
            "breaker_probe": "connector.probe",
        }.get(self.purpose)
        if expected is not None and self.operation != expected:
            raise ValueError(f"{self.purpose} requires operation {expected}")
        return self


class ReadAttempt(Version1Contract):
    contract: Literal["ReadAttempt/v1"]
    read_attempt_id: Identifier
    read_intent_id: Identifier
    ordinal: int = Field(ge=1, le=2)
    state: Literal[
        "prepared",
        "dispatch_authorized",
        "observed",
        "ambiguous",
        "rejected",
        "rate_limited",
    ]
    capability_grant_id: Identifier
    credential_grant_id: Identifier
    connector_manifest_sha256: Sha256
    request_sha256: Sha256
    response_sha256: Sha256 | None
    deadline: Timestamp
    created_at: Timestamp

    @model_validator(mode="after")
    def _response_presence_matches_state(self) -> "ReadAttempt":
        response_required = self.state in {"observed", "rejected", "rate_limited"}
        if response_required != (self.response_sha256 is not None):
            raise ValueError(
                "read response hash presence must match its terminal provider state"
            )
        return self


class ReadObservation(Version1Contract):
    contract: Literal["ReadObservation/v1"]
    observation_id: Identifier
    read_intent_id: Identifier
    read_attempt_id: Identifier
    provider_resource_id: ShortText
    retrieved_at: Timestamp
    observed_at: Timestamp
    normalized_value: JsonValue
    response_sha256: Sha256
    trust: Literal["untrusted_external", "verified_external"]
    evidence_ref: Identifier

    @model_validator(mode="after")
    def _validate_value(self) -> "ReadObservation":
        validate_json_value(self.normalized_value)
        return self


class EffectIntent(Version1Contract):
    contract: Literal["EffectIntent/v1"]
    effect_intent_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    plan_step_id: Identifier
    connector_binding_id: Identifier
    capability_id: Identifier
    operation: Operation
    resource_scope: ResourceScope
    frozen_payload_sha256: Sha256
    idempotency_key: Identifier
    proof_floor: ShortText
    state: Literal[
        "prepared",
        "dispatch_authorized",
        "waiting_breaker",
        "ambiguous",
        "not_observed",
        "provider_accepted",
        "verified",
        "accepted_unverified",
        "provider_rejected",
        "manual_reconciliation",
        "blocked",
        "superseded",
        "cancelled",
    ]
    supersedes_effect_intent_id: Identifier | None
    deadline: Timestamp


class EffectAttempt(Version1Contract):
    contract: Literal["EffectAttempt/v1"]
    effect_attempt_id: Identifier
    effect_intent_id: Identifier
    ordinal: int = Field(ge=1, le=2)
    state: Literal[
        "reserved",
        "dispatch_authorized",
        "provider_accepted",
        "definitive_reject",
        "ambiguous_transport",
        "rate_limited",
        "policy_denied",
        "hash_conflict",
        "internal_failure",
    ]
    dispatch_permit_sha256: Sha256 | None
    capability_grant_id: Identifier
    credential_grant_id: Identifier
    connector_manifest_sha256: Sha256
    request_sha256: Sha256
    response_sha256: Sha256 | None
    error_class: Literal[
        "none",
        "definitive_reject",
        "ambiguous_transport",
        "rate_limited",
        "policy_denied",
        "hash_conflict",
        "internal_failure",
    ]
    deadline: Timestamp
    created_at: Timestamp

    @model_validator(mode="after")
    def _state_fields_are_earned(self) -> "EffectAttempt":
        externally_dispatched = {
            "dispatch_authorized",
            "provider_accepted",
            "definitive_reject",
            "ambiguous_transport",
            "rate_limited",
        }
        pre_dispatch_terminal = {"policy_denied", "hash_conflict", "internal_failure"}
        if self.state in externally_dispatched and self.dispatch_permit_sha256 is None:
            raise ValueError("a dispatched attempt requires its committed permit hash")
        if self.state == "reserved" or self.state in pre_dispatch_terminal:
            if self.dispatch_permit_sha256 is not None:
                raise ValueError(
                    "a non-dispatched attempt cannot claim a dispatch permit"
                )
        expected_error = (
            "none"
            if self.state in {"reserved", "dispatch_authorized", "provider_accepted"}
            else self.state
        )
        if self.error_class != expected_error:
            raise ValueError("error_class must match the attempt state")
        response_required = self.state in {
            "provider_accepted",
            "definitive_reject",
            "rate_limited",
        }
        if response_required != (self.response_sha256 is not None):
            raise ValueError(
                "effect response hash presence must match the provider response state"
            )
        return self


class EffectReceipt(Version1Contract):
    contract: Literal["EffectReceipt/v1"]
    receipt_id: Identifier
    effect_intent_id: Identifier
    effect_attempt_id: Identifier
    decision: Literal["accepted", "rejected"]
    provider_resource_id: ShortText | None
    provider_receipt_sha256: Sha256
    request_payload_sha256: Sha256
    provider_time: Timestamp
    evidence_ref: Identifier

    @model_validator(mode="after")
    def _provider_resource_is_earned(self) -> "EffectReceipt":
        if self.decision == "accepted" and self.provider_resource_id is None:
            raise ValueError("accepted receipt requires a provider resource ID")
        if self.decision == "rejected" and self.provider_resource_id is not None:
            raise ValueError("rejected receipt cannot claim a provider resource ID")
        return self


class EffectReadback(Version1Contract):
    contract: Literal["EffectReadback/v1"]
    readback_id: Identifier
    effect_intent_id: Identifier
    read_intent_id: Identifier
    read_attempt_id: Identifier
    provider_resource_id: ShortText
    idempotency_key: Identifier
    request_sha256: Sha256
    observed_at: Timestamp
    state_sha256: Sha256
    match_status: Literal["matches", "not_observed", "conflict", "inconclusive"]
    evidence_ref: Identifier


class DeliveryIntent(Version1Contract):
    contract: Literal["DeliveryIntent/v1"]
    delivery_id: Identifier
    effect_intent_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    role: Literal["confirmation_delivery", "outcome_delivery"]
    state: Literal[
        "prepared",
        "dispatch_authorized",
        "provider_accepted",
        "acknowledged",
        "ambiguous",
        "failed_terminal",
        "superseded",
        "cancelled",
    ]
    operation: Operation
    recipient: ShortText
    channel: Identifier
    frozen_content_sha256: Sha256
    reply_to: ShortText | None
    proof_floor: ShortText


class ClientAckProof(ContractModel):
    algorithm: Identifier
    request_ref: Identifier
    delivery_id: Identifier
    content_sha256: Sha256
    wire_sha256: Sha256
    challenge: Identifier
    signature: str = Field(
        min_length=86, max_length=128, pattern=r"^[A-Za-z0-9_-]+={0,2}$"
    )
    public_key_sha256: Sha256


class ProviderDeliveryProof(ContractModel):
    mode: Literal["provider"]
    provider_message_id: ShortText
    provider_receipt_sha256: Sha256


class ServerDeliveryProof(ContractModel):
    mode: Literal["server"]
    provider_message_id: ShortText
    provider_receipt_sha256: Sha256
    independent_readback_id: Identifier


class ClientDeliveryProof(ContractModel):
    mode: Literal["client"]
    client_ack: ClientAckProof


DeliveryProof = Annotated[
    ProviderDeliveryProof | ServerDeliveryProof | ClientDeliveryProof,
    Field(discriminator="mode"),
]


class DeliveryReceipt(Version1Contract):
    contract: Literal["DeliveryReceipt/v1"]
    delivery_receipt_id: Identifier
    delivery_id: Identifier
    delivery_offer_sha256: Sha256
    content_sha256: Sha256
    wire_sha256: Sha256
    effect_attempt_id: Identifier
    role: Literal["confirmation_delivery", "outcome_delivery"]
    accepted_at: Timestamp
    proof: DeliveryProof

    @model_validator(mode="after")
    def _client_ack_binds_receipt(self) -> "DeliveryReceipt":
        if self.proof.mode != "client":
            return self
        ack = self.proof.client_ack
        if ack.delivery_id != self.delivery_id:
            raise ValueError("client ACK must bind this delivery")
        if ack.content_sha256 != self.content_sha256:
            raise ValueError("client ACK must bind the receipt content")
        if ack.wire_sha256 != self.wire_sha256:
            raise ValueError("client ACK must bind the receipt wire bytes")
        return self

    def validate_against_offer(self, offer: DeliveryOffer) -> None:
        """Apply the cross-contract proof binding required by the delivery host."""

        if self.delivery_offer_sha256 != canonical_sha256(offer):
            raise ValueError("delivery receipt must bind the selected offer bytes")
        if self.delivery_id != offer.delivery_id:
            raise ValueError(
                "delivery receipt and selected offer identify another delivery"
            )
        if self.content_sha256 != offer.content_sha256:
            raise ValueError("delivery receipt content differs from the selected offer")
        if self.wire_sha256 != offer.wire_sha256:
            raise ValueError(
                "delivery receipt wire bytes differ from the selected offer"
            )
        if self.proof.mode != offer.proof.mode:
            raise ValueError(
                "delivery receipt proof mode differs from the selected offer"
            )
        if self.proof.mode != "client":
            return

        ack = self.proof.client_ack
        offered_proof = offer.proof
        if ack.request_ref != offer.request_id:
            raise ValueError("client ACK request differs from the selected offer")
        if ack.challenge != offered_proof.challenge:
            raise ValueError("client ACK challenge differs from the selected offer")
        if ack.algorithm != offered_proof.ack_algorithm:
            raise ValueError("client ACK algorithm differs from the selected offer")
        if ack.public_key_sha256 != offered_proof.ack_key_sha256:
            raise ValueError("client ACK key differs from the selected offer")


def _decode_p1363_base64url(value: str) -> bytes:
    """Decode the one canonical unpadded base64url form of a 64-byte signature."""

    try:
        decoded = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("client ACK signature is not canonical base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != 64 or canonical != value:
        raise ValueError(
            "client ACK signature must be canonical unpadded base64url for 64 P1363 bytes"
        )
    return decoded


class P256ClientAckProof(ContractModel):
    """Exact ES256 proof; the signed bytes are fixed by ``signing_bytes``."""

    algorithm: Literal["P-256 ES256"]
    signature_format: Literal["P1363"]
    request_ref: Identifier
    delivery_id: Identifier
    presence_reply_sha256: Sha256
    wire_sha256: Sha256
    challenge: Identifier
    signature: str = Field(min_length=86, max_length=86, pattern=r"^[A-Za-z0-9_-]{86}$")
    public_key_sha256: Sha256

    @field_validator("signature")
    @classmethod
    def _signature_is_canonical_p1363_base64url(cls, value: str) -> str:
        _decode_p1363_base64url(value)
        return value

    def signing_bytes(self) -> bytes:
        """Return the exact sealed canonical bytes covered by ES256."""

        return canonical_json_bytes(
            {
                "challenge": self.challenge,
                "deliveryId": self.delivery_id,
                "presenceReplySha256": self.presence_reply_sha256,
                "requestRef": self.request_ref,
                "wireSha256": self.wire_sha256,
            }
        )

    def signature_bytes(self) -> bytes:
        return _decode_p1363_base64url(self.signature)


class P256ClientDeliveryProof(ContractModel):
    mode: Literal["client"]
    client_ack: P256ClientAckProof


P256SignatureVerifier: TypeAlias = Callable[[bytes, bytes, Sha256], bool]
ACTIVE_CLIENT_ACK_RECEIPT_CONTRACT = "DeliveryReceipt/v2"


class DeliveryReceiptV2(Version2Contract):
    """Receipt with a downgrade-closed, host-verified client ACK profile."""

    contract: Literal["DeliveryReceipt/v2"]
    delivery_receipt_id: Identifier
    delivery_id: Identifier
    delivery_offer_sha256: Sha256
    content_sha256: Sha256
    wire_sha256: Sha256
    effect_attempt_id: Identifier
    role: Literal["confirmation_delivery", "outcome_delivery"]
    accepted_at: Timestamp
    proof: P256ClientDeliveryProof

    @model_validator(mode="after")
    def _client_ack_binds_receipt(self) -> "DeliveryReceiptV2":
        ack = self.proof.client_ack
        if ack.delivery_id != self.delivery_id:
            raise ValueError("client ACK must bind this delivery")
        if ack.presence_reply_sha256 != self.content_sha256:
            raise ValueError("client ACK must bind the PresenceReply content")
        if ack.wire_sha256 != self.wire_sha256:
            raise ValueError("client ACK must bind the receipt wire bytes")
        return self

    def validate_against_offer(
        self,
        offer: DeliveryOfferV2,
        ingress: IngressRequest,
        *,
        signature_verifier: P256SignatureVerifier | None = None,
        challenge_claimer: ClientAckChallengeClaimer | None = None,
    ) -> DeliveryReceiptV2:
        """Bind a v2 receipt to its offer and verify the selected ACK proof."""

        if not isinstance(offer, DeliveryOfferV2):
            raise TypeError("DeliveryReceipt/v2 requires DeliveryOffer/v2")
        offer.validate_against_ingress(ingress)
        if self.delivery_offer_sha256 != canonical_sha256(offer):
            raise ValueError("delivery receipt must bind the selected offer bytes")
        if self.delivery_id != offer.delivery_id:
            raise ValueError(
                "delivery receipt and selected offer identify another delivery"
            )
        if self.content_sha256 != offer.content_sha256:
            raise ValueError("delivery receipt content differs from the selected offer")
        if self.wire_sha256 != offer.wire_sha256:
            raise ValueError(
                "delivery receipt wire bytes differ from the selected offer"
            )
        if not offer.offered_at <= self.accepted_at <= offer.expires_at:
            raise ValueError("delivery receipt falls outside the selected offer window")

        ack = self.proof.client_ack
        offered_proof = offer.proof
        if ack.request_ref != offer.request_id:
            raise ValueError("client ACK request differs from the selected offer")
        if ack.challenge != offered_proof.challenge:
            raise ValueError("client ACK challenge differs from the selected offer")
        if ack.algorithm != offered_proof.ack_algorithm:
            raise ValueError("client ACK algorithm differs from the selected offer")
        if ack.signature_format != offered_proof.signature_format:
            raise ValueError(
                "client ACK signature format differs from the selected offer"
            )
        if ack.public_key_sha256 != offered_proof.ack_key_sha256:
            raise ValueError("client ACK key differs from the selected offer")
        if signature_verifier is None:
            raise ValueError("client ACK requires the host P-256 signature verifier")
        if not signature_verifier(
            ack.signing_bytes(), ack.signature_bytes(), ack.public_key_sha256
        ):
            raise ValueError("client ACK P-256/SHA-256 signature is invalid")
        if challenge_claimer is None:
            raise ValueError("client ACK requires a transaction-bound challenge claim")
        ack_proof_sha256 = canonical_sha256(ack)
        claim = challenge_claimer(
            ack.request_ref,
            ack.delivery_id,
            ack.challenge,
            ack_proof_sha256,
            self,
        )
        if not isinstance(claim, ClientAckChallengeClaim):
            raise TypeError("challenge claimer returned an invalid claim result")
        if claim.status in {"conflict", "expired"}:
            raise ValueError(f"client ACK challenge claim is {claim.status}")
        persisted = claim.receipt
        if persisted is None:
            raise ValueError(
                "successful challenge claim did not return its durable receipt"
            )
        if claim.status == "claimed" and persisted != self:
            raise ValueError(
                "fresh challenge claim must persist the proposed receipt exactly"
            )
        if persisted.delivery_offer_sha256 != self.delivery_offer_sha256:
            raise ValueError("persisted client ACK receipt binds another offer")
        if persisted.delivery_id != self.delivery_id:
            raise ValueError("persisted client ACK receipt binds another delivery")
        if persisted.content_sha256 != self.content_sha256:
            raise ValueError("persisted client ACK receipt binds other content")
        if persisted.wire_sha256 != self.wire_sha256:
            raise ValueError("persisted client ACK receipt binds other wire bytes")
        if persisted.effect_attempt_id != self.effect_attempt_id:
            raise ValueError(
                "persisted client ACK receipt binds another effect attempt"
            )
        if persisted.role != self.role:
            raise ValueError("persisted client ACK receipt binds another delivery role")
        if canonical_sha256(persisted.proof.client_ack) != ack_proof_sha256:
            raise ValueError("persisted receipt binds another client ACK proof")
        if not offer.offered_at <= persisted.accepted_at <= offer.expires_at:
            raise ValueError(
                "persisted client ACK receipt falls outside its offer window"
            )
        return persisted


class ClientAckChallengeClaim(ContractModel):
    """Result of the transaction that claims a one-time ACK challenge."""

    status: Literal["claimed", "already_same", "conflict", "expired"]
    receipt: DeliveryReceiptV2 | None

    @model_validator(mode="after")
    def _receipt_presence_matches_status(self) -> "ClientAckChallengeClaim":
        success = self.status in {"claimed", "already_same"}
        if success != (self.receipt is not None):
            raise ValueError(
                "successful challenge claims alone return a durable receipt"
            )
        return self


ClientAckChallengeClaimer: TypeAlias = Callable[
    [Identifier, Identifier, Identifier, Sha256, DeliveryReceiptV2],
    ClientAckChallengeClaim,
]
