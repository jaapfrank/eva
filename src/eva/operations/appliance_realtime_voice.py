"""Full-duplex Realtime Voice Engine for Eva's 3D Stage (`gpt-realtime-2.1`, `gpt-realtime-2.1-mini`, `gemini-3.8-live`, and Codex WebRTC).

Supports:
1. OpenAI Realtime WebRTC (`gpt-realtime-2.1`, `gpt-realtime-2.1-mini`) via vaulted OpenAI API key
   OR zero-extra-key ChatGPT/Codex OAuth WebRTC (`codex app-server` `thread/realtime/start`).
2. Google Gemini 3.8 Live (`gemini-3.8-live`) full-duplex native audio WebSocket (`BidiGenerateContent`)
   with server-side vaulted API key, automatic model alias fallback, barge-in, and live Eva Core tool calls.
3. Warm Dutch neural TTS (`nl-NL-FennaNeural` via `edge_tts`) with bounded LRU cache so typed turns and
   wizard greetings never fall back to robotic OS SAPI5 speech.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import http.client
import json
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote
from uuid import uuid4


REALTIME_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "model_id": "gpt-realtime-2.1",
        "label": "OpenAI GPT Realtime 2.1 (Aanbevolen · Meest menselijk)",
        "provider": "openai_realtime",
        "transport": "webrtc",
        "default_voice": "marin",
        "voices": ["marin", "cedar", "coral", "sage", "juniper", "sol", "vale"],
        "summary_nl": "Volledig doorlopend live gesprek met natuurlijke ademhaling, intonatie, barge-in en tussenzinnetjes.",
    },
    {
        "model_id": "gpt-realtime-2.1-mini",
        "label": "OpenAI GPT Realtime 2.1 Mini (Razendsnel & licht)",
        "provider": "openai_realtime",
        "transport": "webrtc",
        "default_voice": "marin",
        "voices": ["marin", "cedar", "coral", "sage", "juniper", "sol", "vale"],
        "summary_nl": "Ultralage latentie voor vlotte doorlopende spraak in huis met volledige koppeling aan Eva's kern.",
    },
    {
        "model_id": "gemini-3.8-live",
        "label": "Google Gemini 3.8 Live (Native Audio)",
        "provider": "gemini_live",
        "transport": "gemini_ws",
        "default_voice": "Aoede",
        "voices": ["Aoede", "Kore", "Leda", "Puck", "Charon"],
        "summary_nl": "Google's native full-duplex spraakmodel met expressieve Nederlandse stem en live onderbreking.",
    },
)

VALID_REALTIME_MODELS = {m["model_id"]: m for m in REALTIME_MODEL_CATALOG}

_NEURAL_TTS_CACHE_LOCK = threading.Lock()
_NEURAL_TTS_CACHE: OrderedDict[str, bytes] = OrderedDict()
_MAX_TTS_CACHE_ITEMS = 72


def clean_spoken_dutch(text: str) -> str:
    """Clean markdown, URLs, and emojis so neural TTS reads warm, natural Dutch prose."""
    cleaned = re.sub(r"```[\s\S]*?```", " ", text or "")
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[\U0001F1E6-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not re.search(r"[A-Za-z0-9À-ÿ]", cleaned):
        return ""
    return cleaned[:1400]


def synthesize_neural_dutch_mp3(text: str, *, voice: str = "nl-NL-FennaNeural") -> tuple[bytes, str] | None:
    """Synthesize warm, natural female Dutch neural audio (`nl-NL-FennaNeural`) with bounded RAM cache."""
    clean = clean_spoken_dutch(text)
    if not clean:
        return None
    cache_key = f"{voice}:{clean}"
    with _NEURAL_TTS_CACHE_LOCK:
        cached = _NEURAL_TTS_CACHE.get(cache_key)
        if cached is not None:
            _NEURAL_TTS_CACHE.move_to_end(cache_key)
            return cached, "audio/mpeg"

    with tempfile.TemporaryDirectory(prefix="eva-neural-tts-") as tmp_dir:
        out_mp3 = Path(tmp_dir) / "speech.mp3"
        try:
            proc = subprocess.run(  # noqa: S603 - pinned Python interpreter running installed edge_tts module
                [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--voice",
                    voice,
                    "--rate=+4%",
                    "--text",
                    clean,
                    "--write-media",
                    str(out_mp3),
                ],
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
            )
            if proc.returncode != 0 or not out_mp3.is_file():
                return None
            audio_bytes = out_mp3.read_bytes()
        except Exception:
            return None
    if len(audio_bytes) < 256:
        return None

    with _NEURAL_TTS_CACHE_LOCK:
        _NEURAL_TTS_CACHE[cache_key] = audio_bytes
        while len(_NEURAL_TTS_CACHE) > _MAX_TTS_CACHE_ITEMS:
            _NEURAL_TTS_CACHE.popitem(last=False)
    return audio_bytes, "audio/mpeg"


def pick_dutch_filler_phrase(user_text: str, owner_name: str = "Jaap") -> str:
    """Select a natural, warm Dutch interim phrase ('tussenzinnetje') matched to the user's request."""
    low = (user_text or "").lower()
    if any(k in low for k in ("lamp", "licht", "temperatuur", "thermostaat", "home assistant", "sensor", "schakelaar")):
        options = (
            "Even in je huis kijken hoor…",
            "Momentje, ik check Home Assistant meteen even…",
            "Ik kijk gelijk even voor je wat er aanstaat…",
        )
    elif any(k in low for k in ("unifi", "wifi", "wi-fi", "netwerk", "camera", "deurbel", "protect")):
        options = (
            "Even op je thuisnetwerk kijken hoor…",
            "Momentje, ik check je UniFi meteen even…",
        )
    elif any(k in low for k in ("agenda", "afspraak", "kalender", "mail", "e-mail", "inbox")):
        options = (
            "Ik pak je overzicht er meteen even bij…",
            "Momentje hoor, ik kijk gelijk even voor je…",
        )
    elif any(k in low for k in ("maak", "bouw", "programmeer", "zoek uit", "onderzoek")):
        options = (
            f"Goede vraag {owner_name}, ik duik er meteen even in…",
            "Oeh leuk, ik zoek dat meteen even voor je uit…",
        )
    else:
        options = (
            "Even kijken hoor…",
            "Momentje, ik pak het er gelijk bij…",
            "Even meedenken hoor…",
        )
    idx = sum(ord(c) for c in low[:48]) % len(options)
    return options[idx]


def build_realtime_eva_instructions(stage_snapshot: Mapping[str, Any]) -> str:
    """Build Eva's full-duplex Dutch system instructions grounded in her household & connected tools."""
    eva = stage_snapshot.get("eva") or {}
    hh = stage_snapshot.get("household") or {}
    sh = (stage_snapshot.get("channels") or {}).get("smart_home") or {}
    integ = stage_snapshot.get("integrations") or {}

    eva_name = str(eva.get("display_name") or "Eva")
    owner_name = str(hh.get("owner_name") or "Jaap")
    family = [str(x) for x in (hh.get("family_members") or []) if str(x).strip()]
    fam_str = f" en zijn gezinsleden ({', '.join(family)})" if family else ""
    temperament = ", ".join(str(x) for x in (eva.get("temperament") or ["warm", "betrokken", "nuchter"]))
    humour = str(eva.get("humour") or "subtiele, warme Nederlandse humor")
    rules = [str(r) for r in (hh.get("custom_rules") or [])[:10]]
    guest_on = bool(hh.get("guest_mode_active", False))

    ha_status = (
        f"ACTIEF verbonden op {sh.get('url')}"
        if (sh.get("enabled") and sh.get("secret_present"))
        else (
            f"gevonden op {sh.get('lan_detected', {}).get('ip')}, nog te koppelen"
            if (sh.get("lan_detected") or {}).get("reachable")
            else "nog niet gekoppeld"
        )
    )
    vault_svcs = [
        f"{s.get('title') or s.get('service_id')} (id='{s.get('service_id')}')"
        for s in (integ.get("services") or [])
        if s.get("has_credential")
    ]
    caps = [
        f"{c.get('title') or c.get('capability_id')} (id='{c.get('capability_id')}')"
        for c in (integ.get("capabilities") or [])
    ]

    lines = [
        f"Je bent {eva_name}, de levende, warme en slimme digitale huisgenoot van {owner_name}{fam_str}.",
        f"Karakter & spreekstijl: {temperament}. Humor: {humour}.",
        "BELANGRIJKE SPREEKREGELS VOOR DIT DOORLOPENDE LIVE GESPREK:",
        "1. Spreek 100% natuurlijk, warm, vlot en menselijk Nederlands (Nederland), precies zoals een echte huisgenoot in de kamer praat — nooit stijf, nooit opgesomd met bulletpoints en nooit als een robot.",
        "2. Houd je gesproken antwoorden lekker vlot en bondig (meestal 1 tot 3 zinnen), zodat het een echt vlot heen-en-weer gesprek blijft.",
        "3. Geef op begroetingen, gezellige praatjes, grappen of algemene vragen METEEN direct antwoord zonder een tool aan te roepen.",
        "4. RAZENDSNELLE DIRECTE TOOLS (<0,1 sec!): Gebruik voor Home Assistant (temperatuur, weer, lampen lezen/aan/uit/dimmen, thermostaat, scènes) ALTIJD direct `snel_huis_lezen_of_bedienen`, en gebruik voor al gebouwde eigen Python-skills of gekoppelde kluis-diensten (UniFi, agenda, e-mail) ALTIJD direct `gebruik_eigen_vaardigheid_of_kluis`! Gebruik `raadpleeg_eva_kern_en_huis` alleen voor persoonlijke langetermijnherinneringen, web-onderzoek of wanneer je een GLOEDNIEUWE vaardigheid moet programmeren die nog niet bestaat.",
        "5. TUSSENZINNETJES BIJ ZWARE ACTIES: Alleen bij `bekijk_camera_beeld` of `raadpleeg_eva_kern_en_huis` spreek je eerst kort een natuurlijk Nederlands tussenzinnetje uit ('Even op de camera kijken hoor…' of 'Even induiken hoor…'). Bij `snel_huis_lezen_of_bedienen` is dat zo snel (<0,1s) dat je meteen vlot het antwoord kunt geven.",
        "6. Verzin nooit zelf sensorwaarden, camerabeelden, e-mails of apparaatstatussen: haal ze altijd echt op via je tools.",
        f"Huidige huisstatus: Home Assistant is {ha_status}. Gekoppelde kluis-diensten: {', '.join(vault_svcs) if vault_svcs else 'nog geen'}. Zelfgebouwde skills (direct uitvoerbaar!): {', '.join(caps) if caps else '0'}.",
    ]
    if guest_on:
        lines.append(
            "LET OP: Visite-modus (Privéschild) staat NU AAN. Deel geen privé-agenda's of vertrouwelijke gezinszaken hardop."
        )
    if rules:
        lines.append("Actieve huisafspraken: " + "; ".join(rules))
    return "\n".join(lines)


def build_openai_realtime_tools() -> list[dict[str, Any]]:
    """Function tools exposed to OpenAI `gpt-realtime-2.1` / `gpt-realtime-2.1-mini` DataChannel."""
    return [
        {
            "type": "function",
            "name": "snel_huis_lezen_of_bedienen",
            "description": (
                "Ultrasnelle directe Home Assistant koppeling (<100ms op lokaal netwerk) om kamertemperatuur, "
                "buitenweer, lampen of status uit te lezen of direct lampen aan/uit te zetten, te dimmen, "
                "thermostaat in te stellen of een scène te activeren."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actie": {
                        "type": "string",
                        "enum": [
                            "lees_klimaat",
                            "lees_status",
                            "lees_weer",
                            "lampen_aan",
                            "lampen_uit",
                            "dim_lampen",
                            "stel_thermostaat_in",
                            "activeer_scene",
                        ],
                    },
                    "kamer_of_apparaat": {
                        "type": "string",
                        "description": "Kamer of apparaat (bijv. 'woonkamer', 'keuken', 'kantoor', 'lola', 'buiten' of '' voor heel het huis).",
                    },
                    "waarde": {
                        "type": "string",
                        "description": "Optioneel getal bij dimmen ('40') of thermostaat ('20.5'), anders ''.",
                    },
                },
                "required": ["actie", "kamer_of_apparaat", "waarde"],
            },
        },
        {
            "type": "function",
            "name": "gebruik_eigen_vaardigheid_of_kluis",
            "description": (
                "Voer razendsnel (<150ms) een al door Eva gebouwde Python-skill uit (zoals 'stroomkosten_calculator') "
                "of raadpleeg direct een gekoppelde kluis-dienst (UniFi Netwerk, UniFi Protect, Agenda, E-mail)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "soort": {
                        "type": "string",
                        "enum": ["run_capability", "list_capabilities", "vault_service"],
                    },
                    "naam_of_id": {
                        "type": "string",
                        "description": "Exacte capability_id (bijv. 'stroomkosten_calculator') of kluis service_id.",
                    },
                    "operatie": {
                        "type": "string",
                        "description": "Bij vault_service bijv. 'summary', 'clients', 'today', 'inbox_summary'; bij run_capability 'run'.",
                    },
                    "parameters_json": {
                        "type": "string",
                        "description": "JSON-object met parameters als tekst, bijv. '{\"vermogen_watt\": 18, \"prijs_per_kwh\": 0.27}' of '{}'.",
                    },
                },
                "required": ["soort", "naam_of_id", "operatie", "parameters_json"],
            },
        },
        {
            "type": "function",
            "name": "raadpleeg_eva_kern_en_huis",
            "description": (
                "Gebruik Eva's diepe kern en geheugen (Hindsight) voor persoonlijke herinneringen, web-zoekopdrachten, "
                "nieuwe dienst-koppelingen of het programmeren van een GLOEDNIEUWE Python-vaardigheid die nog niet bestaat. "
                "Zeg altijd eerst hardop een kort Nederlands tussenzinnetje ('Even induiken hoor…') wanneer je deze tool aanroept!"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "verzoek": {
                        "type": "string",
                        "description": "Het volledige Nederlandstalige verzoek of de vraag van de spreker.",
                    }
                },
                "required": ["verzoek"],
            },
        },
        {
            "type": "function",
            "name": "open_scherm_paneel",
            "description": "Open direct een paneel op het 3D-scherm (bijv. koppelingen/kluis, huisafspraken, gezins-QR of de 6-staps wizard).",
            "parameters": {
                "type": "object",
                "properties": {
                    "paneel": {
                        "type": "string",
                        "enum": ["integrations", "smart_home", "rules", "family", "wizard"],
                    }
                },
                "required": ["paneel"],
            },
        },
        {
            "type": "function",
            "name": "bekijk_camera_beeld",
            "description": (
                "Kijk live mee via de camera van het Hoofdscherm of een Home Assistant / UniFi Protect camera "
                "(bijv. keuken, voordeur/deurbel, tuin, garage) en analyseer wat of wie er te zien is. "
                "Zeg altijd eerst hardop 'Even op de camera kijken hoor…' voordat je deze tool aanroept!"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_hint": {
                        "type": "string",
                        "description": "Optioneel: welke camera (bijv. 'keuken', 'voordeur', 'deurbel', 'tuin', 'garage' of leeg voor het hoofdscherm).",
                    },
                    "vraag": {
                        "type": "string",
                        "description": "Waar Eva specifiek op moet letten op het beeld.",
                    },
                },
                "required": ["vraag"],
            },
        },
        {
            "type": "function",
            "name": "zet_visite_modus",
            "description": "Zet de visite-modus en het privéschild direct aan of uit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actief": {"type": "boolean"}
                },
                "required": ["actief"],
            },
        },
    ]


def build_gemini_live_tools() -> list[dict[str, Any]]:
    """Function declarations exposed to Google `gemini-3.8-live` BidiGenerateContent session."""
    return [
        {
            "functionDeclarations": [
                {
                    "name": "snel_huis_lezen_of_bedienen",
                    "description": (
                        "Ultrasnelle directe Home Assistant koppeling (<100ms) voor kamertemperatuur, weer, "
                        "lampenstatus of direct lampen/scènes/thermostaat bedienen."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "actie": {
                                "type": "STRING",
                                "description": "Kies uit: lees_klimaat, lees_status, lees_weer, lampen_aan, lampen_uit, dim_lampen, stel_thermostaat_in, activeer_scene.",
                            },
                            "kamer_of_apparaat": {
                                "type": "STRING",
                                "description": "Kamer of apparaat (bijv. woonkamer, keuken, kantoor, lola, buiten of leeg).",
                            },
                            "waarde": {
                                "type": "STRING",
                                "description": "Optionele waarde bij dimmen of thermostaat, anders leeg.",
                            },
                        },
                        "required": ["actie", "kamer_of_apparaat", "waarde"],
                    },
                },
                {
                    "name": "gebruik_eigen_vaardigheid_of_kluis",
                    "description": (
                        "Voer direct (<150ms) een al door Eva gebouwde Python-skill uit of raadpleeg een gekoppelde kluis-dienst."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "soort": {
                                "type": "STRING",
                                "description": "Kies uit: run_capability, list_capabilities, vault_service.",
                            },
                            "naam_of_id": {
                                "type": "STRING",
                                "description": "Capability ID of service ID.",
                            },
                            "operatie": {
                                "type": "STRING",
                                "description": "Operatienaam of run.",
                            },
                            "parameters_json": {
                                "type": "STRING",
                                "description": "JSON-object als tekst.",
                            },
                        },
                        "required": ["soort", "naam_of_id", "operatie", "parameters_json"],
                    },
                },
                {
                    "name": "raadpleeg_eva_kern_en_huis",
                    "description": (
                        "Raadpleeg Eva's diepe kern en geheugen voor herinneringen, web-onderzoek of het bouwen van een nieuwe Python-skill. "
                        "Zeg altijd eerst hardop een kort Nederlands tussenzinnetje voordat je deze functie aanroept."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "verzoek": {
                                "type": "STRING",
                                "description": "Het volledige Nederlandstalige verzoek van de spreker.",
                            }
                        },
                        "required": ["verzoek"],
                    },
                },
                {
                    "name": "open_scherm_paneel",
                    "description": "Open direct een paneel op Eva's 3D-scherm.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "paneel": {
                                "type": "STRING",
                                "description": "Kies uit: integrations, smart_home, rules, family, wizard.",
                            }
                        },
                        "required": ["paneel"],
                    },
                },
                {
                    "name": "bekijk_camera_beeld",
                    "description": (
                        "Kijk live mee via de camera van het Hoofdscherm of een Home Assistant / UniFi Protect camera "
                        "(bijv. keuken, voordeur, deurbel, tuin, garage). Zeg eerst hardop een kort tussenzinnetje."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "camera_hint": {
                                "type": "STRING",
                                "description": "Welke camera (bijv. keuken, voordeur, tuin, garage of leeg voor hoofdscherm).",
                            },
                            "vraag": {
                                "type": "STRING",
                                "description": "Waar Eva op moet letten.",
                            },
                        },
                        "required": ["vraag"],
                    },
                },
                {
                    "name": "zet_visite_modus",
                    "description": "Zet de visite-modus (privéschild) aan of uit.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "actief": {"type": "BOOLEAN"}
                        },
                        "required": ["actief"],
                    },
                },
            ]
        }
    ]


class _CodexWebrtcProcessSession:
    """Manages an active `codex app-server` WebRTC realtime session bridged to browser SDP."""

    def __init__(self, *, session_id: str, proc: subprocess.Popen[str], temp_dir: tempfile.TemporaryDirectory[str]) -> None:
        self.session_id = session_id
        self.proc = proc
        self.temp_dir = temp_dir
        self.created_at = time.monotonic()

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass


class RealtimeVoiceHub:
    """Negotiates WebRTC (`gpt-realtime-2.1`, `gpt-realtime-2.1-mini`, Codex OAuth) & Gemini 3.8 Live WebSockets."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root)
        self._lock = threading.Lock()
        self._codex_sessions: dict[str, _CodexWebrtcProcessSession] = {}

    def _cleanup_expired_unlocked(self) -> None:
        now = time.monotonic()
        expired = [
            sid
            for sid, sess in self._codex_sessions.items()
            if (now - sess.created_at) > 1800.0 or sess.proc.poll() is not None
        ]
        for sid in expired:
            sess = self._codex_sessions.pop(sid, None)
            if sess is not None:
                sess.close()

    def stop_session(self, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if session_id:
                sess = self._codex_sessions.pop(session_id, None)
                if sess is not None:
                    sess.close()
            else:
                for sess in list(self._codex_sessions.values()):
                    sess.close()
                self._codex_sessions.clear()
        return {"ok": True}

    def negotiate_openai_or_codex_webrtc(
        self,
        *,
        offer_sdp: str,
        model: str,
        voice: str,
        stage_snapshot: Mapping[str, Any],
        openai_api_key: str | None,
        codex_lease_path: Path,
    ) -> dict[str, Any]:
        """Exchange browser WebRTC SDP offer for remote SDP answer using OpenAI Realtime API or ChatGPT/Codex OAuth."""
        if not offer_sdp or not offer_sdp.lstrip().startswith("v=0") or len(offer_sdp) > 131_072:
            raise ValueError("Ongeldig WebRTC SDP-aanbod vanuit de browser")

        target_model = model if model in {"gpt-realtime-2.1", "gpt-realtime-2.1-mini"} else "gpt-realtime-2.1"
        safe_voice = voice if voice in {"marin", "cedar", "coral", "sage", "juniper", "sol", "vale", "alloy", "shimmer"} else "marin"
        instructions = build_realtime_eva_instructions(stage_snapshot)
        tools = build_openai_realtime_tools()

        session_update_payload = {
            "type": "session.update",
            "session": {
                "instructions": instructions,
                "voice": safe_voice,
                "input_audio_transcription": {"model": "gpt-4o-mini-transcribe", "language": "nl"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "low",
                    "create_response": True,
                    "interrupt_response": True,
                },
                "tools": tools,
                "tool_choice": "auto",
            },
        }

        # 1. Direct OpenAI Realtime WebRTC when an OpenAI Platform API key is stored in Eva's Vault/Settings
        clean_key = (openai_api_key or "").strip()
        if clean_key and clean_key != "eva-local-keyless-endpoint":
            answer_sdp, resolved_model = self._negotiate_direct_openai_webrtc(
                offer_sdp=offer_sdp,
                api_key=clean_key,
                requested_model=target_model,
                voice=safe_voice,
                instructions=instructions,
            )
            return {
                "ok": True,
                "session_id": f"rt_oai_{uuid4().hex[:16]}",
                "transport": "webrtc",
                "backend": "openai_realtime_api",
                "model": resolved_model,
                "voice": safe_voice,
                "answer_sdp": answer_sdp,
                "session_update": session_update_payload,
            }

        # 2. Zero-extra-key ChatGPT / Codex OAuth WebRTC (`codex app-server` `thread/realtime/start`)
        if codex_lease_path.is_file():
            return self._negotiate_codex_oauth_webrtc(
                offer_sdp=offer_sdp,
                requested_model=target_model,
                voice=safe_voice,
                instructions=instructions,
                session_update_payload=session_update_payload,
                codex_lease_path=codex_lease_path,
            )

        raise RuntimeError(
            "Log eerst in met je ChatGPT-account (Stap 1) of zet een OpenAI API-sleutel in Eva's Kluis voor GPT Realtime 2.1."
        )

    @staticmethod
    def _negotiate_direct_openai_webrtc(
        *,
        offer_sdp: str,
        api_key: str,
        requested_model: str,
        voice: str,
        instructions: str,
    ) -> tuple[str, str]:
        # Try requested model first (e.g. gpt-realtime-2.1), with automatic fallback to gpt-realtime / gpt-4o-realtime-preview
        candidates = [requested_model]
        if requested_model == "gpt-realtime-2.1":
            candidates.extend(["gpt-realtime", "gpt-4o-realtime-preview"])
        elif requested_model == "gpt-realtime-2.1-mini":
            candidates.extend(["gpt-realtime-mini", "gpt-4o-mini-realtime-preview"])

        last_err = "OpenAI Realtime API weigerde de verbinding"
        for cand_model in candidates:
            conn = http.client.HTTPSConnection("api.openai.com", timeout=15.0, context=ssl.create_default_context())
            try:
                path = f"/v1/realtime?model={quote(cand_model)}&voice={quote(voice)}"
                conn.request(
                    "POST",
                    path,
                    body=offer_sdp.encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/sdp",
                        "OpenAI-Beta": "realtime=v1",
                    },
                )
                resp = conn.getresponse()
                raw = resp.read(262_144).decode("utf-8", errors="replace")
                if resp.status in (200, 201) and "v=0" in raw:
                    _ = instructions
                    return raw, cand_model
                last_err = f"OpenAI Realtime ({cand_model}) gaf HTTP {resp.status}"
            except OSError as exc:
                last_err = f"Netwerkfout naar OpenAI Realtime: {exc}"
            finally:
                conn.close()
        raise RuntimeError(last_err)

    def _negotiate_codex_oauth_webrtc(
        self,
        *,
        offer_sdp: str,
        requested_model: str,
        voice: str,
        instructions: str,
        session_update_payload: dict[str, Any],
        codex_lease_path: Path,
    ) -> dict[str, Any]:
        codex_bin = shutil.which("codex") or "/usr/local/bin/codex"
        if not Path(codex_bin).is_file():
            raise RuntimeError("Codex CLI is niet geïnstalleerd voor ChatGPT Live WebRTC")

        lease = json.loads(codex_lease_path.read_text(encoding="utf-8"))
        access_token = str(lease.get("access_token") or "").strip()
        account_id = str(lease.get("account_id") or "").strip()
        if not access_token or not account_id:
            raise RuntimeError("ChatGPT/Codex sessie is verlopen; koppel opnieuw in Stap 1.")

        temp_dir = tempfile.TemporaryDirectory(prefix="eva-rt-webrtc-")
        proc = subprocess.Popen(  # noqa: S603 - pinned local codex binary with isolated CODEX_HOME
            [codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": temp_dir.name,
                "CODEX_HOME": temp_dir.name,
            },
        )

        try:
            assert proc.stdin is not None and proc.stdout is not None
            seq = 0

            def send_obj(obj: dict[str, Any]) -> None:
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                proc.stdin.flush()

            def read_until_id(target_id: int, timeout_s: float = 12.0) -> dict[str, Any]:
                deadline = time.monotonic() + timeout_s
                while time.monotonic() < deadline:
                    assert proc.stdout is not None
                    line = proc.stdout.readline()
                    if not line:
                        raise RuntimeError("Codex realtime proces onverwacht gestopt")
                    msg = json.loads(line)
                    if "id" in msg and "method" in msg:
                        if msg["method"] == "account/chatgptAuthTokens/refresh":
                            fresh = json.loads(codex_lease_path.read_text(encoding="utf-8"))
                            send_obj(
                                {
                                    "id": msg["id"],
                                    "result": {
                                        "accessToken": fresh["access_token"],
                                        "chatgptAccountId": fresh["account_id"],
                                    },
                                }
                            )
                        else:
                            send_obj({"id": msg["id"], "error": {"code": -32601, "message": "Disallowed"}})
                        continue
                    if msg.get("id") == target_id:
                        if "error" in msg:
                            err_msg = (msg["error"] or {}).get("message") or "Realtime fout"
                            raise RuntimeError(str(err_msg))
                        return dict(msg.get("result") or {})
                raise TimeoutError("Codex realtime start duurde te lang")

            seq += 1
            send_obj(
                {
                    "id": seq,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "eva_stage_realtime", "version": "1.0.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            read_until_id(seq, timeout_s=8.0)
            send_obj({"method": "initialized"})

            seq += 1
            send_obj(
                {
                    "id": seq,
                    "method": "account/login/start",
                    "params": {
                        "type": "chatgptAuthTokens",
                        "accessToken": access_token,
                        "chatgptAccountId": account_id,
                    },
                }
            )
            read_until_id(seq, timeout_s=8.0)

            seq += 1
            send_obj(
                {
                    "id": seq,
                    "method": "thread/start",
                    "params": {
                        "ephemeral": True,
                        "cwd": temp_dir.name,
                        "approvalPolicy": "never",
                        "sandbox": "read-only",
                    },
                }
            )
            th_res = read_until_id(seq, timeout_s=10.0)
            thread_id = str((th_res.get("thread") or {}).get("id") or "")
            if not thread_id:
                raise RuntimeError("Kon geen realtime spraaksessie-thread openen")

            # Try requested model (gpt-realtime-2.1 / gpt-realtime-2.1-mini) first, then ChatGPT's gpt-live-1-codex alias
            model_candidates = [requested_model]
            if "gpt-live-1-codex" not in model_candidates:
                model_candidates.append("gpt-live-1-codex")

            answer_sdp = ""
            resolved_model = requested_model
            for cand_model in model_candidates:
                seq += 1
                send_obj(
                    {
                        "id": seq,
                        "method": "thread/realtime/start",
                        "params": {
                            "threadId": thread_id,
                            "outputModality": "audio",
                            "version": "v3",
                            "transport": {"type": "webrtc", "sdp": offer_sdp},
                            "clientManagedHandoffs": True,
                            "model": cand_model,
                            "voice": voice,
                            "includeStartupContext": False,
                            "prompt": instructions,
                        },
                    }
                )
                try:
                    read_until_id(seq, timeout_s=12.0)
                except RuntimeError:
                    continue

                deadline = time.monotonic() + 14.0
                got_error = False
                while time.monotonic() < deadline:
                    line = proc.stdout.readline()
                    if not line:
                        got_error = True
                        break
                    ev = json.loads(line)
                    method = ev.get("method")
                    params = ev.get("params") or {}
                    if method == "account/chatgptAuthTokens/refresh" and "id" in ev:
                        fresh = json.loads(codex_lease_path.read_text(encoding="utf-8"))
                        send_obj(
                            {
                                "id": ev["id"],
                                "result": {
                                    "accessToken": fresh["access_token"],
                                    "chatgptAccountId": fresh["account_id"],
                                },
                            }
                        )
                        continue
                    if params.get("threadId") != thread_id:
                        continue
                    if method in {"thread/realtime/error", "thread/realtime/closed"}:
                        got_error = True
                        break
                    if method == "thread/realtime/sdp" and isinstance(params.get("sdp"), str):
                        answer_sdp = params["sdp"]
                        resolved_model = cand_model
                        break
                if answer_sdp and not got_error:
                    break

            if not answer_sdp:
                raise RuntimeError(
                    "ChatGPT Live WebRTC gaf geen SDP-antwoord. Zet eventueel in Koppelingen & Kluis een directe OpenAI of Gemini Live sleutel."
                )

            session_id = f"rt_cdx_{secrets.token_hex(8)}"
            # Keep background drain thread so stdout pipe never blocks during long conversations
            def _drain_stdout() -> None:
                try:
                    assert proc.stdout is not None
                    for raw_line in proc.stdout:
                        try:
                            ev = json.loads(raw_line)
                            if ev.get("method") == "account/chatgptAuthTokens/refresh" and "id" in ev:
                                fresh = json.loads(codex_lease_path.read_text(encoding="utf-8"))
                                send_obj(
                                    {
                                        "id": ev["id"],
                                        "result": {
                                            "accessToken": fresh["access_token"],
                                            "chatgptAccountId": fresh["account_id"],
                                        },
                                    }
                                )
                        except Exception:
                            pass
                except Exception:
                    pass

            threading.Thread(target=_drain_stdout, daemon=True, name=f"eva-rt-{session_id}").start()

            with self._lock:
                self._cleanup_expired_unlocked()
                for old in list(self._codex_sessions.values()):
                    old.close()
                self._codex_sessions.clear()
                self._codex_sessions[session_id] = _CodexWebrtcProcessSession(
                    session_id=session_id,
                    proc=proc,
                    temp_dir=temp_dir,
                )

            return {
                "ok": True,
                "session_id": session_id,
                "transport": "webrtc",
                "backend": "chatgpt_codex_webrtc",
                "model": resolved_model,
                "requested_model": requested_model,
                "voice": voice,
                "answer_sdp": answer_sdp,
                "session_update": session_update_payload,
            }
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            temp_dir.cleanup()
            raise


async def run_gemini_live_bridge(
    *,
    browser_ws: Any,
    gemini_api_key: str,
    requested_model: str,
    voice: str,
    stage_snapshot: Mapping[str, Any],
    execute_core_turn: Callable[[str], dict[str, Any]],
    set_guest_mode_fn: Callable[[bool], dict[str, Any]],
    execute_vision_look: Callable[[str, str], dict[str, Any]] | None = None,
    execute_fast_tool: Callable[[str, Mapping[str, Any]], dict[str, Any]] | None = None,
) -> None:
    """Full-duplex WebSocket bridge between browser 16kHz/24kHz PCM and Google Gemini 3.8 Live (`BidiGenerateContent`)."""
    from eva.adapters.connectors.appliance_gemini_live_ws import connect_gemini_live_websocket

    clean_key = (gemini_api_key or "").strip()
    if not clean_key:
        await browser_ws.send_json(
            {
                "type": "error",
                "need_key": "gemini_live",
                "detail": (
                    "Plak eenmalig je Google Gemini API-sleutel in Eva's AES-256 Kluis om "
                    "Gemini 3.8 Live (native spraak) te activeren."
                ),
            }
        )
        return

    safe_voice = voice if voice in {"Aoede", "Kore", "Leda", "Puck", "Charon", "Fenrir"} else "Aoede"
    instructions = build_realtime_eva_instructions(stage_snapshot)
    tools = build_gemini_live_tools()

    model_candidates = [
        f"models/{requested_model}" if not requested_model.startswith("models/") else requested_model,
        "models/gemini-2.5-flash-native-audio-preview-12-2025",
        "models/gemini-2.0-flash-live-001",
    ]
    seen_models: list[str] = []
    for m in model_candidates:
        if m not in seen_models:
            seen_models.append(m)

    uri = (
        "wss://generativelanguage.googleapis.com/ws/"
        f"google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={quote(clean_key)}"
    )

    upstream = None
    active_model = seen_models[0]
    for cand_model in seen_models:
        try:
            ws = await connect_gemini_live_websocket(uri, open_timeout=10.0)
            setup_msg = {
                "setup": {
                    "model": cand_model,
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": safe_voice}
                            }
                        },
                    },
                    "systemInstruction": {
                        "parts": [{"text": instructions}]
                    },
                    "tools": tools,
                }
            }
            await ws.send(json.dumps(setup_msg, ensure_ascii=False))
            first_raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
            first_msg = json.loads(first_raw.decode("utf-8") if isinstance(first_raw, bytes) else first_raw)
            if "setupComplete" in first_msg:
                upstream = ws
                active_model = cand_model
                break
            await ws.close()
        except Exception:
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    pass
            upstream = None

    if upstream is None:
        await browser_ws.send_json(
            {
                "type": "error",
                "detail": "Kon geen Gemini Live sessie openen. Controleer je Gemini API-sleutel in Eva's Kluis.",
            }
        )
        return

    await browser_ws.send_json(
        {
            "type": "ready",
            "model": active_model.replace("models/", ""),
            "voice": safe_voice,
            "sample_rate_in": 16000,
            "sample_rate_out": 24000,
        }
    )

    stop_event = asyncio.Event()

    async def _browser_to_gemini() -> None:
        try:
            while not stop_event.is_set():
                msg = await browser_ws.receive_json()
                mtype = msg.get("type")
                if mtype == "audio" and isinstance(msg.get("pcm16_b64"), str):
                    await upstream.send(
                        json.dumps(
                            {
                                "realtimeInput": {
                                    "mediaChunks": [
                                        {
                                            "mimeType": "audio/pcm;rate=16000",
                                            "data": msg["pcm16_b64"],
                                        }
                                    ]
                                }
                            }
                        )
                    )
                elif mtype == "text" and isinstance(msg.get("text"), str) and msg["text"].strip():
                    await upstream.send(
                        json.dumps(
                            {
                                "clientContent": {
                                    "turns": [
                                        {
                                            "role": "user",
                                            "parts": [{"text": msg["text"].strip()[:2000]}],
                                        }
                                    ],
                                    "turnComplete": True,
                                }
                            },
                            ensure_ascii=False,
                        )
                    )
                elif mtype == "stop":
                    stop_event.set()
                    return
        except Exception:
            stop_event.set()

    async def _gemini_to_browser() -> None:
        try:
            async for raw in upstream:
                if stop_event.is_set():
                    return
                ev = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                sc = ev.get("serverContent")
                if isinstance(sc, dict):
                    if sc.get("interrupted"):
                        await browser_ws.send_json({"type": "interrupted"})
                    model_turn = sc.get("modelTurn")
                    if isinstance(model_turn, dict):
                        for part in model_turn.get("parts") or []:
                            if not isinstance(part, dict):
                                continue
                            inline = part.get("inlineData")
                            if isinstance(inline, dict) and inline.get("data"):
                                await browser_ws.send_json(
                                    {
                                        "type": "audio",
                                        "pcm24_b64": inline["data"],
                                    }
                                )
                            if isinstance(part.get("text"), str) and part["text"].strip():
                                await browser_ws.send_json(
                                    {"type": "transcript", "role": "eva", "text": part["text"]}
                                )
                    if sc.get("turnComplete"):
                        await browser_ws.send_json({"type": "turn_complete"})

                tool_call = ev.get("toolCall")
                if isinstance(tool_call, dict):
                    fn_responses = []
                    for fc in tool_call.get("functionCalls") or []:
                        if not isinstance(fc, dict):
                            continue
                        fn_id = str(fc.get("id") or "")
                        fn_name = str(fc.get("name") or "")
                        args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                        await browser_ws.send_json({"type": "tool_start", "name": fn_name})
                        if fn_name == "open_scherm_paneel":
                            panel = str(args.get("paneel") or "integrations")
                            await browser_ws.send_json({"type": "conjure_panel", "panel": panel})
                            result_obj = {"ok": True, "geopend_paneel": panel}
                        elif fn_name == "zet_visite_modus":
                            gm_res = await asyncio.to_thread(set_guest_mode_fn, bool(args.get("actief", True)))
                            await browser_ws.send_json({"type": "stage_update", "stage": gm_res.get("stage")})
                            result_obj = {"ok": True, "visite_modus": bool(args.get("actief", True))}
                        elif fn_name in {"snel_huis_lezen_of_bedienen", "gebruik_eigen_vaardigheid_of_kluis"} and execute_fast_tool is not None:
                            result_obj = await asyncio.to_thread(execute_fast_tool, fn_name, args)
                        elif fn_name == "bekijk_camera_beeld" and execute_vision_look is not None:
                            vis_out = await asyncio.to_thread(
                                execute_vision_look,
                                str(args.get("camera_hint") or ""),
                                str(args.get("vraag") or "Beschrijf wat je op het beeld ziet."),
                            )
                            result_obj = {
                                "camera": str(vis_out.get("camera_label") or "camera"),
                                "waarneming_nl": str(vis_out.get("summary_nl") or ""),
                            }
                        else:
                            verzoek = str(args.get("verzoek") or "").strip() or "Kijk even in huis"
                            turn_out = await asyncio.to_thread(execute_core_turn, verzoek)
                            await browser_ws.send_json(
                                {
                                    "type": "stage_update",
                                    "stage": turn_out.get("stage"),
                                    "applied_changes": turn_out.get("applied_changes") or [],
                                    "conjure_panel": turn_out.get("conjure_panel"),
                                    "conjure_preset": turn_out.get("conjure_preset"),
                                }
                            )
                            result_obj = {"antwoord_uit_eva_kern": str(turn_out.get("reply_text") or "")}
                        fn_responses.append(
                            {"id": fn_id, "name": fn_name, "response": {"result": result_obj}}
                        )
                    if fn_responses:
                        await upstream.send(
                            json.dumps({"toolResponse": {"functionResponses": fn_responses}}, ensure_ascii=False)
                        )
        except Exception:
            stop_event.set()

    try:
        t1 = asyncio.create_task(_browser_to_gemini())
        t2 = asyncio.create_task(_gemini_to_browser())
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        stop_event.set()
        for t in (t1, t2):
            t.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
    finally:
        try:
            await upstream.close()
        except Exception:
            pass


__all__ = [
    "REALTIME_MODEL_CATALOG",
    "RealtimeVoiceHub",
    "VALID_REALTIME_MODELS",
    "build_gemini_live_tools",
    "build_openai_realtime_tools",
    "build_realtime_eva_instructions",
    "clean_spoken_dutch",
    "pick_dutch_filler_phrase",
    "run_gemini_live_bridge",
    "synthesize_neural_dutch_mp3",
]
