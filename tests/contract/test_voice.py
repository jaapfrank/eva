from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest
from pydantic import ValidationError

from eva.application.voice import admit_final_voice_turn
from eva.contracts.voice import BargeIn, VoicePrivacy, VoiceSession, VoiceTurn


def _privacy(**overrides: object) -> VoicePrivacy:
    values: dict[str, object] = {
        "contract": "VoicePrivacy/v1",
        "schema_version": 1,
        "partial_transcript_retention": "ephemeral",
        "raw_audio_retention": "none",
        "external_provider_audio_retention": "disabled",
        "final_transcript_admission": "conversation_admission_required",
    }
    values.update(overrides)
    return VoicePrivacy.model_validate(values)


def _session(**overrides: object) -> VoiceSession:
    values: dict[str, object] = {
        "contract": "VoiceSession/v1",
        "schema_version": 1,
        "session_id": "voice-session:001",
        "tenant_id": "tenant:001",
        "installation_id": "installation:001",
        "principal_id": "person:001",
        "conversation_id": "conversation:001",
        "audience": "private",
        "channel": "local_test",
        "device_binding_id": "voice-device:001",
        "identity_id": "identity:eva",
        "identity_pack_version": 1,
        "identity_pack_sha256": "a" * 64,
        "policy_id": "policy:default",
        "policy_pack_version": 1,
        "policy_pack_sha256": "b" * 64,
        "privacy": _privacy().model_dump(mode="json"),
        "created_at": "2026-08-26T10:00:00Z",
        "deadline": "2026-08-26T10:30:00Z",
    }
    values.update(overrides)
    return VoiceSession.model_validate(values)


def _turn(**overrides: object) -> VoiceTurn:
    text = "Zet de lamp in de keuken aan."
    values: dict[str, object] = {
        "contract": "VoiceTurn/v1",
        "schema_version": 1,
        "voice_turn_id": "voice-turn:001",
        "session_id": "voice-session:001",
        "tenant_id": "tenant:001",
        "installation_id": "installation:001",
        "principal_id": "person:001",
        "conversation_id": "conversation:001",
        "conversation_message_id": "voice-message:001",
        "audience": "private",
        "transcript_state": "final",
        "final_transcript": text,
        "final_transcript_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "language": "nl-NL",
        "reply_generation": 4,
        "finalized_at": "2026-08-26T10:01:00Z",
    }
    values.update(overrides)
    return VoiceTurn.model_validate(values)


def _barge_in(**overrides: object) -> BargeIn:
    values: dict[str, object] = {
        "contract": "BargeIn/v1",
        "schema_version": 1,
        "barge_in_id": "barge-in:001",
        "session_id": "voice-session:001",
        "tenant_id": "tenant:001",
        "installation_id": "installation:001",
        "principal_id": "person:001",
        "conversation_id": "conversation:001",
        "audience": "private",
        "interrupted_voice_turn_id": "voice-turn:001",
        "cancel_generation": 4,
        "played_ms": 312,
        "occurred_at": "2026-08-26T10:01:03Z",
        "cancellation_scope": "playback_and_unfinished_reply_generation",
        "effect_cancel_authorized": False,
    }
    values.update(overrides)
    return BargeIn.model_validate(values)


def test_voice_v01_binds_a_final_private_turn_to_one_session() -> None:
    session = _session()
    turn = _turn()

    session.validate_final_turn(turn)
    core_turn = admit_final_voice_turn(session=session, turn=turn)
    assert core_turn.principal_id == "person:001"
    assert core_turn.conversation_id == "conversation:001"
    assert core_turn.message_id == "voice-message:001"
    assert core_turn.text == turn.final_transcript
    assert VoiceSession.from_payload(session.to_payload()) == session
    assert VoiceTurn.from_payload(turn.to_payload()) == turn


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("principal_id", "person:other", "not bound"),
        ("conversation_id", "conversation:other", "not bound"),
        ("finalized_at", "2026-08-26T10:31:00Z", "outside"),
    ),
)
def test_voice_v01_rejects_cross_person_and_expired_final_turn(
    field: str, value: str, message: str
) -> None:
    turn = _turn(**{field: value})
    with pytest.raises(ValueError, match=message):
        _session().validate_final_turn(turn)


def test_voice_v01_never_accepts_partial_audio_or_a_wrong_transcript_hash() -> None:
    raw = _turn().to_payload()
    raw["raw_audio"] = "base64-does-not-belong-in-a-contract"
    with pytest.raises(ValidationError, match="Extra inputs"):
        VoiceTurn.model_validate(raw)

    raw = deepcopy(_turn().to_payload())
    raw["final_transcript_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="transcript hash"):
        VoiceTurn.model_validate(raw)

    raw = deepcopy(_turn().to_payload())
    raw["transcript_state"] = "partial"
    with pytest.raises(ValidationError):
        VoiceTurn.model_validate(raw)


def test_voice_v01_barge_in_stops_only_playback_and_unfinished_reply() -> None:
    session = _session()
    barge_in = _barge_in()

    session.validate_barge_in(barge_in)
    assert barge_in.effect_cancel_authorized is False
    assert BargeIn.from_payload(barge_in.to_payload()) == barge_in

    with pytest.raises(ValidationError):
        _barge_in(effect_cancel_authorized=True)
    with pytest.raises(ValueError, match="outside"):
        session.validate_barge_in(_barge_in(occurred_at="2026-08-26T10:30:01Z"))
    with pytest.raises(ValueError, match="not bound"):
        session.validate_barge_in(_barge_in(principal_id="person:other"))


def test_voice_v01_diagnostics_do_not_grant_external_retention() -> None:
    _privacy(raw_audio_retention="diagnostic_opt_in_24h")
    with pytest.raises(ValidationError, match="diagnostics"):
        _privacy(
            raw_audio_retention="diagnostic_opt_in_24h",
            external_provider_audio_retention="explicit_consent",
        )
