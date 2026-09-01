"""Portable platform, release, installation, export, and Forge contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import (
    JSON_SAFE_INTEGER_MAX,
    ContractModel,
    Identifier,
    RelativeArtifactPath,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
    Version2Contract,
    Version3Contract,
)


class OciImageLock(ContractModel):
    component: Identifier
    architecture: Literal["amd64", "arm64"]
    validation_state: Literal["selected_for_validation", "validated"]
    image_repository: ShortText | None
    image_digest: str | None = Field(
        pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71
    )
    sbom_sha256: Sha256 | None
    runnable: bool

    @model_validator(mode="after")
    def _runnable_only_after_resolution(self) -> "OciImageLock":
        supplied = (self.image_repository, self.image_digest, self.sbom_sha256)
        resolved = all(value is not None for value in supplied)
        if self.validation_state == "validated" and (not self.runnable or not resolved):
            raise ValueError(
                "validated OCI image must be runnable and digest/SBOM bound"
            )
        if self.validation_state == "selected_for_validation" and (
            self.runnable or any(value is not None for value in supplied)
        ):
            raise ValueError(
                "selected OCI slot is non-runnable and carries no invented artifact data"
            )
        return self


class PlatformTarget(ContractModel):
    operating_system: Literal[
        "Ubuntu Server 24.04.4 LTS", "macOS 15", "Windows 11 24H2"
    ]
    architecture: Literal["amd64", "arm64", "x86_64", "amd64_or_arm64"]
    execution_mode: Literal[
        "containerized", "containerized_not_native", "rootless_podman"
    ]
    container_engine: ShortText
    target_class: Literal["tier1_release_gate", "compatibility_target"]
    validation_state: Literal["unvalidated", "validated"]
    support_status: Literal["tier1", "unsupported"]
    validation_receipt_sha256: Sha256 | None

    @model_validator(mode="after")
    def _combined_arch_is_only_the_podman_target(self) -> "PlatformTarget":
        combined = self.architecture == "amd64_or_arm64"
        podman = self.execution_mode == "rootless_podman"
        if combined != podman:
            raise ValueError(
                "amd64_or_arm64 is reserved for the rootless Podman compatibility target"
            )
        return self


class PlatformLock(Version1Contract):
    contract: Literal["PlatformLock/v1"]
    lock_id: Identifier
    claim_state: Literal["selected_for_validation", "validated"]
    python_version: str = Field(pattern=r"^3\.12\.14$", min_length=7, max_length=7)
    postgres_version: str = Field(pattern=r"^16\.13$", min_length=5, max_length=5)
    dbos_version: str = Field(pattern=r"^2\.30\.0$", min_length=6, max_length=6)
    pydantic_version: str = Field(pattern=r"^2\.12\.5$", min_length=6, max_length=6)
    pydantic_ai_slim_version: str = Field(
        pattern=r"^2\.32\.1$", min_length=6, max_length=6
    )
    tier1_targets: list[PlatformTarget] = Field(min_length=2, max_length=2)
    compatibility_targets: list[PlatformTarget] = Field(min_length=3, max_length=3)
    oci_images: list[OciImageLock] = Field(min_length=1, max_length=64)
    mutable_image_tags_allowed: Literal[False]
    generated_at: Timestamp

    @model_validator(mode="after")
    def _validate_claims(self) -> "PlatformLock":
        _check_platform_lock_claims(self)
        return self


class PlatformLockV2(Version2Contract):
    """Current platform lock without rewriting the sealed v1 dependency set."""

    contract: Literal["PlatformLock/v2"]
    lock_id: Identifier
    claim_state: Literal["selected_for_validation", "validated"]
    python_version: str = Field(pattern=r"^3\.12\.14$", min_length=7, max_length=7)
    postgres_version: str = Field(pattern=r"^16\.13$", min_length=5, max_length=5)
    dbos_version: str = Field(pattern=r"^2\.30\.0$", min_length=6, max_length=6)
    pydantic_version: str = Field(pattern=r"^2\.12\.5$", min_length=6, max_length=6)
    pydantic_ai_slim_version: str = Field(
        pattern=r"^2\.36\.0$", min_length=6, max_length=6
    )
    tier1_targets: list[PlatformTarget] = Field(min_length=2, max_length=2)
    compatibility_targets: list[PlatformTarget] = Field(min_length=3, max_length=3)
    oci_images: list[OciImageLock] = Field(min_length=1, max_length=64)
    mutable_image_tags_allowed: Literal[False]
    generated_at: Timestamp

    @model_validator(mode="after")
    def _validate_claims(self) -> "PlatformLockV2":
        _check_platform_lock_claims(self)
        return self


class PlatformLockV3(Version3Contract):
    """Current platform lock without rewriting the sealed v1/v2 dependency sets."""

    contract: Literal["PlatformLock/v3"]
    lock_id: Identifier
    claim_state: Literal["selected_for_validation", "validated"]
    python_version: str = Field(pattern=r"^3\.12\.14$", min_length=7, max_length=7)
    postgres_version: str = Field(pattern=r"^16\.13$", min_length=5, max_length=5)
    dbos_version: str = Field(pattern=r"^2\.30\.0$", min_length=6, max_length=6)
    pydantic_version: str = Field(pattern=r"^2\.12\.5$", min_length=6, max_length=6)
    pydantic_ai_slim_version: str = Field(
        pattern=r"^2\.37\.0$", min_length=6, max_length=6
    )
    tier1_targets: list[PlatformTarget] = Field(min_length=2, max_length=2)
    compatibility_targets: list[PlatformTarget] = Field(min_length=3, max_length=3)
    oci_images: list[OciImageLock] = Field(min_length=1, max_length=64)
    mutable_image_tags_allowed: Literal[False]
    generated_at: Timestamp

    @model_validator(mode="after")
    def _validate_claims(self) -> "PlatformLockV3":
        _check_platform_lock_claims(self)
        return self


def _check_platform_lock_claims(
    lock: PlatformLock | PlatformLockV2 | PlatformLockV3,
) -> None:
    expected_tier1 = {
        (
            "Ubuntu Server 24.04.4 LTS",
            "amd64",
            "containerized",
            "Docker Engine 29.7.2",
        ),
        (
            "Ubuntu Server 24.04.4 LTS",
            "arm64",
            "containerized",
            "Docker Engine 29.7.2",
        ),
    }
    actual_tier1 = {
        (
            target.operating_system,
            target.architecture,
            target.execution_mode,
            target.container_engine,
        )
        for target in lock.tier1_targets
        if target.target_class == "tier1_release_gate"
    }
    if actual_tier1 != expected_tier1:
        raise ValueError(
            "Tier 1 release gates are exactly the two sealed Ubuntu/Docker targets"
        )
    if any(
        target.target_class != "compatibility_target"
        for target in lock.compatibility_targets
    ):
        raise ValueError("other hosts must remain compatibility targets")
    all_targets = lock.tier1_targets + lock.compatibility_targets
    if lock.claim_state == "selected_for_validation":
        if any(target.validation_state != "unvalidated" for target in all_targets):
            raise ValueError("selected lock cannot claim target validation")
        if any(
            target.support_status != "unsupported"
            or target.validation_receipt_sha256 is not None
            for target in all_targets
        ):
            raise ValueError(
                "selected lock cannot claim support or carry fictional receipts"
            )
        if any(
            image.validation_state != "selected_for_validation"
            for image in lock.oci_images
        ):
            raise ValueError("selected lock cannot contain validated OCI images")
        return
    if any(
        target.validation_state != "validated"
        or target.support_status != "tier1"
        or target.validation_receipt_sha256 is None
        for target in lock.tier1_targets
    ):
        raise ValueError("validated release lock requires both Tier 1 receipts")
    if any(
        target.support_status != "unsupported" for target in lock.compatibility_targets
    ):
        raise ValueError(
            f"{lock.contract.removeprefix('PlatformLock/')} makes no "
            "compatibility-target support claim"
        )
    if any(image.validation_state != "validated" for image in lock.oci_images):
        raise ValueError("validated release lock requires resolved OCI images")


class ReleaseArtifact(ContractModel):
    path: RelativeArtifactPath
    media_type: ShortText
    content_sha256: Sha256
    byte_length: int = Field(ge=1, le=137_438_953_472)


class SignatureBinding(ContractModel):
    signer_id: Identifier
    algorithm: Literal["Ed25519"]
    signed_payload_sha256: Sha256
    signature: str = Field(
        min_length=86, max_length=88, pattern=r"^[A-Za-z0-9_-]+={0,2}$"
    )


class ReleaseManifest(Version1Contract):
    contract: Literal["ReleaseManifest/v1"]
    release_id: Identifier
    release_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$",
    )
    source_tree_sha256: Sha256
    dependency_lock_sha256: Sha256
    platform_lock_sha256: Sha256
    schema_manifest_sha256: Sha256
    artifacts: list[ReleaseArtifact] = Field(min_length=1, max_length=1024)
    sbom_sha256: Sha256
    license_manifest_sha256: Sha256
    revocation_manifest_sha256: Sha256
    signatures: list[SignatureBinding] = Field(min_length=1, max_length=16)
    created_at: Timestamp


class InstallationManifest(Version1Contract):
    contract: Literal["InstallationManifest/v1"]
    installation_id: Identifier
    release_manifest_sha256: Sha256
    platform_lock_sha256: Sha256
    target: PlatformTarget
    identity_pack_sha256: Sha256
    policy_pack_sha256: Sha256
    postgres_schema_revision: Identifier
    configured_connector_binding_ids: list[Identifier] = Field(max_length=256)
    connector_bindings_requiring_authorization: list[Identifier] = Field(max_length=256)
    support_status: Literal["tier1", "unsupported"]
    secrets_included: Literal[False]
    initialized_at: Timestamp

    @model_validator(mode="after")
    def _support_matches_target(self) -> "InstallationManifest":
        if self.support_status == "tier1" and self.target.support_status != "tier1":
            raise ValueError(
                "installation cannot claim support absent a validated target"
            )
        return self


class InstallationRemap(Version1Contract):
    contract: Literal["InstallationRemap/v1"]
    remap_id: Identifier
    source_archive_id: Identifier
    source_installation_id: Identifier
    target_installation_id: Identifier
    active_foreign_key_mapping_sha256: Sha256
    stable_domain_object_ids_preserved: Literal[True]
    old_effects_resumable: Literal[False]
    connector_binding_state: Literal["reauthorization_required"]
    pending_episode_state: Literal["paused_reconcile_required"]
    state: Literal["prepared", "applied", "rejected"]
    signer_id: Identifier
    signature_algorithm: Literal["Ed25519"]
    signed_payload_sha256: Sha256
    signature: str = Field(
        min_length=86, max_length=88, pattern=r"^[A-Za-z0-9_-]+={0,2}$"
    )
    created_at: Timestamp


class ExportRecordSet(ContractModel):
    contract_type: Identifier
    relative_path: RelativeArtifactPath
    contract_schema_sha256: Sha256
    jsonl_sha256: Sha256
    record_count: int = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    minimum_schema_version: int = Field(ge=1, le=65_535)
    maximum_schema_version: int = Field(ge=1, le=65_535)


class ArchiveManifestChunk(ContractModel):
    relative_path: RelativeArtifactPath
    record_schema_sha256: Sha256
    jsonl_sha256: Sha256
    record_count: int = Field(ge=1, le=JSON_SAFE_INTEGER_MAX)


class EvaPortableArchive(Version1Contract):
    contract: Literal["EvaPortableArchive/v1"]
    archive_id: Identifier
    source_installation_id: Identifier
    export_sequence: int = Field(ge=1, le=JSON_SAFE_INTEGER_MAX)
    canonical_profile: Literal["EvaCanonicalJSON/v1"]
    record_sets: list[ExportRecordSet] = Field(min_length=1, max_length=1024)
    json_schema_manifest_sha256: Sha256
    identity_pack_sha256s: list[Sha256] = Field(min_length=1, max_length=128)
    encrypted_object_manifest_chunks: list[ArchiveManifestChunk] = Field(
        max_length=1024
    )
    encrypted_object_count: int = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    pending_state_in_record_sets: Literal[True]
    pending_episode_count: int = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    pending_effect_intent_count: int = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    pending_delivery_count: int = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    checksums_sha256: Sha256
    dbos_data_included: Literal[False]
    credentials_included: Literal[False]
    installation_master_key_included: Literal[False]
    telemetry_included: Literal[False]
    absolute_host_paths_included: Literal[False]
    retention_until: Timestamp
    created_at: Timestamp

    @model_validator(mode="after")
    def _manifest_counts_are_bound(self) -> "EvaPortableArchive":
        if (
            sum(chunk.record_count for chunk in self.encrypted_object_manifest_chunks)
            != self.encrypted_object_count
        ):
            raise ValueError(
                "encrypted object count must equal its content-addressed manifest chunks"
            )
        return self


class DocumentationSource(ContractModel):
    source_id: Identifier
    canonical_url: ShortText
    fetched_sha256: Sha256
    fetched_at: Timestamp
    trust: Literal["vendor_primary", "standards_primary", "operator_approved"]


class DocumentationSnapshot(Version1Contract):
    contract: Literal["DocumentationSnapshot/v1"]
    snapshot_id: Identifier
    requested_capability: Identifier
    sources: list[DocumentationSource] = Field(min_length=1, max_length=128)
    normalized_documentation_sha256: Sha256
    prompt_injection_scan: Literal["passed", "failed"]
    created_at: Timestamp


class ConnectorCandidate(Version1Contract):
    contract: Literal["ConnectorCandidate/v1"]
    candidate_id: Identifier
    documentation_snapshot_sha256: Sha256
    source_bundle_sha256: Sha256
    dependency_lock_sha256: Sha256
    manifest_sha256: Sha256
    executable_sha256: Sha256
    requested_operations: list[Identifier] = Field(min_length=1, max_length=64)
    state: Literal["generated", "quarantined", "rejected", "eligible_for_canary"]
    created_at: Timestamp


class PromotionDecision(Version1Contract):
    contract: Literal["PromotionDecision/v1"]
    decision_id: Identifier
    candidate_id: Identifier
    decision: Literal["reject", "keep_quarantined", "approve_canary", "promote"]
    conformance_evidence_sha256: Sha256
    security_evidence_sha256: Sha256
    canary_evidence_sha256: Sha256 | None
    operator_principal_id: Identifier
    authorization_evidence_ref: Identifier
    decided_at: Timestamp

    @model_validator(mode="after")
    def _promotion_requires_canary_evidence(self) -> "PromotionDecision":
        if self.decision == "promote" and self.canary_evidence_sha256 is None:
            raise ValueError("promotion requires canary evidence")
        return self
