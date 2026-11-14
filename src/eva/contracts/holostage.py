"""Strict Pydantic v2 contracts for Fase 4 — Eva HoloStage & Biometric Presence Fusion.

All biometric observations carry explicit non-authentication provenance:
face and speaker similarity provide candidate presence and automatic bystander
privacy shielding, never authentication or privilege escalation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StageFormFactor = Literal["cockpit", "unifi_screen", "tv_cast", "pocket"]
StageCardKind = Literal[
    "shopping_list",
    "household_today",
    "home_overview",
    "visitors_review",
    "custom_spotlight",
]
VisitorClassification = Literal["unreviewed", "bekende_buur", "bezorger", "genegeerd"]


class ShoppingListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=3, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    quantity: str = Field(default="", max_length=32)
    checked: bool = False
    added_by: str = Field(default="gezin", max_length=48)
    updated_at: str = Field(min_length=20, max_length=32)


class VisitorReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitor_id: str = Field(min_length=6, max_length=64)
    source_id: str = Field(min_length=1, max_length=80)
    room_id: str = Field(min_length=1, max_length=48)
    first_seen_at: str = Field(min_length=20, max_length=32)
    last_seen_at: str = Field(min_length=20, max_length=32)
    dwell_seconds: float = Field(ge=0.0, le=86400.0)
    sharp_frames: int = Field(ge=1, le=10000)
    frontal_quality: float = Field(ge=0.0, le=1.0)
    classification: VisitorClassification = "unreviewed"
    note_nl: str = Field(default="", max_length=160)
    chat_interrupt_sent: Literal[False] = False


class StageSpotlightCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=96)
    body_nl: str = Field(min_length=1, max_length=800)
    items: list[str] = Field(default_factory=list, max_length=16)
    privacy_level: Literal["shared", "private_household"] = "shared"
    created_at: str = Field(min_length=20, max_length=32)
    expires_at: str = Field(min_length=20, max_length=32)


class BiometricFrameFaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bbox: tuple[int, int, int, int]
    det_score: float = Field(default=0.85, ge=0.0, le=1.0)
    sharpness: float = Field(default=0.75, ge=0.0, le=1.0)
    frontal_pose: float = Field(default=0.80, ge=0.0, le=1.0)
    status: Literal["known_candidate", "unknown"] = "unknown"
    person_label: str | None = Field(default=None, max_length=120)
    similarity_ppm: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    margin_ppm: int = Field(default=0, ge=-2_000_000, le=2_000_000)
    gaze_engaged: bool = False


class StageDeviceGrantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(min_length=4, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    form_factor: StageFormFactor
    room_id: str = Field(min_length=1, max_length=48)
    allow_private_cards_when_alone: bool = False
    allow_owner_voice: bool = False
    status: Literal["active", "revoked", "expired"]
    created_at: str = Field(min_length=20, max_length=32)
    expires_at: str = Field(min_length=20, max_length=32)


__all__ = [
    "BiometricFrameFaceInput",
    "ShoppingListItem",
    "StageCardKind",
    "StageDeviceGrantSummary",
    "StageFormFactor",
    "StageSpotlightCard",
    "VisitorClassification",
    "VisitorReviewEntry",
]
