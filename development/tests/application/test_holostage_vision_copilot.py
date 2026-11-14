"""Comprehensive functional, privacy & adversarial tests for Eva HoloStage Live Vision Co-Pilot,
Lifelike 3D Presence (eye-blinks, iris saccades, Liquid-Core neural field), and LiveKit state sync.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
from typing import Any
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx2
import pytest

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_holostage import HOLOSTAGE_HTML, install_dashboard_holostage
import eva.adapters.model.holostage_vision as holostage_vision_mod
from eva.adapters.model.holostage_vision import (
    HoloStageVisionBridge,
    HoloStageVisionError,
    VisualPerceptionReceipt,
    _load_lease_credentials,
    sanitize_visual_frame_bytes,
)
from eva.application.conversation import ConversationTurn, GroundedReply, publish_conversation_text
from eva.application.holostage import HoloStageManager
from eva.bootstrap.holostage_runtime import HoloStageConversationActions


def _build_synthetic_jpeg(*, with_exif_gps: bool = True, with_comment: bool = True) -> bytes:
    """Build a valid minimal JPEG bitstream (>= 64 bytes) with optional APP1 EXIF/GPS & COM metadata."""
    out = bytearray(b"\xff\xd8")  # SOI
    app0_body = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out.extend(b"\xff\xe0" + struct.pack(">H", len(app0_body) + 2) + app0_body)
    if with_exif_gps:
        exif_body = b"Exif\x00\x00II*\x00GPS_SECRET_COORDINATES_52.0907_5.1214"
        out.extend(b"\xff\xe1" + struct.pack(">H", len(exif_body) + 2) + exif_body)
    if with_comment:
        com_body = b"SECRET_CAMERA_SERIAL_NUMBER_998877"
        out.extend(b"\xff\xfe" + struct.pack(">H", len(com_body) + 2) + com_body)
    # DQT + SOF0 + DHT + SOS + scan data + EOI
    dqt_body = b"\x00" + bytes([16] * 64)
    out.extend(b"\xff\xdb" + struct.pack(">H", len(dqt_body) + 2) + dqt_body)
    sof_body = b"\x08\x00\x08\x00\x08\x01\x01\x11\x00"
    out.extend(b"\xff\xc0" + struct.pack(">H", len(sof_body) + 2) + sof_body)
    sos_hdr = b"\x01\x01\x00\x00\x3f\x00"
    scan_entropy = b"\x7f\x3a\x91\x04\x55\x88\xbc\x12"
    out.extend(b"\xff\xda" + struct.pack(">H", len(sos_hdr) + 2) + sos_hdr + scan_entropy + b"\xff\xd9")
    return bytes(out)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _build_synthetic_png(*, with_text_meta: bool = True) -> bytes:
    """Build a valid 8x8 RGB PNG (>= 64 bytes) with optional tEXt/eXIf metadata chunks."""
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    out.extend(_png_chunk(b"IHDR", ihdr))
    if with_text_meta:
        out.extend(_png_chunk(b"tEXt", b"Author\x00PRIVATE_DEVICE_OWNER_META"))
        out.extend(_png_chunk(b"eXIf", b"II*\x00HIDDEN_EXIF_PAYLOAD"))
    raw_rows = b"".join(b"\x00" + (b"\x10\x80\xc0" * 8) for _ in range(8))
    out.extend(_png_chunk(b"IDAT", zlib.compress(raw_rows)))
    out.extend(_png_chunk(b"IEND", b""))
    return bytes(out)


def test_sanitize_visual_frame_strips_exif_comments_and_rejects_invalid_formats() -> None:
    raw_jpeg = _build_synthetic_jpeg(with_exif_gps=True, with_comment=True)
    assert b"GPS_SECRET_COORDINATES" in raw_jpeg
    assert b"SECRET_CAMERA_SERIAL_NUMBER" in raw_jpeg

    clean_jpeg, mime_j, sha_j = sanitize_visual_frame_bytes(raw_jpeg, "image/jpeg")
    assert mime_j == "image/jpeg"
    assert len(sha_j) == 64
    assert clean_jpeg.startswith(b"\xff\xd8\xff\xe0")
    assert clean_jpeg.endswith(b"\xff\xd9")
    assert b"GPS_SECRET_COORDINATES" not in clean_jpeg
    assert b"SECRET_CAMERA_SERIAL_NUMBER" not in clean_jpeg

    raw_png = _build_synthetic_png(with_text_meta=True)
    assert b"PRIVATE_DEVICE_OWNER_META" in raw_png
    assert b"HIDDEN_EXIF_PAYLOAD" in raw_png

    clean_png, mime_p, sha_p = sanitize_visual_frame_bytes(raw_png, "image/png")
    assert mime_p == "image/png"
    assert len(sha_p) == 64
    assert b"PRIVATE_DEVICE_OWNER_META" not in clean_png
    assert b"HIDDEN_EXIF_PAYLOAD" not in clean_png

    # Reject SVG/XSS, tiny payloads, truncated JPEGs, and oversized buffers
    svg_payload = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script>" + (b" " * 40) + b"</svg>"
    with pytest.raises(ValueError, match="Alleen JPEG- en PNG"):
        sanitize_visual_frame_bytes(svg_payload, "image/svg+xml")
    with pytest.raises(ValueError, match="te klein"):
        sanitize_visual_frame_bytes(b"\xff\xd8\xff\xd9", "image/jpeg")
    with pytest.raises(ValueError):
        sanitize_visual_frame_bytes(raw_jpeg[:-2], "image/jpeg")
    with pytest.raises(ValueError, match="1,25 MB"):
        sanitize_visual_frame_bytes(b"\xff\xd8\xff" + (b"\x00" * 1_260_000), "image/jpeg")


def test_vision_bridge_local_first_stateless_codex_fallback_and_secret_scrubbing() -> None:
    raw_jpeg = _build_synthetic_jpeg()
    captured_requests: list[dict[str, Any]] = []

    def _mock_handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization", ""),
                "body": body,
            }
        )
        if request.url.host == "127.0.0.1":
            return httpx2.Response(
                200,
                json={"choices": [{"message": {"content": "Een pak havermelk en twee citroenen op het aanrecht."}}]},
            )
        sse_lines = (
            'data: {"type":"response.output_text.delta","delta":"Ik zie een Bosch vaatwasser "}\n\n'
            'data: {"type":"response.output_text.delta","delta":"met foutcode E24 op het display."}\n\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":310,"output_tokens":18,"total_tokens":328}}}\n\n'
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse_lines.encode("utf-8"))

    transport = httpx2.MockTransport(_mock_handler)

    # 1. Public HTTP local_vision_url MUST be rejected (ignored) so frames never traverse plaintext WAN
    bridge_insecure_local = HoloStageVisionBridge(
        local_vision_url="http://example.com:8080/v1",
        local_vision_model="llava",
        credential_loader=lambda: ("SUPER_SECRET_BEARER_TOKEN_XYZ", "acct-123"),
        http_transport=transport,
    )
    r_codex = bridge_insecure_local.analyze_frame(raw_jpeg, media_type="image/jpeg", focus_hint="Welke foutcode staat er?")
    assert r_codex.backend == "codex_stateless_vision"
    assert "foutcode E24" in r_codex.summary_nl
    assert r_codex.total_tokens == 328
    assert len(captured_requests) == 1
    sent_codex = captured_requests[0]["body"]
    assert sent_codex["store"] is False
    assert sent_codex["tool_choice"] == "none"
    # Verify EXIF was stripped BEFORE building data URI!
    img_url = sent_codex["input"][0]["content"][1]["image_url"]
    assert img_url.startswith("data:image/jpeg;base64,")
    decoded_sent = base64.b64decode(img_url.split(",", 1)[1])
    assert b"GPS_SECRET_COORDINATES" not in decoded_sent

    # 2. Loopback/LAN local vision is preferred first when configured and reachable
    captured_requests.clear()
    bridge_local = HoloStageVisionBridge(
        local_vision_url="http://127.0.0.1:44740/v1",
        local_vision_model="qwen2.5-vl-local",
        credential_loader=lambda: ("SUPER_SECRET_BEARER_TOKEN_XYZ", "acct-123"),
        http_transport=transport,
    )
    r_local = bridge_local.analyze_frame(raw_jpeg, media_type="image/jpeg")
    assert r_local.backend == "local_vision"
    assert "havermelk" in r_local.summary_nl
    assert len(captured_requests) == 1
    assert captured_requests[0]["url"] == "http://127.0.0.1:44740/v1/chat/completions"

    # 3. Upstream failure never leaks bearer token or raw upstream response body
    def _failing_handler(_req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, text="upstream failed with SUPER_SECRET_BEARER_TOKEN_XYZ")

    bridge_fail = HoloStageVisionBridge(
        credential_loader=lambda: ("SUPER_SECRET_BEARER_TOKEN_XYZ", "acct-123"),
        http_transport=httpx2.MockTransport(_failing_handler),
    )
    with pytest.raises(HoloStageVisionError) as exc_info:
        bridge_fail.analyze_frame(raw_jpeg)
    assert "SUPER_SECRET_BEARER_TOKEN_XYZ" not in str(exc_info.value)


def test_zero_disk_retention_ttl_shield_wipe_and_single_core_active_context(tmp_path: Path) -> None:
    mgr = HoloStageManager(tmp_path)
    t0 = datetime(2026, 11, 14, 22, 0, 0, tzinfo=UTC)

    entry = mgr.record_ephemeral_visual_context(
        summary_nl="Twee pakken biologische melk en een geopend kookboek op pagina 42.",
        source_label="Eva Pocket camera",
        image_sha256="a" * 64,
        ttl_seconds=90,
        now=t0,
    )
    assert entry["remaining_seconds"] == 90

    # Force a disk write via shopping_add and verify holostage-state.json NEVER persists visual context!
    mgr.add_shopping_item(label="Biologische melk", added_by="Jaap", now=t0)
    disk_bytes = mgr.state_file.read_text(encoding="utf-8")
    assert "kookboek op pagina 42" not in disk_bytes
    assert entry["observation_id"] not in disk_bytes

    # Active context injects visual perception into Single Core for private owner voice & dashboard turns
    fake_rt = SimpleNamespace(_holostage_manager=mgr, _dashboard_owner_principal_id="owner-jaap")
    actions = HoloStageConversationActions(lambda: fake_rt)

    voice_turn = ConversationTurn.private(
        "tenant-1", "inst-1", "owner-jaap", "voice:room-1", "msg-v1", "Moet ik er nog een kopen?", "2026-11-14T22:00:10Z"
    )
    non_owner_turn = ConversationTurn.private(
        "tenant-1", "inst-1", "family-lola", "telegram:555", "msg-v2", "Hoi Eva", "2026-11-14T22:00:10Z"
    )
    ctx_owner = actions.active_context(voice_turn)
    assert any("kookboek op pagina 42" in line for line in ctx_owner)
    assert actions.active_context(non_owner_turn) == ()

    # Expires automatically after 90s TTL
    assert mgr.get_ephemeral_visual_context(now=t0 + timedelta(seconds=89)) is not None
    assert mgr.get_ephemeral_visual_context(now=t0 + timedelta(seconds=91)) is None

    # Bystander Privacy Shield immediately wipes in-memory visual context and blocks new captures
    mgr.record_ephemeral_visual_context(
        summary_nl="Gevoelige brief op bureau.",
        source_label="Eva Pocket camera",
        ttl_seconds=90,
        now=t0 + timedelta(seconds=100),
    )
    mgr.biometrics.set_manual_privacy_shield(True, now=t0 + timedelta(seconds=102))
    assert mgr.get_ephemeral_visual_context(now=t0 + timedelta(seconds=103)) is None
    with pytest.raises(PermissionError):
        mgr.record_ephemeral_visual_context(
            summary_nl="Mag niet opgeslagen worden",
            now=t0 + timedelta(seconds=104),
        )


def test_holostage_vision_turn_endpoint_auth_grants_context_only_and_streaming(tmp_path: Path) -> None:
    owner_token = "owner-secret-vision-token-2026-0123456789"
    seen_dashboard_prompts: list[tuple[str, str | None]] = []

    class _StubVisionBridge:
        available = True

        def analyze_frame(self, raw_bytes: bytes, *, media_type: str = "image/jpeg", focus_hint: str = "") -> VisualPerceptionReceipt:
            clean, clean_mime, sha = sanitize_visual_frame_bytes(raw_bytes, media_type)
            return VisualPerceptionReceipt(
                summary_nl=f"Zichtbaar op aanrecht: rode paprika en rijst (vraag: {focus_hint or 'geen'}).",
                image_sha256=sha,
                media_type=clean_mime,
                sanitized_bytes=len(clean),
                backend="codex_vision",
                model="gpt-6-astra",
                latency_ms=412.5,
            )

    class _FakeCtrl:
        settings = SimpleNamespace(directory=tmp_path)

    class _FakeRuntime:
        _model_provider_control = _FakeCtrl()
        _dashboard_owner_principal_id = "owner-jaap"
        _holostage_vision = _StubVisionBridge()

        def dashboard_turn(self, text: str) -> tuple[ConversationTurn, GroundedReply]:
            mgr_now: HoloStageManager = getattr(self, "_holostage_manager")
            active_vis = mgr_now.get_ephemeral_visual_context()
            seen_dashboard_prompts.append((text, None if active_vis is None else active_vis["summary_nl"]))
            reply_txt = "Ik zie de rode paprika en rijst op je aanrecht liggen! Zal ik sojasaus op de lijst zetten?"
            publish_conversation_text(reply_txt, tools_disabled=True, final_answer=True)
            turn = ConversationTurn.private(
                "tenant-1", "inst-1", "owner-jaap", "dashboard:owner", "msg-vis-1", text, "2026-11-14T22:30:00Z"
            )
            reply = GroundedReply(
                conversation_id=turn.conversation_id,
                message_id=turn.message_id,
                text=reply_txt,
                generated_at="2026-11-14T22:30:00Z",
                provider="single-core-test",
                evidence_memory_ids=(),
            )
            return turn, reply

        def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None:
            pass

    rt = _FakeRuntime()
    app = FastAPI()
    install_dashboard_holostage(app, access=DashboardAccess(owner_token), runtime=rt)
    client = TestClient(app)
    owner_hdrs = {"Authorization": f"Bearer {owner_token}", "X-HoloStage-Client": "1"}
    jpeg_b64 = base64.b64encode(_build_synthetic_jpeg()).decode("ascii")

    # 1. Unauthenticated & missing CSRF header rejected
    assert client.post("/internal/v1/dashboard/holostage/vision-turn", json={"image_b64": jpeg_b64}).status_code == 401
    assert (
        client.post(
            "/internal/v1/dashboard/holostage/vision-turn",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"image_b64": jpeg_b64},
        ).status_code
        == 403
    )

    # 2. Issue non-owner Pocket grant (Lola) and owner Pocket grant (Jaap, allow_owner_voice=True) + Cast TV grant
    lola_tok = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={
            "action": "issue_device_token",
            "label": "Lola Pocket",
            "form_factor": "pocket",
            "allow_private_cards_when_alone": True,
            "allow_owner_voice": False,
        },
    ).json()["issued"]["raw_token"]
    jaap_pocket_tok = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={
            "action": "issue_device_token",
            "label": "Jaap Eva Pocket",
            "form_factor": "pocket",
            "allow_private_cards_when_alone": True,
            "allow_owner_voice": True,
        },
    ).json()["issued"]["raw_token"]
    tv_tok = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={
            "action": "issue_device_token",
            "label": "Woonkamer TV",
            "form_factor": "tv_cast",
            "allow_private_cards_when_alone": False,
        },
    ).json()["issued"]["raw_token"]

    # Non-owner-voice grant & TV Cast grant are forbidden from vision-turn (403)
    for denied_tok in (lola_tok, tv_tok):
        res_denied = client.post(
            "/internal/v1/dashboard/holostage/vision-turn",
            headers={"X-HoloStage-Token": denied_tok, "X-HoloStage-Client": "1"},
            json={"image_b64": jpeg_b64, "media_type": "image/jpeg"},
        )
        assert res_denied.status_code == 403

    # 3. Jaap's Eva Pocket grant (`allow_owner_voice=True`) succeeds in `context_only=True` (LiveKit voice co-pilot mode)
    jaap_hdrs = {"X-HoloStage-Token": jaap_pocket_tok, "X-HoloStage-Client": "1"}
    res_ctx = client.post(
        "/internal/v1/dashboard/holostage/vision-turn",
        headers=jaap_hdrs,
        json={
            "image_b64": jpeg_b64,
            "media_type": "image/jpeg",
            "text": "Wat kan ik hiermee koken?",
            "mode": "pocket",
            "context_only": True,
        },
    )
    assert res_ctx.status_code == 200
    body_ctx = res_ctx.json()
    assert body_ctx["contract"] == "EvaHoloStageVisionTurn/v1"
    assert body_ctx["context_only"] is True
    assert body_ctx["stage"]["viewer"]["can_use_camera"] is True
    assert body_ctx["stage"]["visual_copilot"]["active"] is True
    assert body_ctx["stage"]["visual_copilot"]["observation_id"] == body_ctx["observation_id"]

    # Non-owner viewer never sees Jaap's active `visual_copilot` badge
    lola_state = client.get(
        "/internal/v1/dashboard/holostage/state?mode=pocket",
        headers={"X-HoloStage-Token": lola_tok},
    ).json()
    assert lola_state["viewer"]["can_use_camera"] is False
    assert lola_state["visual_copilot"]["active"] is False

    # 4. Streaming `context_only=False` runs through Eva's Single Core with active visual context!
    res_stream = client.post(
        "/internal/v1/dashboard/holostage/vision-turn",
        headers=jaap_hdrs,
        json={
            "image_b64": jpeg_b64,
            "media_type": "image/jpeg",
            "text": "Wat ligt er op het aanrecht?",
            "mode": "pocket",
            "context_only": False,
        },
    )
    assert res_stream.status_code == 200
    events = [json.loads(line) for line in res_stream.text.splitlines() if line.strip()]
    assert events[0]["type"] == "vision_ack"
    assert any(ev["type"] == "speech_clause" for ev in events)
    assert events[-1]["type"] == "final"
    assert "rode paprika en rijst" in events[-1]["text"]
    assert seen_dashboard_prompts[-1][0] == "Wat ligt er op het aanrecht?"
    assert "rode paprika en rijst" in (seen_dashboard_prompts[-1][1] or "")

    # 5. Jaap can explicitly clear visual context from Eva Pocket (`clear_visual_context`)
    res_clr = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=jaap_hdrs,
        json={"action": "clear_visual_context"},
    )
    assert res_clr.status_code == 200
    assert res_clr.json()["stage"]["visual_copilot"]["active"] is False


def test_holostage_3d_eyeblink_liquid_neural_field_livekit_attributes_and_lan_proxy() -> None:
    # 1. Verify 3D eye-blink, iris micro-saccade, Liquid-Core neural field, LiveKit attributes & Camera PIP in HTML
    assert "buildContouredEyeRig" in HOLOSTAGE_HTML
    assert "buildLiquidNeuralField" in HOLOSTAGE_HTML
    assert "ParticipantAttributesChanged" in HOLOSTAGE_HTML
    assert "lk.agent.state" in HOLOSTAGE_HTML
    assert "ActiveSpeakersChanged" in HOLOSTAGE_HTML
    assert "rtcInferredSpeaking" in HOLOSTAGE_HTML
    assert "cameraOpenSeq" in HOLOSTAGE_HTML
    assert "/internal/v1/dashboard/holostage/vision-turn" in HOLOSTAGE_HTML
    assert "env(safe-area-inset-bottom" in HOLOSTAGE_HTML
    assert 'name="apple-mobile-web-app-capable"' in HOLOSTAGE_HTML

    # 2. Verify LAN/Funnel proxy (`deploy/dashboard/holostage-lan.mjs`) allowlists `/vision-turn` only under stage scope
    repo_root = Path(__file__).resolve().parents[3]
    lan_proxy_src = (repo_root / "deploy/dashboard/holostage-lan.mjs").read_text(encoding="utf-8")
    assert "pathname === '/internal/v1/dashboard/holostage/vision-turn'" in lan_proxy_src


def test_progressive_multiscan_jpeg_and_mpf_ultrahdr_trailing_exif_stripped() -> None:
    """Verify multi-scan progressive JPEGs strip interleaved APP1/COM and truncate trailing MPF/UltraHDR JPEGs."""
    out = bytearray(b"\xff\xd8")
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out.extend(b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0)
    dqt = b"\x00" + bytes([16] * 64)
    out.extend(b"\xff\xdb" + struct.pack(">H", len(dqt) + 2) + dqt)
    sof2 = b"\x08\x00\x08\x00\x08\x01\x01\x11\x00"
    out.extend(b"\xff\xc2" + struct.pack(">H", len(sof2) + 2) + sof2)  # SOF2 progressive
    sos1_hdr = b"\x01\x01\x00\x00\x00\x00"
    # Entropy scan #1 includes byte-stuffed 0xFF00 and restart marker 0xFFD0 (RST0)
    sos1_entropy = b"\x12\xff\x00\x34\xff\xd0\x56\x78"
    out.extend(b"\xff\xda" + struct.pack(">H", len(sos1_hdr) + 2) + sos1_hdr + sos1_entropy)

    # Interleaved APP1 EXIF & COM between progressive scans!
    interleaved_exif = b"Exif\x00\x00INTERLEAVED_EXIF_GPS_SECRET_52.37"
    out.extend(b"\xff\xe1" + struct.pack(">H", len(interleaved_exif) + 2) + interleaved_exif)
    interleaved_com = b"INTERLEAVED_COMMENT_SECRET_SERIAL"
    out.extend(b"\xff\xfe" + struct.pack(">H", len(interleaved_com) + 2) + interleaved_com)

    # Progressive scan #2 + primary EOI
    sos2_hdr = b"\x01\x01\x00\x01\x3f\x00"
    sos2_entropy = b"\x9a\xbc\xff\x00\xde"
    out.extend(b"\xff\xda" + struct.pack(">H", len(sos2_hdr) + 2) + sos2_hdr + sos2_entropy + b"\xff\xd9")

    # Appended secondary MPF / UltraHDR GainMap JPEG after primary EOI containing unstripped EXIF!
    mpf_tail = bytearray(b"\xff\xd8")
    mpf_exif = b"Exif\x00\x00ULTRAHDR_SECONDARY_GAINMAP_EXIF_SECRET"
    mpf_tail.extend(b"\xff\xe1" + struct.pack(">H", len(mpf_exif) + 2) + mpf_exif)
    mpf_tail.extend(b"\xff\xd9")
    raw_poly_jpeg = bytes(out) + bytes(mpf_tail)

    assert b"INTERLEAVED_EXIF_GPS_SECRET_52.37" in raw_poly_jpeg
    assert b"ULTRAHDR_SECONDARY_GAINMAP_EXIF_SECRET" in raw_poly_jpeg

    clean_jpeg, mime, _sha = sanitize_visual_frame_bytes(raw_poly_jpeg, "image/jpeg")
    assert mime == "image/jpeg"
    assert clean_jpeg.endswith(b"\xff\xd9")
    assert b"INTERLEAVED_EXIF_GPS_SECRET_52.37" not in clean_jpeg
    assert b"INTERLEAVED_COMMENT_SECRET_SERIAL" not in clean_jpeg
    assert b"ULTRAHDR_SECONDARY_GAINMAP_EXIF_SECRET" not in clean_jpeg
    # Verify entropy byte-stuffing (0xFF00) and restart marker (0xFFD0) survived intact
    assert b"\x12\xff\x00\x34\xff\xd0\x56\x78" in clean_jpeg
    assert b"\x9a\xbc\xff\x00\xde" in clean_jpeg


def test_lease_credential_loader_real_file_and_endpoint_runtime_error_scrubbing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify real EvaModelAccessLease/v1 disk loading and scrubbed 502 error handling on raw RuntimeError."""
    monkeypatch.setattr(holostage_vision_mod, "_MODEL_LEASE_OWNER_UID", os.getuid())
    monkeypatch.setattr(holostage_vision_mod, "_MODEL_LEASE_READER_GID", os.getgid())

    lease_file = tmp_path / "model-access-lease.json"
    lease_payload = {
        "contract": "EvaModelAccessLease/v1",
        "source_sha256": "b" * 64,
        "access_token": "LEASE_TOKEN_SECRET_VALUE_888",
        "account_id": "acct-jaap-01",
    }
    lease_file.write_text(json.dumps(lease_payload), encoding="utf-8")
    lease_file.chmod(0o640)

    tok, acct = _load_lease_credentials(lease_file)
    assert tok == "LEASE_TOKEN_SECRET_VALUE_888"
    assert acct == "acct-jaap-01"

    # Verify from_runtime_settings wires _load_lease_credentials cleanly end-to-end
    settings = SimpleNamespace(conversation_auth_file=str(lease_file), conversation_model="gpt-5.4")
    bridge = HoloStageVisionBridge.from_runtime_settings(settings)
    assert bridge.available is True

    # Wrong file mode (0o644) or corrupt JSON must raise scrubbed HoloStageVisionError("credentials") inside analyze_frame
    lease_file.chmod(0o644)
    with pytest.raises(HoloStageVisionError) as exc_perm:
        bridge.analyze_frame(_build_synthetic_jpeg())
    assert exc_perm.value.category == "credentials"
    assert str(lease_file) not in exc_perm.value.detail_nl

    lease_file.chmod(0o640)
    lease_file.write_text("{corrupt_json_LEASE_TOKEN_SECRET_VALUE_888", encoding="utf-8")
    with pytest.raises(HoloStageVisionError) as exc_json:
        bridge.analyze_frame(_build_synthetic_jpeg())
    assert exc_json.value.category == "credentials"
    assert "LEASE_TOKEN_SECRET_VALUE_888" not in exc_json.value.detail_nl

    # Verify `/vision-turn` scrubs raw RuntimeError without leaking internal details
    owner_token = "owner-secret-scrub-token-2026-9999999999"

    class _ExplodingBridge:
        available = True

        def analyze_frame(self, raw_bytes: bytes, *, media_type: str = "image/jpeg", focus_hint: str = "") -> VisualPerceptionReceipt:
            raise RuntimeError("INTERNAL_SECRET_TRACE_999_PATH_/run/secrets/auth.json")

    class _FakeRuntime:
        _model_provider_control = SimpleNamespace(settings=SimpleNamespace(directory=tmp_path))
        _dashboard_owner_principal_id = "owner-jaap"
        _holostage_vision = _ExplodingBridge()

    app = FastAPI()
    install_dashboard_holostage(app, access=DashboardAccess(owner_token), runtime=_FakeRuntime())
    client = TestClient(app)
    res_502 = client.post(
        "/internal/v1/dashboard/holostage/vision-turn",
        headers={"Authorization": f"Bearer {owner_token}", "X-HoloStage-Client": "1"},
        json={"image_b64": base64.b64encode(_build_synthetic_jpeg()).decode("ascii")},
    )
    assert res_502.status_code == 502
    assert res_502.json()["detail"] == "Beeldanalyse tijdelijk niet beschikbaar."
    assert "INTERNAL_SECRET_TRACE_999" not in res_502.text
