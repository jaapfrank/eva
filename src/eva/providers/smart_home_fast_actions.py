"""Fast 1-step owner/household Home Assistant climate, sensor, light, scene & automation adapter.

Executes bounded, readback-verified LAN Home Assistant reads and everyday controls
in a single foreground step (<50ms LAN round-trip) so spoken questions like
"Wat is de temperatuur in de woonkamer?", dimming/coloring room lights, activating
scenes, or toggling automations answer in ~1 second without multi-round discovery.
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

_READABLE_DOMAINS = frozenset({
    "climate",
    "weather",
    "sensor",
    "binary_sensor",
    "light",
    "switch",
    "scene",
    "media_player",
    "automation",
    "fan",
})

_FORBIDDEN_DOMAINS = frozenset({
    "lock",
    "alarm_control_panel",
    "script",
    "shell_command",
})

_REDACTED_ATTR_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "latitude",
    "longitude",
    "gps",
    "entity_picture",
)

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
                "enum": [
                    "read_climate",
                    "set_climate",
                    "read_status",
                    "control_lights",
                    "activate_scene",
                    "control_media",
                    "list_or_toggle_automation",
                ],
            },
            "target": {
                "type": ["string", "null"],
                "maxLength": 120,
                "description": "Natural Dutch room, device, scene, or entity name (e.g. 'woonkamer', 'keuken', 'badkamer', 'kantoor', 'lola', or null for all).",
            },
            "state": {
                "type": ["string", "null"],
                "enum": ["on", "off", "toggle", None],
            },
            "temperature_c": {
                "type": ["string", "integer", "null"],
                "maxLength": 16,
                "description": "Target thermostat temperature in °C as string or integer (10 to 26, e.g. '20.5', '21,5', or 21) for set_climate.",
            },
            "hvac_mode": {
                "type": ["string", "null"],
                "enum": ["heat", "off", "auto", "cool", None],
            },
            "brightness_pct": {
                "type": ["integer", "null"],
                "minimum": 1,
                "maximum": 100,
            },
            "color_temp_kelvin": {
                "type": ["integer", "null"],
                "minimum": 2000,
                "maximum": 6500,
            },
            "rgb_color": {
                "type": ["array", "null"],
                "items": {"type": "integer", "minimum": 0, "maximum": 255},
                "minItems": 3,
                "maxItems": 3,
            },
            "media_action": {
                "type": ["string", "null"],
                "enum": ["play", "pause", "stop", "turn_on", "turn_off", "volume_set", None],
            },
            "volume_pct": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": 100,
            },
            "automation_action": {
                "type": ["string", "null"],
                "enum": ["list", "turn_on", "turn_off", "toggle", "trigger", None],
            },
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


def _normalize_tokens(target: str | None) -> tuple[str, tuple[str, ...]]:
    raw = (target or "").strip().casefold()
    if not raw or raw in {"alles", "alle", "huis", "thuis", "overzicht", "all"}:
        return "", ()
    expanded: list[str] = [raw]
    words = [
        w
        for w in re.split(r"\W+", raw)
        if len(w) >= 2
        and w not in {"de", "het", "een", "in", "op", "van", "lamp", "lampen", "licht", "lichten", "temperatuur", "thermostaat", "kamer"}
    ]
    expanded.extend(words)
    for canonical, syns in _ROOM_SYNONYMS.items():
        if raw == canonical or any(w in syns for w in words) or any(s in raw for s in syns):
            expanded.extend(syns)
    deduped = tuple(dict.fromkeys(t for t in expanded if t))
    return raw, deduped


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
    hits = sum(1 for tok in tokens if tok in hay)
    return hits * 15


def _json_safe_scalar(val: Any) -> Any:
    if isinstance(val, bool) or val is None or isinstance(val, int):
        return val
    if isinstance(val, float):
        return f"{val:g}"
    if isinstance(val, str):
        return val
    return str(val)


def _safe_summary(row: dict[str, Any]) -> dict[str, Any]:
    eid = str(row.get("entity_id") or "")
    domain = eid.partition(".")[0]
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    out: dict[str, Any] = {
        "entity_id": eid,
        "name": str(attrs.get("friendly_name") or eid),
        "state": str(row.get("state") or "unknown"),
    }
    if domain == "climate":
        out["current_temperature_c"] = _json_safe_scalar(attrs.get("current_temperature"))
        out["target_temperature_c"] = _json_safe_scalar(attrs.get("temperature"))
        if attrs.get("hvac_action") is not None:
            out["hvac_action"] = str(attrs.get("hvac_action"))
        if attrs.get("current_humidity") is not None:
            out["humidity_pct"] = _json_safe_scalar(attrs.get("current_humidity"))
    elif domain == "weather":
        out["temperature_c"] = _json_safe_scalar(attrs.get("temperature"))
        out["humidity_pct"] = _json_safe_scalar(attrs.get("humidity"))
        out["wind_speed"] = _json_safe_scalar(attrs.get("wind_speed"))
    elif domain == "sensor":
        unit = attrs.get("unit_of_measurement")
        if unit:
            out["unit"] = str(unit)
        dev_cls = attrs.get("device_class")
        if dev_cls:
            out["device_class"] = str(dev_cls)
    elif domain == "light":
        bri = attrs.get("brightness")
        if isinstance(bri, (int, float)):
            out["brightness_pct"] = int(round(float(bri) * 100.0 / 255.0))
        if attrs.get("color_temp_kelvin") is not None:
            out["color_temp_kelvin"] = _json_safe_scalar(attrs.get("color_temp_kelvin"))
    elif domain == "media_player":
        if attrs.get("volume_level") is not None and isinstance(attrs.get("volume_level"), (int, float)):
            out["volume_pct"] = int(round(float(attrs["volume_level"]) * 100))
        if attrs.get("media_title"):
            out["media_title"] = str(attrs["media_title"])[:80]
    elif domain == "automation":
        if attrs.get("last_triggered"):
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
        digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
        return digest in self._ha.effect_principal_sha256_allowlist

    def _is_authorized(self, turn: Any, *, write_effect: bool = False) -> bool:
        if self._ha is None or not self._ha.endpoint_url or not self._ha.credential:
            return False
        if require_initiative_context(turn) is not None:
            return False
        if getattr(turn, "project_id", None) is not None:
            return False
        aud_kind = getattr(turn.audience, "kind", None)
        if aud_kind not in {"private_person", "household"}:
            return False
        conv_id = str(getattr(turn, "conversation_id", ""))
        if ":-" in conv_id:
            return False
        principal_id = str(getattr(turn, "principal_id", "") or "")
        if not principal_id or getattr(turn.audience, "principal_id", None) != principal_id:
            return False
        in_allowlist = self._principal_in_allowlist(turn)
        if write_effect:
            return bool(self._ha.effects_enabled and in_allowlist)
        # Ordinary home reads follow authorize_ordinary_home_control: bound private_person or allowlisted household
        return aud_kind == "private_person" or in_allowlist

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
        return (
            receipt.call.name == SMART_HOME_LIVE.name
            and receipt.output.get("status") == "verified"
            and self.available_for(turn)
        )

    def reply_guard(self, turn: Any, receipts: tuple[ConversationActionReceipt, ...]):
        if not any(r.call.name == SMART_HOME_LIVE.name for r in receipts):
            return None

        def _guard(text: str) -> str:
            if not self.available_for(turn):
                raise ConversationConflict("Smart home authority is unavailable")
            return text

        return _guard

    def direct_voice_reply(self, turn: Any, receipts: tuple[ConversationActionReceipt, ...]) -> str | None:
        """Eliminate phase-1 LLM round-trip on single-step verified home reads & controls."""
        if len(receipts) != 1 or not self.available_for(turn):
            return None
        receipt = receipts[0]
        if receipt.call.name != SMART_HOME_LIVE.name or receipt.output.get("status") != "verified":
            return None
        prompt_low = str(getattr(turn, "text", "") or "").casefold()
        # Let multi-intent requests (e.g. "wat is de temperatuur én zet het op de chromecast") continue to phase 1
        if any(
            k in prompt_low
            for k in (
                "chromecast",
                "holostage",
                "op de tv",
                "op tv",
                "op het scherm",
                "automatisering maken",
                "boodschappen",
                " en zet ",
                " én zet ",
                " en zoek ",
                "alle kamers",
            )
        ):
            return None

        def _fmt_num(val: Any) -> str:
            return str(val).replace(".", ",")

        out = receipt.output
        op = str(out.get("operation") or "")
        target = str(out.get("target") or "").strip()

        if op == "read_climate":
            if any(k in prompt_low for k in ("buiten", "het weer")) and out.get("outdoor_weather"):
                w = out["outdoor_weather"][0]
                wt = w.get("temperature_c")
                if wt is not None:
                    return f"Buiten is het nu {_fmt_num(wt)} graden."
            zones = out.get("climate_zones") or []
            if zones:
                # Prefer a real room zone over house-wide aggregate when target is generic
                chosen = next((z for z in zones if z.get("entity_id") != "climate.home"), zones[0])
                cur = chosen.get("current_temperature_c")
                tgt = chosen.get("target_temperature_c")
                hum = chosen.get("humidity_pct")
                room_label = target if target and target != "huis" else str(chosen.get("name") or "huis").casefold()
                if room_label in {"woonkamer", "keuken", "badkamer", "slaapkamer", "hal", "kantoor", "zolder"}:
                    loc = f"In de {room_label}"
                elif room_label == "huis":
                    loc = "In huis"
                else:
                    loc = f"Bij {chosen.get('name') or room_label}"
                if any(k in prompt_low for k in ("luchtvochtigheid", "vochtig")) and hum is not None:
                    if cur is not None:
                        return f"{loc} is de luchtvochtigheid nu {_fmt_num(hum)} procent en het is {_fmt_num(cur)} graden."
                    return f"{loc} is de luchtvochtigheid nu {_fmt_num(hum)} procent."
                if cur is not None:
                    if tgt is not None and any(k in prompt_low for k in ("ingesteld", "thermostaat", "doel")):
                        return f"{loc} is het nu {_fmt_num(cur)} graden, en de thermostaat staat op {_fmt_num(tgt)} graden."
                    return f"{loc} is het nu {_fmt_num(cur)} graden."
            sensors = out.get("sensors") or []
            for s in sensors:
                unit = str(s.get("unit") or "")
                st_val = s.get("state")
                if st_val not in {None, "unknown", "unavailable"} and "c" in unit.casefold():
                    loc = f"In de {target}" if target and target != "huis" else f"Bij {s.get('name')}"
                    return f"{loc} is het nu {_fmt_num(st_val)} graden."
            return None

        if op == "set_climate":
            cl = out.get("climate") or {}
            name = str(cl.get("name") or target or "de thermostaat")
            tgt = cl.get("target_temperature_c")
            st_val = str(cl.get("state") or "")
            if tgt is not None and receipt.call.arguments.get("temperature_c") is not None:
                return f"Ik heb {name} ingesteld op {_fmt_num(tgt)} graden."
            if st_val:
                return f"Ik heb {name} op {st_val} gezet."
            return None

        if op == "control_lights":
            ents = out.get("entities") or []
            svc = str(out.get("service") or "turn_on")
            bri = receipt.call.arguments.get("brightness_pct")
            label = f"de verlichting in de {target}" if target in {"woonkamer", "keuken", "badkamer", "slaapkamer", "hal", "kantoor", "tuin", "overkapping", "schuur"} else (str(ents[0].get("name")) if len(ents) == 1 else (target or "de verlichting"))
            if svc == "turn_off":
                return f"Ik heb {label} uitgezet."
            if isinstance(bri, int):
                return f"Ik heb {label} op {bri} procent gezet."
            if svc == "toggle":
                return f"Ik heb {label} omgeschakeld."
            return f"Ik heb {label} aangezet."

        if op == "activate_scene":
            sc = out.get("scene") or {}
            return f"Ik heb scène {sc.get('name') or target} geactiveerd."

        if op == "control_media":
            mp = out.get("media_player") or {}
            name = str(mp.get("name") or target or "de speler")
            act = str(out.get("action") or "")
            vol = receipt.call.arguments.get("volume_pct")
            if act == "volume_set" and isinstance(vol, int):
                return f"Ik heb het volume van {name} op {vol} procent gezet."
            verbs = {
                "media_play": "gestart",
                "media_pause": "gepauzeerd",
                "media_stop": "gestopt",
                "turn_on": "aangezet",
                "turn_off": "uitgezet",
            }
            if act in verbs:
                return f"Ik heb {name} {verbs[act]}."
            return None

        return None

    def pending(self, turn: Any, *, step_index: int = 0) -> None:
        _ = (turn, step_index)
        return None

    def _ha_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        assert self._ha is not None
        origin = self._ha.endpoint_url.rstrip("/")
        if not path.startswith("/api/"):
            raise ValueError("invalid Home Assistant API path")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._ha.credential}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib_request.Request(f"{origin}{path}", data=body, method=method, headers=headers)
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
        allowed_keys = set(SMART_HOME_LIVE.input_schema["properties"].keys())
        if "operation" not in args or not set(args).issubset(allowed_keys):
            raise ConversationActionInputRejected(("operation",))

        op = str(args.get("operation") or "")
        write_ops = {"set_climate", "control_lights", "activate_scene", "control_media"}
        auto_act = str(args.get("automation_action") or "list")
        if op == "list_or_toggle_automation" and not self._principal_in_allowlist(turn):
            raise ConversationConflict("Automation inventory and control require an authorized household/owner principal")
        needs_write = op in write_ops or (op == "list_or_toggle_automation" and auto_act != "list")
        if needs_write and not self._is_authorized(turn, write_effect=True):
            raise ConversationConflict("Smart-home write effects require authorized household/owner permission")
        api_write_ops = {"set_climate", "activate_scene", "control_media"}
        if (op in api_write_ops or (op == "list_or_toggle_automation" and auto_act != "list")) and not (
            self._ha is not None and self._ha.api_enabled
        ):
            raise ConversationConflict("General Home Assistant API is required for this smart-home domain write")

        try:
            return self._dispatch(call, args, op=op)
        except (ConversationActionInputRejected, ConversationConflict):
            raise
        except Exception as exc:
            return ConversationActionReceipt(
                call,
                {"status": "unavailable", "operation": op, "reason": type(exc).__name__},
            )

    def _dispatch(self, call: Any, args: dict[str, Any], *, op: str) -> ConversationActionReceipt:
        raw_needle, tokens = _normalize_tokens(args.get("target"))
        states = self._fetch_states()

        if op == "read_climate":
            climates = []
            temp_sensors = []
            weather_items = []
            for row in states:
                eid = str(row["entity_id"])
                dom = eid.partition(".")[0]
                if dom not in {"climate", "sensor", "weather"}:
                    continue
                if str(row.get("state") or "") in {"unavailable", "unknown"} and dom != "climate":
                    continue
                score = _matches_entity(row, raw_needle, tokens)
                if dom == "climate" and score > 0:
                    climates.append((score, _safe_summary(row)))
                elif dom == "weather" and (not raw_needle or "buiten" in tokens or "weer" in raw_needle or score > 0):
                    weather_items.append(_safe_summary(row))
                elif dom == "sensor" and score > 0:
                    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
                    unit = str(attrs.get("unit_of_measurement") or "")
                    dcls = str(attrs.get("device_class") or "")
                    if dcls in {"temperature", "humidity"} or "°c" in unit.casefold() or unit == "%":
                        if "cpu" not in eid and "processor" not in eid and "battery" not in dcls:
                            temp_sensors.append((score, _safe_summary(row)))

            climates.sort(key=lambda x: (-x[0], x[1]["name"]))
            temp_sensors.sort(key=lambda x: (-x[0], x[1]["name"]))
            all_climates_fallback = []
            if not climates and not temp_sensors:
                all_climates_fallback = [
                    _safe_summary(r) for r in states if str(r["entity_id"]).startswith("climate.")
                ]
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "read_climate",
                    "target": raw_needle or "huis",
                    "climate_zones": [x[1] for x in climates[:8]] or all_climates_fallback[:8],
                    "sensors": [x[1] for x in temp_sensors[:8]],
                    "outdoor_weather": weather_items[:2],
                },
            )

        if op == "set_climate":
            raw_temp = args.get("temperature_c")
            hvac_mode = args.get("hvac_mode")
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
            candidates = [
                (_matches_entity(r, raw_needle, tokens), r)
                for r in states
                if str(r["entity_id"]).startswith("climate.")
            ]
            candidates = [c for c in candidates if c[0] > 0]
            candidates.sort(key=lambda x: (-x[0], 0 if str(x[1]["entity_id"]) != "climate.home" else 1))
            if not candidates:
                return ConversationActionReceipt(
                    call,
                    {
                        "status": "target_not_found",
                        "available_climate_zones": [
                            _safe_summary(r) for r in states if str(r["entity_id"]).startswith("climate.")
                        ],
                    },
                )
            chosen_id = str(candidates[0][1]["entity_id"])
            if hvac_mode in {"heat", "off", "auto", "cool"}:
                self._ha_request(
                    "POST",
                    "/api/services/climate/set_hvac_mode",
                    {"entity_id": chosen_id, "hvac_mode": hvac_mode},
                )
            if parsed_temp is not None:
                self._ha_request(
                    "POST",
                    "/api/services/climate/set_temperature",
                    {"entity_id": chosen_id, "temperature": round(parsed_temp * 2.0) / 2.0},
                )
            verified_row = self._fetch_entity_state(chosen_id) or candidates[0][1]
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "set_climate",
                    "climate": _safe_summary(verified_row),
                },
            )

        if op == "read_status":
            matched: list[tuple[int, dict[str, Any]]] = []
            for row in states:
                eid = str(row["entity_id"])
                dom = eid.partition(".")[0]
                if dom not in _READABLE_DOMAINS or dom in _FORBIDDEN_DOMAINS:
                    continue
                if str(row.get("state") or "") == "unavailable":
                    continue
                sc = _matches_entity(row, raw_needle, tokens)
                if sc > 0:
                    matched.append((sc, _safe_summary(row)))
            matched.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "read_status",
                    "target": raw_needle or "huis",
                    "entities": [x[1] for x in matched[:20]],
                },
            )

        if op == "control_lights":
            if not raw_needle:
                raise ConversationActionInputRejected(("target",))
            desired = str(args.get("state") or "on")
            bri = args.get("brightness_pct")
            kelvin = args.get("color_temp_kelvin")
            rgb = args.get("rgb_color")
            if (bri is not None or kelvin is not None or rgb is not None) and desired == "off":
                desired = "on"

            light_hits: list[tuple[int, dict[str, Any]]] = []
            for row in states:
                eid = str(row["entity_id"])
                dom = eid.partition(".")[0]
                if dom not in {"light", "switch"}:
                    continue
                if "led_ring" in eid and "ring" not in raw_needle:
                    continue
                sc = _matches_entity(row, raw_needle, tokens)
                if sc > 0 and str(row.get("state") or "") != "unavailable":
                    light_hits.append((sc, row))
            if not light_hits:
                return ConversationActionReceipt(
                    call,
                    {"status": "target_not_found", "target": raw_needle},
                )
            light_hits.sort(key=lambda x: -x[0])
            top_score = light_hits[0][0]
            exact = [r for sc, r in light_hits if sc >= 100]
            selected_rows = exact[:2] if exact else [r for sc, r in light_hits if sc >= max(15, top_score - 15)][:8]
            entity_ids = [str(r["entity_id"]) for r in selected_rows]
            light_ids = [e for e in entity_ids if e.startswith("light.")]
            switch_ids = [e for e in entity_ids if e.startswith("switch.")]

            svc = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}.get(desired, "turn_on")
            assert self._ha is not None
            if not self._ha.api_enabled:
                if (
                    svc not in {"turn_on", "turn_off"}
                    or bri is not None
                    or kelvin is not None
                    or rgb is not None
                    or not all(f"{eid}:{svc}" in self._ha.effect_allowlist for eid in entity_ids)
                ):
                    raise ConversationConflict("Target light/switch effect is outside the configured allowlist")
            if light_ids:
                payload: dict[str, Any] = {"entity_id": light_ids}
                if svc == "turn_on":
                    if isinstance(bri, int) and 1 <= bri <= 100:
                        payload["brightness_pct"] = bri
                    if isinstance(kelvin, int) and 2000 <= kelvin <= 6500:
                        payload["color_temp_kelvin"] = kelvin
                    if (
                        isinstance(rgb, list)
                        and len(rgb) == 3
                        and all(isinstance(c, int) and 0 <= c <= 255 for c in rgb)
                    ):
                        payload["rgb_color"] = rgb
                self._ha_request("POST", f"/api/services/light/{svc}", payload)
            if switch_ids:
                self._ha_request("POST", f"/api/services/switch/{svc}", {"entity_id": switch_ids})

            verified = []
            for eid in entity_ids[:6]:
                st_row = self._fetch_entity_state(eid)
                if st_row is not None:
                    verified.append(_safe_summary(st_row))
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "control_lights",
                    "service": svc,
                    "entities": verified,
                },
            )

        if op == "activate_scene":
            if not raw_needle:
                raise ConversationActionInputRejected(("target",))
            scenes = [
                (_matches_entity(r, raw_needle, tokens), r)
                for r in states
                if str(r["entity_id"]).startswith("scene.")
            ]
            scenes = [s for s in scenes if s[0] > 0]
            scenes.sort(key=lambda x: -x[0])
            if not scenes:
                return ConversationActionReceipt(
                    call,
                    {
                        "status": "target_not_found",
                        "available_scenes": [
                            _safe_summary(r) for r in states if str(r["entity_id"]).startswith("scene.")
                        ][:12],
                    },
                )
            chosen_scene = str(scenes[0][1]["entity_id"])
            self._ha_request("POST", "/api/services/scene/turn_on", {"entity_id": chosen_scene})
            readback = self._fetch_entity_state(chosen_scene) or scenes[0][1]
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "activate_scene",
                    "scene": _safe_summary(readback),
                },
            )

        if op == "control_media":
            if not raw_needle:
                raise ConversationActionInputRejected(("target",))
            m_action = str(args.get("media_action") or "play")
            players = [
                (_matches_entity(r, raw_needle, tokens), r)
                for r in states
                if str(r["entity_id"]).startswith("media_player.")
                and str(r.get("state") or "") != "unavailable"
            ]
            players = [p for p in players if p[0] > 0]
            players.sort(key=lambda x: -x[0])
            if not players:
                return ConversationActionReceipt(call, {"status": "target_not_found", "target": raw_needle})
            chosen_mp = str(players[0][1]["entity_id"])
            svc_map = {
                "play": "media_play",
                "pause": "media_pause",
                "stop": "media_stop",
                "turn_on": "turn_on",
                "turn_off": "turn_off",
                "volume_set": "volume_set",
            }
            svc_name = svc_map.get(m_action, "media_play")
            mp_payload: dict[str, Any] = {"entity_id": chosen_mp}
            if svc_name == "volume_set":
                vol = args.get("volume_pct")
                if not isinstance(vol, int) or not 0 <= vol <= 100:
                    raise ConversationActionInputRejected(("volume_pct",))
                mp_payload["volume_level"] = round(vol / 100.0, 2)
            self._ha_request("POST", f"/api/services/media_player/{svc_name}", mp_payload)
            readback = self._fetch_entity_state(chosen_mp) or players[0][1]
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "control_media",
                    "action": svc_name,
                    "media_player": _safe_summary(readback),
                },
            )

        # op == "list_or_toggle_automation"
        auto_action = str(args.get("automation_action") or "list")
        autos = [r for r in states if str(r["entity_id"]).startswith("automation.")]
        if auto_action == "list":
            scored = [(_matches_entity(r, raw_needle, tokens), r) for r in autos]
            filtered = [r for sc, r in sorted(scored, key=lambda x: -x[0]) if sc > 0] if raw_needle else autos
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "operation": "list_or_toggle_automation",
                    "automations": [_safe_summary(r) for r in filtered[:24]],
                },
            )
        if not raw_needle:
            raise ConversationActionInputRejected(("target",))
        scored_autos = [(_matches_entity(r, raw_needle, tokens), r) for r in autos]
        scored_autos = [a for a in scored_autos if a[0] > 0]
        scored_autos.sort(key=lambda x: -x[0])
        if not scored_autos:
            return ConversationActionReceipt(
                call,
                {
                    "status": "target_not_found",
                    "available_automations": [_safe_summary(r) for r in autos[:16]],
                },
            )
        chosen_auto = str(scored_autos[0][1]["entity_id"])
        if auto_action in {"turn_on", "turn_off", "toggle", "trigger"}:
            self._ha_request("POST", f"/api/services/automation/{auto_action}", {"entity_id": chosen_auto})
        readback = self._fetch_entity_state(chosen_auto) or scored_autos[0][1]
        return ConversationActionReceipt(
            call,
            {
                "status": "verified",
                "operation": "list_or_toggle_automation",
                "automation_action": auto_action,
                "automation": _safe_summary(readback),
            },
        )


__all__ = ["SMART_HOME_LIVE", "SmartHomeFastActions"]
