"""Host-owned episode, commitment, and attention lifecycle contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import Identifier, ShortText, Timestamp, Version1Contract, Version2Contract


class EpisodeState(Version1Contract):
    contract: Literal["EpisodeState/v1"]
    episode_id: Identifier
    tenant_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    ingress_request_id: Identifier
    state: Literal[
        "accepted",
        "planning",
        "executing",
        "reporting",
        "completed",
        "partially_completed",
        "waiting_user",
        "waiting_external",
        "blocked",
        "cancelled",
    ]
    current_generation: int = Field(ge=1, le=2_147_483_647)
    supersedes_generation: int | None = Field(ge=1, le=2_147_483_647)
    last_event_id: Identifier
    updated_at: Timestamp

    @model_validator(mode="after")
    def _generation_chain_is_explicit(self) -> "EpisodeState":
        expected = None if self.current_generation == 1 else self.current_generation - 1
        if self.supersedes_generation != expected:
            raise ValueError(
                "generation must explicitly supersede the immediately preceding generation"
            )
        return self


class Commitment(Version1Contract):
    contract: Literal["Commitment/v1"]
    commitment_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    kind: Literal[
        "complete_request", "resume_work", "deliver_outcome", "request_user_input"
    ]
    state: Literal["open", "waiting", "blocked", "fulfilled", "cancelled"]
    description: ShortText
    continuation_ref: Identifier | None
    due_at: Timestamp | None
    updated_at: Timestamp


class AttentionWake(Version1Contract):
    contract: Literal["AttentionWake/v1"]
    attention_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    kind: Literal[
        "ingress", "continuation", "deadline", "external_signal", "delivery_recovery"
    ]
    state: Literal["pending", "claimed", "completed", "waiting", "dead_letter"]
    reason: ShortText
    not_before: Timestamp
    deadline: Timestamp
    lease_owner: Identifier | None
    lease_expires_at: Timestamp | None
    ordinal: int = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def _lease_presence_matches_state(self) -> "AttentionWake":
        if self.deadline < self.not_before:
            raise ValueError("attention deadline cannot precede not_before")
        leased = self.lease_owner is not None and self.lease_expires_at is not None
        if self.state == "claimed" and not leased:
            raise ValueError("claimed attention requires a complete lease")
        if self.state != "claimed" and (
            self.lease_owner is not None or self.lease_expires_at is not None
        ):
            raise ValueError("only claimed attention may carry a lease")
        return self


class AttentionWakeV2(Version2Contract):
    """Compatible attention task with an explicit delegated-work queue kind."""

    contract: Literal["AttentionWake/v2"]
    attention_id: Identifier
    episode_id: Identifier
    generation: int = Field(ge=1, le=2_147_483_647)
    kind: Literal[
        "ingress",
        "continuation",
        "deadline",
        "external_signal",
        "delivery_recovery",
        "delegated_work",
    ]
    state: Literal["pending", "claimed", "completed", "waiting", "dead_letter"]
    reason: ShortText
    not_before: Timestamp
    deadline: Timestamp
    lease_owner: Identifier | None
    lease_expires_at: Timestamp | None
    ordinal: int = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def _lease_presence_matches_state(self) -> "AttentionWakeV2":
        if self.deadline < self.not_before:
            raise ValueError("attention deadline cannot precede not_before")
        leased = self.lease_owner is not None and self.lease_expires_at is not None
        if self.state == "claimed" and not leased:
            raise ValueError("claimed attention requires a complete lease")
        if self.state != "claimed" and (
            self.lease_owner is not None or self.lease_expires_at is not None
        ):
            raise ValueError("only claimed attention may carry a lease")
        return self
