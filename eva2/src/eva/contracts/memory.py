"""Canonical relationship, experience, memory, and improvement contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import (
    ContractModel,
    Identifier,
    JsonValue,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
    validate_json_value,
)


class RelationshipBinding(Version1Contract):
    contract: Literal["RelationshipBinding/v1"]
    relationship_binding_id: Identifier
    installation_id: Identifier
    tenant_id: Identifier
    identity_id: Identifier
    principal_id: Identifier
    related_principal_id: Identifier
    relationship_class: Identifier
    preferred_address: ShortText
    source_event_id: Identifier
    trust: Literal["direct_user_confirmed", "verified_authority"]
    confirmation_status: Literal["confirmed", "superseded", "revoked"]
    valid_from: Timestamp
    valid_until: Timestamp | None
    allowed_channel_ids: list[Identifier] = Field(max_length=64)
    channel_binding_evidence_refs: list[Identifier] = Field(min_length=1, max_length=64)
    privacy_class: Literal["internal", "confidential", "restricted"]
    context_scopes: list[Identifier] = Field(min_length=1, max_length=64)
    retention_until: Timestamp
    delete_sla_at: Timestamp


class ExperienceSourceEvent(Version1Contract):
    contract: Literal["ExperienceSourceEvent/v1"]
    source_event_id: Identifier
    tenant_id: Identifier
    principal_id: Identifier | None
    subject_id: Identifier
    event_type: Identifier
    source_kind: Literal[
        "direct_user_input",
        "direct_user_correction",
        "verified_connector_observation",
        "model_inference",
        "legacy_unverified",
        "untrusted_external_content",
    ]
    source_ref: Identifier
    content_sha256: Sha256
    provenance_refs: list[Identifier] = Field(min_length=1, max_length=128)
    observed_at: Timestamp
    valid_from: Timestamp
    valid_until: Timestamp | None
    ingested_at: Timestamp
    trust: Literal[
        "confirmed_direct_user",
        "verified_external",
        "model_inference_unconfirmed",
        "legacy_unverified",
        "untrusted_external",
    ]
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    purpose: Identifier
    retention_until: Timestamp
    delete_sla_at: Timestamp
    corrects_source_event_id: Identifier | None
    supersedes_source_event_id: Identifier | None
    retracts_source_event_id: Identifier | None
    erasure_request_id: Identifier | None


class MemoryFact(Version1Contract):
    contract: Literal["MemoryFact/v1"]
    fact_id: Identifier
    tenant_id: Identifier
    subject_id: Identifier
    predicate: Identifier
    value: JsonValue
    source_event_ids: list[Identifier] = Field(min_length=1, max_length=128)
    observed_at: Timestamp
    valid_from: Timestamp
    valid_until: Timestamp | None
    trust: Literal[
        "confirmed_direct_user",
        "verified_external",
        "model_inference_unconfirmed",
        "legacy_unverified",
    ]
    content_sha256: Sha256
    assertion_status: Literal["active", "superseded", "retracted"]
    superseded_by_fact_id: Identifier | None
    retraction_source_event_id: Identifier | None

    @model_validator(mode="after")
    def _status_references_are_coherent(self) -> "MemoryFact":
        validate_json_value(self.value)
        if (self.assertion_status == "superseded") != (
            self.superseded_by_fact_id is not None
        ):
            raise ValueError("superseded fact requires exactly one successor")
        if (self.assertion_status == "retracted") != (
            self.retraction_source_event_id is not None
        ):
            raise ValueError("retracted fact requires its retraction source event")
        return self


class RecalledFact(ContractModel):
    rank: int = Field(ge=1, le=1024)
    fact_id: Identifier
    source_event_ids: list[Identifier] = Field(min_length=1, max_length=128)
    trust: Literal[
        "confirmed_direct_user",
        "verified_external",
        "model_inference_unconfirmed",
        "legacy_unverified",
    ]
    ranking_reason: ShortText


class MemoryRecall(Version1Contract):
    contract: Literal["MemoryRecall/v1"]
    recall_id: Identifier
    query_purpose: Identifier
    tenant_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier | None
    project_id: Identifier | None
    requested_valid_at: Timestamp
    admitted_fact_ids_sha256: Sha256
    selected: list[RecalledFact] = Field(max_length=256)
    excluded_reasons: dict[Identifier, ShortText] = Field(max_length=1024)
    projection_version: Identifier
    scope_sha256: Sha256
    created_at: Timestamp

    @model_validator(mode="after")
    def _scope_and_ranks_are_closed(self) -> "MemoryRecall":
        if self.conversation_id is None and self.project_id is None:
            raise ValueError("recall requires a conversation or project scope")
        ranks = [item.rank for item in self.selected]
        if len(ranks) != len(set(ranks)):
            raise ValueError("recall ranks must be unique")
        return self


class ImprovementBudgets(ContractModel):
    privacy_policy_sha256: Sha256
    maximum_effect_attempts: int = Field(ge=0, le=8)
    maximum_connector_calls: int = Field(ge=0, le=128)
    maximum_provider_cost_euro_micros: int = Field(ge=0, le=1_000_000_000)


class ImprovementCandidate(Version1Contract):
    contract: Literal["ImprovementCandidate/v1"]
    candidate_id: Identifier
    problem_statement: ShortText
    source_evidence_refs: list[Identifier] = Field(min_length=1, max_length=128)
    bounded_hypothesis: ShortText
    changed_artifact_sha256s: dict[Identifier, Sha256] = Field(
        min_length=1, max_length=128
    )
    benchmark_case_ids: list[Identifier] = Field(min_length=1, max_length=128)
    hidden_transfer_case_id: Identifier
    forbidden_regressions: list[ShortText] = Field(min_length=1, max_length=128)
    budgets: ImprovementBudgets
    candidate_configuration_sha256: Sha256
    rollback_release_manifest_sha256: Sha256
    scope_widening: Literal[False]
    credential_change: Literal[False]
    policy_widening: Literal[False]
    retention_widening: Literal[False]
    effect_class_widening: Literal[False]
    benchmark_or_judge_changed: Literal[False]
    production_mutation_authorized: Literal[False]
    state: Literal[
        "proposed",
        "evaluated",
        "canary_eligible",
        "canary",
        "promoted",
        "rejected",
        "rolled_back",
    ]
    independent_review_status: Literal["pending", "approved", "rejected"]
    expires_at: Timestamp
    created_at: Timestamp
