"""Identity and policy contracts.  Identity never grants effect authority."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .base import (
    ContractModel,
    Identifier,
    LanguageTag,
    Sha256,
    ShortText,
    Timestamp,
    Version1Contract,
)

Pronoun = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class VoiceStyle(ContractModel):
    name: ShortText
    qualities: list[ShortText] = Field(min_length=1, max_length=16)
    preferred_wording: list[ShortText] = Field(max_length=32)
    forbidden_wording: list[ShortText] = Field(max_length=32)
    maximum_paragraphs: int = Field(ge=1, le=12)


class ChannelPresentation(ContractModel):
    channel_class: Identifier
    greeting: ShortText | None
    signoff: ShortText | None
    concise_by_default: bool


class IdentityPack(Version1Contract):
    contract: Literal["IdentityPack/v1"]
    identity_id: Identifier
    pack_version: int = Field(ge=1, le=2_147_483_647)
    state: Literal["staged", "current", "superseded", "revoked"]
    display_name: ShortText
    pronouns: list[Pronoun] = Field(min_length=1, max_length=8)
    language: LanguageTag
    voice_style: VoiceStyle
    values: list[ShortText] = Field(min_length=1, max_length=32)
    temperament: list[ShortText] = Field(min_length=1, max_length=16)
    humour: ShortText
    conversation_boundaries: list[ShortText] = Field(min_length=1, max_length=32)
    channel_presentations: list[ChannelPresentation] = Field(
        min_length=1, max_length=16
    )
    privacy_preferences: list[ShortText] = Field(max_length=32)
    consent_preferences: list[ShortText] = Field(max_length=32)
    initiative_preferences: list[ShortText] = Field(max_length=32)
    unknown_relation_preferences: list[ShortText] = Field(max_length=16)
    issuer: Identifier
    source_sha256: Sha256
    signature_algorithm: Literal["Ed25519"]
    signature: Annotated[
        str,
        StringConstraints(
            min_length=86, max_length=88, pattern=r"^[A-Za-z0-9_-]+={0,2}$"
        ),
    ]
    issued_at: Timestamp


class EffectPolicy(ContractModel):
    operation: Identifier
    effect_class: Literal[
        "read",
        "effect",
        "confirmation_delivery_effect",
        "outcome_delivery_effect",
        "recovery_read",
        "verification_read",
        "breaker_probe_read",
    ]
    consent_exempt: bool
    proof_floor: ShortText
    maximum_attempts: int = Field(ge=1, le=2)


class RuntimeBudgets(ContractModel):
    maximum_model_attempts: int = Field(ge=0, le=16)
    maximum_connector_calls: int = Field(ge=0, le=128)
    maximum_visible_deliveries: int = Field(ge=0, le=8)
    maximum_wall_clock_ms: int = Field(ge=1_000, le=3_600_000)
    maximum_input_tokens: int = Field(ge=0, le=1_000_000)
    maximum_output_tokens: int = Field(ge=0, le=1_000_000)
    maximum_provider_cost_euro_cents: int = Field(ge=0, le=100_000)


class PolicyPack(Version1Contract):
    contract: Literal["PolicyPack/v1"]
    policy_id: Identifier
    policy_version: int = Field(ge=1, le=2_147_483_647)
    effect_policies: list[EffectPolicy] = Field(min_length=1, max_length=256)
    budgets: RuntimeBudgets
    retry_policy_id: Identifier
    retention_policy_id: Identifier
    privacy_policy_id: Identifier
    source_sha256: Sha256
    release_signature_sha256: Sha256
    effective_at: Timestamp
