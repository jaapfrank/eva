"""Authenticated, channel-neutral ingress contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import (
    ContractModel,
    Identifier,
    NaturalText,
    Sha256,
    Timestamp,
    Version1Contract,
    Version2Contract,
)


class TurnEnvelope(Version1Contract):
    contract: Literal["TurnEnvelope/v1"]
    installation_id: Identifier
    tenant_id: Identifier
    principal_id: Identifier
    principal_binding_id: Identifier
    authentication_evidence_sha256: Sha256
    channel_id: Identifier
    request_id: Identifier
    message_id: Identifier
    conversation_id: Identifier
    content_type: Literal["text/plain", "application/json"]
    text: NaturalText
    raw_bytes_sha256: Sha256
    received_at: Timestamp


class IngressRequest(Version1Contract):
    contract: Literal["IngressRequest/v1"]
    installation_id: Identifier
    tenant_id: Identifier
    channel_id: Identifier
    request_id: Identifier
    conversation_id: Identifier
    turn_envelope_sha256: Sha256
    canonical_input_sha256: Sha256
    client_ack_key_sha256: Sha256 | None
    admitted_deadline: Timestamp


class IngressClaim(Version1Contract):
    contract: Literal["IngressClaim/v1"]
    installation_id: Identifier
    channel_id: Identifier
    request_id: Identifier
    claimant_id: Identifier
    claim_nonce: Identifier
    claimed_at: Timestamp
    expires_at: Timestamp


class IngressOutcome(Version1Contract):
    contract: Literal["IngressOutcome/v1"]
    status: Literal["admitted", "duplicate", "conflict", "rejected"]
    installation_id: Identifier
    channel_id: Identifier
    request_id: Identifier
    canonical_input_sha256: Sha256
    episode_id: Identifier | None
    generation: int | None = Field(ge=1, le=2_147_483_647)
    pending_attention_id: Identifier | None
    rejection_code: (
        Literal[
            "authentication_failed",
            "authorization_failed",
            "payload_invalid",
            "payload_too_large",
            "request_hash_conflict",
            "unsupported_contract",
        ]
        | None
    )
    decided_at: Timestamp

    @model_validator(mode="after")
    def _state_fields_are_coherent(self) -> "IngressOutcome":
        accepted = self.status in {"admitted", "duplicate"}
        if accepted and (
            self.episode_id is None
            or self.generation is None
            or self.pending_attention_id is None
            or self.rejection_code is not None
        ):
            raise ValueError(
                "admitted/duplicate outcomes require episode, generation and the canonical attention item"
            )
        if not accepted and (
            self.episode_id is not None
            or self.generation is not None
            or self.pending_attention_id is not None
        ):
            raise ValueError("conflict/rejected outcomes cannot claim admitted work")
        if self.status == "conflict" and self.rejection_code != "request_hash_conflict":
            raise ValueError("conflict requires request_hash_conflict")
        if self.status == "rejected" and self.rejection_code in {
            None,
            "request_hash_conflict",
        }:
            raise ValueError("rejected requires a non-conflict rejection code")
        return self


class ProviderProofOffer(ContractModel):
    mode: Literal["provider"]


class ServerProofOffer(ContractModel):
    mode: Literal["server"]


class ClientProofOffer(ContractModel):
    mode: Literal["client"]
    challenge: Identifier
    ack_algorithm: Identifier
    ack_key_sha256: Sha256


DeliveryProofOffer = Annotated[
    ProviderProofOffer | ServerProofOffer | ClientProofOffer,
    Field(discriminator="mode"),
]


class DeliveryOffer(Version1Contract):
    contract: Literal["DeliveryOffer/v1"]
    delivery_id: Identifier
    installation_id: Identifier
    channel_id: Identifier
    request_id: Identifier
    conversation_id: Identifier
    content_sha256: Sha256
    wire_sha256: Sha256
    proof: DeliveryProofOffer
    offered_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _challenge_expiry_follows_offer(self) -> "DeliveryOffer":
        if self.expires_at <= self.offered_at:
            raise ValueError("ACK challenge expiry must follow its offer")
        return self


class P256ClientProofOffer(ContractModel):
    """Closed client-ACK offer for the sealed P-256/SHA-256 profile."""

    mode: Literal["client"]
    challenge: Identifier
    ack_algorithm: Literal["P-256 ES256"]
    ack_key_sha256: Sha256
    signature_format: Literal["P1363"]


class DeliveryOfferV2(Version2Contract):
    """Delivery offer whose client mode cannot negotiate away from P-256."""

    contract: Literal["DeliveryOffer/v2"]
    delivery_id: Identifier
    installation_id: Identifier
    channel_id: Identifier
    request_id: Identifier
    conversation_id: Identifier
    content_sha256: Sha256
    wire_sha256: Sha256
    proof: P256ClientProofOffer
    offered_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _challenge_expiry_follows_offer(self) -> "DeliveryOfferV2":
        if self.expires_at <= self.offered_at:
            raise ValueError("ACK challenge expiry must follow its offer")
        return self

    def validate_against_ingress(self, ingress: IngressRequest) -> None:
        """Bind the offer and ACK key to the authenticated admitted request."""

        if not isinstance(ingress, IngressRequest):
            raise TypeError("DeliveryOffer/v2 requires IngressRequest/v1")
        for offer_value, ingress_value, label in (
            (self.installation_id, ingress.installation_id, "installation"),
            (self.channel_id, ingress.channel_id, "channel"),
            (self.request_id, ingress.request_id, "request"),
            (self.conversation_id, ingress.conversation_id, "conversation"),
        ):
            if offer_value != ingress_value:
                raise ValueError(f"delivery offer {label} differs from ingress")
        if ingress.client_ack_key_sha256 is None:
            raise ValueError("authenticated ingress did not bind a client ACK key")
        if self.proof.ack_key_sha256 != ingress.client_ack_key_sha256:
            raise ValueError(
                "delivery offer ACK key differs from authenticated ingress"
            )
