"""Regression tests for Nov 14 production fixes: Hindsight null source_facts, status code guidance, camera snapshot buffer, and Funnel public_stage_origin."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eva.adapters.api.app import DashboardAccess
from eva.adapters.api.dashboard_holostage import install_dashboard_holostage
from eva.adapters.connectors.camera_observer import CameraDevice, CameraObservationSource
from eva.adapters.connectors.camera_snapshot_buffer import CameraSnapshotRingBuffer
from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.adapters.postgres import delegated_work_actions as delegated_actions_module
from eva.adapters.projection.hindsight import (
    HindsightMemoryEngine,
    HindsightProjectionProtocolError,
)
from eva.application.conversation import ConversationActionCall, ConversationTurn
from eva.application.holostage import HoloStageManager
from eva.application.outbound_media_claims import claim_telegram_outbound_photo
from eva.application.sensory import SensoryEvent
from eva.bootstrap.camera_photo_runtime import CameraPhotoConversationActions
import threading


def _valid_jpeg(tag: bytes = b"frame1") -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + tag + b"\xff\xd9"


def test_hindsight_recall_handles_null_source_facts_without_crashing():
    engine = object.__new__(HindsightMemoryEngine)
    engine._config = SimpleNamespace(recall_budget="low", recall_max_tokens=1024)  # type: ignore[attr-defined]
    calls: list[tuple] = []

    def fake_request(method: str, path: str, body: dict | None = None, **_kwargs):
        calls.append((method, path, body))
        return 200, {
            "results": [
                {
                    "id": "mem-1",
                    "type": "world",
                    "text": "Het werkt inderdaad!",
                }
            ],
            "source_facts": None,
        }

    engine._request = fake_request  # type: ignore[attr-defined]
    fake_query = SimpleNamespace(requested_valid_at="2026-11-14T16:00:00Z", query_purpose="turn_context")
    rows = engine._recall_response(
        "bank-test",
        "owner-jaap",
        fake_query,  # type: ignore[arg-type]
        "werkt het",
        None,
        None,
    )
    assert len(rows) == 1
    assert rows[0]["text"] == "Het werkt inderdaad!"

    def bad_request(*_args, **_kwargs):
        return 200, {"results": [], "source_facts": ["invalid-list"]}

    engine._request = bad_request  # type: ignore[attr-defined]
    with pytest.raises(HindsightProjectionProtocolError):
        engine._recall_response(
            "bank-test",
            "owner-jaap",
            fake_query,  # type: ignore[arg-type]
            "werkt het",
            None,
            None,
        )


def test_delegated_work_guidance_forbids_echoing_internal_status_tokens():
    descriptors = {
        item.name: item
        for item in delegated_actions_module.PostgresDelegatedWorkActions.conversation_tools()
    }
    revise_guidance = descriptors["revise_digital_work"].completion_guidance or ""
    assert "nooit letterlijk" in revise_guidance
    assert "Rapporteer operation_result.status." not in revise_guidance


def test_camera_snapshot_ring_buffer_observer_and_telegram_photo_claim(tmp_path):
    buf_dir = tmp_path / "camera-snapshots"
    buf = CameraSnapshotRingBuffer(buf_dir, max_items=3, ttl_seconds=3600)
    now = datetime(2026, 11, 14, 16, 0, 0, tzinfo=UTC)

    snap1 = CameraSnapshot("camera.achtertuin", "image/jpeg", _valid_jpeg(b"tuin1"), now)
    meta1 = buf.record_snapshot(
        snap1, area_id="achtertuin", scene_description="Kat in het gras", now=now
    )
    assert (buf_dir.stat().st_mode & 0o777) == 0o700
    saved_files = [p for p in buf_dir.iterdir() if p.suffix == ".jpg"]
    assert len(saved_files) == 1
    assert (saved_files[0].stat().st_mode & 0o777) == 0o600

    # Observer enrichment stores motion-event snapshot automatically
    snap2 = CameraSnapshot("camera.oprit", "image/jpeg", _valid_jpeg(b"oprit2"), datetime.now(UTC))

    class FakeSource:
        def revalidate(self, ev, *, now):
            return ev

    class FakeDiscovery:
        def discover(self):
            return SimpleNamespace(devices=(), discovered_camera_count=0, unsupported_camera_count=0, revision="a" * 64)

    class FakeSnapshots:
        def snapshot(self, entity_id: str):
            assert entity_id == "camera.oprit"
            return snap2

    observer = CameraObservationSource(
        FakeSource(),
        discovery=FakeDiscovery(),
        snapshots=FakeSnapshots(),
        vision=SimpleNamespace(
            analyze=lambda _s: SimpleNamespace(
                payload={
                    "description": "Auto rijdt de oprit op",
                    "model": "llava",
                    "model_revision": "r1",
                    "analyzed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
        ),
        snapshot_buffer=buf,
    )
    ev = SensoryEvent(
        source_event_id="ev-1",
        source="home_assistant",
        event_type="state_changed",
        occurred_at=datetime.now(UTC),
        payload={"entity_id": "binary_sensor.oprit_motion", "new_state": "on"},
    )
    dev = CameraDevice(
        device_id="dev-oprit",
        camera_entity_id="camera.oprit",
        trigger_entity_ids=("binary_sensor.oprit_motion",),
        alternate_camera_count=0,
        integration="unifi",
        area_id="oprit",
    )
    enriched = observer.enrich(ev, dev, stop=threading.Event(), discovery_revision="a" * 64)
    assert enriched.payload["camera_observation"]["status"] == "observed"
    assert len(buf.list_recent()) == 2

    # Owner foreground tool queues Telegram photo and claim consumes it once
    runtime = SimpleNamespace(
        _dashboard_owner_principal_id="owner-jaap",
        _delegated_work_owner_service=None,
    )
    actions = CameraPhotoConversationActions(lambda: runtime, buffer_factory=lambda: buf)
    owner_turn = ConversationTurn.private(
        tenant_id="tenant:test",
        installation_id="install:test",
        principal_id="owner-jaap",
        conversation_id="telegram:12345",
        message_id="telegram:12345:5374",
        text="Heb je een foto?",
        occurred_at="2026-11-14T16:01:00Z",
    )
    receipt = actions.execute(
        owner_turn,
        ConversationActionCall(
            "cam-1",
            "send_camera_photo",
            {"operation": "send_photo", "source_mode": "recent_event", "area_or_hint": "oprit"},
        ),
    )
    assert receipt.output["status"] == "telegram_photo_queued"
    assert receipt.output["snapshot"]["camera_entity_id"] == "camera.oprit"

    claimed = claim_telegram_outbound_photo("telegram:12345:5374")
    assert claimed is not None
    assert claimed["camera_entity_id"] == "camera.oprit"
    # Second claim returns None (one-time)
    assert claim_telegram_outbound_photo("telegram:12345:5374") is None

    # Ring-buffer TTL expiry cleans up files
    assert buf.list_recent(now=datetime.now(UTC) + timedelta(hours=2)) == []
    assert meta1["snapshot_id"].startswith("snap_")


def test_holostage_public_stage_origin_and_voice_grant_check(tmp_path):
    mgr = HoloStageManager(tmp_path / "mp")
    mgr.set_lan_stage_origin("http://10.0.20.46:44712")
    mgr.set_public_stage_origin("https://jaap-pc.tail1234.ts.net")

    runtime = SimpleNamespace(
        _holostage_manager=mgr,
        _dashboard_owner_principal_id="owner-jaap",
    )
    app = FastAPI()
    install_dashboard_holostage(app, access=DashboardAccess("owner-tok-secret-1234567890123456"), runtime=runtime)
    client = TestClient(app)
    owner_hdrs = {
        "Authorization": "Bearer owner-tok-secret-1234567890123456",
        "X-HoloStage-Client": "1",
        "Content-Type": "application/json",
    }

    # Pocket owner pairing allows voice; non-owner pocket, unifi_screen, tv_cast, and active privacy shield all block voice
    pocket_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={
            "action": "issue_device_token",
            "label": "Telefoon Jaap",
            "form_factor": "pocket",
            "allow_private_cards_when_alone": True,
            "allow_owner_voice": True,
        },
    )
    assert pocket_res.status_code == 200
    p_data = pocket_res.json()
    assert p_data["recommended_origin"] == "https://jaap-pc.tail1234.ts.net"
    pocket_tok = p_data["issued"]["raw_token"]

    lola_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={
            "action": "issue_device_token",
            "label": "Telefoon Lola",
            "form_factor": "pocket",
            "allow_private_cards_when_alone": True,
            "allow_owner_voice": False,
        },
    )
    assert lola_res.status_code == 200
    lola_tok = lola_res.json()["issued"]["raw_token"]

    kiosk_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={
            "action": "issue_device_token",
            "label": "Wandpaneel Keuken",
            "form_factor": "unifi_screen",
            "allow_private_cards_when_alone": True,
            "allow_owner_voice": True,  # must be coerced to False for unifi_screen!
        },
    )
    assert kiosk_res.status_code == 200
    assert kiosk_res.json()["issued"]["grant"]["allow_owner_voice"] is False
    kiosk_tok = kiosk_res.json()["issued"]["raw_token"]

    tv_res = client.post(
        "/internal/v1/dashboard/holostage/action",
        headers=owner_hdrs,
        json={"action": "issue_device_token", "label": "TV Schuur", "form_factor": "tv_cast", "allow_private_cards_when_alone": False},
    )
    assert tv_res.status_code == 200
    t_data = tv_res.json()
    assert t_data["recommended_origin"] == "http://10.0.20.46:44712"
    tv_tok = t_data["issued"]["raw_token"]

    chk_priv = client.get(
        "/internal/v1/dashboard/holostage/voice-grant-check",
        headers={"X-HoloStage-Lan-Proxy": "1", "X-HoloStage-Token": pocket_tok},
    )
    assert chk_priv.status_code == 200
    assert chk_priv.json()["voice_allowed"] is True

    # Non-owner pocket, unifi_screen kiosk, and tv_cast are all rejected (403)
    for denied_tok in (lola_tok, kiosk_tok, tv_tok):
        chk_den = client.get(
            "/internal/v1/dashboard/holostage/voice-grant-check",
            headers={"X-HoloStage-Lan-Proxy": "1", "X-HoloStage-Token": denied_tok},
        )
        assert chk_den.status_code == 403

    # Privacy shield active also blocks owner pocket voice immediately (403)
    mgr.biometrics.set_manual_privacy_shield(True)
    chk_shielded = client.get(
        "/internal/v1/dashboard/holostage/voice-grant-check",
        headers={"X-HoloStage-Lan-Proxy": "1", "X-HoloStage-Token": pocket_tok},
    )
    assert chk_shielded.status_code == 403
