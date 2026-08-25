"""Purpose-scoped model contracts and host-owned planning/reply contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import (
    ContractModel,
    Identifier,
    JsonValue,
    LanguageTag,
    MimeType,
    NaturalText,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
    validate_json_value,
)
from .connector import ConnectorResult
from .canonical_json import canonical_sha256
from .execution import (
    ConsentGrant,
    DeliveryIntent,
    DeliveryReceipt,
    EffectReadback,
    EffectReceipt,
    ReadObservation,
)
from .identity import IdentityPack
from .memory import RelationshipBinding

ModelPurpose = Literal["plan", "document_draft", "reply"]


class ConfirmedFact(ContractModel):
    fact_id: Identifier
    subject: ShortText
    predicate: Identifier
    value: JsonValue
    source_ref: Identifier
    evidence_ref: Identifier
    observed_at: Timestamp
    trust: Literal["direct_user", "verified_external", "host_policy"]

    @model_validator(mode="after")
    def _validate_value(self) -> "ConfirmedFact":
        validate_json_value(self.value)
        return self


class ConversationExcerpt(ContractModel):
    message_id: Identifier
    role: Literal["user", "assistant"]
    text: NaturalText
    created_at: Timestamp


class CurrentUtterance(ContractModel):
    turn_ref: Identifier
    message_id: Identifier
    text: NaturalText
    received_at: Timestamp


class EpisodeGoal(ContractModel):
    goal_id: Identifier
    source_message_ref: Identifier
    description: NaturalText


class MissingHumanChoice(ContractModel):
    choice_id: Identifier
    required_for: Identifier
    question: ShortText
    option_ids: list[Identifier] = Field(max_length=16)


class ActiveConsentGrant(ConsentGrant):
    state: Literal["active"]


class CurrentIdentityPack(IdentityPack):
    state: Literal["current"]


class PresenceContext(Version1Contract):
    contract: Literal["PresenceContext/v1"]
    tenant_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    identity_pack: CurrentIdentityPack
    identity_pack_sha256: Sha256
    relationships: list[RelationshipBinding] = Field(max_length=32)
    current_utterance: CurrentUtterance
    episode_goal: EpisodeGoal
    confirmed_facts: list[ConfirmedFact] = Field(max_length=64)
    conversation: list[ConversationExcerpt] = Field(max_length=32)
    valid_consent_grants: list[ActiveConsentGrant] = Field(max_length=32)
    connector_results: list[ConnectorResult] = Field(max_length=64)
    read_observations: list[ReadObservation] = Field(max_length=64)
    effect_receipts: list[EffectReceipt] = Field(max_length=64)
    effect_readbacks: list[EffectReadback] = Field(max_length=64)
    delivery_intents: list[DeliveryIntent] = Field(max_length=8)
    delivery_receipts: list[DeliveryReceipt] = Field(max_length=8)
    unresolved_evidence_refs: list[Identifier] = Field(max_length=64)
    missing_human_choice: MissingHumanChoice | None
    token_budget: int = Field(ge=1, le=100_000)

    @model_validator(mode="after")
    def _embedded_authority_is_exactly_bound(self) -> "PresenceContext":
        if canonical_sha256(self.identity_pack) != self.identity_pack_sha256:
            raise ValueError(
                "identity_pack_sha256 must bind the embedded current IdentityPack"
            )
        if any(
            relationship.tenant_id != self.tenant_id
            or relationship.principal_id != self.principal_id
            or relationship.identity_id != self.identity_pack.identity_id
            for relationship in self.relationships
        ):
            raise ValueError(
                "relationship context must match tenant, principal and identity"
            )
        if any(
            grant.tenant_id != self.tenant_id
            or grant.principal_id != self.principal_id
            or grant.episode_id != self.episode_id
            or grant.generation != self.generation
            for grant in self.valid_consent_grants
        ):
            raise ValueError(
                "consent context must match tenant, principal, episode and generation"
            )
        return self


class ContextSelection(Version1Contract):
    contract: Literal["ContextSelection/v1"]
    tenant_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    purpose: ModelPurpose
    selected_source_refs: list[Identifier] = Field(max_length=256)
    excluded_source_reasons: dict[Identifier, ShortText] = Field(max_length=256)
    context_sha256: Sha256
    selected_at: Timestamp


class PlanContextSelection(ContextSelection):
    purpose: Literal["plan"]


class DocumentDraftContextSelection(ContextSelection):
    purpose: Literal["document_draft"]


class ReplyContextSelection(ContextSelection):
    purpose: Literal["reply"]


class ProposedAction(ContractModel):
    action_id: Identifier
    kind: Literal["request_read", "propose_effect"]
    operation: Identifier
    resource: ShortText
    arguments: dict[str, JsonValue] = Field(max_length=64)
    depends_on: list[Identifier] = Field(max_length=32)
    uncertainty_basis_points: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def _validate_arguments(self) -> "ProposedAction":
        validate_json_value(self.arguments)
        return self


class ModelDecision(Version1Contract):
    contract: Literal["ModelDecision/v1"]
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    purpose: Literal["plan"]
    decision: Literal["clarify", "request_read", "propose_effect", "wait"]
    actions: list[ProposedAction] = Field(max_length=64)
    clarification_question: ShortText | None
    wait_reason: ShortText | None
    uncertainty_basis_points: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def _coherent_decision(self) -> "ModelDecision":
        if self.decision == "clarify":
            if (
                self.clarification_question is None
                or self.wait_reason is not None
                or self.actions
            ):
                raise ValueError("clarify requires one question and no actions")
        elif self.decision == "wait":
            if (
                self.wait_reason is None
                or self.clarification_question is not None
                or self.actions
            ):
                raise ValueError("wait requires a reason and no actions")
        elif (
            self.clarification_question is not None
            or self.wait_reason is not None
            or not self.actions
        ):
            raise ValueError(
                "read/effect decisions require actions and no question or wait reason"
            )
        elif self.decision == "request_read" and any(
            action.kind != "request_read" for action in self.actions
        ):
            raise ValueError("request_read may contain only read actions")
        elif self.decision == "propose_effect" and not any(
            action.kind == "propose_effect" for action in self.actions
        ):
            raise ValueError("propose_effect requires at least one proposed effect")
        identifiers = [action.action_id for action in self.actions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("model action IDs must be unique")
        known: set[str] = set()
        for action in self.actions:
            if len(action.depends_on) != len(set(action.depends_on)):
                raise ValueError("model action dependencies must be unique")
            if not set(action.depends_on).issubset(known):
                raise ValueError(
                    "model actions must be topologically ordered and reference "
                    "earlier actions"
                )
            known.add(action.action_id)
        return self


class ValidatedStep(ContractModel):
    step_id: Identifier
    kind: Literal["read", "effect", "confirmation_delivery", "outcome_delivery"]
    operation: Identifier
    resource: ShortText
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256
    depends_on: list[Identifier] = Field(max_length=32)
    capability_manifest_id: Identifier
    proof_floor: ShortText


class ValidatedPlan(Version1Contract):
    contract: Literal["ValidatedPlan/v1"]
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    model_decision_sha256: Sha256
    policy_sha256: Sha256
    steps: list[ValidatedStep] = Field(min_length=1, max_length=64)
    maximum_connector_calls: int = Field(ge=0, le=128)
    maximum_effect_attempts: int = Field(ge=0, le=32)
    maximum_visible_deliveries: int = Field(ge=0, le=8)
    deadline: Timestamp

    @model_validator(mode="after")
    def _validate_dag(self) -> "ValidatedPlan":
        identifiers = [step.step_id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("plan step IDs must be unique")
        known: set[str] = set()
        for step in self.steps:
            if not set(step.depends_on).issubset(known):
                raise ValueError(
                    "steps must be topologically ordered and reference earlier steps"
                )
            known.add(step.step_id)
        return self


class ModelAttempt(Version1Contract):
    contract: Literal["ModelAttempt/v1"]
    attempt_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    purpose: ModelPurpose
    ordinal: int = Field(ge=1, le=2)
    state: Literal[
        "reserved",
        "dispatch_authorized",
        "responded",
        "ambiguous",
        "provider_rejected",
        "rate_limited",
        "budget_exhausted",
    ]
    provider_profile_id: Identifier
    request_sha256: Sha256
    response_sha256: Sha256 | None
    credential_grant_id: Identifier | None
    maximum_input_tokens: int = Field(ge=1, le=100_000)
    maximum_output_tokens: int = Field(ge=1, le=100_000)
    timeout_ms: int = Field(ge=1, le=300_000)
    input_tokens_used: int | None = Field(ge=0, le=1_000_000)
    output_tokens_used: int | None = Field(ge=0, le=1_000_000)
    provider_cost_euro_micros: int | None = Field(ge=0, le=1_000_000_000)
    created_at: Timestamp

    @model_validator(mode="after")
    def _second_attempt_is_only_the_sealed_plan_recovery(self) -> "ModelAttempt":
        if self.ordinal == 2 and self.purpose != "plan":
            raise ValueError(
                "the first vertical permits ordinal 2 only for plan after the sealed 429"
            )
        pre_dispatch = self.state in {"reserved", "budget_exhausted"}
        if pre_dispatch != (self.credential_grant_id is None):
            raise ValueError(
                "only a provider-dispatched model attempt may bind a credential grant"
            )
        response_required = self.state in {
            "responded",
            "provider_rejected",
            "rate_limited",
        }
        if response_required != (self.response_sha256 is not None):
            raise ValueError(
                "model response hash presence must match the terminal provider state"
            )
        if self.state in {"reserved", "dispatch_authorized", "budget_exhausted"} and (
            self.input_tokens_used is not None
            or self.output_tokens_used is not None
            or self.provider_cost_euro_micros is not None
        ):
            raise ValueError("pre-response model attempt cannot claim provider usage")
        return self


class DocumentDraft(Version1Contract):
    contract: Literal["DocumentDraft/v1"]
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    purpose: Literal["document_draft"]
    mime_type: MimeType
    title: ShortText
    content: NaturalText
    source_refs: list[Identifier] = Field(min_length=1, max_length=128)
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    owner_principal_id: Identifier
    retention_until: Timestamp

    @model_validator(mode="after")
    def _first_vertical_document_byte_budget(self) -> "DocumentDraft":
        if len(self.content.encode("utf-8")) > 65_536:
            raise ValueError("document content exceeds the sealed 65,536-byte budget")
        return self


class StepOutcome(ContractModel):
    step_id: Identifier
    status: Literal["completed", "partial", "ambiguous", "blocked", "failed"]
    human_summary: ShortText
    evidence_refs: list[Identifier] = Field(max_length=64)
    unresolved_refs: list[Identifier] = Field(max_length=64)

    @model_validator(mode="after")
    def _evidence_matches_status(self) -> "StepOutcome":
        if self.status == "completed" and not self.evidence_refs:
            raise ValueError("completed step requires evidence")
        if (
            self.status in {"ambiguous", "blocked", "failed"}
            and not self.unresolved_refs
        ):
            raise ValueError("non-conclusive step requires an unresolved reference")
        return self


class OutcomeView(Version1Contract):
    contract: Literal["OutcomeView/v1"]
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    steps: list[StepOutcome] = Field(min_length=1, max_length=64)
    overall_status: Literal[
        "completed",
        "partially_completed",
        "waiting_user",
        "waiting_external",
        "blocked",
        "cancelled",
    ]
    created_at: Timestamp

    @model_validator(mode="after")
    def _overall_state_matches_steps(self) -> "OutcomeView":
        states = {step.status for step in self.steps}
        if self.overall_status == "completed" and states != {"completed"}:
            raise ValueError("completed outcome requires every step to be completed")
        if self.overall_status == "partially_completed" and (
            "completed" not in states or states == {"completed"}
        ):
            raise ValueError(
                "partial outcome requires both completed and incomplete work"
            )
        return self


class PlanModelInput(Version1Contract):
    contract: Literal["PlanModelInput/v1"]
    purpose: Literal["plan"]
    output_contract: Literal["ModelDecision/v1"]
    presence_context: PresenceContext
    context_selection: PlanContextSelection
    policy_pack_sha256: Sha256
    allowed_operations: list[Identifier] = Field(min_length=1, max_length=64)
    maximum_steps: int = Field(ge=1, le=64)

    @model_validator(mode="after")
    def _context_hash_is_exact(self) -> "PlanModelInput":
        if (
            canonical_sha256(self.presence_context)
            != self.context_selection.context_sha256
        ):
            raise ValueError(
                "context selection hash must bind the complete PresenceContext"
            )
        return self


class DocumentDraftModelInput(Version1Contract):
    contract: Literal["DocumentDraftModelInput/v1"]
    purpose: Literal["document_draft"]
    output_contract: Literal["DocumentDraft/v1"]
    presence_context: PresenceContext
    context_selection: DocumentDraftContextSelection
    policy_pack_sha256: Sha256
    document_goal: NaturalText
    target_mime_type: MimeType
    source_refs: list[Identifier] = Field(min_length=1, max_length=128)
    privacy_class: Literal["public", "internal", "confidential", "restricted"]
    owner_principal_id: Identifier
    retention_until: Timestamp

    @model_validator(mode="after")
    def _context_hash_is_exact(self) -> "DocumentDraftModelInput":
        if (
            canonical_sha256(self.presence_context)
            != self.context_selection.context_sha256
        ):
            raise ValueError(
                "context selection hash must bind the complete PresenceContext"
            )
        return self


class ReplyModelInput(Version1Contract):
    contract: Literal["ReplyModelInput/v1"]
    purpose: Literal["reply"]
    output_contract: Literal["ReplyCandidate/v1"]
    presence_context: PresenceContext
    context_selection: ReplyContextSelection
    policy_pack_sha256: Sha256
    outcome_view: OutcomeView
    allowed_claim_refs: list[Identifier] = Field(max_length=128)

    @model_validator(mode="after")
    def _context_hash_is_exact(self) -> "ReplyModelInput":
        if (
            canonical_sha256(self.presence_context)
            != self.context_selection.context_sha256
        ):
            raise ValueError(
                "context selection hash must bind the complete PresenceContext"
            )
        return self


PurposeModelInput = Annotated[
    PlanModelInput | DocumentDraftModelInput | ReplyModelInput,
    Field(discriminator="purpose"),
]


class PlannerRequest(Version1Contract):
    """One complete, purpose-scoped model request with no adapter side inputs."""

    contract: Literal["PlannerRequest/v1"]
    request_id: Identifier
    model_input: PurposeModelInput
    deadline: Timestamp


class ReplyCandidate(Version1Contract):
    contract: Literal["ReplyCandidate/v1"]
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    purpose: Literal["reply"]
    text: NaturalText
    claim_refs: list[Identifier] = Field(max_length=128)
    unresolved_refs: list[Identifier] = Field(max_length=128)
    identity_id: Identifier
    identity_pack_version: int = Field(ge=1, le=2_147_483_647)
    style_check: Literal["passed", "failed"]


class ClaimBinding(ContractModel):
    claim_ref: Identifier
    evidence_refs: list[Identifier] = Field(min_length=1, max_length=32)


class PresenceReply(Version1Contract):
    contract: Literal["PresenceReply/v1"]
    message_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    outcome_view_sha256: Sha256
    reply_candidate_sha256: Sha256 | None
    fallback_reason: Literal["candidate_rejected", "candidate_absent"] | None
    host_validation_sha256: Sha256
    text: NaturalText
    claim_bindings: list[ClaimBinding] = Field(max_length=128)
    identity_id: Identifier
    identity_pack_version: int = Field(ge=1, le=2_147_483_647)
    language: LanguageTag
    created_at: Timestamp

    @model_validator(mode="after")
    def _candidate_or_fallback(self) -> "PresenceReply":
        if (self.reply_candidate_sha256 is None) == (self.fallback_reason is None):
            raise ValueError(
                "exactly one of reply_candidate_sha256 or fallback_reason is required"
            )
        if "\u2013" in self.text or "\u2014" in self.text:
            raise ValueError("PresenceReply text may not contain en or em dashes")
        return self
