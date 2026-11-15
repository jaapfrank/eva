"""Functional & adversarial tests for fast 1-step Smart Home, HoloStage Choreography/Cast & Single-Answer Voice Flow."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import urllib.request

import pytest

from eva.adapters.api.dashboard_holostage import HOLOSTAGE_HTML
from eva.application.biometric_presence import BiometricPresenceFusion
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationModelResult,
    ConversationTurn,
    IdentityProfile,
    LivingConversation,
)
from eva.application.holostage import HoloStageManager
from eva.application.home_proprioception import HomeProprioceptionManager
from eva.application.household_head_settings import HouseholdHeadSettings
from eva.application.voice_context import VoiceContext, voice_context
from eva.bootstrap.holostage_runtime import MANAGE_HOLOSTAGE, HoloStageConversationActions
from eva.providers.home_assistant import HomeAssistantProviderSettings
from eva.providers.smart_home_fast_actions import SMART_HOME_LIVE, SmartHomeFastActions


class _FakeHTTPResponse:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._raw = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self, _limit: int = -1) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


NOW = datetime(2026, 11, 15, 0, 30, tzinfo=UTC)


def _private_turn(text: str, *, principal_id: str = "owner-jaap", conv_id: str = "voice:1") -> ConversationTurn:
    return ConversationTurn.private(
        "tenant-1",
        "install-1",
        principal_id,
        "local_assist",
        conv_id,
        text,
        "2026-11-15T00:30:00Z",
    )


def test_smart_home_fast_climate_lights_automations_and_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[dict[str, Any]] = [
        {
            "entity_id": "climate.woonkamer",
            "state": "heat",
            "attributes": {
                "friendly_name": "Woonkamer",
                "current_temperature": 21.5,
                "temperature": 21.5,
                "hvac_action": "idle",
                "access_token": "secret-must-never-leak",
            },
        },
        {
            "entity_id": "climate.kantoor",
            "state": "heat",
            "attributes": {
                "friendly_name": "Kantoor",
                "current_temperature": 16.0,
                "temperature": 15.5,
            },
        },
        {
            "entity_id": "weather.forecast_thuis",
            "state": "partlycloudy",
            "attributes": {"friendly_name": "Thuis", "temperature": 9.4, "humidity": 81},
        },
        {
            "entity_id": "light.woonkamer_plafond",
            "state": "off",
            "attributes": {"friendly_name": "Woonkamer Plafond", "brightness": 0},
        },
        {
            "entity_id": "automation.avondlicht",
            "state": "on",
            "attributes": {"friendly_name": "Avondlicht Woonkamer"},
        },
    ]
    service_calls: list[tuple[str, str, dict[str, Any]]] = []

    class FakeOpener:
        def open(self, req: urllib.request.Request, timeout: float = 4.5) -> _FakeHTTPResponse:
            url = req.full_url
            if req.get_method() == "GET" and url.endswith("/api/states"):
                return _FakeHTTPResponse(states)
            if req.get_method() == "GET" and "/api/states/" in url:
                ent_id = url.rsplit("/api/states/", 1)[1]
                for item in states:
                    if item["entity_id"] == ent_id:
                        return _FakeHTTPResponse(item)
                return _FakeHTTPResponse({}, status=404)
            if req.get_method() == "POST" and "/api/services/" in url:
                rest = url.rsplit("/api/services/", 1)[1]
                domain, service = rest.split("/", 1)
                payload = json.loads((req.data or b"{}").decode("utf-8"))
                service_calls.append((domain, service, payload))
                target_ids = payload.get("entity_id")
                targets = [target_ids] if isinstance(target_ids, str) else list(target_ids or [])
                for item in states:
                    if item["entity_id"] in targets:
                        if domain == "climate" and service == "set_temperature":
                            item["attributes"]["temperature"] = payload["temperature"]
                        elif domain == "light" and service == "turn_on":
                            item["state"] = "on"
                            if "brightness_pct" in payload:
                                item["attributes"]["brightness"] = round(payload["brightness_pct"] * 2.55)
                        elif domain == "light" and service == "turn_off":
                            item["state"] = "off"
                return _FakeHTTPResponse([])
            raise AssertionError(f"Unexpected HA call: {req.get_method()} {url}")

    monkeypatch.setattr("eva.providers.smart_home_fast_actions._SAFE_OPENER", FakeOpener())

    owner_sha = hashlib.sha256(b"owner-jaap").hexdigest()
    settings = HomeAssistantProviderSettings(
        endpoint_url="http://127.0.0.1:8123",
        credential="ha-test-token",
        effects_enabled=True,
        effect_allowlist=(),
        effect_principal_sha256_allowlist=(owner_sha,),
        event_domains=("light",),
        api_enabled=True,
    )
    fast = SmartHomeFastActions(settings)
    owner_turn = _private_turn("Wat is de temperatuur in de woonkamer?")

    from eva.adapters.model.tool_namespaces import function_defer_loading
    assert function_defer_loading(SMART_HOME_LIVE.name) is False
    assert function_defer_loading(MANAGE_HOLOSTAGE.name) is False

    # 1. Instant 1-step room temperature lookup with secret redaction & contact_reply_allowed
    r_clim = fast.execute(
        owner_turn,
        ConversationActionCall("c1", SMART_HOME_LIVE.name, {"operation": "read_climate", "target": "woonkamer"}),
    )
    assert r_clim.output["status"] == "verified"
    assert r_clim.output["climate_zones"][0]["entity_id"] == "climate.woonkamer"
    assert r_clim.output["climate_zones"][0]["current_temperature_c"] == "21.5"
    assert "secret-must-never-leak" not in json.dumps(r_clim.output)
    assert fast.contact_reply_allowed(owner_turn, r_clim) is True

    # 2. Set climate validates [10.0, 26.0] °C bounds (accepting Dutch '20,5') and reads back verified state
    r_set = fast.execute(
        owner_turn,
        ConversationActionCall(
            "c2",
            SMART_HOME_LIVE.name,
            {"operation": "set_climate", "target": "woonkamer", "temperature_c": "20,5"},
        ),
    )
    assert r_set.output["status"] == "verified"
    assert r_set.output["climate"]["target_temperature_c"] == "20.5"
    assert ("climate", "set_temperature", {"entity_id": "climate.woonkamer", "temperature": 20.5}) in service_calls

    # 3. Control room lights with brightness & kelvin in 1 step
    r_light = fast.execute(
        owner_turn,
        ConversationActionCall(
            "c3",
            SMART_HOME_LIVE.name,
            {
                "operation": "control_lights",
                "target": "woonkamer",
                "state": "on",
                "brightness_pct": 60,
                "color_temp_kelvin": 2700,
            },
        ),
    )
    assert r_light.output["status"] == "verified"
    assert r_light.output["entities"][0]["state"] == "on"

    # 4. List automations
    r_auto = fast.execute(
        owner_turn,
        ConversationActionCall("c4", SMART_HOME_LIVE.name, {"operation": "list_or_toggle_automation"}),
    )
    assert r_auto.output["status"] == "verified"
    assert r_auto.output["automations"][0]["entity_id"] == "automation.avondlicht"

    # 5. Non-allowlisted principal may read private_person climate, but is strictly blocked from write effects
    lola_turn = _private_turn("Wat is de temperatuur?", principal_id="family-lola")
    r_lola_read = fast.execute(
        lola_turn,
        ConversationActionCall("c5", SMART_HOME_LIVE.name, {"operation": "read_climate", "target": "woonkamer"}),
    )
    assert r_lola_read.output["status"] == "verified"
    with pytest.raises(ConversationConflict):
        fast.execute(
            lola_turn,
            ConversationActionCall("c6", SMART_HOME_LIVE.name, {"operation": "control_lights", "target": "woonkamer", "state": "off"}),
        )
    with pytest.raises(ConversationConflict):
        fast.execute(
            lola_turn,
            ConversationActionCall("c6b", SMART_HOME_LIVE.name, {"operation": "list_or_toggle_automation"}),
        )

    # 6. Restricted mode (api_enabled=False) enforces effect_allowlist and blocks general API writes
    restricted = SmartHomeFastActions(
        HomeAssistantProviderSettings(
            endpoint_url="http://127.0.0.1:8123",
            credential="ha-test-token",
            effects_enabled=True,
            effect_allowlist=("light.woonkamer_plafond:turn_on",),
            effect_principal_sha256_allowlist=(owner_sha,),
            event_domains=("light",),
            api_enabled=False,
        )
    )
    assert (
        restricted.execute(
            owner_turn,
            ConversationActionCall("c7", SMART_HOME_LIVE.name, {"operation": "control_lights", "target": "woonkamer", "state": "on"}),
        ).output["status"]
        == "verified"
    )
    with pytest.raises(ConversationConflict):
        restricted.execute(
            owner_turn,
            ConversationActionCall("c8", SMART_HOME_LIVE.name, {"operation": "control_lights", "target": "woonkamer", "state": "off"}),
        )
    with pytest.raises(ConversationConflict):
        restricted.execute(
            owner_turn,
            ConversationActionCall("c9", SMART_HOME_LIVE.name, {"operation": "set_climate", "target": "woonkamer", "temperature_c": 21}),
        )


def test_holostage_cast_fallback_auto_consent_and_animated_leaderboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hh = HouseholdHeadSettings(tmp_path)
    proprio = HomeProprioceptionManager(tmp_path)
    with proprio._locked():
        proprio._write_unlocked(
            {
                "contract": "EvaHomeProprioceptionState/v1",
                "revision": 1,
                "updated_at": "2026-11-15T00:30:00Z",
                "last_scan_at": "2026-11-15T00:30:00Z",
                "scanned_subnets": ["10.0.20.0/24"],
                "devices": {
                    "cast_woonkamer": {
                        "device_id": "cast_woonkamer",
                        "ip": "10.0.20.134",
                        "port": 8009,
                        "display_name": "DIW7022-AF9052",
                        "room_id": "woonkamer",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "pending_consent",
                        "auth_requirement": "none",
                    },
                    "cast_schuur": {
                        "device_id": "cast_schuur",
                        "ip": "10.0.20.247",
                        "port": 8009,
                        "display_name": "Tv streaker schuur",
                        "room_id": "schuur",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "pending_consent",
                        "auth_requirement": "none",
                    },
                    "cast_ignored": {
                        "device_id": "cast_ignored",
                        "ip": "10.0.20.199",
                        "port": 8009,
                        "display_name": "Buren TV",
                        "room_id": "buiten",
                        "kind": "google_cast",
                        "protocols": ["castv2"],
                        "consent_status": "ignored",
                        "auth_requirement": "none",
                    },
                },
                "rooms": {},
                "organs": {},
            },
            now=NOW,
        )

    stage = HoloStageManager(tmp_path)
    stage.set_lan_stage_origin("http://10.0.20.46:44712", now=NOW)

    fake_runtime = SimpleNamespace(
        _holostage_manager=stage,
        _dashboard_owner_principal_id="owner-jaap",
        _delegated_work_owner_service=None,
    )
    actions = HoloStageConversationActions(lambda: fake_runtime)

    attempted_ids: list[str] = []

    def fake_cast_launch(_prop: Any, *, device_id: str, stage_url: str) -> dict[str, Any]:
        attempted_ids.append(device_id)
        if device_id == "cast_woonkamer" and len(attempted_ids) == 1:
            raise TimeoutError("living-room TV in deep standby")
        return {"ok": True, "device_id": device_id, "launched_url": stage_url}

    monkeypatch.setattr("eva.bootstrap.holostage_runtime.query_or_launch_approved_cast_device", fake_cast_launch)

    owner_turn = _private_turn("Laat de Formule 1 stand zien op de Chromecast met een mooie animatie")
    receipt = actions.execute(
        owner_turn,
        ConversationActionCall(
            "h1",
            MANAGE_HOLOSTAGE.name,
            {
                "operation": "cast_stage",
                "target_device": "chromecast",
                "title": "Formule 1 Stand 2026",
                "body_nl": "Actuele WK-tussenstand bij de coureurs:",
                "items": ["1. Max Verstappen — 392 pt", "2. Lando Norris — 356 pt", "3. Charles Leclerc — 318 pt"],
                "card_theme": "leaderboard",
                "animation_effect": "celebration",
            },
        ),
    )
    assert receipt.output["status"] == "verified"
    assert actions.contact_reply_allowed(owner_turn, receipt) is True
    # Living-room Cast tried first, then automatic fallback to second Cast screen; ignored screen NEVER touched!
    assert attempted_ids == ["cast_woonkamer", "cast_schuur"]
    assert "cast_ignored" not in attempted_ids

    tv_state = stage.build_stage_state(viewer_kind="tv_cast", form_factor="tv_cast", now=datetime.now(UTC))
    assert "custom_spotlight" in tv_state["cards"]["conjured_cards"]
    spot = tv_state["cards"]["spotlights"][0]
    assert spot["title"] == "Formule 1 Stand 2026"
    assert spot["theme"] == "leaderboard"
    assert len(spot["items"]) == 3
    assert tv_state["stage_animation"]["effect"] == "celebration"

    # Explicit approve_device=False must NOT auto-approve a pending device
    with stage.proprioception._locked():
        st = stage.proprioception._read_unlocked(now=NOW)
        st["devices"]["cast_slaapkamer"] = {
            "device_id": "cast_slaapkamer",
            "ip": "10.0.20.155",
            "port": 8009,
            "display_name": "Slaapkamer Cast",
            "room_id": "slaapkamer",
            "kind": "google_cast",
            "protocols": ["castv2"],
            "consent_status": "pending_consent",
            "auth_requirement": "none",
        }
        stage.proprioception._write_unlocked(st, now=NOW)
    r_no_approve = actions.execute(
        owner_turn,
        ConversationActionCall(
            "h2",
            MANAGE_HOLOSTAGE.name,
            {"operation": "cast_stage", "target_device": "slaapkamer", "approve_device": False},
        ),
    )
    assert r_no_approve.output["status"] == "consent_required"

    # Stage HTML includes the animated FX layer and leaderboard styling
    assert 'id="stage-fx-layer"' in HOLOSTAGE_HTML
    assert "theme-leaderboard" in HOLOSTAGE_HTML
    assert "triggerStageChoreography" in HOLOSTAGE_HTML


def test_fast_foreground_tools_suppress_double_answer_spoken_updates() -> None:
    from tests.application.test_conversation import _Memory

    identity = IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80)
    events: list[dict[str, Any]] = []

    class FastActions:
        action_names = (SMART_HOME_LIVE.name, MANAGE_HOLOSTAGE.name, "search_public_info")

        def pending(self, turn: ConversationTurn) -> None:
            return None

        def active_context(self, turn: ConversationTurn) -> tuple[str, ...]:
            return ()

        def continuation_action_names(self, turn: ConversationTurn) -> tuple[str, ...]:
            return self.action_names

        def execute(self, turn: ConversationTurn, call: ConversationActionCall) -> ConversationActionReceipt:
            return ConversationActionReceipt(call, {"status": "verified", "current_temperature_c": "21.5"})

    calls = 0

    class FakeModel:
        def generate(self, *args: Any, **context: Any) -> ConversationModelResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ConversationModelResult(
                    "Ik kijk even naar de thermostaat in de woonkamer.",
                    "gpt-6-sol",
                    ConversationActionCall("fast-1", SMART_HOME_LIVE.name, {"operation": "read_climate"}),
                    text_phase="commentary",
                )
            return ConversationModelResult(
                "Het is nu 21,5 graden in de woonkamer.",
                "gpt-6-sol",
                None,
            )

    ctx = VoiceContext((), (), lambda _: None, events.append)
    with voice_context(ctx):
        reply = LivingConversation(
            _Memory(),
            FakeModel(),
            identity,
            FastActions(),
        ).respond(_private_turn("Wat is de temperatuur in de woonkamer?"))

    assert not any(e.get("type") == "spoken_update" for e in events)
    assert reply.text == "Het is nu 21,5 graden in de woonkamer."
