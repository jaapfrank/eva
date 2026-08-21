"""Evidence metadata and claim binding contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import (
    ContractModel,
    Identifier,
    JSON_SAFE_INTEGER_MAX,
    MimeType,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
    Version2Contract,
)


class ObjectRef(Version1Contract):
    contract: Literal["ObjectRef/v1"]
    object_id: Identifier
    ciphertext_sha256: Sha256
    plaintext_sha256: Sha256
    byte_length: int = Field(ge=0, le=1_073_741_824)
    mime_type: MimeType
    encryption_algorithm: Literal["XChaCha20-Poly1305"]
    wrapped_key_ref: Identifier
    retention_until: Timestamp


class ObjectStore(Version1Contract):
    contract: Literal["ObjectStore/v1"]
    object_store_id: Identifier
    installation_id: Identifier
    backend: Literal["encrypted_local_immutable_filesystem"]
    state: Literal["design_only_not_runnable", "active", "read_only", "unavailable"]
    encryption_algorithm: Literal["XChaCha20-Poly1305"]
    key_authority_ref: Identifier
    maximum_object_bytes: int = Field(ge=1, le=1_073_741_824)
    integrity_root_sha256: Sha256 | None
    absolute_host_path_in_contract: Literal[False]
    verified_at: Timestamp | None

    @model_validator(mode="after")
    def _state_fields_are_coherent(self) -> "ObjectStore":
        if self.state == "design_only_not_runnable" and (
            self.integrity_root_sha256 is not None or self.verified_at is not None
        ):
            raise ValueError(
                "design-only object store cannot contain runtime verification"
            )
        if self.state in {"active", "read_only"} and (
            self.integrity_root_sha256 is None or self.verified_at is None
        ):
            raise ValueError("usable object store requires verified integrity state")
        return self


class EvidenceEnvelope(Version1Contract):
    contract: Literal["EvidenceEnvelope/v1"]
    evidence_id: Identifier
    producer: Identifier
    subject_contract: Identifier
    subject_id: Identifier
    subject_payload_sha256: Sha256
    object_ref_id: Identifier | None
    object_sha256: Sha256 | None
    predecessor_sha256: Sha256 | None
    provenance_refs: list[Identifier] = Field(max_length=128)
    captured_at: Timestamp
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    retention_until: Timestamp

    @model_validator(mode="after")
    def _object_reference_is_complete(self) -> "EvidenceEnvelope":
        if (self.object_ref_id is None) != (self.object_sha256 is None):
            raise ValueError(
                "object reference and object hash must be present together"
            )
        return self


class EvidenceEnvelopeV2(Version2Contract):
    """Sequenced evidence authority without changing the sealed v1 wire shape."""

    contract: Literal["EvidenceEnvelope/v2"]
    evidence_id: Identifier
    tenant_id: Identifier
    aggregate_type: Identifier
    aggregate_id: Identifier
    sequence: int = Field(ge=1, le=JSON_SAFE_INTEGER_MAX)
    idempotency_key: Identifier
    producer: Identifier
    subject_contract: Identifier
    subject_id: Identifier
    subject_payload_sha256: Sha256
    object_ref_id: Identifier | None
    object_sha256: Sha256 | None
    predecessor_sha256: Sha256 | None
    provenance_refs: list[Identifier] = Field(max_length=128)
    captured_at: Timestamp
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    retention_until: Timestamp

    @model_validator(mode="after")
    def _object_reference_is_complete(self) -> "EvidenceEnvelopeV2":
        if (self.object_ref_id is None) != (self.object_sha256 is None):
            raise ValueError(
                "object reference and object hash must be present together"
            )
        if (self.sequence == 1) != (self.predecessor_sha256 is None):
            raise ValueError(
                "evidence sequence one starts a chain; later sequences require predecessor"
            )
        return self


class ClaimEvidenceBinding(Version1Contract):
    contract: Literal["ClaimEvidenceBinding/v1"]
    binding_id: Identifier
    claim_ref: Identifier
    outcome_view_id: Identifier
    evidence_refs: list[Identifier] = Field(min_length=1, max_length=64)
    proof_floor: ShortText
    verdict: Literal["proven", "partial", "ambiguous", "blocked", "failed"]
    bound_at: Timestamp


class EvidenceArtifact(ContractModel):
    artifact_id: Identifier
    artifact_class: Literal[
        "source",
        "dependency_lock",
        "schema",
        "fixture_before",
        "fixture_after",
        "model_request",
        "model_response",
        "effect_receipt",
        "effect_readback",
        "delivery_proof",
        "crash_trace",
        "export",
        "restore",
        "rollback",
    ]
    content_sha256: Sha256
    object_ref_id: Identifier | None
    captured_at: Timestamp


class EvaAcceptanceEvidenceBundle(Version1Contract):
    contract: Literal["EvaAcceptanceEvidenceBundle/v1"]
    bundle_id: Identifier
    run_class: Literal["sim", "core_live", "native_live", "resident_long"]
    release_manifest_sha256: Sha256
    source_commit_sha256: Sha256
    dependency_lock_sha256: Sha256
    schema_manifest_sha256: Sha256
    fixture_sha256: Sha256
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    identity_pack_sha256: Sha256
    policy_pack_sha256: Sha256
    artifacts: list[EvidenceArtifact] = Field(min_length=1, max_length=1024)
    tests_only: bool
    live_readback_complete: bool
    created_at: Timestamp
