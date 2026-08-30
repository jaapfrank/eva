"""Connector-neutral voice boundary contracts.

These contracts deliberately carry no audio frames, provider session token or
partial transcript.  They let a media adapter hand one *final* spoken turn to
the same Eva conversation core, and let that core identify a barge-in without
mistaking it for permission to cancel a tool effect.

They are not yet added to the sealed portable-contract registry: V01 has no
voice wire endpoint or durable storage authority.  Promotion to such a
boundary belongs with V03/V04, when the corresponding connector and migration
exist.  Keeping the types here nevertheless fixes the identity, privacy and
interruption contract before either provider is selected.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Literal, Self

from pydantic import Field, StringConstraints, model_validator
from typing_extensions import Annotated

from eva.contracts.base import (
    Identifier,
    LanguageTag,
    Sha256,
    Timestamp,
    Version1Contract,
)


VoiceTranscript = Annotated[
    str,
    StringConstraints(min_length=1, max_length=16_384),
]


class _VoiceContract(Version1Contract):
    """Small payload helpers shared by the intentionally closed V01 types."""

    def to_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @classmethod
    def from_payload(cls, raw: object) -> Self:
        return cls.model_validate(raw)


class VoicePrivacy(_VoiceContract):
    """The privacy floor for every V01 voice session.

    Audio and partial recognition remain outside this contract.  A final text
    still goes through the regular conversation admission before it can reach
    memory, so a media adapter cannot create a memory write by itself.
    """

    contract: Literal["VoicePrivacy/v1"]
    partial_transcript_retention: Literal["ephemeral"]
    raw_audio_retention: Literal["none", "diagnostic_opt_in_24h"]
    external_provider_audio_retention: Literal["disabled", "explicit_consent"]
    final_transcript_admission: Literal["conversation_admission_required"]

    @model_validator(mode="after")
    def _diagnostics_never_enable_provider_retention(self) -> "VoicePrivacy":
        if (
            self.raw_audio_retention == "diagnostic_opt_in_24h"
            and self.external_provider_audio_retention != "disabled"
        ):
            raise ValueError(
                "voice diagnostics cannot implicitly allow provider audio retention"
            )
        return self


class VoiceSession(_VoiceContract):
    """One server-bound private voice session before any media provider runs."""

    contract: Literal["VoiceSession/v1"]
    session_id: Identifier
    tenant_id: Identifier
    installation_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    audience: Literal["private"]
    channel: Literal["local_test", "local_assist", "telegram_mini_app"]
    device_binding_id: Identifier
    identity_id: Identifier
    identity_pack_version: int = Field(ge=1, le=2_147_483_647)
    identity_pack_sha256: Sha256
    policy_id: Identifier
    policy_pack_version: int = Field(ge=1, le=2_147_483_647)
    policy_pack_sha256: Sha256
    privacy: VoicePrivacy
    created_at: Timestamp
    deadline: Timestamp

    @model_validator(mode="after")
    def _has_a_short_closed_lifetime(self) -> "VoiceSession":
        created = _parse_timestamp(self.created_at)
        deadline = _parse_timestamp(self.deadline)
        seconds = (deadline - created).total_seconds()
        if not 0 < seconds <= 7_200:
            raise ValueError("voice session deadline must be within two hours")
        return self

    def validate_final_turn(self, turn: "VoiceTurn") -> None:
        """Refuse a final spoken turn from another session/person/time window."""

        if (
            turn.session_id != self.session_id
            or turn.tenant_id != self.tenant_id
            or turn.installation_id != self.installation_id
            or turn.principal_id != self.principal_id
            or turn.conversation_id != self.conversation_id
            or turn.audience != self.audience
        ):
            raise ValueError("voice turn is not bound to this private session")
        if not self.created_at <= turn.finalized_at <= self.deadline:
            raise ValueError("voice turn falls outside the session deadline")

    def validate_barge_in(self, barge_in: "BargeIn") -> None:
        """Accept only a same-session playback interruption in the time window."""

        if (
            barge_in.session_id != self.session_id
            or barge_in.tenant_id != self.tenant_id
            or barge_in.installation_id != self.installation_id
            or barge_in.principal_id != self.principal_id
            or barge_in.conversation_id != self.conversation_id
            or barge_in.audience != self.audience
        ):
            raise ValueError("barge-in is not bound to this private voice session")
        if not self.created_at <= barge_in.occurred_at <= self.deadline:
            raise ValueError("barge-in falls outside the session deadline")


class VoiceTurn(_VoiceContract):
    """One final, private spoken user turn awaiting normal core admission."""

    contract: Literal["VoiceTurn/v1"]
    voice_turn_id: Identifier
    session_id: Identifier
    tenant_id: Identifier
    installation_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    conversation_message_id: Identifier
    audience: Literal["private"]
    transcript_state: Literal["final"]
    final_transcript: VoiceTranscript
    final_transcript_sha256: Sha256
    language: LanguageTag
    reply_generation: int = Field(ge=1, le=2_147_483_647)
    finalized_at: Timestamp

    @model_validator(mode="after")
    def _bind_final_transcript_bytes(self) -> "VoiceTurn":
        if not self.final_transcript.strip():
            raise ValueError("voice final transcript cannot be blank")
        actual = hashlib.sha256(self.final_transcript.encode("utf-8")).hexdigest()
        if self.final_transcript_sha256 != actual:
            raise ValueError("voice final transcript hash differs")
        return self


class BargeIn(_VoiceContract):
    """An interruption of audible delivery, never a tool-effect cancellation."""

    contract: Literal["BargeIn/v1"]
    barge_in_id: Identifier
    session_id: Identifier
    tenant_id: Identifier
    installation_id: Identifier
    principal_id: Identifier
    conversation_id: Identifier
    audience: Literal["private"]
    interrupted_voice_turn_id: Identifier
    cancel_generation: int = Field(ge=1, le=2_147_483_647)
    played_ms: int = Field(ge=0, le=7_200_000)
    occurred_at: Timestamp
    cancellation_scope: Literal["playback_and_unfinished_reply_generation"]
    effect_cancel_authorized: Literal[False]


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "BargeIn",
    "VoicePrivacy",
    "VoiceSession",
    "VoiceTranscript",
    "VoiceTurn",
]
