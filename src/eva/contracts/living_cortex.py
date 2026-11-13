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


__all__ = [
    "BaseModel",
    "ConfigDict",
    "CortexStimulusRequest",
    "Field",
    "SubscriptionProviderId",
    "SubscriptionProviderInfo",
    "SubscriptionSelectionRequest",
    "field_validator",
    "model_validator",
    "rust_to_json",
]
