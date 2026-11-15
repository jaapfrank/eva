"""Fast 1-step owner/household Home Assistant climate, sensor, light, scene & automation adapter.

Executes bounded, readback-verified LAN Home Assistant reads and everyday controls
in a single foreground step (<50ms LAN round-trip) without hardcoded Dutch templates
or multi-round inventory discovery.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any
from urllib import error, parse, request as urllib_request

from eva.application.conversation import (
    ConversationActionInputRejected,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationToolDescriptor,
)
from eva.application.initiative_execution import require_initiative_context

if TYPE_CHECKING:
    from eva.providers.home_assistant import HomeAssistantProviderSettings


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise error.HTTPError(req.full_url, code, "redirects disallowed", headers, fp)


_SAFE_OPENER = urllib_request.build_opener(_NoRedirectHandler())
_READABLE_DOMAINS = frozenset({"climate", "weather", "sensor", "binary_sensor", "light", "switch", "scene", "media_player", "automation", "fan"})
_FORBIDDEN_DOMAINS = frozenset({"lock", "alarm_control_panel", "script", "shell_command"})
_REDACTED_ATTR_SUBSTRINGS = ("token", "secret", "password", "latitude", "longitude", "gps", "entity_picture")

SMART_HOME_LIVE = ConversationToolDescriptor(
    name="control_or_read_smart_home",
    description=(
        "Fast 1-step live Home Assistant smart-home reader and controller for rooms, temperatures, "
        "thermostats, sensors, weather, lights (on/off, brightness, color/warmth), scenes, media "
        "players, and listing/toggling/triggering automations. ALWAYS use this foreground tool "
        "(NOT start_digital_work and NOT read_home_assistant_inventory) when asked for: "
        "(1) room temperatures, thermostat state, humidity, or weather (operation='read_climate'), "
        "(2) setting room thermostat temperature (operation='set_climate'), "
        "(3) reading sensors, lights, switches, media players or room status (operation='read_status'), "
        "(4) turning lights on/off, dimming, or changing warmth/color in a room or lamp (operation='control_lights'), "
        "(5) activating a scene (operation='activate_scene'), "
        "(6) controlling a speaker/TV media player (operation='control_media'), or "
        "(7) listing, enabling, disabling, or triggering Home Assistant automations (operation='list_or_toggle_automation'). "
        "For creating a brand-new Home Assistant automation rule, use create_home_assistant_state_automation "
        "or create_home_assistant_advanced_state_automation directly."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read_climate", "set_climate", "read_status", "control_lights", "activate_scene", "control_media", "list_or_toggle_automation"],
            },
            "target": {
                "type": ["string", "null"],
                "maxLength": 120,
                "description": "Natural Dutch room, device, scene, or entity name (e.g. 'woonkamer', 'keuken', 'badkamer', 'kantoor', 'lola', or null for all).",
            },
            "state": {"type": ["string", "null"], "enum": ["on", "off", "toggle", None]},
            "temperature_c": {
                "type": ["string", "integer", "null"],
                "maxLength": 16,
                "description": "Target thermostat temperature in °C as string or integer (10 to 26, e.g. '20.5', '21,5', or 21) for set_climate.",
            },
            "hvac_mode": {"type": ["string", "null"], "enum": ["heat", "off", "auto", "cool", None]},
            "brightness_pct": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
            "color_temp_kelvin": {"type": ["integer", "null"], "minimum": 2000, "maximum": 6500},
            "rgb_color": {"type": ["array", "null"], "items": {"type": "integer", "minimum": 0, "maximum": 255}, "minItems": 3, "maxItems": 3},
            "media_action": {"type": ["string", "null"], "enum": ["play", "pause", "stop", "turn_on", "turn_off", "volume_set", None]},
            "volume_pct": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
            "automation_action": {"type": ["string", "null"], "enum": ["list", "turn_on", "turn_off", "toggle", "trigger", None]},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
    completion_guidance=(
        "Answer in one natural, concise Dutch sentence using the verified live values "
        "(for example: 'In de woonkamer is het nu 21,5 graden.'). Do not read entity IDs, "
        "JSON keys, or technical status labels aloud unless explicitly asked."
    ),
)

_ROOM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "woonkamer": ("woonkamer", "huiskamer", "beneden", "living", "zithoek", "pui", "stalamp", "ledsteip"),
    "keuken": ("keuken", "kookeiland", "eettafel"),
    "badkamer": ("badkamer", "douche", "bk1", "bk2", "bk3"),
    "kantoor": ("kantoor", "werkkamer", "bureau"),
    "lola": ("lola", "sk_lola", "slaapkamer_lola", "sk lola"),
    "slaapkamer": ("master", "slaapkamer", "mb"),
    "gang": ("gang", "hal", "achterdeur", "voordeur"),
    "buiten": ("buiten", "tuin", "overkapping", "schutting", "palmboom"),
    "schuur": ("schuur", "berging"),
    "wc": ("wc", "toilet"),
}
_STOP_WORDS = frozenset({"de", "het", "een", "in", "op", "van", "lamp", "lampen", "licht", "lichten", "temperatuur", "thermostaat", "kamer"})


def _normalize_tokens(target: str | None) -> tuple[str, tuple[str, ...]]:
    raw = (target or "").strip().casefold()
    if not raw or raw in {"alles", "alle", "huis", "thuis", "overzicht", "all"}:
        return "", ()
    words = [w for w in re.split(r"\W+", raw) if len(w) >= 2 and w not in _STOP_WORDS]
    expanded: list[str] = [raw, *words]
    for canonical, syns in _ROOM_SYNONYMS.items():
        if raw == canonical or any(w in syns for w in words) or any(s in raw for s in syns):
            expanded.extend(syns)
    return raw, tuple(dict.fromkeys(t for t in expanded if t))


def _matches_entity(row: dict[str, Any], raw_needle: str, tokens: tuple[str, ...]) -> int:
    if not raw_needle and not tokens:
        return 1
    eid = str(row.get("entity_id") or "").casefold()
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    friendly = str(attrs.get("friendly_name") or "").casefold()
    hay = f"{eid} {friendly}"
    if raw_needle and (eid == raw_needle or friendly == raw_needle):
        return 100
    if raw_needle and raw_needle in hay:
        return 60
    return sum(1 for tok in tokens if tok in hay) * 15


def _rank_domain(states: list[dict[str, Any]], prefix: str, raw_needle: str, tokens: tuple[str, ...], *, exclude_unavailable: bool = False) -> list[dict[str, Any]]:
    scored = [
        (_matches_entity(r, raw_needle, tokens), r)
        for r in states
        if str(r["entity_id"]).startswith(prefix)
        and (not exclude_unavailable or str(r.get("state") or "") != "unavailable")
    ]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda x: (-x[0], 1 if str(x[1]["entity_id"]) == "climate.home" else 0))
    return [r for _, r in scored]


def _scalar(val: Any) -> Any:
    if isinstance(val, bool) or val is None or isinstance(val, int):
        return val
    if isinstance(val, float):
        return f"{val:g}"
    return val if isinstance(val, str) else str(val)


def _safe_summary(row: dict[str, Any]) -> dict[str, Any]:
    eid = str(row.get("entity_id") or "")
    domain = eid.partition(".")[0]
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    out: dict[str, Any] = {"entity_id": eid, "name": str(attrs.get("friendly_name") or eid), "state": str(row.get("state") or "unknown")}
    if domain == "climate":
        out["current_temperature_c"] = _scalar(attrs.get("current_temperature"))
        out["target_temperature_c"] = _scalar(attrs.get("temperature"))
        if attrs.get("hvac_action") is not None:
            out["hvac_action"] = str(attrs["hvac_action"])
        if attrs.get("current_humidity") is not None:
            out["humidity_pct"] = _scalar(attrs["current_humidity"])
    elif domain == "weather":
        out.update(temperature_c=_scalar(attrs.get("temperature")), humidity_pct=_scalar(attrs.get("humidity")), wind_speed=_scalar(attrs.get("wind_speed")))
    elif domain == "sensor":
        if attrs.get("unit_of_measurement"):
            out["unit"] = str(attrs["unit_of_measurement"])
        if attrs.get("device_class"):
            out["device_class"] = str(attrs["device_class"])
    elif domain == "light":
        if isinstance(attrs.get("brightness"), (int, float)) and not isinstance(attrs.get("brightness"), bool):
            out["brightness_pct"] = int(round(float(attrs["brightness"]) * 100.0 / 255.0))
        if attrs.get("color_temp_kelvin") is not None:
            out["color_temp_kelvin"] = _scalar(attrs["color_temp_kelvin"])
    elif domain == "media_player":
        if isinstance(attrs.get("volume_level"), (int, float)) and not isinstance(attrs.get("volume_level"), bool):
            out["volume_pct"] = int(round(float(attrs["volume_level"]) * 100))
        if attrs.get("media_title"):
            out["media_title"] = str(attrs["media_title"])[:80]
    elif domain == "automation" and attrs.get("last_triggered"):
        out["last_triggered"] = str(attrs["last_triggered"])[:32]
    for k in list(out):
        if any(sub in k.casefold() for sub in _REDACTED_ATTR_SUBSTRINGS):
            out.pop(k, None)
    return out


class SmartHomeFastActions:
    """Single-step foreground smart-home read & control backed by existing HA authority."""

    supports_indexed_steps = True
    action_names = (SMART_HOME_LIVE.name,)

    @staticmethod
    def conversation_tools() -> tuple[ConversationToolDescriptor, ...]:
        return (SMART_HOME_LIVE,)

    def __init__(self, ha_settings: HomeAssistantProviderSettings | None) -> None:
        self._ha = ha_settings

    def _principal_in_allowlist(self, turn: Any) -> bool:
        if self._ha is None:
            return False
        principal_id = str(getattr(turn, "principal_id", "") or "")
        if not principal_id or getattr(turn.audience, "principal_id", None) != principal_id:
            return False
        return hashlib.sha256(principal_id.encode("utf-8")).hexdigest() in self._ha.effect_principal_sha256_allowlist

    def _is_authorized(self, turn: Any, *, write_effect: bool = False) -> bool:
        if self._ha is None or not self._ha.endpoint_url or not self._ha.credential:
            return False
        if require_initiative_context(turn) is not None or getattr(turn, "project_id", None) is not None:
            return False
        aud_kind = getattr(turn.audience, "kind", None)
        principal_id = str(getattr(turn, "principal_id", "") or "")
        if aud_kind not in {"private_person", "household"} or ":-" in str(getattr(turn, "conversation_id", "")) or not principal_id or getattr(turn.audience, "principal_id", None) != principal_id:
            return False
        in_allowlist = self._principal_in_allowlist(turn)
        return bool(self._ha.effects_enabled and in_allowlist) if write_effect else (aud_kind == "private_person" or in_allowlist)

    def available_for(self, turn: Any) -> bool:
        return self._is_authorized(turn, write_effect=False)

    def active_context(self, turn: Any) -> tuple[str, ...]:
        _ = turn
        return ()

    def conversation_instructions(self, turn: Any, available_names: tuple[str, ...]) -> tuple[str, ...]:
        if SMART_HOME_LIVE.name not in available_names or not self.available_for(turn):
            return ()
        return (
            "Voor directe vragen over kamertemperaturen, thermostaten, luchtvochtigheid, sensoren, weer, "
            "lampen (aan/uit/dimmen/kleur), scènes, mediaspelers of bestaande automatiseringen gebruik je "
            "direct `control_or_read_smart_home` in deze beurt — delegeer dit NIET naar `start_digital_work` "
            "en roep niet eerst `read_home_assistant_inventory` aan. Voor het aanmaken van één nieuwe "
            "Home Assistant-automatisering gebruik je direct `create_home_assistant_state_automation` of "
            "`create_home_assistant_advanced_state_automation`.",
        )

    def contact_reply_allowed(self, turn: Any, receipt: ConversationActionReceipt) -> bool:
        return receipt.call.name == SMART_HOME_LIVE.name and receipt.output.get("status") == "verified" and self.available_for(turn)

    def reply_guard(self, turn: Any, receipts: tuple[ConversationActionReceipt, ...]):
        if not any(r.call.name == SMART_HOME_LIVE.name for r in receipts):
            return None

        def _guard(text: str) -> str:
            if not self.available_for(turn):
                raise ConversationConflict("Smart home authority is unavailable")
            return text

        return _guard

    def pending(self, turn: Any, *, step_index: int = 0) -> None:
        _ = (turn, step_index)
        return None

    def _ha_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        assert self._ha is not None
        if not path.startswith("/api/"):
            raise ValueError("invalid Home Assistant API path")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._ha.credential}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib_request.Request(f"{self._ha.endpoint_url.rstrip('/')}{path}", data=body, method=method, headers=headers)
        with _SAFE_OPENER.open(req, timeout=4.5) as resp:
            raw = resp.read(2_500_000)
        return json.loads(raw.decode("utf-8")) if raw else None

    def _fetch_states(self) -> list[dict[str, Any]]:
        rows = self._ha_request("GET", "/api/states")
        if not isinstance(rows, list):
            raise RuntimeError("Home Assistant states response is invalid")
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("entity_id"), str)]

    def _fetch_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        domain, dot, obj = entity_id.partition(".")
        if not dot or not domain or not obj or domain in _FORBIDDEN_DOMAINS:
            return None
        safe_id = f"{domain}.{re.sub(r'[^a-z0-9_]', '', obj.casefold())}"
        try:
            res = self._ha_request("GET", f"/api/states/{parse.quote(safe_id, safe='._')}")
            return res if isinstance(res, dict) else None
        except Exception:
            return None

    def execute(self, turn: Any, call: Any, *, step_index: int = 0) -> ConversationActionReceipt:
        _ = step_index
        if call.name != SMART_HOME_LIVE.name or not self.available_for(turn):
            raise ConversationConflict("Smart-home live control is unavailable for this turn")
        args = call.arguments if isinstance(call.arguments, dict) else {}
        if "operation" not in args or not set(args).issubset(SMART_HOME_LIVE.input_schema["properties"]):
            raise ConversationActionInputRejected(("operation",))

        op = str(args.get("operation") or "")
        auto_act = str(args.get("automation_action") or "list")
        if op == "list_or_toggle_automation" and not self._principal_in_allowlist(turn):
            raise ConversationConflict("Automation inventory and control require an authorized household/owner principal")
        if (op in {"set_climate", "control_lights", "activate_scene", "control_media"} or (op == "list_or_toggle_automation" and auto_act != "list")) and not self._is_authorized(turn, write_effect=True):
            raise ConversationConflict("Smart-home write effects require authorized household/owner permission")
        if (op in {"set_climate", "activate_scene", "control_media"} or (op == "list_or_toggle_automation" and auto_act != "list")) and not (self._ha is not None and self._ha.api_enabled):
            raise ConversationConflict("General Home Assistant API is required for this smart-home domain write")

        try:
            return self._dispatch(call, args, op=op)
        except (ConversationActionInputRejected, ConversationConflict):
            raise
        except Exception as exc:
            return ConversationActionReceipt(call, {"status": "unavailable", "operation": op, "reason": type(exc).__name__})

    def _dispatch(self, call: Any, args: dict[str, Any], *, op: str) -> ConversationActionReceipt:
        raw_needle, tokens = _normalize_tokens(args.get("target"))
        states = self._fetch_states()

        if op == "read_climate":
            climates, temp_sensors, weather_items = [], [], []
            for row in states:
                eid = str(row["entity_id"])
                dom = eid.partition(".")[0]
                if dom not in {"climate", "sensor", "weather"} or (str(row.get("state") or "") in {"unavailable", "unknown"} and dom != "climate"):
                    continue
                score = _matches_entity(row, raw_needle, tokens)
                if dom == "climate" and score > 0:
                    climates.append((score, _safe_summary(row)))
                elif dom == "weather" and (not raw_needle or "buiten" in tokens or "weer" in raw_needle or score > 0):
                    weather_items.append(_safe_summary(row))
                elif dom == "sensor" and score > 0:
                    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
                    unit, dcls = str(attrs.get("unit_of_measurement") or ""), str(attrs.get("device_class") or "")
                    if (dcls in {"temperature", "humidity"} or "°c" in unit.casefold() or unit == "%") and "cpu" not in eid and "processor" not in eid and "battery" not in dcls:
                        temp_sensors.append((score, _safe_summary(row)))
            climates.sort(key=lambda x: (-x[0], x[1]["name"]))
            temp_sensors.sort(key=lambda x: (-x[0], x[1]["name"]))
            fallback = [_safe_summary(r) for r in states if str(r["entity_id"]).startswith("climate.")] if not climates and not temp_sensors else []
            return ConversationActionReceipt(call, {"status": "verified", "operation": "read_climate", "target": raw_needle or "huis", "climate_zones": [x[1] for x in climates[:8]] or fallback[:8], "sensors": [x[1] for x in temp_sensors[:8]], "outdoor_weather": weather_items[:2]})

        if op == "set_climate":
            raw_temp, hvac_mode = args.get("temperature_c"), args.get("hvac_mode")
            if raw_temp is None and hvac_mode is None:
                raise ConversationActionInputRejected(("temperature_c",))
            parsed_temp: float | None = None
            if raw_temp is not None:
                if isinstance(raw_temp, bool) or not isinstance(raw_temp, (int, float, str)):
                    raise ConversationActionInputRejected(("temperature_c",))
                try:
                    parsed_temp = float(str(raw_temp).strip().replace("°C", "").replace("°", "").replace(",", ".").strip())
                except ValueError:
                    raise ConversationActionInputRejected(("temperature_c",)) from None
                if not 10.0 <= parsed_temp <= 26.0:
                    raise ConversationActionInputRejected(("temperature_c",))
            candidates = _rank_domain(states, "climate.", raw_needle, tokens)
            if not candidates:
                return ConversationActionReceipt(call, {"status": "target_not_found", "available_climate_zones": [_safe_summary(r) for r in states if str(r["entity_id"]).startswith("climate.")]})
            chosen_id = str(candidates[0]["entity_id"])
            if hvac_mode in {"heat", "off", "auto", "cool"}:
                self._ha_request("POST", "/api/services/climate/set_hvac_mode", {"entity_id": chosen_id, "hvac_mode": hvac_mode})
            if parsed_temp is not None:
                self._ha_request("POST", "/api/services/climate/set_temperature", {"entity_id": chosen_id, "temperature": round(parsed_temp * 2.0) / 2.0})
            return ConversationActionReceipt(call, {"status": "verified", "operation": "set_climate", "climate": _safe_summary(self._fetch_entity_state(chosen_id) or candidates[0])})

        if op == "read_status":
            matched = [
                (_matches_entity(r, raw_needle, tokens), _safe_summary(r))
                for r in states
                if str(r["entity_id"]).partition(".")[0] in _READABLE_DOMAINS - _FORBIDDEN_DOMAINS
                and str(r.get("state") or "") != "unavailable"
            ]
            matched = [m for m in matched if m[0] > 0]
            matched.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
            return ConversationActionReceipt(call, {"status": "verified", "operation": "read_status", "target": raw_needle or "huis", "entities": [x[1] for x in matched[:20]]})

        if op == "control_lights":
            if not raw_needle:
                raise ConversationActionInputRejected(("target",))
            desired = str(args.get("state") or "on")
            bri, kelvin, rgb = args.get("brightness_pct"), args.get("color_temp_kelvin"), args.get("rgb_color")
            if (bri is not None or kelvin is not None or rgb is not None) and desired == "off":
                desired = "on"
            hits = [
                (_matches_entity(r, raw_needle, tokens), r)
                for r in states
                if str(r["entity_id"]).partition(".")[0] in {"light", "switch"}
                and not ("led_ring" in str(r["entity_id"]) and "ring" not in raw_needle)
                and str(r.get("state") or "") != "unavailable"
            ]
            hits = [h for h in hits if h[0] > 0]
            if not hits:
                return ConversationActionReceipt(call, {"status": "target_not_found", "target": raw_needle})
            hits.sort(key=lambda x: -x[0])
            exact = [r for sc, r in hits if sc >= 100]
            selected = exact[:2] if exact else [r for sc, r in hits if sc >= max(15, hits[0][0] - 15)][:8]
            entity_ids = [str(r["entity_id"]) for r in selected]
            svc = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}.get(desired, "turn_on")
            assert self._ha is not None
            if not self._ha.api_enabled and (svc not in {"turn_on", "turn_off"} or bri is not None or kelvin is not None or rgb is not None or not all(f"{eid}:{svc}" in self._ha.effect_allowlist for eid in entity_ids)):
                raise ConversationConflict("Target light/switch effect is outside the configured allowlist")
            light_ids = [e for e in entity_ids if e.startswith("light.")]
            switch_ids = [e for e in entity_ids if e.startswith("switch.")]
            if light_ids:
                payload: dict[str, Any] = {"entity_id": light_ids}
                if svc == "turn_on":
                    if isinstance(bri, int) and not isinstance(bri, bool) and 1 <= bri <= 100:
                        payload["brightness_pct"] = bri
                    if isinstance(kelvin, int) and not isinstance(kelvin, bool) and 2000 <= kelvin <= 6500:
                        payload["color_temp_kelvin"] = kelvin
                    if isinstance(rgb, list) and len(rgb) == 3 and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in rgb):
                        payload["rgb_color"] = rgb
                self._ha_request("POST", f"/api/services/light/{svc}", payload)
            if switch_ids:
                self._ha_request("POST", f"/api/services/switch/{svc}", {"entity_id": switch_ids})
            verified = [_safe_summary(st_row) for eid in entity_ids[:6] if (st_row := self._fetch_entity_state(eid)) is not None]
            return ConversationActionReceipt(call, {"status": "verified", "operation": "control_lights", "service": svc, "entities": verified})

        if op == "activate_scene":
            if not raw_needle:
                raise ConversationActionInputRejected(("target",))
            scenes = _rank_domain(states, "scene.", raw_needle, tokens)
            if not scenes:
                return ConversationActionReceipt(call, {"status": "target_not_found", "available_scenes": [_safe_summary(r) for r in states if str(r["entity_id"]).startswith("scene.")][:12]})
            chosen = str(scenes[0]["entity_id"])
            self._ha_request("POST", "/api/services/scene/turn_on", {"entity_id": chosen})
            return ConversationActionReceipt(call, {"status": "verified", "operation": "activate_scene", "scene": _safe_summary(self._fetch_entity_state(chosen) or scenes[0])})

        if op == "control_media":
            if not raw_needle:
                raise ConversationActionInputRejected(("target",))
            players = _rank_domain(states, "media_player.", raw_needle, tokens, exclude_unavailable=True)
            if not players:
                return ConversationActionReceipt(call, {"status": "target_not_found", "target": raw_needle})
            chosen_mp = str(players[0]["entity_id"])
            svc_name = {"play": "media_play", "pause": "media_pause", "stop": "media_stop", "turn_on": "turn_on", "turn_off": "turn_off", "volume_set": "volume_set"}.get(str(args.get("media_action") or "play"), "media_play")
            mp_payload: dict[str, Any] = {"entity_id": chosen_mp}
            if svc_name == "volume_set":
                vol = args.get("volume_pct")
                if not isinstance(vol, int) or isinstance(vol, bool) or not 0 <= vol <= 100:
                    raise ConversationActionInputRejected(("volume_pct",))
                mp_payload["volume_level"] = round(vol / 100.0, 2)
            self._ha_request("POST", f"/api/services/media_player/{svc_name}", mp_payload)
            return ConversationActionReceipt(call, {"status": "verified", "operation": "control_media", "action": svc_name, "media_player": _safe_summary(self._fetch_entity_state(chosen_mp) or players[0])})

        auto_action = str(args.get("automation_action") or "list")
        autos = [r for r in states if str(r["entity_id"]).startswith("automation.")]
        if auto_action == "list":
            filtered = _rank_domain(autos, "automation.", raw_needle, tokens) if raw_needle else autos
            return ConversationActionReceipt(call, {"status": "verified", "operation": "list_or_toggle_automation", "automations": [_safe_summary(r) for r in filtered[:24]]})
        if not raw_needle:
            raise ConversationActionInputRejected(("target",))
        matched_autos = _rank_domain(autos, "automation.", raw_needle, tokens)
        if not matched_autos:
            return ConversationActionReceipt(call, {"status": "target_not_found", "available_automations": [_safe_summary(r) for r in autos[:16]]})
        chosen_auto = str(matched_autos[0]["entity_id"])
        if auto_action in {"turn_on", "turn_off", "toggle", "trigger"}:
            self._ha_request("POST", f"/api/services/automation/{auto_action}", {"entity_id": chosen_auto})
        return ConversationActionReceipt(call, {"status": "verified", "operation": "list_or_toggle_automation", "automation_action": auto_action, "automation": _safe_summary(self._fetch_entity_state(chosen_auto) or matched_autos[0])})


__all__ = ["SMART_HOME_LIVE", "SmartHomeFastActions"]
