"""Pydantic v2 (Rust-backed) contracts for Eva's Living Entity Cortex & Subscription LLMs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

_ANY_JSON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)


def rust_to_json(value: Any) -> bytes:
    """Serialize any JSON-compatible value to UTF-8 bytes via Pydantic v2's Rust core."""
    return _ANY_JSON_ADAPTER.dump_json(value)


SubscriptionProviderId = Literal[
    "gemini_argon",
    "claude_subscription",
    "openai_codex",
]


class SubscriptionProviderInfo(BaseModel):
    """Pydantic v2 contract for a subscription-backed LLM (no API keys required)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: SubscriptionProviderId
    name: str
    vendor: str
    model_id: str
    subscription_plan: str
    auth_mode: Literal["oauth_session", "consumer_lease", "ide_bridge"]
    api_key_required: Literal[False] = False
    session_ready: bool
    quota_state: Literal["available", "exhausted", "standby"]
    selected: bool
    fallback_priority: int = Field(ge=1, le=3)
    latency_profile: str
    strengths_nl: str
    status_detail_nl: str


class SubscriptionSelectionRequest(BaseModel):
    """Pydantic v2 validated request for 1-click subscription LLM switching."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: SubscriptionProviderId
    auto_fallback: bool = True


class CortexStimulusRequest(BaseModel):
    """Pydantic v2 validated request to test/record an interaction or Theory-of-Mind update."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "conversation_turn",
        "resident_arrived",
        "household_anomaly",
        "task_completed",
        "provider_recovered",
        "resident_feedback",
    ]
    intensity: float = Field(default=0.6, ge=0.0, le=1.0)
    resident_id: str | None = None
    resident_name: str | None = None
    actual_receptivity: float | None = Field(default=None, ge=0.0, le=1.0)
    observed_mood: str | None = None
    context_hypothesis: str | None = None


class ConditionalRoutineHypothesis(BaseModel):
    """Pydantic v2 contract for E1.7: learned conditional routine with exceptions & counter-evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    routine_id: str
    title_nl: str
    condition_nl: str
    expected_window: str
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    supporting_days: int = Field(default=1, ge=0)
    exceptions_count: int = Field(default=0, ge=0)
    last_exception_nl: str | None = None
    adjusted_behavior_nl: str
    norm_preserved: bool = True
    source_refs: tuple[str, ...] = ()
    updated_at: str = ""


class AnticipatoryForesightItem(BaseModel):
    """Pydantic v2 contract for E1.8: proactive bottleneck/opportunity foresight comparing alternatives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    foresight_id: str
    title_nl: str
    window_nl: str
    status_quo_nl: str
    alternative_nl: str
    recommended_disposition: Literal["prepare", "propose", "act", "silence"]
    rationale_nl: str
    confidence: float = Field(default=0.80, ge=0.0, le=1.0)


class CuriosityProject(BaseModel):
    """Pydantic v2 contract for E1.10: Eva's autonomous creative/research project & shared trace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    title_nl: str
    theme: str
    stage: Literal["exploring", "synthesizing", "ready_to_share"] = "exploring"
    latest_insight_nl: str
    shared_trace_nl: str
    progress: float = Field(default=0.50, ge=0.0, le=1.0)
    updated_at: str = ""


class RoutineObservationRequest(BaseModel):
    """Pydantic v2 validated request for recording a routine confirmation or counter-evidence exception (E1.7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    routine_id: str = "routine-healthy-mon-tue"
    is_exception: bool = True
    detail_nl: str = "Vandaag loopt de middag anders; eenmalige uitzondering zonder de weekroutine te wissen."


class ActiveInferencePolicyEvaluation(BaseModel):
    """Pydantic v2 contract for Karl Friston's Expected Free Energy G(pi) policy evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str
    label_nl: str
    expected_free_energy_g: float
    epistemic_value: float = Field(ge=0.0, le=1.0)
    pragmatic_value: float = Field(ge=0.0, le=1.0)
    interruption_cost: float = Field(ge=0.0, le=1.0)
    posterior_probability: float = Field(ge=0.0, le=1.0)
    selected: bool = False


class AttentionSourceWatch(BaseModel):
    """Pydantic v2 contract for E1.6: autonomous source-change watch with deduplication & missing-access explanation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    watch_id: str
    source_kind: str
    target_ref: str
    purpose_nl: str
    access_status: Literal["connected", "missing_access", "degraded"] = "connected"
    missing_access_explanation_nl: str | None = None
    last_event_sha256: str | None = None
    deduplicated_callbacks_count: int = Field(default=0, ge=0)
    last_disposition: Literal["silence", "action", "pending"] = "silence"
    last_rationale_nl: str = ""
    updated_at: str = ""


class HouseholdResponsibilityArtifact(BaseModel):
    """Pydantic v2 contract for E1.11 & E1.12: recurring family responsibility & household provision lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    title_nl: str
    owner_principal_id: str
    desired_outcome_nl: str
    boundaries_nl: str
    lifecycle_state: Literal["proposed", "active", "paused", "rejected", "retired"] = "active"
    adoption_state: Literal[
        "proposed",
        "trial_active",
        "explicitly_adopted",
        "implicit_trial_confirmed",
        "paused_by_owner",
        "rolled_back",
    ] = "explicitly_adopted"
    version: int = Field(default=1, ge=1)
    previous_version_summary_nl: str | None = None
    can_rollback: bool = False
    current_standing_nl: str
    evidence_memory_ids: tuple[str, ...] = ()
    updated_at: str = ""


class SourceWatchEventRequest(BaseModel):
    """Pydantic v2 validated request to simulate or record a source-watch change or missing-access check (E1.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    watch_id: str = "watch-calendar-transition"
    event_payload_sha256: str = "e106a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e"
    summary_nl: str = "Nieuwe middagwijziging gedetecteerd op gekoppelde bron."
    access_available: bool = True


class ResponsibilityActionRequest(BaseModel):
    """Pydantic v2 validated request to adopt, pause, resume, take over, or roll back a household responsibility (E1.11/E1.12)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = "household-provision:weekday-evening-calm"
    action: Literal["adopt", "pause", "resume", "take_over", "rollback", "retire"] = "adopt"
    note_nl: str = "Bewust bevestigd door gezinslid."


class WorldModelTrajectory(BaseModel):
    """JEPA-inspired counterfactual rollout trajectory for one candidate action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    label_nl: str
    counterfactual_utility: float
    selected: bool = False


class WorldModelState(BaseModel):
    """Latent 6D causal World Model state z_t and 2-step predicted state z_{t+2}."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_action_id: str = "observe_silently"
    trajectories: tuple[WorldModelTrajectory, ...] = ()
    predicted_next_state: dict[str, float] = Field(default_factory=dict)


class SynapticMemorySnapshot(BaseModel):
    """Titans / Liquid O(1) 8x8 test-time associative fast-weights memory summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matrix_dim: Literal[8] = 8
    updates_count: int = Field(default=0, ge=0)
    last_surprise_score: float = Field(default=0.0, ge=0.0, le=1.0)
    frobenius_norm: float = Field(default=0.3536, ge=0.0)
    predicted_concept_readout: tuple[float, ...] = (0.0,) * 8
    status_nl: str = "Actief O(1) synaptisch werkgeheugen (8x8 fast-weights)"


class ConceptSpaceSnapshot(BaseModel):
    """Language-agnostic 8D semantic Concept Space + Neuro-Symbolic Guard readout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    last_utterance_preview: str = ""
    dominant_concept: str = "social_greeting"
    dimensions: dict[str, float] = Field(default_factory=dict)
    is_non_task_social: bool = True
    requires_memory_lookup: bool = False
    requires_verified_receipt: bool = False


class JarvisStagedAction(BaseModel):
    """Proactively prepared Jarvis-style action awaiting 1-click or chat confirmation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    title_nl: str
    prepared_summary_nl: str
    why_now_nl: str
    confidence: float = Field(default=0.88, ge=0.0, le=1.0)
    status: Literal["staged", "confirmed", "dismissed"] = "staged"
    updated_at: str = ""


class SleepConsolidationSummary(BaseModel):
    """Nightly episodic-to-semantic sleep/dream consolidation telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cycles_completed: int = Field(default=1, ge=0)
    last_consolidated_at: str = ""
    pruned_noise_synapses: int = Field(default=0, ge=0)
    reinforced_routines_count: int = Field(default=2, ge=0)
    dream_insight_nl: str = (
        "Episodische patronen geordend; ruis uit synaptische gewichten gefilterd en weekritme aangescherpt."
    )


class CloudProviderConfigureRequest(BaseModel):
    """Pydantic v2 validated request to configure, check, or select a real Cloud/Optional-Local LLM in Cortex."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["configure_and_activate", "select", "check", "disable", "use_default_codex"] = (
        "configure_and_activate"
    )
    kind: Literal["gemini", "anthropic", "openai_compatible"] = "gemini"
    label: str = "Google Gemini Cloud"
    model: str = "gemini-2.5-pro"
    endpoint: str = ""
    api_key: str = ""
    connection_id: str = ""


class JarvisActionDecisionRequest(BaseModel):
    """Pydantic v2 validated request to confirm or dismiss a Jarvis staged action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str = "jarvis-stage-mon-tue-menu"
    decision: Literal["confirm", "dismiss"] = "confirm"


__all__ = [
    "ActiveInferencePolicyEvaluation",
    "AnticipatoryForesightItem",
    "AttentionSourceWatch",
    "BaseModel",
    "CloudProviderConfigureRequest",
    "ConceptSpaceSnapshot",
    "ConditionalRoutineHypothesis",
    "ConfigDict",
    "CortexStimulusRequest",
    "CuriosityProject",
    "Field",
    "HouseholdResponsibilityArtifact",
    "JarvisActionDecisionRequest",
    "JarvisStagedAction",
    "ResponsibilityActionRequest",
    "RoutineObservationRequest",
    "SleepConsolidationSummary",
    "SourceWatchEventRequest",
    "SubscriptionProviderId",
    "SubscriptionProviderInfo",
    "SubscriptionSelectionRequest",
    "SynapticMemorySnapshot",
    "WorldModelState",
    "WorldModelTrajectory",
    "field_validator",
    "model_validator",
    "rust_to_json",
]
