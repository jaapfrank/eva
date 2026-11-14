"""Comprehensive functional & adversarial tests for Fase 4 — Eva HoloStage, Biometric Presence & Cast V2."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json
import math
import socket
import ssl
import stat
import struct
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_holostage import install_dashboard_holostage
from eva.application.biometric_presence import (
    FACE_EVIDENCE_KIND,
    VOICE_EVIDENCE_KIND,
    BiometricPresenceFusion,
    extract_pcm16_voiceprint,
)
from eva.application.cast_v2_protocol import (
    NS_HEARTBEAT,
    NS_RECEIVER,
    decode_cast_v2_body,
    encode_cast_v2_frame,
    query_or_launch_approved_cast_device,
    validate_stage_cast_url,
)
from eva.application.conversation import ConversationTurn, GroundedReply, publish_conversation_text
from eva.application.holostage import HoloStageManager, SentenceBoundaryChunker
from eva.application.home_proprioception import HomeProprioceptionManager
from eva.application.household_head_settings import HouseholdHeadSettings


def _synth_voice_pcm16(
    *,
    f0_hz: float,
    formants_hz: tuple[float, float, float],
    amplitude: float = 9500.0,
    duration_s: float = 0.85,
    sample_rate: int = 16000,
) -> bytes:
    """Synthesize a deterministic voiced harmonic + formant-shaped 16 kHz mono s16le waveform."""
    n_samples = int(sample_rate * duration_s)
    samples: list[int] = []
    for n in range(n_samples):
        t = n / float(sample_rate)
        # Subtle natural vibrato
        inst_f0 = f0_hz * (1.0 + 0.015 * math.sin(2.0 * math.pi * 5.2 * t))
        val = 0.0
        for h in range(1, 22):
            freq = h * inst_f0
            if freq >= 7500.0:
                break
            # Spectral envelope peaked around the 3 speaker formants
            env = sum(
                math.exp(-0.5 * ((freq - fmt) / (110.0 + 0.08 * fmt)) ** 2)
                for fmt in formants_hz
            )
            val += (env / (1.0 + 0.12 * h)) * math.sin(2.0 * math.pi * freq * t + 0.3 * h)
        # Normalize & clip to int16
        s = int(max(-32000.0, min(32000.0, val * amplitude * 0.42)))
        samples.append(s)
    return struct.pack(f"<{len(samples)}h", *samples)


def test_face_tracklet_fusion_household_boundary_and_bystander_shield(tmp_path: Path) -> None:
    hh = HouseholdHeadSettings(tmp_path)
    fusion = BiometricPresenceFusion(tmp_path, hh)
    t0 = datetime(2026, 11, 13, 18, 0, 0, tzinfo=UTC)

    # 1. Blurry/tiny single frame is ignored and does not confirm a candidate
    s1 = fusion.observe_camera_frame(
        source_id="camera.unifi_woonkamer",
        room_id="woonkamer",
        faces=[
            {
                "bbox": (10, 10, 32, 32),  # area 484 < 1400
                "det_score": 0.51,
                "sharpness": 0.25,
                "frontal_pose": 0.30,
                "status": "known_candidate",
                "person_label": "Jaap",
                "similarity_ppm": 520_000,
                "margin_ppm": 120_000,
            }
        ],
        now=t0,
    )
    assert s1["household_candidate"] is None
    assert s1["privacy_shield_active"] is False

    # 2. Two sharp frontal frames of Lola fuse into a confirmed household tracklet + Gaze-to-Talk!
    for step in (1, 2):
        snap = fusion.observe_camera_frame(
            source_id="camera.unifi_woonkamer",
            room_id="woonkamer",
            faces=[
                {
                    "bbox": (100 + step * 4, 80, 210 + step * 4, 200),
                    "det_score": 0.91,
                    "sharpness": 0.84,
                    "frontal_pose": 0.88,
                    "status": "known_candidate",
                    "person_label": "Lola Frank",
                    "similarity_ppm": 430_000,
                    "margin_ppm": 110_000,
                    "gaze_engaged": True,
                }
            ],
            now=t0 + timedelta(seconds=step),
        )
    assert snap["household_candidate"] is not None
    assert snap["household_candidate"]["member_id"] == "family-lola"
    assert snap["household_candidate"]["display_name"] == "Lola"
    assert snap["household_candidate"]["evidence_kind"] == FACE_EVIDENCE_KIND
    assert snap["gaze_to_talk_active"] is True
    assert snap["privacy_shield_active"] is False

    # 3. Legacy Eva1 neighbor/family label ("Mark" or "Oma Marjan") is NEVER treated as household
    #    and IMMEDIATELY engages the Bystander Privacy Shield!
    shield_snap = fusion.observe_camera_frame(
        source_id="camera.unifi_woonkamer",
        room_id="woonkamer",
        faces=[
            {
                "bbox": (260, 90, 360, 205),
                "det_score": 0.93,
                "sharpness": 0.86,
                "frontal_pose": 0.85,
                "status": "known_candidate",
                "person_label": "Mark",
                "similarity_ppm": 610_000,
                "margin_ppm": 240_000,
            }
        ],
        now=t0 + timedelta(seconds=4),
    )
    assert shield_snap["privacy_shield_active"] is True
    assert shield_snap["privacy_shield_reason"] == "non_household_acquaintance_present"
    assert shield_snap["household_candidate"] is None
    assert shield_snap["gaze_to_talk_active"] is False


def test_visitor_anti_spam_lingering_gate_and_owner_classification(tmp_path: Path) -> None:
    hh = HouseholdHeadSettings(tmp_path)
    fusion = BiometricPresenceFusion(tmp_path, hh)
    t0 = datetime(2026, 11, 13, 19, 0, 0, tzinfo=UTC)

    # Fleeting passer-by (only 2 frames over 1.5s) -> NO visitor review card, NO spam
    for dt_s in (0.0, 1.5):
        fusion.observe_camera_frame(
            source_id="camera.voordeur",
            room_id="gang",
            faces=[
                {
                    "bbox": (120, 80, 220, 195),
                    "det_score": 0.88,
                    "sharpness": 0.78,
                    "frontal_pose": 0.82,
                    "status": "unknown",
                }
            ],
            now=t0 + timedelta(seconds=dt_s),
        )
    assert fusion.list_visitors() == []

    # Lingering sharp frontal visitor at front door (>= 3 frames and >= 4.5s dwell) -> 1 quiet card!
    fusion.observe_camera_frame(
        source_id="camera.voordeur",
        room_id="gang",
        faces=[
            {
                "bbox": (124, 82, 224, 197),
                "det_score": 0.92,
                "sharpness": 0.85,
                "frontal_pose": 0.86,
                "status": "unknown",
            }
        ],
        now=t0 + timedelta(seconds=5.0),
    )
    visitors = fusion.list_visitors()
    assert len(visitors) == 1
    assert visitors[0]["chat_interrupt_sent"] is False
    assert visitors[0]["room_id"] == "gang"
    assert visitors[0]["dwell_seconds"] >= 4.5

    # Additional frame 2s later coalesces into the SAME card without creating duplicates
    fusion.observe_camera_frame(
        source_id="camera.voordeur",
        room_id="gang",
        faces=[
            {
                "bbox": (125, 82, 225, 198),
                "det_score": 0.94,
                "sharpness": 0.89,
                "frontal_pose": 0.90,
                "status": "unknown",
            }
        ],
        now=t0 + timedelta(seconds=7.0),
    )
    visitors_after = fusion.list_visitors()
    assert len(visitors_after) == 1
    assert visitors_after[0]["dwell_seconds"] >= 6.5

    # Owner classifies visitor as neighbor/acquaintance; file permissions stay 0o600
    updated = fusion.classify_visitor(
        visitor_id=visitors_after[0]["visitor_id"],
        classification="bekende_buur",
        note_nl="Buurman pakketje",
    )
    assert updated[0]["classification"] == "bekende_buur"
    assert stat.S_IMODE(fusion.visitor_file.stat().st_mode) == 0o600


def test_local_speaker_biometrics_jaap_and_lola_gain_invariance_and_guest_rejection(tmp_path: Path) -> None:
    hh = HouseholdHeadSettings(tmp_path)
    hh.update_member_or_invite(
        action="upsert_member",
        target="guest-piet",
        role="gast",
        display_name="Piet",
        telegram_user_id=999888777,
    )

    fusion = BiometricPresenceFusion(tmp_path, hh)
    jaap_pcm = _synth_voice_pcm16(f0_hz=112.0, formants_hz=(480.0, 1380.0, 2420.0), amplitude=11000.0)
    lola_pcm = _synth_voice_pcm16(f0_hz=218.0, formants_hz=(690.0, 2050.0, 3150.0), amplitude=10000.0)
    stranger_pcm = _synth_voice_pcm16(f0_hz=160.0, formants_hz=(310.0, 920.0, 3680.0), amplitude=10000.0)

    # Guest cannot be enrolled into household voiceprints
    with pytest.raises(PermissionError):
        fusion.speaker.enroll_pcm(member_id="guest-piet", pcm_bytes=jaap_pcm)

    r_jaap = fusion.speaker.enroll_pcm(member_id="owner-jaap", pcm_bytes=jaap_pcm)
    r_lola = fusion.speaker.enroll_pcm(member_id="family-lola", pcm_bytes=lola_pcm)
    assert r_jaap["display_name"] == "Jaap"
    assert r_lola["display_name"] == "Lola"
    assert stat.S_IMODE(fusion.speaker.state_file.stat().st_mode) == 0o600

    # Status summary never leaks voiceprint vectors
    summary = fusion.speaker.status_summary()
    assert "vector" not in json.dumps(summary)

    # Quiet (0.35x volume) Jaap utterance still matches Jaap thanks to gain-invariant MFCCs!
    jaap_quiet = _synth_voice_pcm16(f0_hz=114.0, formants_hz=(485.0, 1370.0, 2410.0), amplitude=3800.0)
    m_jaap = fusion.observe_voice_pcm(jaap_quiet)
    assert m_jaap["status"] == "household_voice_candidate"
    assert m_jaap["member_id"] == "owner-jaap"
    assert m_jaap["evidence_kind"] == VOICE_EVIDENCE_KIND

    # Lola utterance matches Lola
    lola_test = _synth_voice_pcm16(f0_hz=215.0, formants_hz=(685.0, 2040.0, 3130.0), amplitude=7500.0)
    m_lola = fusion.observe_voice_pcm(lola_test)
    assert m_lola["status"] == "household_voice_candidate"
    assert m_lola["member_id"] == "family-lola"

    # Stranger voice returns unknown_voice and activates Bystander Privacy Shield!
    m_stranger = fusion.observe_voice_pcm(stranger_pcm)
    assert m_stranger["status"] == "unknown_voice"
    assert fusion.is_privacy_shield_active()[0] is True

    # 192-D ECAPA-TDNN external embedding compatibility
    ecapa_vec = [math.sin(i * 0.17) for i in range(192)]
    fusion.speaker.enroll_embedding(member_id="owner-jaap", embedding=ecapa_vec)
    m_ecapa = fusion.speaker.match_embedding(ecapa_vec)
    assert m_ecapa["status"] == "household_voice_candidate"
    assert m_ecapa["space"] == "ecapa192"


def test_cast_v2_protobuf_framing_and_consent_ssrf_guards(tmp_path: Path) -> None:
    frame = encode_cast_v2_frame(
        source_id="sender-0",
        destination_id="receiver-0",
        namespace=NS_RECEIVER,
        payload={"type": "GET_STATUS", "requestId": 1},
    )
    (body_len,) = struct.unpack(">I", frame[:4])
    decoded = decode_cast_v2_body(frame[4 : 4 + body_len])
    assert decoded["source_id"] == "sender-0"
    assert decoded["destination_id"] == "receiver-0"
    assert decoded["namespace"] == NS_RECEIVER
    assert decoded["payload"] == {"type": "GET_STATUS", "requestId": 1}

    # Query secret tokens in Cast URLs are strictly rejected
    with pytest.raises(ValueError):
        validate_stage_cast_url("http://10.0.20.10:44710/internal/v1/dashboard/holostage?token=secret123")
    assert (
        validate_stage_cast_url("http://10.0.20.10:44710/internal/v1/dashboard/holostage?mode=tv_cast#stg_abc")
        .endswith("#stg_abc")
    )

    # Unapproved Cast device in HomeProprioceptionManager is blocked with PermissionError
    hp = HomeProprioceptionManager(tmp_path)
    with hp._locked():
        hp._write_unlocked(
            {
                "contract": "EvaHomeProprioceptionState/v1",
                "revision": 1,
                "updated_at": "2026-11-13T20:00:00Z",
                "last_scan_at": "2026-11-13T20:00:00Z",
                "scanned_subnets": ["10.0.20.0/24"],
                "devices": {
                    "cast_woonkamer_tv": {
                        "device_id": "cast_woonkamer_tv",
                        "ip": "10.0.20.55",
                        "port": 8009,
                        "display_name": "Google TV Woonkamer",
                        "room_id": "woonkamer",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "pending_consent",
                        "auth_requirement": "none",
                    },
                    "cast_loopback_ssrf": {
                        "device_id": "cast_loopback_ssrf",
                        "ip": "127.0.0.1",
                        "approved_ip": "127.0.0.1",
                        "port": 8009,
                        "approved_port": 8009,
                        "display_name": "Fake Loopback Cast",
                        "room_id": "woonkamer",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "approved",
                        "auth_requirement": "none",
                    },
                },
                "rooms": {},
                "organs": {},
            },
            datetime(2026, 11, 13, 20, 0, 0, tzinfo=UTC),
        )
    with pytest.raises(PermissionError):
        query_or_launch_approved_cast_device(hp, device_id="cast_woonkamer_tv")
    with pytest.raises(ValueError):
        query_or_launch_approved_cast_device(hp, device_id="cast_loopback_ssrf")


def test_sentence_boundary_chunker_emits_low_latency_first_clause() -> None:
    chunker = SentenceBoundaryChunker(first_clause_min_words=5, steady_min_words=8)
    emitted: list[str] = []
    stream_tokens = [
        "Hoi ",
        "Jaap, ",
        "ik ",
        "heb ",
        "**melk** ",
        "direct ",
        "toegevoegd. ",
        "Zal ",
        "ik ",
        "ook ",
        "kijken ",
        "of ",
        "er ",
        "nog ",
        "sushi-rijst ",
        "nodig ",
        "is?",
    ]
    first_emit_idx: int | None = None
    for idx, tok in enumerate(stream_tokens):
        clauses = chunker.feed(tok)
        if clauses and first_emit_idx is None:
            first_emit_idx = idx
        emitted.extend(clauses)
    emitted.extend(chunker.flush())

    # First clause emitted right at token 6 ("toegevoegd. ") BEFORE the second sentence even starts!
    assert first_emit_idx == 6
    assert emitted[0] == "Hoi Jaap, ik heb melk direct toegevoegd."
    assert "**" not in emitted[0]
    assert emitted[1] == "Zal ik ook kijken of er nog sushi-rijst nodig is?"


def test_holostage_api_auth_csrf_privacy_redaction_and_sub300ms_turn_stream(tmp_path: Path) -> None:
    owner_token = "owner-secret-dashboard-token-0123456789abcdef"

    class _FakeSettings:
        directory = tmp_path

    class _FakeOptionalProviders:
        settings = _FakeSettings()

    class _FakeRuntime:
        _optional_model_providers = _FakeOptionalProviders()
        _dashboard_owner_principal_id = "owner-jaap"
        owner_turn_calls = 0

        def dashboard_turn(self, text: str) -> tuple[ConversationTurn, GroundedReply]:
            self.owner_turn_calls += 1
            publish_conversation_text("Staat genoteerd op de lijst! ", tools_disabled=True, final_answer=True)
            publish_conversation_text("Ik lees hem zo voor.", tools_disabled=True, final_answer=True)
            turn = ConversationTurn.private(
                "tenant-1",
                "inst-1",
                "owner-jaap",
                "dashboard:owner",
                "msg-stage-1",
                text,
                "2026-11-13T21:00:00Z",
            )
            reply = GroundedReply(
                conversation_id=turn.conversation_id,
                message_id=turn.message_id,
                text="Staat genoteerd op de lijst! Ik lees hem zo voor.",
                generated_at="2026-11-13T21:00:00Z",
                provider="test-provider",
                evidence_memory_ids=(),
            )
            return turn, reply

        def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None:
            pass

    runtime = _FakeRuntime()
    app = FastAPI()
    install_dashboard_holostage(app, access=DashboardAccess(owner_token), runtime=runtime)
    client = TestClient(app)

    owner_headers = {
        "Authorization": f"Bearer {owner_token}",
        "X-HoloStage-Client": "1",
    }

    # 1. Unauthenticated state/action requests fail closed (401)
    assert client.get("/internal/v1/dashboard/holostage/state").status_code == 401
    assert client.post("/internal/v1/dashboard/holostage/action", json={"action": "shopping_clear_checked"}).status_code == 401

    # 2. Missing X-HoloStage-Client CSRF header fails closed (403)
    res_no_csrf = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"action": "shopping_add", "label": "Melk"},
    )
    assert res_no_csrf.status_code == 403

    # 3. Owner issues an ambient TV stage token (`allow_private_cards_when_alone = False`)
    issue_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_headers,
        json={
            "action": "issue_device_token",
            "label": "Woonkamer Chromecast",
            "form_factor": "tv_cast",
            "allow_private_cards_when_alone": False,
        },
    )
    assert issue_res.status_code == 200
    issued = issue_res.json()["issued"]
    raw_stg_token = issued["raw_token"]
    grant_id = issued["grant"]["grant_id"]

    # Verify raw token is NEVER stored on disk (only sha256)
    disk_json = (tmp_path / "holostage-state.json").read_text(encoding="utf-8")
    assert raw_stg_token not in disk_json

    # 4. Pair TV client via POST /session -> receives HttpOnly cookie
    tv_client = TestClient(app)
    pair_res = tv_client.post(
        "/internal/v1/dashboard/holostage/session",
        headers={"X-HoloStage-Client": "1"},
        json={"token": raw_stg_token},
    )
    assert pair_res.status_code == 200
    assert "eva_holostage_session" in tv_client.cookies

    # Cross-origin POST with cookie is rejected (403)
    csrf_attack = tv_client.post(
        "/internal/v1/dashboard/holostage/action",
        headers={"X-HoloStage-Client": "1", "Origin": "https://evil.example.org"},
        json={"action": "shopping_add", "label": "Spam"},
    )
    assert csrf_attack.status_code == 403

    # Ambient TV client gets SERVER-SIDE redacted state (household_today is None!)
    tv_state = tv_client.get("/internal/v1/dashboard/holostage/state?mode=tv_cast").json()
    assert tv_state["viewer"]["kind"] == "ambient_stage"
    assert tv_state["viewer"]["private_cards_visible"] is False
    assert tv_state["cards"]["household_today"] is None

    # Ambient TV client CANNOT call owner-only actions (403) and CANNOT invoke owner private runtime!
    assert (
        tv_client.post(
            "/internal/v1/dashboard/holostage/action",
            headers={"X-HoloStage-Client": "1"},
            json={"action": "issue_device_token", "label": "Hack", "form_factor": "pocket"},
        ).status_code
        == 403
    )
    tv_turn = tv_client.post(
        "/internal/v1/dashboard/holostage/turn-stream",
        headers={"X-HoloStage-Client": "1"},
        json={"text": "Zet sushi-rijst en komkommer op de boodschappenlijst"},
    )
    assert tv_turn.status_code == 200
    assert runtime.owner_turn_calls == 0  # Strictly zero private owner memory invocations!
    tv_events = [json.loads(line) for line in tv_turn.text.splitlines() if line.strip()]
    assert any(ev["type"] == "speech_clause" for ev in tv_events)

    # 5. Owner turn-stream invokes real owner runtime, streams sub-300ms speech_clause & updates shopping list!
    owner_turn = client.post(
        "/internal/v1/dashboard/holostage/turn-stream",
        headers=owner_headers,
        json={"text": "Zet verse melk op de boodschappenlijst"},
    )
    assert owner_turn.status_code == 200
    assert runtime.owner_turn_calls == 1
    owner_events = [json.loads(line) for line in owner_turn.text.splitlines() if line.strip()]
    clauses = [ev["text"] for ev in owner_events if ev["type"] == "speech_clause"]
    assert clauses == ["Staat genoteerd op de lijst!", "Ik lees hem zo voor."]
    final_stage = owner_events[-1]["stage"]
    shop_labels = [i["label"].lower() for i in final_stage["cards"]["shopping_list"]]
    assert "sushi-rijst" in shop_labels
    assert "komkommer" in shop_labels
    assert "verse melk" in shop_labels

    # 6. Revoking the TV grant immediately locks out the TV cookie (401)
    rev_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_headers,
        json={"action": "revoke_device_grant", "grant_id": grant_id},
    )
    assert rev_res.status_code == 200
    assert tv_client.get("/internal/v1/dashboard/holostage/state").status_code == 401


def test_audit_high01_and_medium01_camera_redaction_ambient_block_and_private_card_protection(tmp_path: Path) -> None:
    owner_token = "owner-secret-audit-token-0123456789abcdef"

    class _FakeSettings:
        directory = tmp_path

    class _FakeCtrl:
        settings = _FakeSettings()

    class _FakeRuntime:
        _model_provider_control = _FakeCtrl()
        _dashboard_owner_principal_id = "owner-jaap"

    app = FastAPI()
    install_dashboard_holostage(app, access=DashboardAccess(owner_token), runtime=_FakeRuntime())
    client = TestClient(app)
    owner_headers = {"Authorization": f"Bearer {owner_token}", "X-HoloStage-Client": "1"}

    # Owner creates a private spotlight card and a classified neighbor visitor card
    spot_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_headers,
        json={
            "action": "summon_spotlight",
            "title": "Privé Somtoday",
            "body_nl": "Rooster van Lola",
            "privacy_level": "private_household",
        },
    )
    assert spot_res.status_code == 200
    private_card_id = spot_res.json()["card"]["card_id"]

    mgr: HoloStageManager = client.app.state  # resolved on runtime; inspect via direct manager on tmp_path
    direct_mgr = HoloStageManager(tmp_path)
    t0 = datetime(2026, 11, 13, 15, 0, 0, tzinfo=UTC)
    for dt_s in (0.0, 2.0, 5.0):
        direct_mgr.biometrics.observe_camera_frame(
            source_id="camera.voordeur",
            room_id="gang",
            faces=[{"bbox": (100, 80, 220, 200), "det_score": 0.9, "sharpness": 0.85, "frontal_pose": 0.88, "status": "unknown"}],
            now=t0 + timedelta(seconds=dt_s),
        )
    vis_id = direct_mgr.biometrics.list_visitors()[0]["visitor_id"]
    direct_mgr.biometrics.classify_visitor(visitor_id=vis_id, classification="bekende_buur", note_nl="Buurvrouw")

    # Verify visitor eviction preserves classified 'bekende_buur' when > 24 unreviewed entries arrive
    for idx in range(28):
        base_t = t0 + timedelta(hours=idx + 1)
        for dt_s in (0.0, 2.0, 5.0):
            direct_mgr.biometrics.observe_camera_frame(
                source_id=f"camera.gate_{idx}",
                room_id="gang",
                faces=[{"bbox": (90, 70, 210, 190), "det_score": 0.9, "sharpness": 0.85, "frontal_pose": 0.88, "status": "unknown"}],
                now=base_t + timedelta(seconds=dt_s),
            )
    kept_visitors = direct_mgr.biometrics.list_visitors()
    assert len(kept_visitors) == 24
    assert any(v["visitor_id"] == vis_id and v["classification"] == "bekende_buur" for v in kept_visitors)

    # Issue ambient token & private kiosk token
    amb_tok = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_headers,
        json={"action": "issue_device_token", "label": "TV", "form_factor": "tv_cast", "allow_private_cards_when_alone": False},
    ).json()["issued"]["raw_token"]
    priv_tok = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_headers,
        json={"action": "issue_device_token", "label": "Kiosk", "form_factor": "unifi_screen", "allow_private_cards_when_alone": True},
    ).json()["issued"]["raw_token"]

    # Ambient stage is blocked from observe_camera_frame (403) and from dismissing private spotlight card (403)
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/action",
            headers={"X-HoloStage-Token": amb_tok, "X-HoloStage-Client": "1"},
            json={"action": "observe_camera_frame", "faces": []},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/action",
            headers={"X-HoloStage-Token": amb_tok, "X-HoloStage-Client": "1"},
            json={"action": "dismiss_spotlight", "card_id": private_card_id},
        ).status_code
        == 403
    )

    # Private stage trying to forge 'known_candidate: Jaap' alongside an unknown bystander:
    # 1) forged identity is sanitized away, 2) bystander shield activates, 3) raw 'biometrics' is NEVER returned (HIGH-01)!
    priv_cam = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers={"X-HoloStage-Token": priv_tok, "X-HoloStage-Client": "1"},
        json={
            "action": "observe_camera_frame",
            "source_id": "forged.source",
            "faces": [
                {
                    "bbox": (100, 80, 220, 200),
                    "det_score": 0.95,
                    "sharpness": 0.90,
                    "frontal_pose": 0.92,
                    "status": "known_candidate",
                    "person_label": "Jaap",
                    "similarity_ppm": 900_000,
                    "margin_ppm": 400_000,
                }
            ],
        },
    )
    assert priv_cam.status_code == 200
    priv_body = priv_cam.json()
    assert "biometrics" not in priv_body
    assert priv_body["stage"]["privacy_shield"]["active"] is True
    assert priv_body["stage"]["privacy_shield"]["household_candidate"] is None
    assert priv_body["stage"]["cards"]["visitors_review"] == []
    assert "Buurvrouw" not in json.dumps(priv_body)

    # While shield is active, even private_stage cannot dismiss the owner's private spotlight card (403)
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/action",
            headers={"X-HoloStage-Token": priv_tok, "X-HoloStage-Client": "1"},
            json={"action": "dismiss_spotlight", "card_id": private_card_id},
        ).status_code
        == 403
    )
    _ = mgr


def test_audit_high02_cast_approved_ip_pinning_path_allowlist_and_wallclock_deadline(tmp_path: Path) -> None:
    # Off-path URLs and stg_ tokens in query params are rejected
    with pytest.raises(ValueError):
        validate_stage_cast_url("http://10.0.20.10:44710/internal/v1/dashboard/cortex")
    with pytest.raises(ValueError):
        validate_stage_cast_url("http://10.0.20.10:44710/internal/v1/dashboard/holostage?pair=stg_0123456789")

    hp = HomeProprioceptionManager(tmp_path)
    with hp._locked():
        hp._write_unlocked(
            {
                "contract": "EvaHomeProprioceptionState/v1",
                "revision": 1,
                "updated_at": "2026-11-13T20:00:00Z",
                "last_scan_at": "2026-11-13T20:00:00Z",
                "scanned_subnets": ["10.0.20.0/24"],
                "devices": {
                    "cast_missing_pin": {
                        "device_id": "cast_missing_pin",
                        "ip": "10.0.20.61",
                        "approved_ip": None,
                        "port": 8009,
                        "display_name": "Unpinned Cast",
                        "room_id": "woonkamer",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "approved",
                    },
                    "cast_ip_drift": {
                        "device_id": "cast_ip_drift",
                        "ip": "10.0.20.99",
                        "approved_ip": "10.0.20.62",
                        "port": 8009,
                        "approved_port": 8009,
                        "display_name": "Spoofed Cast",
                        "room_id": "woonkamer",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "approved",
                    },
                },
                "rooms": {},
                "organs": {},
            },
            datetime(2026, 11, 13, 20, 0, 0, tzinfo=UTC),
        )

    with pytest.raises(PermissionError):
        query_or_launch_approved_cast_device(hp, device_id="cast_missing_pin")
    with pytest.raises(PermissionError):
        query_or_launch_approved_cast_device(hp, device_id="cast_ip_drift")

    # Verify wall-clock deadline aborts slowloris socket reads cleanly
    import time as _time
    from eva.application.cast_v2_protocol import _recv_exact

    s1, s2 = socket.socketpair()
    try:
        s1.sendall(b"\x00\x01")  # Only 2 of 4 expected bytes sent, then stalls
        t_start = _time.monotonic()
        with pytest.raises((TimeoutError, socket.timeout)):
            _recv_exact(s2, 4, _time.monotonic() + 0.12)  # type: ignore[arg-type]
        assert (_time.monotonic() - t_start) < 1.0
    finally:
        s1.close()
        s2.close()


def test_audit_medium02_unconditional_origin_csrf_ambient_voice_oracle_and_midstream_shield(tmp_path: Path) -> None:
    owner_token = "owner-secret-m02-token-0123456789abcdef"

    class _FakeSettings:
        directory = tmp_path

    class _FakeCtrl:
        settings = _FakeSettings()

    runtime_ref: dict[str, Any] = {}

    class _FakeRuntime:
        _model_provider_control = _FakeCtrl()
        _dashboard_owner_principal_id = "owner-jaap"

        def dashboard_turn(self, text: str) -> tuple[ConversationTurn, GroundedReply]:
            publish_conversation_text("Eerste veilige zin voor Jaap. ", tools_disabled=True, final_answer=True)
            # Simulate bystander walking into frame mid-stream!
            mgr_live: HoloStageManager = runtime_ref["mgr"]
            mgr_live.biometrics.set_manual_privacy_shield(True)
            publish_conversation_text("GEHEIM_PRIVÉ_AGENDA_DETAIL dat nooit mag lekken!", tools_disabled=True, final_answer=True)
            turn = ConversationTurn.private(
                "tenant-1", "inst-1", "owner-jaap", "dashboard:owner", "msg-m02", text, "2026-11-13T21:10:00Z"
            )
            reply = GroundedReply(
                conversation_id=turn.conversation_id,
                message_id=turn.message_id,
                text="Eerste veilige zin voor Jaap. GEHEIM_PRIVÉ_AGENDA_DETAIL dat nooit mag lekken!",
                generated_at="2026-11-13T21:10:00Z",
                provider="test-provider",
                evidence_memory_ids=(),
            )
            return turn, reply

        def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None:
            pass

    rt = _FakeRuntime()
    app = FastAPI()
    install_dashboard_holostage(app, access=DashboardAccess(owner_token), runtime=rt)
    client = TestClient(app)
    owner_headers = {"Authorization": f"Bearer {owner_token}", "X-HoloStage-Client": "1"}

    # 1. Cross-origin Origin header is rejected (403) even WITHOUT cookies on POST /session & Bearer POST /action
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/session",
            headers={"X-HoloStage-Client": "1", "Origin": "https://attacker.example"},
            json={"token": "stg_" + "a" * 43},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/action",
            headers={**owner_headers, "Origin": "https://attacker.example"},
            json={"action": "shopping_add", "label": "CrossOrigin"},
        ).status_code
        == 403
    )

    # 2. Enroll Jaap's voiceprint, then verify ambient_stage CANNOT use turn-stream as a voiceprint oracle via added_by
    jaap_pcm = _synth_voice_pcm16(f0_hz=112.0, formants_hz=(480.0, 1380.0, 2420.0), amplitude=11000.0)
    jaap_b64 = base64.b64encode(jaap_pcm).decode("ascii")
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/action",
            headers=owner_headers,
            json={"action": "enroll_voice_pcm", "member_id": "owner-jaap", "audio_pcm_b64": jaap_b64},
        ).status_code
        == 200
    )
    amb_tok = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_headers,
        json={"action": "issue_device_token", "label": "GedeeldeTV", "form_factor": "tv_cast", "allow_private_cards_when_alone": False},
    ).json()["issued"]["raw_token"]

    amb_turn = client.post(
        "/internal/v1/dashboard/holostage/turn-stream",
        headers={"X-HoloStage-Token": amb_tok, "X-HoloStage-Client": "1"},
        json={"text": "Zet appels op de boodschappenlijst", "audio_pcm_b64": jaap_b64},
    )
    assert amb_turn.status_code == 200
    amb_events = [json.loads(line) for line in amb_turn.text.splitlines() if line.strip()]
    added_appels = [
        i for i in amb_events[-1]["stage"]["cards"]["shopping_list"] if i["label"].lower() == "appels"
    ][0]
    assert added_appels["added_by"] == "GedeeldeTV"  # NOT "Jaap"!

    # 3. Mid-stream bystander shield activation cuts off further clauses and suppresses secret reply text!
    runtime_ref["mgr"] = getattr(rt, "_holostage_manager")
    owner_stream = client.post(
        "/internal/v1/dashboard/holostage/turn-stream",
        headers=owner_headers,
        json={"text": "Wat staat er in mijn privéagenda?"},
    )
    assert owner_stream.status_code == 200
    assert "GEHEIM_PRIVÉ_AGENDA_DETAIL" not in owner_stream.text
    evs = [json.loads(line) for line in owner_stream.text.splitlines() if line.strip()]
    assert evs[-1]["type"] == "final"
    assert evs[-1]["shield_interrupted"] is True
    assert "reply" not in evs[-1]


def test_audit_low01_spacefree_chunker_and_fast_non_shopping_regex(tmp_path: Path) -> None:
    chunker = SentenceBoundaryChunker()
    long_nospace = "A" * 300
    pieces = chunker.feed(long_nospace) + chunker.flush()
    assert len(pieces) == 2
    assert len(pieces[0]) == 180
    assert len(pieces[1]) == 120

    mgr = HoloStageManager(tmp_path)
    import time as _time

    t0 = _time.monotonic()
    adversarial = "zet " + ("heel veel woorden zonder einde " * 120)
    assert mgr.apply_spoken_stage_side_effects(adversarial) == []
    assert (_time.monotonic() - t0) < 0.02


def test_fullscreen_default_conjure_on_demand_livekit_html_and_manage_holostage_tool(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from eva.adapters.api.dashboard_holostage import HOLOSTAGE_HTML
    from eva.application.conversation import ConversationActionCall as ToolCall, ConversationConflict
    from eva.bootstrap.holostage_runtime import HoloStageConversationActions

    # 1. Verify HOLOSTAGE_HTML uses LiveKit continuous WebRTC voice and ZERO browser speechSynthesis / SpeechRecognition
    assert "/internal/v1/dashboard/holostage/voice/livekit-client.js" in HOLOSTAGE_HTML
    assert "LivekitClient.Room" in HOLOSTAGE_HTML
    assert "speechSynthesis" not in HOLOSTAGE_HTML
    assert "SpeechRecognition" not in HOLOSTAGE_HTML

    # 2. Verify initial stage state starts 100% full-screen with ZERO conjured cards
    mgr = HoloStageManager(tmp_path)
    init_state = mgr.build_stage_state(viewer_kind="owner", form_factor="cockpit")
    assert init_state["cards"]["conjured_cards"] == []
    assert init_state["cards"]["active_focus"] == "none"

    # 3. Spoken "Laat de boodschappenlijst zien" conjures shopping_list; "Haal weg" dismisses all cards
    mgr.apply_spoken_stage_side_effects("Laat de boodschappenlijst even zien", speaker_name="Jaap")
    assert mgr.build_stage_state(viewer_kind="owner")["cards"]["conjured_cards"] == ["shopping_list"]
    mgr.apply_spoken_stage_side_effects("Haal de kaarten maar weg", speaker_name="Jaap")
    assert mgr.build_stage_state(viewer_kind="owner")["cards"]["conjured_cards"] == []

    # 4. Private cards ('household_today', 'visitors_review') and private utterances are redacted on ambient TV
    mgr.conjure_card("household_today", allow_private=True)
    mgr.record_stage_utterance(eva_text="Privébericht voor Jaap", privacy_level="private_household")
    owner_st = mgr.build_stage_state(viewer_kind="owner", form_factor="cockpit")
    tv_st = mgr.build_stage_state(viewer_kind="ambient_stage", form_factor="tv_cast")
    assert "household_today" in owner_st["cards"]["conjured_cards"]
    assert owner_st["live_utterance"] is not None
    assert "household_today" not in tv_st["cards"]["conjured_cards"]
    assert tv_st["live_utterance"] is None

    # 5. Foreground `manage_holostage` tool enforces owner authority, conjures/dismisses, handles offline Cast, and leaks zero #stg_ tokens
    mgr.set_lan_stage_origin("http://10.0.20.46:44712")
    with mgr.proprioception._locked():
        mgr.proprioception._write_unlocked(
            {
                "contract": "EvaHomeProprioceptionState/v1",
                "revision": 1,
                "updated_at": "2026-11-14T14:00:00Z",
                "last_scan_at": "2026-11-14T14:00:00Z",
                "scanned_subnets": ["10.0.20.0/24"],
                "devices": {
                    "cast_schuur": {
                        "device_id": "cast_schuur",
                        "ip": "10.0.20.247",
                        "approved_ip": "10.0.20.247",
                        "port": 8009,
                        "approved_port": 8009,
                        "display_name": "Tv streaker schuur",
                        "room_id": "schuur",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "pending_consent",
                        "auth_requirement": "none",
                    }
                },
                "rooms": {},
                "organs": {},
            },
            datetime(2026, 11, 14, 14, 0, 0, tzinfo=UTC),
        )

    fake_rt = SimpleNamespace(_holostage_manager=mgr, _dashboard_owner_principal_id="owner-jaap")
    actions = HoloStageConversationActions(lambda: fake_rt)

    owner_turn = ConversationTurn.private(
        "tenant-1", "inst-1", "owner-jaap", "telegram:123", "msg-1", "Cast naar de schuur", "2026-11-14T15:00:00Z"
    )
    non_owner_turn = ConversationTurn.private(
        "tenant-1", "inst-1", "other-user", "telegram:999", "msg-2", "Cast naar de schuur", "2026-11-14T15:00:00Z"
    )
    with pytest.raises(ConversationConflict):
        actions.execute(non_owner_turn, ToolCall(call_id="c1", name="manage_holostage", arguments={"operation": "status"}))

    r_conj = actions.execute(
        owner_turn,
        ToolCall(
            call_id="c2",
            name="manage_holostage",
            arguments={"operation": "conjure_card", "card_kind": "custom_spotlight", "title": "Plan", "body_nl": "Avondeten"},
        ),
    )
    assert r_conj.output["status"] == "verified"
    assert "custom_spotlight" in mgr.build_stage_state(viewer_kind="owner")["cards"]["conjured_cards"]

    # Monkeypatch Cast V2 launch to simulate powered-off schuur Chromecast timeout cleanly without network access
    import eva.bootstrap.holostage_runtime as hr_mod

    orig_launch = hr_mod.query_or_launch_approved_cast_device
    hr_mod.query_or_launch_approved_cast_device = lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("port 8009 timeout"))
    try:
        r_cast = actions.execute(
            owner_turn,
            ToolCall(
                call_id="c3",
                name="manage_holostage",
                arguments={"operation": "cast_stage", "target_device": "schuur", "approve_device": True},
            ),
        )
    finally:
        hr_mod.query_or_launch_approved_cast_device = orig_launch

    assert r_cast.output["status"] == "device_offline"
    assert "stg_" not in json.dumps(r_cast.output)


def test_holostage_natural_standing_rig_lipsync_and_streaming_tts_contract() -> None:
    from eva.adapters.api.dashboard_holostage import HOLOSTAGE_HTML

    # Walking loop must NEVER be played automatically anymore
    assert "clipAction(gltf.animations[0]).play()" not in HOLOSTAGE_HTML
    # Natural standing bone rig + sphere-contoured 3D mouth + GPU jaw morph + WebRTC RTP lip-sync
    assert "buildSphereContouredMouth" in HOLOSTAGE_HTML
    assert "morphAttributes.position" in HOLOSTAGE_HTML
    assert "getSynchronizationSources" in HOLOSTAGE_HTML
    assert "silentSink" in HOLOSTAGE_HTML
    assert "'Head'," in HOLOSTAGE_HTML
    assert "poseBone('LeftForeArm'" in HOLOSTAGE_HTML
    assert "msg.type === 'speech_clause'" in HOLOSTAGE_HTML
    assert 'id="stage-tts-audio"' in HOLOSTAGE_HTML


def test_holostage_mobile_antijank_reconnect_and_idempotent_funnel_sync() -> None:
    from eva.adapters.api.dashboard_holostage import HOLOSTAGE_HTML

    # Mobile GPU fill-rate cap + RTP audio-level throttling + DOM signature memoization
    assert "powerPreference: 'high-performance'" in HOLOSTAGE_HTML
    assert "isPocketInit ? 1.35 : 1.75" in HOLOSTAGE_HTML
    assert "lastRtpSampleAt" in HOLOSTAGE_HTML
    assert "lastCardsSig" in HOLOSTAGE_HTML
    assert "lastAdminSig" in HOLOSTAGE_HTML
    # Transient mobile network handoff feedback
    assert "RoomEvent.Reconnecting" in HOLOSTAGE_HTML
    assert "RoomEvent.Reconnected" in HOLOSTAGE_HTML
    # Idempotent Tailscale Funnel sync script & 60s timer
    repo_root = Path(__file__).resolve().parents[3]
    sync_script = (repo_root / "deploy/dashboard/tailscale-funnel-sync.sh").read_text(encoding="utf-8")
    timer_unit = (repo_root / "deploy/dashboard/eva2-tailscale-funnel-sync.timer").read_text(encoding="utf-8")
    assert "check_funnel_configured" in sync_script
    assert 'if [[ "${funnel_active}" != "yes" ]]; then' in sync_script
    assert "OnUnitActiveSec=60s" in timer_unit



