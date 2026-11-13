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


__all__ = [
    "AnticipatoryForesightItem",
    "BaseModel",
    "ConditionalRoutineHypothesis",
    "ConfigDict",
    "CortexStimulusRequest",
    "CuriosityProject",
    "Field",
    "RoutineObservationRequest",
    "SubscriptionProviderId",
    "SubscriptionProviderInfo",
    "SubscriptionSelectionRequest",
    "field_validator",
    "model_validator",
    "rust_to_json",
]
