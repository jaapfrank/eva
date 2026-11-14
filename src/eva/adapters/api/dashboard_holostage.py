"""Fase 4 — Eva HoloStage Full-Screen 3D Stage, LiveKit Continuous Voice & Conjure-On-Demand Cards.

Exposes:
- GET  /internal/v1/dashboard/holostage                  -> Full-screen 3D Eva (`cockpit`, `unifi_screen`, `tv_cast`, `pocket`)
- POST /internal/v1/dashboard/holostage/session          -> Exchange `#stg_<token>` pairing fragment for `HttpOnly; SameSite=Strict` cookie
- GET  /internal/v1/dashboard/holostage/manifest.json    -> `Eva Pocket` mobile PWA manifest
- GET  /internal/v1/dashboard/holostage/assets/{name}    -> Stage-authenticated `eva.glb` & Three.js ES modules
- GET  /internal/v1/dashboard/holostage/state            -> Privacy-redacted `EvaHoloStageState/v1` JSON
- POST /internal/v1/dashboard/holostage/speech-audio     -> Neural Dutch TTS audio for ambient Cast TVs & text replies
- POST /internal/v1/dashboard/holostage/action           -> Conjure/dismiss cards, shopping list, biometrics & Cast V2 (`DashCast 84912283`)
- POST /internal/v1/dashboard/holostage/turn-stream      -> Sub-300ms clause-chunked NDJSON stream + live stage card updates
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import SimpleQueue
from threading import Thread
import time
from typing import Any, Iterator, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_conversation import grounded_reply_wire, record_conversation_failure
from eva.adapters.api.dashboard_voice import DashboardVoice
from eva.adapters.api.installer_request_json import read_small_json_request
from eva.application.biometric_presence import MAX_PCM_BYTES
from eva.application.cast_v2_protocol import (
    DASHCAST_WEB_RECEIVER_APP_ID,
    DEFAULT_MEDIA_RECEIVER_APP_ID,
    query_or_launch_approved_cast_device,
)
from eva.application.conversation import (
    ConversationTurn,
    GroundedReply,
    silent_completion_scope,
    stream_conversation_text,
)
from eva.application.holostage import (
    STAGE_TOKEN_PATTERN,
    VALID_STAGE_CARD_KINDS,
    HoloStageManager,
    SentenceBoundaryChunker,
)
from eva.contracts.holostage import StageFormFactor

_CORTEX_STATIC_DIR = Path(__file__).resolve().parent / "cortex_static"
_HOLOSTAGE_ASSETS: dict[str, str] = {
    "eva.glb": "model/gltf-binary",
    "three.core.min.js": "text/javascript; charset=utf-8",
    "three.module.min.js": "text/javascript; charset=utf-8",
    "BufferGeometryUtils.js": "text/javascript; charset=utf-8",
    "GLTFLoader.js": "text/javascript; charset=utf-8",
}

_COOKIE_NAME = "eva_holostage_session"
_COOKIE_PATH = "/internal/v1/dashboard/holostage"
_VALID_MODES: tuple[StageFormFactor, ...] = ("cockpit", "unifi_screen", "tv_cast", "pocket")


@dataclass(frozen=True, slots=True)
class _StageViewer:
    kind: Literal["owner", "private_stage", "ambient_stage"]
    label: str
    form_factor: StageFormFactor
    used_cookie: bool
    grant_id: str | None = None
    room_id: str = "woonkamer"
    allow_owner_voice: bool = False


def _resolve_holostage_manager(runtime: Any) -> HoloStageManager:
    existing = getattr(runtime, "_holostage_manager", None)
    if isinstance(existing, HoloStageManager):
        return existing
    ctrl = getattr(runtime, "_model_provider_control", None) or getattr(runtime, "_optional_model_providers", None)
    settings = getattr(ctrl, "settings", None)
    directory = getattr(settings, "directory", None) or Path(
        os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers")
    )
    mgr = HoloStageManager(directory)
    try:
        setattr(runtime, "_holostage_manager", mgr)
    except Exception:
        pass
    return mgr


def _has_explicit_owner_authority(access: DashboardAccess, runtime: Any) -> bool:
    return bool(
        access.token is not None
        or getattr(runtime, "_dashboard_owner_principal_id", None)
        or getattr(runtime, "_optional_model_providers", None)
        or getattr(runtime, "_model_provider_control", None)
    )


def _parse_mode(raw: str | None, default: StageFormFactor = "cockpit") -> StageFormFactor:
    if raw in _VALID_MODES:
        return raw  # type: ignore[return-value]
    return default


def _authenticate_stage_viewer(
    request: Request,
    *,
    access: DashboardAccess,
    runtime: Any,
    mgr: HoloStageManager,
    requested_mode: StageFormFactor = "cockpit",
) -> _StageViewer:
    # 1. Explicit scoped Stage Device Token header (`X-HoloStage-Token`)
    header_tok = request.headers.get("x-holostage-token")
    if header_tok:
        grant = mgr.authenticate_device_token(header_tok.strip())
        if grant is None:
            raise HTTPException(status_code=401, detail="Ongeldige of verlopen HoloStage-schermkoppeling")
        kind: Literal["private_stage", "ambient_stage"] = (
            "private_stage" if grant["allow_private_cards_when_alone"] else "ambient_stage"
        )
        return _StageViewer(
            kind=kind,
            label=str(grant["label"]),
            form_factor=_parse_mode(grant.get("form_factor"), requested_mode),
            used_cookie=False,
            grant_id=str(grant["grant_id"]),
            room_id=str(grant.get("room_id") or "woonkamer"),
            allow_owner_voice=bool(grant.get("allow_owner_voice", False)),
        )

    # 2. Scoped `HttpOnly; SameSite=Strict` pairing cookie
    cookie_tok = request.cookies.get(_COOKIE_NAME)
    if cookie_tok:
        grant = mgr.authenticate_device_token(cookie_tok.strip())
        if grant is not None:
            kind = "private_stage" if grant["allow_private_cards_when_alone"] else "ambient_stage"
            return _StageViewer(
                kind=kind,
                label=str(grant["label"]),
                form_factor=_parse_mode(grant.get("form_factor"), requested_mode),
                used_cookie=True,
                grant_id=str(grant["grant_id"]),
                room_id=str(grant.get("room_id") or "woonkamer"),
                allow_owner_voice=bool(grant.get("allow_owner_voice", False)),
            )

    # 3. Master Owner `DashboardAccess` (strictly local loopback cockpit only — never via LAN stage proxy)
    req_host = (request.headers.get("host") or "").strip()
    if req_host.startswith("["):
        req_hostname = req_host.split("]", 1)[0].lower() + "]"
    else:
        req_hostname = req_host.split(":", 1)[0].lower()
    if request.headers.get("x-holostage-lan-proxy") == "1" or req_hostname not in {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
        "[::1]",
        "testserver",
    }:
        raise HTTPException(status_code=401, detail="HoloStage-schermkoppeling vereist op het thuisnetwerk")

    if access.token is not None:
        access.require(request)
        return _StageViewer(
            kind="owner",
            label="Gezinshoofd Cockpit",
            form_factor=requested_mode,
            used_cookie=False,
        )
    if _has_explicit_owner_authority(access, runtime):
        return _StageViewer(
            kind="owner",
            label="Gezinshoofd Cockpit",
            form_factor=requested_mode,
            used_cookie=False,
        )
    raise HTTPException(status_code=401, detail="HoloStage-authenticatie vereist")


def _enforce_post_csrf_and_framing(request: Request, *, used_cookie: bool) -> None:
    _ = used_cookie
    if request.query_params:
        raise HTTPException(status_code=422, detail="Queryparameters niet toegestaan op POST-acties")
    ct = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ct != "application/json":
        raise HTTPException(status_code=415, detail="JSON-verzoek vereist")
    if request.headers.get("x-holostage-client") != "1":
        raise HTTPException(status_code=403, detail="Ontbrekende X-HoloStage-Client beveiligingsheader")
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlsplit(origin).netloc.lower()
        req_host = (request.headers.get("host") or "").lower()
        if not origin_host or (req_host and origin_host != req_host):
            raise HTTPException(status_code=403, detail="Cross-origin verzoek geweigerd")


_CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' wss: ws: blob: data:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: data:; "
    "worker-src 'self' blob:; "
    "base-uri 'none'; frame-ancestors 'self'"
)

_PAIRING_BOOTSTRAP_HTML = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eva HoloStage — Schermkoppeling</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#060b16;color:#f8fafc;font:16px/1.5 system-ui,sans-serif}.box{max-width:420px;padding:28px;border:1px solid rgba(56,189,248,.3);border-radius:18px;background:#0f192d;text-align:center}h1{margin:0 0 8px;font-size:1.35rem}p{color:#94a3b8;margin:0}</style>
</head><body><div class="box"><h1>Eva HoloStage koppelen…</h1><p id="msg">Bezig met veilige schermauthenticatie.</p></div>
<script>
(async()=>{
  const msg=document.getElementById('msg');
  const raw=(location.hash||'').replace(/^#/,'').trim();
  if(raw){history.replaceState(null,'',location.pathname+location.search);}
  if(!/^stg_[A-Za-z0-9_-]{40,48}$/.test(raw)){
    msg.textContent='Open deze pagina via http://localhost:44711/internal/v1/dashboard/holostage of gebruik een geldige #stg_ koppelingslink.';
    return;
  }
  try{
    const res=await fetch('/internal/v1/dashboard/holostage/session',{
      method:'POST',
      headers:{'content-type':'application/json','x-holostage-client':'1'},
      body:JSON.stringify({token:raw})
    });
    if(!res.ok)throw new Error('rejected');
    location.reload();
  }catch(_e){
    msg.textContent='Schermkoppeling is ongeldig of verlopen. Maak een nieuwe link aan via Beheer op je pc.';
  }
})();
</script></body></html>
"""

HOLOSTAGE_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <link rel="manifest" href="/internal/v1/dashboard/holostage/manifest.json" />
  <meta name="theme-color" content="#060b16" />
  <title>Eva — 3D Huiskamer &amp; Doorlopend Gesprek</title>
  <script type="importmap">
    {
      "imports": {
        "three": "/internal/v1/dashboard/holostage/assets/three.module.min.js"
      }
    }
  </script>
  <script src="/internal/v1/dashboard/holostage/voice/livekit-client.js" defer></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #060b16;
      --glass: rgba(12, 20, 38, 0.76);
      --card: rgba(15, 25, 46, 0.84);
      --border: rgba(148, 163, 184, 0.18);
      --cyan: #38bdf8;
      --emerald: #10b981;
      --amber: #f59e0b;
      --rose: #fb7185;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0; width: 100vw; height: 100vh; overflow: hidden;
      background:
        radial-gradient(circle at 50% 42%, rgba(56, 189, 248, 0.16), transparent 56%),
        radial-gradient(circle at 50% 88%, rgba(16, 185, 129, 0.09), transparent 48%),
        var(--bg);
      color: var(--text);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    #eva-canvas {
      position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 1;
    }

    /* Top bar — subtle floating glass, zero visual clutter */
    .top-hud {
      position: fixed; top: 18px; left: 22px; right: 22px; z-index: 10;
      display: flex; justify-content: space-between; align-items: flex-start;
      gap: 12px; pointer-events: none;
    }
    .top-left, .top-right {
      display: flex; flex-wrap: wrap; gap: 8px; align-items: center; pointer-events: auto;
    }
    .pill {
      display: inline-flex; align-items: center; gap: 7px;
      padding: 6px 13px; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
      background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    }
    .pill.live-on {
      border-color: rgba(16, 185, 129, 0.65);
      background: rgba(6, 78, 59, 0.58);
      color: #d1fae5;
      box-shadow: 0 0 22px rgba(16, 185, 129, 0.22);
    }
    .pill.shield-on {
      border-color: var(--amber); color: #fde68a; background: rgba(120, 53, 15, 0.62);
    }
    button {
      border: 1px solid var(--border); border-radius: 12px;
      background: var(--glass); color: var(--text);
      padding: 8px 14px; font-size: 0.84rem; font-weight: 600; cursor: pointer;
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      transition: transform 0.14s ease, border-color 0.18s ease, background 0.18s ease;
    }
    button:hover:not(:disabled) { border-color: rgba(56, 189, 248, 0.6); }
    button:disabled { opacity: 0.48; cursor: default; }
    button.btn-call-start {
      background: linear-gradient(135deg, rgba(16, 185, 129, 0.92), rgba(5, 150, 105, 0.92));
      border-color: rgba(110, 231, 183, 0.55);
      color: #031c14; font-weight: 700; font-size: 0.95rem;
      padding: 11px 22px; border-radius: 999px;
      box-shadow: 0 10px 28px rgba(16, 185, 129, 0.28);
    }
    button.btn-call-end {
      background: rgba(159, 18, 57, 0.82);
      border-color: rgba(251, 113, 133, 0.65);
      color: #ffe4e6; font-weight: 700; font-size: 0.92rem;
      padding: 11px 20px; border-radius: 999px;
    }
    button.btn-sm { padding: 5px 10px; font-size: 0.76rem; border-radius: 9px; }

    /* Conjure-on-demand floating hologram cards (hidden completely when empty!) */
    .conjured-overlay {
      position: fixed; top: 74px; right: 24px; bottom: 116px;
      width: min(430px, calc(100vw - 48px));
      z-index: 9; display: flex; flex-direction: column; gap: 12px;
      overflow-y: auto; pointer-events: auto;
      padding-right: 4px;
    }
    .conjured-overlay[hidden] { display: none !important; }
    .holo-card {
      background: var(--card);
      border: 1px solid rgba(56, 189, 248, 0.34);
      border-radius: 18px; padding: 16px 18px;
      box-shadow: 0 18px 44px rgba(2, 6, 23, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.08);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      animation: holoMaterialize 0.26s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes holoMaterialize {
      from { opacity: 0; transform: translateY(12px) scale(0.97); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }
    .holo-card-head {
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      margin-bottom: 10px;
    }
    .holo-card-head h2 { margin: 0; font-size: 1rem; letter-spacing: -0.01em; }
    .shop-list { list-style: none; padding: 0; margin: 0 0 10px 0; }
    .shop-item {
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      padding: 7px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.12);
      font-size: 0.9rem;
    }
    .shop-item.checked span.lbl { text-decoration: line-through; color: var(--muted); }
    .inline-form { display: flex; gap: 7px; margin-top: 8px; }
    input, select {
      background: rgba(8, 14, 28, 0.82); color: var(--text);
      border: 1px solid var(--border); border-radius: 10px;
      padding: 8px 11px; font-size: 0.86rem; width: 100%;
    }
    .muted { color: var(--muted); font-size: 0.82rem; }

    /* Bottom floating conversation & voice dock */
    .bottom-dock {
      position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%);
      width: min(760px, calc(100vw - 32px)); z-index: 10;
      display: flex; flex-direction: column; gap: 10px; align-items: center;
    }
    .transcript-strip {
      width: 100%; display: flex; flex-direction: column; gap: 6px;
      max-height: 148px; overflow-y: auto;
      padding: 10px 14px; border-radius: 16px;
      background: rgba(8, 14, 28, 0.72); border: 1px solid var(--border);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      font-size: 0.92rem; line-height: 1.45;
    }
    .transcript-strip[hidden] { display: none !important; }
    .turn-line { white-space: pre-wrap; }
    .turn-line.user { color: #bae6fd; }
    .turn-line.eva { color: #f8fafc; font-weight: 500; }
    .dock-controls {
      width: 100%; display: flex; align-items: center; gap: 10px;
      padding: 9px 12px; border-radius: 999px;
      background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      box-shadow: 0 16px 38px rgba(2, 6, 23, 0.5);
    }
    .dock-controls form {
      flex: 1; display: flex; gap: 8px; align-items: center; margin: 0;
    }
    .dock-controls input {
      border-radius: 999px; background: rgba(6, 11, 22, 0.65);
      border-color: rgba(148, 163, 184, 0.14); padding: 9px 15px;
    }

    /* Ambient TV Cast mode: 100% pure 3D Eva + clean cinema subtitle + conjured cards only */
    body[data-mode="tv_cast"] .top-right,
    body[data-mode="tv_cast"] .dock-controls,
    body[data-mode="tv_cast"] .admin-drawer {
      display: none !important;
    }
    body[data-mode="tv_cast"] .transcript-strip {
      font-size: 1.25rem; text-align: center; max-height: none;
      background: rgba(6, 11, 22, 0.78);
    }

    /* Discreet Owner Admin Drawer (closed by default) */
    .admin-drawer {
      position: fixed; top: 68px; left: 22px; bottom: 116px;
      width: min(390px, calc(100vw - 44px)); z-index: 12;
      background: rgba(10, 17, 33, 0.94);
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 18px; padding: 16px 18px;
      overflow-y: auto; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      box-shadow: 0 22px 50px rgba(0, 0, 0, 0.65);
    }
    .admin-drawer[hidden] { display: none !important; }
    .drawer-sec {
      padding: 12px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.14);
    }
    .drawer-sec:last-child { border-bottom: 0; }
    .drawer-sec h3 { margin: 0 0 8px 0; font-size: 0.9rem; color: #e2e8f0; }
    .chip-wrap { display: flex; flex-wrap: wrap; gap: 6px; }
    #remote-audio { display: none; }
  </style>
</head>
<body data-mode="cockpit">
  <canvas id="eva-canvas" aria-label="Eva 3D Huiskamer"></canvas>
  <div id="remote-audio"></div>

  <header class="top-hud">
    <div class="top-left">
      <span class="pill" id="voice-pill">🎙️ Klik op Start gesprek om te praten</span>
      <span class="pill" id="shield-pill" hidden>🛡️ Privéschild actief</span>
    </div>
    <div class="top-right">
      <button type="button" id="btn-clear-cards" class="btn-sm" hidden>✕ Kaarten verbergen</button>
      <button type="button" id="btn-toggle-admin" class="btn-sm" hidden>⚙️ Beheer &amp; TV</button>
    </div>
  </header>

  <!-- Conjure-on-demand floating hologram cards (hidden when conjured_cards == []) -->
  <aside class="conjured-overlay" id="conjured-overlay" hidden></aside>

  <!-- Discreet Owner Drawer for TV Cast, Card Toggles, Pairing & Voiceprints -->
  <aside class="admin-drawer" id="admin-drawer" hidden>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <strong style="font-size:0.96rem">⚙️ Scherm, Chromecast &amp; Gezin</strong>
      <button type="button" id="btn-close-admin" class="btn-sm">Sluit</button>
    </div>

    <div class="drawer-sec">
      <h3>✨ Kaart tevoorschijn toveren</h3>
      <p class="muted" style="margin:0 0 8px">Je kunt dit ook gewoon tegen Eva zeggen tijdens het gesprek.</p>
      <div class="chip-wrap">
        <button type="button" class="btn-sm" data-conjure="shopping_list">🛒 Boodschappen</button>
        <button type="button" class="btn-sm" data-conjure="household_today">🏠 Vandaag &amp; Koken</button>
        <button type="button" class="btn-sm" data-conjure="home_overview">📡 Schermen in huis</button>
        <button type="button" class="btn-sm" data-conjure="visitors_review">🚪 Bezoekers</button>
      </div>
    </div>

    <div class="drawer-sec">
      <h3>📺 Zet 3D Eva op Chromecast / Google TV</h3>
      <div id="cast-list" class="muted">Laden…</div>
    </div>

    <div class="drawer-sec">
      <h3>📱 Koppel telefoon of wandpaneel</h3>
      <div style="display:grid;gap:7px">
        <input id="pair-label" placeholder="Naam (bijv. Jaap Telefoon of Keukenpaneel)" maxlength="80" />
        <select id="pair-mode">
          <option value="pocket_owner">Mijn telefoon — Jaap (Eva Pocket + live spraak)</option>
          <option value="pocket">Gezinstelefoon — Lola (Eva Pocket kaarten)</option>
          <option value="unifi_screen">Wandpaneel / Kiosk</option>
          <option value="tv_cast">Gedeelde TV (alleen gezamenlijke kaarten)</option>
        </select>
        <button type="button" id="btn-issue-token" class="btn-sm">🔗 Maak eenmalige koppelingslink</button>
        <div id="issued-token-box" class="muted" style="word-break:break-all"></div>
      </div>
    </div>

    <div class="drawer-sec">
      <h3>🎙️ Lokale stemherkenning &amp; Privéschild</h3>
      <div id="voiceprint-list" class="muted" style="margin-bottom:8px"></div>
      <div class="chip-wrap">
        <button type="button" class="btn-sm" data-enroll="owner-jaap">Stem Jaap (3s)</button>
        <button type="button" class="btn-sm" data-enroll="family-lola">Stem Lola (3s)</button>
        <button type="button" class="btn-sm" id="btn-shield-toggle">🛡️ Wissel privéschild</button>
      </div>
    </div>
  </aside>

  <!-- Bottom Floating Voice & Chat Dock -->
  <section class="bottom-dock">
    <div class="transcript-strip" id="transcript-strip" hidden aria-live="polite"></div>
    <div class="dock-controls">
      <button type="button" id="btn-voice-start" class="btn-call-start">🎙️ Start gesprek</button>
      <button type="button" id="btn-voice-end" class="btn-call-end" hidden>⏹️ Beëindig gesprek</button>
      <form id="chat-form">
        <input id="chat-input" maxlength="4000" autocomplete="off" placeholder="Of typ iets tegen Eva (bijv. 'Laat de boodschappenlijst zien')…" />
        <button type="submit" id="btn-chat-send">Stuur</button>
      </form>
    </div>
  </section>

  <script type="module">
    import * as THREE from 'three';
    import { GLTFLoader } from '/internal/v1/dashboard/holostage/assets/GLTFLoader.js';

    const params = new URLSearchParams(location.search);
    let currentMode = ['cockpit', 'unifi_screen', 'tv_cast', 'pocket'].includes(params.get('mode'))
      ? params.get('mode')
      : (window.innerWidth < 740 ? 'pocket' : 'cockpit');
    document.body.dataset.mode = currentMode;

    const $ = id => document.getElementById(id);
    const hdrs = { 'content-type': 'application/json', 'x-holostage-client': '1' };

    let stageState = null;
    let evaSpeaking = false;
    let voiceEnergy = 0.0;
    let cameraTargetX = 0.0;
    let lastSpokenUtteranceId = null;

    // WebAudio Analyser wired directly to Eva's LiveKit WebRTC remote audio stream
    let audioCtx = null;
    let activeAnalyser = null;
    let freqBins = null;

    function attachStreamAnalyser(mediaStreamTrack) {
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx || !mediaStreamTrack) return;
        if (!audioCtx) audioCtx = new Ctx();
        if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
        const stream = new MediaStream([mediaStreamTrack]);
        const src = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.72;
        src.connect(analyser);
        activeAnalyser = analyser;
        freqBins = new Uint8Array(analyser.frequencyBinCount);
      } catch (_e) {}
    }

    function attachElementAnalyser(audioEl) {
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx || !audioEl) return;
        if (!audioCtx) audioCtx = new Ctx();
        if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
        const src = audioCtx.createMediaElementSource(audioEl);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.72;
        src.connect(analyser);
        analyser.connect(audioCtx.destination);
        activeAnalyser = analyser;
        freqBins = new Uint8Array(analyser.frequencyBinCount);
      } catch (_e) {}
    }

    // --- 1. Full-Screen Three.js 3D Eva Stage ---
    function init3DStage() {
      const canvas = $('eva-canvas');
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
      } catch (_err) {
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, window.innerWidth / window.innerHeight, 0.1, 50);
      camera.position.set(0, 1.38, 2.75);

      scene.add(new THREE.HemisphereLight(0xbae6fd, 0x091326, 1.45));
      const keyLight = new THREE.DirectionalLight(0xffffff, 1.85);
      keyLight.position.set(1.6, 2.8, 2.6);
      scene.add(keyLight);
      const rimLight = new THREE.DirectionalLight(0x38bdf8, 1.15);
      rimLight.position.set(-1.9, 2.0, -1.8);
      scene.add(rimLight);

      const haloGeo = new THREE.RingGeometry(0.46, 0.56, 72);
      const haloMat = new THREE.MeshBasicMaterial({
        color: 0x38bdf8, side: THREE.DoubleSide, transparent: true, opacity: 0.42
      });
      const halo = new THREE.Mesh(haloGeo, haloMat);
      halo.rotation.x = -Math.PI / 2;
      halo.position.y = 0.04;
      scene.add(halo);

      let avatarRoot = null;
      let mixer = null;
      const loader = new GLTFLoader();
      loader.load('/internal/v1/dashboard/holostage/assets/eva.glb', gltf => {
        avatarRoot = gltf.scene;
        const box = new THREE.Box3().setFromObject(avatarRoot);
        const size = box.getSize(new THREE.Vector3());
        const scale = 1.68 / Math.max(size.y, 0.01);
        avatarRoot.scale.setScalar(scale);
        const centered = new THREE.Box3().setFromObject(avatarRoot);
        avatarRoot.position.y = -centered.min.y + 0.04;
        scene.add(avatarRoot);
        if (gltf.animations && gltf.animations.length > 0) {
          mixer = new THREE.AnimationMixer(avatarRoot);
          mixer.clipAction(gltf.animations[0]).play();
        }
      });

      const resize = () => {
        const w = window.innerWidth;
        const h = window.innerHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / Math.max(h, 1);
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', resize);
      resize();

      let lookX = 0.0;
      const clock = new THREE.Clock();
      function animate() {
        requestAnimationFrame(animate);
        const dt = Math.min(clock.getDelta(), 0.1);
        const t = clock.elapsedTime;
        if (mixer) mixer.update(dt);

        if (activeAnalyser && freqBins) {
          activeAnalyser.getByteFrequencyData(freqBins);
          let sum = 0;
          for (let i = 2; i < 36; i++) sum += freqBins[i];
          const instant = Math.min(1.0, (sum / (34 * 155)));
          voiceEnergy = voiceEnergy * 0.72 + instant * 0.28;
          evaSpeaking = voiceEnergy > 0.06 || liveVoiceState === 'speaking';
        } else {
          voiceEnergy *= 0.86;
        }

        // Smooth camera pan when cards are conjured so Eva stands left of the hologram card
        lookX += (cameraTargetX - lookX) * Math.min(1, dt * 5.0);
        camera.position.x = lookX * 0.55;
        camera.lookAt(lookX, 1.18, 0);

        if (avatarRoot) {
          avatarRoot.rotation.y = Math.sin(t * 0.65) * 0.06 + (evaSpeaking ? Math.sin(t * 9.5) * 0.025 * (0.4 + voiceEnergy) : 0);
          avatarRoot.position.y = 0.04 + Math.sin(t * 1.45) * 0.008 + (voiceEnergy * 0.018);
        }
        const pulse = 1.0 + (voiceEnergy * 0.34) + Math.sin(t * 2.4) * 0.025;
        halo.scale.setScalar(pulse);
        haloMat.color.setHex(
          stageState?.privacy_shield?.active ? 0xf59e0b : (evaSpeaking || liveSession ? 0x10b981 : 0x38bdf8)
        );
        haloMat.opacity = liveSession ? (0.48 + voiceEnergy * 0.38) : 0.36;
        renderer.render(scene, camera);
      }
      animate();
    }

    // --- 2. Transcript Strip Helper ---
    const transcriptMap = new Map();
    function upsertTranscript(id, speaker, text) {
      const clean = (text || '').trim();
      if (!clean) return;
      const strip = $('transcript-strip');
      strip.hidden = false;
      let el = transcriptMap.get(id);
      if (!el) {
        el = document.createElement('div');
        el.className = 'turn-line ' + (speaker === 'user' ? 'user' : 'eva');
        strip.appendChild(el);
        transcriptMap.set(id, el);
        while (strip.children.length > 4) {
          const first = strip.firstElementChild;
          for (const [k, v] of transcriptMap.entries()) {
            if (v === first) { transcriptMap.delete(k); break; }
          }
          first.remove();
        }
      }
      el.textContent = (speaker === 'user' ? 'Jij: ' : 'Eva: ') + clean;
      strip.scrollTop = strip.scrollHeight;
    }

    // --- 3. Neural Audio Helper for TV Cast / Text Mode ---
    let currentStageAudio = null;
    async function playNeuralSpeech(text) {
      const clean = (text || '').trim();
      if (!clean) return;
      try {
        const res = await fetch('/internal/v1/dashboard/holostage/speech-audio', {
          method: 'POST', headers: hdrs, body: JSON.stringify({ text: clean })
        });
        if (!res.ok) return;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        if (currentStageAudio) {
          try { currentStageAudio.pause(); } catch (_e) {}
        }
        const audio = new Audio(url);
        currentStageAudio = audio;
        attachElementAnalyser(audio);
        evaSpeaking = true;
        audio.onended = () => { evaSpeaking = false; URL.revokeObjectURL(url); };
        audio.onerror = () => { evaSpeaking = false; URL.revokeObjectURL(url); };
        await audio.play();
      } catch (_e) {
        evaSpeaking = false;
      }
    }

    // --- 4. Real Continuous Full-Duplex LiveKit + Codex Voice Session ---
    const VOICE_BASE = '/internal/v1/dashboard/holostage/voice';
    let liveSession = null;
    let liveVoiceState = 'idle';
    const seenFinalSegments = new Set();

    function setVoicePill(text, isLive = false) {
      const pill = $('voice-pill');
      pill.textContent = text;
      pill.classList.toggle('live-on', isLive);
    }

    function syncVoiceButtons() {
      const active = !!liveSession;
      $('btn-voice-start').hidden = active;
      $('btn-voice-end').hidden = !active;
    }

    async function endLiveVoice(reasonText = '🎙️ Gesprek gepauzeerd — klik op Start gesprek om verder te praten') {
      const attempt = liveSession;
      liveSession = null;
      liveVoiceState = 'idle';
      evaSpeaking = false;
      activeAnalyser = null;
      syncVoiceButtons();
      setVoicePill(reasonText, false);
      $('remote-audio').replaceChildren();
      if (!attempt) return;
      if (attempt.connection?.room && !attempt.closedServer) {
        attempt.closedServer = true;
        fetch(`${VOICE_BASE}/session/end/${encodeURIComponent(attempt.connection.room)}`, {
          method: 'POST', keepalive: true
        }).catch(() => {});
      }
      for (const tr of attempt.stream?.getTracks() || []) {
        try { tr.stop(); } catch (_e) {}
      }
      if (attempt.room) {
        try { await attempt.room.disconnect(); } catch (_e) {}
      }
    }

    async function startLiveVoice() {
      if (liveSession) return;
      if (!window.LivekitClient) {
        setVoicePill('⚠️ Spraakbibliotheek laadt nog of deze schermkoppeling heeft geen microfoontoegang', false);
        return;
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setVoicePill('⚠️ Microfoon vereist een beveiligde HTTPS-link (Tailscale Funnel) of localhost', false);
        return;
      }

      const attempt = { closedServer: false };
      liveSession = attempt;
      syncVoiceButtons();
      setVoicePill('⏳ Live spraakverbinding openen (~8 sec)…', true);

      try {
        // Request mic upfront while user gesture is active
        attempt.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: false
        });
        if (liveSession !== attempt) {
          for (const tr of attempt.stream.getTracks()) tr.stop();
          return;
        }

        const res = await fetch(`${VOICE_BASE}/session`, { method: 'POST' });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || 'Kon Eva spraaksessie niet openen.');
        }
        const connection = attempt.connection = await res.json();
        if (liveSession !== attempt) {
          await endLiveVoice();
          return;
        }

        const room = attempt.room = new window.LivekitClient.Room({ adaptiveStream: true, dynacast: true });

        room.on(window.LivekitClient.RoomEvent.TrackSubscribed, track => {
          if (liveSession !== attempt || track.kind !== 'audio') return;
          const el = track.attach();
          $('remote-audio').append(el);
          if (track.mediaStreamTrack) attachStreamAnalyser(track.mediaStreamTrack);
        });

        room.on(window.LivekitClient.RoomEvent.TranscriptionReceived, (segments, participant) => {
          if (liveSession !== attempt) return;
          const isEva = participant?.identity === 'eva';
          for (const seg of segments) {
            upsertTranscript(seg.id, isEva ? 'eva' : 'user', seg.text);
            if (!isEva && seg.final && seg.text && !seenFinalSegments.has(seg.id)) {
              seenFinalSegments.add(seg.id);
              postAction({ action: 'spoken_side_effects', text: seg.text }, true).catch(() => {});
            }
          }
        });

        room.on(window.LivekitClient.RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
          if (liveSession !== attempt || topic !== 'eva.work' || participant?.identity !== 'eva') return;
          try {
            const ev = JSON.parse(new TextDecoder().decode(payload));
            if (ev.type === 'voice_state') {
              liveVoiceState = ev.state || 'listening';
              const labels = {
                listening: '🟢 Eva luistert — praat gewoon door',
                thinking: '💭 Eva denkt even na…',
                speaking: '🗣️ Eva spreekt — je kunt haar gewoon onderbreken'
              };
              setVoicePill(labels[ev.state] || '🟢 Gesprek actief', true);
              refreshState();
            } else if (ev.type === 'work_finished' || ev.type === 'stt_final') {
              refreshState();
            }
          } catch (_e) {}
        });

        room.on(window.LivekitClient.RoomEvent.ParticipantDisconnected, p => {
          if (liveSession === attempt && p?.identity === 'eva') {
            endLiveVoice('De stemverbinding met Eva is gesloten.');
          }
        });
        room.on(window.LivekitClient.RoomEvent.Disconnected, () => {
          if (liveSession === attempt) endLiveVoice();
        });

        await room.connect(connection.url, connection.token, { rtcConfig: connection.rtc_config || {} });
        if (liveSession !== attempt) return;
        await room.startAudio();
        if (liveSession !== attempt) return;
        await room.localParticipant.publishTrack(attempt.stream.getAudioTracks()[0], {
          source: window.LivekitClient.Track.Source.Microphone
        });
        if (liveSession !== attempt) return;

        liveVoiceState = 'listening';
        setVoicePill('🟢 Eva luistert — praat gerust, microfoon blijft open', true);
      } catch (err) {
        if (liveSession === attempt) {
          await endLiveVoice('⚠️ ' + (err?.message || 'Spraakverbinding mislukt'));
        }
      }
    }

    $('btn-voice-start').onclick = () => startLiveVoice();
    $('btn-voice-end').onclick = () => endLiveVoice();
    window.addEventListener('offline', () => endLiveVoice('⚠️ Netwerkverbinding verbroken.'));
    window.addEventListener('pagehide', () => { endLiveVoice(); });

    // --- 5. Conjure-On-Demand Hologram Card Renderer ---
    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    async function postAction(body, silent = false) {
      const res = await fetch('/internal/v1/dashboard/holostage/action', {
        method: 'POST', headers: hdrs, body: JSON.stringify(body)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (!silent) setVoicePill('⚠️ ' + (data.detail || 'Actie geweigerd'), !!liveSession);
        return null;
      }
      if (data.stage) renderState(data.stage);
      return data;
    }

    function renderConjuredCards(st) {
      const overlay = $('conjured-overlay');
      const conjured = Array.isArray(st.cards?.conjured_cards) ? st.cards.conjured_cards : [];
      $('btn-clear-cards').hidden = conjured.length === 0;
      cameraTargetX = (conjured.length > 0 && window.innerWidth >= 860) ? 0.42 : 0.0;

      if (conjured.length === 0) {
        overlay.hidden = true;
        overlay.replaceChildren();
        return;
      }

      overlay.hidden = false;
      const cardsHtml = [];

      for (const kind of conjured) {
        if (kind === 'shopping_list') {
          const items = st.cards.shopping_list || [];
          const rows = items.length === 0
            ? '<li class="muted">De boodschappenlijst is nog leeg.</li>'
            : items.map(it => `
                <li class="shop-item ${it.checked ? 'checked' : ''}">
                  <span class="lbl">${esc(it.label)}${it.quantity ? ' (' + esc(it.quantity) + ')' : ''}</span>
                  <span style="display:flex;gap:5px">
                    <button type="button" class="btn-sm" data-shop-toggle="${esc(it.item_id)}">${it.checked ? '↩' : '✓'}</button>
                    <button type="button" class="btn-sm" data-shop-rm="${esc(it.item_id)}">✕</button>
                  </span>
                </li>`).join('');
          cardsHtml.push(`
            <article class="holo-card">
              <div class="holo-card-head">
                <h2>🛒 Boodschappenlijst</h2>
                <button type="button" class="btn-sm" data-dismiss-card="shopping_list">Sluit ✕</button>
              </div>
              <ul class="shop-list">${rows}</ul>
              <form class="inline-form" data-shop-form="1">
                <input name="label" placeholder="Voeg product toe…" maxlength="120" required />
                <button type="submit" class="btn-sm">+</button>
              </form>
            </article>`);
        }

        if (kind === 'household_today' && st.cards.household_today) {
          const hh = st.cards.household_today;
          cardsHtml.push(`
            <article class="holo-card">
              <div class="holo-card-head">
                <h2>🏠 Vandaag in huis</h2>
                <button type="button" class="btn-sm" data-dismiss-card="household_today">Sluit ✕</button>
              </div>
              <p style="margin:0 0 6px">${esc(hh.headline_nl || hh.cooking_notice_nl || '')}</p>
              <p class="muted" style="margin:0">Gezinsleden: Jaap, Lola</p>
            </article>`);
        }

        if (kind === 'custom_spotlight') {
          for (const sp of (st.cards.spotlights || [])) {
            const listHtml = (sp.items || []).length
              ? '<ul style="margin:8px 0 0 18px;padding:0">' + sp.items.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>'
              : '';
            cardsHtml.push(`
              <article class="holo-card">
                <div class="holo-card-head">
                  <h2>✨ ${esc(sp.title)}</h2>
                  <button type="button" class="btn-sm" data-dismiss-spot="${esc(sp.card_id)}">Sluit ✕</button>
                </div>
                <div style="font-size:0.9rem;line-height:1.45">${esc(sp.body_nl)}${listHtml}</div>
              </article>`);
          }
        }

        if (kind === 'home_overview' && st.cards.home_overview) {
          const casts = st.cards.home_overview.cast_targets || [];
          cardsHtml.push(`
            <article class="holo-card">
              <div class="holo-card-head">
                <h2>📡 Schermen &amp; Kamers</h2>
                <button type="button" class="btn-sm" data-dismiss-card="home_overview">Sluit ✕</button>
              </div>
              <div class="muted">${casts.length === 0 ? 'Geen Cast-schermen gevonden.' : casts.map(c =>
                `<div style="padding:5px 0;display:flex;justify-content:space-between;align-items:center">
                  <span>📺 ${esc(c.label)} (${esc(c.room_id)})</span>
                  ${st.viewer.kind === 'owner' && c.consent_status === 'approved'
                    ? `<button type="button" class="btn-sm" data-cast-go="${esc(c.device_id)}">Cast 3D Eva</button>`
                    : `<span class="muted">${esc(c.consent_status)}</span>`}
                </div>`).join('')}
              </div>
            </article>`);
        }

        if (kind === 'visitors_review') {
          const vis = st.cards.visitors_review || [];
          cardsHtml.push(`
            <article class="holo-card">
              <div class="holo-card-head">
                <h2>🚪 Recente bezoekers</h2>
                <button type="button" class="btn-sm" data-dismiss-card="visitors_review">Sluit ✕</button>
              </div>
              <div class="muted">${vis.length === 0 ? 'Geen onbekende bezoekers aan de deur.' : vis.map(v =>
                `<div style="padding:5px 0">${esc(v.room_id)} · ${esc(v.dwell_seconds)}s (${esc(v.classification)})</div>`).join('')}
              </div>
            </article>`);
        }
      }

      if (cardsHtml.length === 0) {
        overlay.hidden = true;
        overlay.replaceChildren();
        $('btn-clear-cards').hidden = true;
        cameraTargetX = 0.0;
        return;
      }
      overlay.innerHTML = cardsHtml.join('');

      overlay.querySelectorAll('[data-dismiss-card]').forEach(btn => {
        btn.onclick = () => postAction({ action: 'dismiss_cards', card_kind: btn.dataset.dismissCard });
      });
      overlay.querySelectorAll('[data-dismiss-spot]').forEach(btn => {
        btn.onclick = () => postAction({ action: 'dismiss_spotlight', card_id: btn.dataset.dismissSpot });
      });
      overlay.querySelectorAll('[data-shop-toggle]').forEach(btn => {
        btn.onclick = () => postAction({ action: 'shopping_toggle', target: btn.dataset.shopToggle });
      });
      overlay.querySelectorAll('[data-shop-rm]').forEach(btn => {
        btn.onclick = () => postAction({ action: 'shopping_remove', target: btn.dataset.shopRm });
      });
      overlay.querySelectorAll('[data-cast-go]').forEach(btn => {
        btn.onclick = () => postAction({ action: 'cast_launch', device_id: btn.dataset.castGo });
      });
      const shopForm = overlay.querySelector('[data-shop-form]');
      if (shopForm) {
        shopForm.onsubmit = ev => {
          ev.preventDefault();
          const inp = shopForm.querySelector('input[name="label"]');
          const val = inp.value.trim();
          if (!val) return;
          inp.value = '';
          postAction({ action: 'shopping_add', label: val });
        };
      }
    }

    function renderAdminDrawer(st) {
      const isOwner = st.viewer?.kind === 'owner';
      $('btn-toggle-admin').hidden = !isOwner || currentMode === 'tv_cast';
      if (!isOwner) return;

      const casts = st.cards?.home_overview?.cast_targets || [];
      const castList = $('cast-list');
      if (casts.length === 0) {
        castList.textContent = 'Geen Chromecast/Google TV gevonden op het netwerk.';
      } else {
        castList.innerHTML = casts.map(c => `
          <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid rgba(148,163,184,0.1)">
            <span><strong>${esc(c.label)}</strong> <span class="muted">(${esc(c.room_id)})</span></span>
            ${c.consent_status === 'approved'
              ? `<button type="button" class="btn-sm" data-admin-cast="${esc(c.device_id)}">📺 Zet Eva op TV</button>`
              : `<span class="muted">${esc(c.consent_status)}</span>`}
          </div>`).join('');
        castList.querySelectorAll('[data-admin-cast]').forEach(btn => {
          btn.onclick = async () => {
            btn.disabled = true;
            const res = await postAction({ action: 'cast_launch', device_id: btn.dataset.adminCast });
            btn.disabled = false;
            if (res?.message) setVoicePill(res.message, !!liveSession);
          };
        });
      }

      const vps = st.biometrics_admin?.enrolled_members || st.speaker_biometrics?.enrolled_members || [];
      $('voiceprint-list').textContent = vps.length
        ? 'Ingestelde stemmen: ' + vps.map(v => `${v.display_name} (${v.sample_count || v.samples || 1}x)`).join(', ')
        : 'Nog geen lokale stemafdruk opgeslagen.';
    }

    function renderState(st) {
      stageState = st;
      const sh = st.privacy_shield || {};
      $('shield-pill').hidden = !sh.active;

      renderConjuredCards(st);
      renderAdminDrawer(st);

      // Ambient TV Cast mode: display and speak shared live utterances out of TV speakers
      const utt = st.live_utterance;
      if (currentMode === 'tv_cast' && utt && utt.utterance_id && utt.utterance_id !== lastSpokenUtteranceId) {
        lastSpokenUtteranceId = utt.utterance_id;
        if (utt.speaker_text) upsertTranscript(utt.utterance_id + ':u', 'user', utt.speaker_text);
        if (utt.eva_text) {
          upsertTranscript(utt.utterance_id + ':e', 'eva', utt.eva_text);
          playNeuralSpeech(utt.eva_text);
        }
      }
    }

    async function refreshState() {
      try {
        const res = await fetch(`/internal/v1/dashboard/holostage/state?mode=${encodeURIComponent(currentMode)}`);
        if (!res.ok) return;
        renderState(await res.json());
      } catch (_e) {}
    }

    // --- 6. Text Input: Routes Through LiveKit Voice Room If Active, Else Streaming Core + Neural Audio ---
    $('chat-form').onsubmit = async ev => {
      ev.preventDefault();
      const inp = $('chat-input');
      const text = inp.value.trim();
      if (!text) return;
      inp.value = '';

      if (liveSession?.room) {
        upsertTranscript('txt-' + Date.now(), 'user', text);
        postAction({ action: 'spoken_side_effects', text }, true).catch(() => {});
        try {
          await liveSession.room.localParticipant.sendText(text, { topic: 'lk.chat' });
        } catch (_e) {
          setVoicePill('⚠️ Kon bericht niet meesturen in actief gesprek', true);
        }
        return;
      }

      const turnId = 'turn-' + Date.now();
      upsertTranscript(turnId + ':u', 'user', text);
      try {
        const res = await fetch('/internal/v1/dashboard/holostage/turn-stream', {
          method: 'POST', headers: hdrs, body: JSON.stringify({ text, mode: currentMode })
        });
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let acc = '';
        let finalReply = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split('\\n');
          buf = lines.pop() || '';
          for (const ln of lines) {
            if (!ln.trim()) continue;
            const msg = JSON.parse(ln);
            if (msg.type === 'stage_delta') {
              acc += msg.text;
              upsertTranscript(turnId + ':e', 'eva', acc);
            } else if (msg.type === 'stage_state' && msg.stage) {
              renderState(msg.stage);
            } else if (msg.type === 'final') {
              finalReply = msg.text || acc;
              upsertTranscript(turnId + ':e', 'eva', finalReply);
              if (msg.stage) renderState(msg.stage);
            }
          }
        }
        if (finalReply) playNeuralSpeech(finalReply);
      } catch (_e) {}
    };

    // --- 7. Admin Drawer Controls & Voiceprint Enrollment ---
    $('btn-clear-cards').onclick = () => postAction({ action: 'dismiss_cards', card_kind: 'all' });
    $('btn-toggle-admin').onclick = () => { $('admin-drawer').hidden = !$('admin-drawer').hidden; };
    $('btn-close-admin').onclick = () => { $('admin-drawer').hidden = true; };
    document.querySelectorAll('[data-conjure]').forEach(btn => {
      btn.onclick = () => postAction({ action: 'conjure_card', card_kind: btn.dataset.conjure });
    });
    $('btn-shield-toggle').onclick = () => {
      const nextLocked = !(stageState?.privacy_shield?.active);
      postAction({ action: 'set_privacy_shield', locked: nextLocked });
    };
    $('btn-issue-token').onclick = async () => {
      const label = $('pair-label').value.trim() || 'Scherm in huis';
      const rawChoice = $('pair-mode').value;
      const isOwnerPocket = rawChoice === 'pocket_owner';
      const form_factor = isOwnerPocket ? 'pocket' : rawChoice;
      const allowPriv = form_factor !== 'tv_cast';
      const res = await postAction({
        action: 'issue_device_token',
        label,
        form_factor,
        allow_private_cards_when_alone: allowPriv,
        allow_owner_voice: isOwnerPocket
      });
      if (res?.issued) {
        const base = res.recommended_origin || res.public_stage_origin || res.lan_stage_origin || location.origin;
        $('issued-token-box').textContent = `${base}${res.issued.pair_path}`;
      }
    };

    async function capture3sPcm16Base64() {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx({ sampleRate: 16000 });
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const samples = [];
      proc.onaudioprocess = e => {
        const ch = e.inputBuffer.getChannelData(0);
        for (let i = 0; i < ch.length; i++) {
          const s = Math.max(-1, Math.min(1, ch[i]));
          samples.push(s < 0 ? s * 0x8000 : s * 0x7FFF);
        }
      };
      src.connect(proc);
      proc.connect(ctx.destination);
      await new Promise(r => setTimeout(r, 2600));
      proc.disconnect();
      src.disconnect();
      stream.getTracks().forEach(t => t.stop());
      await ctx.close();
      const pcm = new Int16Array(samples.slice(0, 16000 * 3));
      const bytes = new Uint8Array(pcm.buffer);
      let bin = '';
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      return btoa(bin);
    }

    document.querySelectorAll('[data-enroll]').forEach(btn => {
      btn.onclick = async () => {
        btn.disabled = true;
        setVoicePill('🎙️ Spreek nu 3 seconden rustig in…', !!liveSession);
        try {
          const b64 = await capture3sPcm16Base64();
          const res = await postAction({ action: 'enroll_voice_pcm', member_id: btn.dataset.enroll, audio_pcm_b64: b64 });
          if (res?.message) setVoicePill(res.message, !!liveSession);
        } catch (_e) {
          setVoicePill('⚠️ Microfoonopname voor stemprofiel mislukt', !!liveSession);
        } finally {
          btn.disabled = false;
        }
      };
    });

    // Boot!
    init3DStage();
    refreshState();
    setInterval(refreshState, currentMode === 'tv_cast' ? 2500 : 6000);
    if (currentMode !== 'tv_cast') {
      fetch(`${VOICE_BASE}/session/ready`).then(r => {
        if (r.ok && !liveSession) {
          setVoicePill('🎙️ Klaar voor echt gesprek — klik op Start gesprek', false);
        }
      }).catch(() => {});
    }
  </script>
</body>
</html>
"""


def install_dashboard_holostage(
    app: FastAPI,
    *,
    access: DashboardAccess,
    runtime: Any,
    voice_config: DashboardVoice | None = None,
) -> None:
    _ = voice_config

    @app.get("/internal/v1/dashboard/holostage")
    def holostage_page(request: Request) -> HTMLResponse:
        mgr = _resolve_holostage_manager(runtime)
        req_mode = _parse_mode(request.query_params.get("mode"))
        try:
            _authenticate_stage_viewer(
                request, access=access, runtime=runtime, mgr=mgr, requested_mode=req_mode
            )
            html_body = HOLOSTAGE_HTML
        except HTTPException:
            html_body = _PAIRING_BOOTSTRAP_HTML
        return HTMLResponse(
            html_body,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": _CSP_HEADER,
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/internal/v1/dashboard/holostage/session")
    async def establish_stage_device_session(request: Request) -> JSONResponse:
        _enforce_post_csrf_and_framing(request, used_cookie=False)
        payload = await read_small_json_request(request, 2048, "holostage_session")
        raw_token = str(payload.get("token") or "").strip() if isinstance(payload, dict) else ""
        if not STAGE_TOKEN_PATTERN.match(raw_token):
            raise HTTPException(status_code=401, detail="Ongeldige schermkoppelings-code")
        mgr = _resolve_holostage_manager(runtime)
        grant = mgr.authenticate_device_token(raw_token)
        if grant is None:
            raise HTTPException(status_code=401, detail="Schermkoppeling is ongeldig of ingetrokken")

        resp = JSONResponse(
            {
                "contract": "EvaHoloStageSession/v1",
                "grant_id": grant["grant_id"],
                "form_factor": grant["form_factor"],
                "label": grant["label"],
            },
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        is_https = (
            (request.url.scheme == "https")
            or (request.headers.get("x-forwarded-proto", "").lower() == "https")
            or (request.headers.get("x-holostage-https") == "1")
        )
        resp.set_cookie(
            key=_COOKIE_NAME,
            value=raw_token,
            max_age=30 * 24 * 3600,
            path=_COOKIE_PATH,
            httponly=True,
            samesite="strict",
            secure=is_https,
        )
        return resp

    @app.get("/internal/v1/dashboard/holostage/manifest.json")
    def holostage_pwa_manifest(request: Request) -> JSONResponse:
        mgr = _resolve_holostage_manager(runtime)
        _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr, requested_mode="pocket")
        return JSONResponse(
            {
                "name": "Eva Pocket — Digitale Huisgenoot",
                "short_name": "Eva Pocket",
                "start_url": "/internal/v1/dashboard/holostage?mode=pocket",
                "display": "standalone",
                "background_color": "#060b16",
                "theme_color": "#060b16",
                "lang": "nl-NL",
            },
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/internal/v1/dashboard/holostage/assets/{asset_name}")
    def holostage_static_asset(request: Request, asset_name: str) -> Response:
        mgr = _resolve_holostage_manager(runtime)
        _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        media_type = _HOLOSTAGE_ASSETS.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="holostage asset not found")
        asset_path = _CORTEX_STATIC_DIR / asset_name
        if not asset_path.is_file():
            raise HTTPException(status_code=404, detail="holostage asset unavailable")
        return FileResponse(
            path=asset_path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/internal/v1/dashboard/holostage/voice-grant-check")
    def holostage_voice_grant_check(request: Request) -> JSONResponse:
        mgr = _resolve_holostage_manager(runtime)
        viewer = _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        if not (viewer.kind == "owner" or viewer.allow_owner_voice):
            raise HTTPException(
                status_code=403,
                detail="Live eigenaarsspraak is alleen toegestaan op het persoonlijke Eva Pocket-apparaat van Jaap",
            )
        shield_active, _ = mgr.biometrics.is_privacy_shield_active()
        if shield_active:
            raise HTTPException(
                status_code=403,
                detail="Privéschild is actief: live eigenaarsspraak is tijdelijk afgeschermd",
            )
        return JSONResponse(
            {"voice_allowed": True, "viewer_kind": viewer.kind, "label": viewer.label},
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/internal/v1/dashboard/holostage/state")
    def holostage_state(request: Request, mode: str = "cockpit") -> JSONResponse:
        mgr = _resolve_holostage_manager(runtime)
        req_mode = _parse_mode(mode)
        viewer = _authenticate_stage_viewer(
            request, access=access, runtime=runtime, mgr=mgr, requested_mode=req_mode
        )
        return JSONResponse(
            mgr.build_stage_state(
                viewer_kind=viewer.kind,
                viewer_label=viewer.label,
                form_factor=req_mode,
            ),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/holostage/speech-audio")
    async def holostage_speech_audio(request: Request) -> Response:
        """Synthesize neural Dutch speech (`FennaNeural`/`ColetteNeural`/`Piper`) for TV Cast & typed stage replies."""
        mgr = _resolve_holostage_manager(runtime)
        viewer = _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        _enforce_post_csrf_and_framing(request, used_cookie=viewer.used_cookie)
        payload = await read_small_json_request(request, 8192, "holostage_speech_audio")
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if not 1 <= len(text) <= 1500:
            raise HTTPException(status_code=422, detail="Ongeldige spreektekst")
        speech_bridge = getattr(runtime, "_holostage_speech", None)
        if speech_bridge is None or not getattr(speech_bridge, "available", False):
            raise HTTPException(status_code=503, detail="Neurale spraaksynthese niet geconfigureerd")
        result = await asyncio.to_thread(speech_bridge.synthesize_speech, text)
        if result is None:
            raise HTTPException(status_code=502, detail="Spraaksynthese tijdelijk niet beschikbaar")
        audio_bytes, mime_type = result
        return Response(
            content=audio_bytes,
            media_type=mime_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/holostage/action")
    async def holostage_action(request: Request) -> JSONResponse:
        mgr = _resolve_holostage_manager(runtime)
        viewer = _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        _enforce_post_csrf_and_framing(request, used_cookie=viewer.used_cookie)
        payload = await read_small_json_request(request, 524_288, "holostage_action")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Ongeldig verzoek")

        action = str(payload.get("action") or "").strip()
        owner_only_actions = {
            "issue_device_token",
            "revoke_device_grant",
            "enroll_voice_pcm",
            "enroll_voice_embedding",
            "delete_voiceprint",
            "classify_visitor",
            "set_privacy_shield",
            "set_lan_stage_origin",
            "set_public_stage_origin",
            "cast_query",
            "cast_launch",
        }
        if action in owner_only_actions and (
            viewer.kind != "owner" or not _has_explicit_owner_authority(access, runtime)
        ):
            raise HTTPException(
                status_code=403,
                detail="Alleen het gezinshoofd kan biometrie, schermkoppelingen en Cast-acties beheren",
            )

        def _execute_sync() -> tuple[str, dict[str, Any], dict[str, Any]]:
            def _done(msg: str, extra: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
                stage_state = mgr.build_stage_state(
                    viewer_kind=viewer.kind,
                    viewer_label=viewer.label,
                    form_factor=viewer.form_factor,
                )
                return msg, (extra or {}), stage_state

            allow_priv_viewer = (
                viewer.kind in ("owner", "private_stage")
                and not mgr.biometrics.is_privacy_shield_active()[0]
            )

            if action == "conjure_card":
                ck = str(payload.get("card_kind") or "").strip()
                if ck not in VALID_STAGE_CARD_KINDS:
                    raise ValueError("Onbekend kaarttype")
                mgr.conjure_card(ck, allow_private=allow_priv_viewer)  # type: ignore[arg-type]
                return _done("✨ Kaart op het scherm getoverd.")

            if action == "dismiss_cards":
                ck = str(payload.get("card_kind") or "all").strip()
                mgr.dismiss_conjured_card(ck, allow_private=allow_priv_viewer)
                return _done("✓ Kaart verborgen.")

            if action == "spoken_side_effects":
                spoken_txt = str(payload.get("text") or "").strip()
                t_fx = time.monotonic()
                effects = mgr.apply_spoken_stage_side_effects(
                    spoken_txt,
                    speaker_name=viewer.label,
                    allow_private=allow_priv_viewer,
                )
                if effects:
                    obs_ledger = getattr(runtime, "_household_observation", None)
                    if obs_ledger is not None:
                        try:
                            obs_ledger.record_reflex_hit(latency_ms=(time.monotonic() - t_fx) * 1000.0)
                        except Exception:
                            pass
                return _done("✓ Spraakactie verwerkt.", {"effects": effects})

            if action == "set_lan_stage_origin":
                saved_origin = mgr.set_lan_stage_origin(str(payload.get("origin") or ""))
                return _done("✓ LAN HoloStage-adres opgeslagen.", {"lan_stage_origin": saved_origin})

            if action == "set_public_stage_origin":
                saved_pub = mgr.set_public_stage_origin(str(payload.get("origin") or ""))
                return _done("✓ Publiek HTTPS HoloStage-adres opgeslagen.", {"public_stage_origin": saved_pub})

            if action == "shopping_add":
                item = mgr.add_shopping_item(
                    label=str(payload.get("label") or ""),
                    quantity=str(payload.get("quantity") or ""),
                    added_by=viewer.label,
                )
                return _done(f"✓ '{item.label}' op de boodschappenlijst gezet.", {"item": item.model_dump(mode="json")})

            if action in ("shopping_toggle", "shopping_remove", "shopping_clear_checked"):
                op_map: dict[str, Literal["toggle", "remove", "clear_checked"]] = {
                    "shopping_toggle": "toggle",
                    "shopping_remove": "remove",
                    "shopping_clear_checked": "clear_checked",
                }
                mgr.toggle_or_remove_shopping_item(
                    target=str(payload.get("target") or ""),
                    action=op_map[action],
                )
                return _done("✓ Boodschappenlijst bijgewerkt.")

            if action == "summon_spotlight":
                priv: Literal["shared", "private_household"] = (
                    "private_household"
                    if (viewer.kind == "owner" and payload.get("privacy_level") == "private_household")
                    else "shared"
                )
                ttl_raw = payload.get("ttl_minutes", 60)
                ttl_val = ttl_raw if isinstance(ttl_raw, int) and not isinstance(ttl_raw, bool) else 60
                items_raw = payload.get("items")
                items_list = [str(x) for x in items_raw[:16]] if isinstance(items_raw, list) else []
                card = mgr.summon_spotlight_card(
                    title=str(payload.get("title") or ""),
                    body_nl=str(payload.get("body_nl") or ""),
                    items=items_list,
                    privacy_level=priv,
                    ttl_minutes=ttl_val,
                )
                return _done(f"✨ Kaart '{card.title}' op het podium getoverd.", {"card": card.model_dump(mode="json")})

            if action == "dismiss_spotlight":
                mgr.dismiss_spotlight_card(str(payload.get("card_id") or ""), allow_private=allow_priv_viewer)
                return _done("✓ Podiumkaart gesloten.")

            if action == "observe_camera_frame":
                if viewer.kind == "ambient_stage":
                    raise PermissionError("Gedeelde tv-/cast-schermen mogen geen cameratracklets insturen")
                raw_faces = payload.get("faces")
                if not isinstance(raw_faces, list):
                    raise ValueError("faces moet een lijst zijn")
                if viewer.kind == "private_stage":
                    eff_source_id = f"stage.{viewer.grant_id or viewer.form_factor}"
                    eff_room_id = viewer.room_id
                    sanitized_faces: list[dict[str, Any]] = []
                    for rf in raw_faces[:8]:
                        if not isinstance(rf, dict):
                            continue
                        clean_rf = dict(rf)
                        clean_rf["status"] = "unknown"
                        clean_rf["person_label"] = None
                        clean_rf["similarity_ppm"] = 0
                        clean_rf["margin_ppm"] = 0
                        sanitized_faces.append(clean_rf)
                    faces_in: list[Any] = sanitized_faces
                else:
                    eff_source_id = str(payload.get("source_id") or f"stage.{viewer.form_factor}")
                    eff_room_id = str(payload.get("room_id") or "woonkamer")
                    faces_in = raw_faces[:8]

                snap = mgr.biometrics.observe_camera_frame(
                    source_id=eff_source_id,
                    room_id=eff_room_id,
                    faces=faces_in,
                )
                extra_bio = {"biometrics": snap} if viewer.kind == "owner" else {}
                return _done("✓ Cameratracklet verwerkt.", extra_bio)

            if action == "issue_device_token":
                ff = _parse_mode(str(payload.get("form_factor") or "unifi_screen"), "unifi_screen")
                ttl_h = payload.get("ttl_hours", 720)
                issued = mgr.issue_device_token(
                    label=str(payload.get("label") or ""),
                    form_factor=ff,
                    room_id=str(payload.get("room_id") or "woonkamer"),
                    allow_private_cards_when_alone=(payload.get("allow_private_cards_when_alone") is True),
                    allow_owner_voice=(payload.get("allow_owner_voice") is True),
                    ttl_hours=ttl_h if isinstance(ttl_h, int) and not isinstance(ttl_h, bool) else 720,
                )
                lan_orig = mgr.get_lan_stage_origin() or None
                pub_orig = mgr.get_public_stage_origin() or None
                rec_orig = (lan_orig or pub_orig) if ff == "tv_cast" else (pub_orig or lan_orig)
                return _done(
                    "🔗 Eenmalige schermkoppeling aangemaakt.",
                    {
                        "issued": issued,
                        "lan_stage_origin": lan_orig,
                        "public_stage_origin": pub_orig,
                        "recommended_origin": rec_orig,
                    },
                )

            if action == "revoke_device_grant":
                mgr.revoke_device_grant(str(payload.get("grant_id") or ""))
                return _done("✓ Schermkoppeling ingetrokken.")

            if action == "enroll_voice_pcm":
                b64 = str(payload.get("audio_pcm_b64") or "")
                try:
                    pcm_bytes = base64.b64decode(b64, validate=True)
                except Exception as exc:
                    raise ValueError("Ongeldige base64 PCM-audio") from exc
                if len(pcm_bytes) > MAX_PCM_BYTES:
                    raise ValueError("Audiofragment overschrijdt 10s limiet")
                res = mgr.biometrics.speaker.enroll_pcm(
                    member_id=str(payload.get("member_id") or ""),
                    pcm_bytes=pcm_bytes,
                )
                return _done(f"🎙️ Lokale stemafdruk bijgewerkt voor {res['display_name']}.", {"enrollment": res})

            if action == "enroll_voice_embedding":
                emb = payload.get("embedding")
                if not isinstance(emb, list):
                    raise ValueError("embedding moet een lijst van getallen zijn")
                res = mgr.biometrics.speaker.enroll_embedding(
                    member_id=str(payload.get("member_id") or ""),
                    embedding=emb,
                )
                return _done(f"🎙️ Lokale stem-embedding opgeslagen voor {res['display_name']}.", {"enrollment": res})

            if action == "delete_voiceprint":
                deleted = mgr.biometrics.speaker.delete_voiceprint(str(payload.get("member_id") or ""))
                return _done("🗑️ Stemprofiel gewist." if deleted else "Geen stemprofiel aanwezig.")

            if action == "classify_visitor":
                cls_val = str(payload.get("classification") or "bekende_buur")
                if cls_val not in ("unreviewed", "bekende_buur", "bezorger", "genegeerd"):
                    raise ValueError("Ongeldige bezoekersclassificatie")
                mgr.biometrics.classify_visitor(
                    visitor_id=str(payload.get("visitor_id") or ""),
                    classification=cls_val,  # type: ignore[arg-type]
                    note_nl=str(payload.get("note_nl") or ""),
                )
                return _done("✓ Bezoekerskaartje bijgewerkt.")

            if action == "set_privacy_shield":
                mgr.biometrics.set_manual_privacy_shield(bool(payload.get("locked", False)))
                return _done("🛡️ Privéschild bijgewerkt.")

            if action in ("cast_query", "cast_launch"):
                dev_id = str(payload.get("device_id") or "")
                stage_url: str | None = None
                minted_grant_id: str | None = None
                if action == "cast_launch":
                    explicit_url = str(payload.get("stage_url") or "").strip()
                    if explicit_url:
                        stage_url = explicit_url
                    else:
                        lan_origin = mgr.get_lan_stage_origin()
                        if not lan_origin:
                            raise ValueError("Geen LAN HoloStage-adres ingesteld voor Chromecast-weergave")
                        tv_grant = mgr.issue_device_token(
                            label=f"Cast {dev_id}"[:78],
                            form_factor="tv_cast",
                            room_id="woonkamer",
                            allow_private_cards_when_alone=False,
                            ttl_hours=720,
                        )
                        minted_grant_id = str(tv_grant["grant"]["grant_id"])
                        stage_url = f"{lan_origin}{tv_grant['pair_path']}"

                default_app = DASHCAST_WEB_RECEIVER_APP_ID if stage_url else DEFAULT_MEDIA_RECEIVER_APP_ID
                app_id = str(payload.get("app_id") or default_app)
                try:
                    cast_res = query_or_launch_approved_cast_device(
                        mgr.proprioception,
                        device_id=dev_id,
                        stage_url=stage_url,
                        app_id=app_id,
                    )
                except Exception:
                    if minted_grant_id:
                        try:
                            mgr.revoke_device_grant(minted_grant_id)
                        except Exception:
                            pass
                    raise
                return _done(f"📺 3D Eva staat nu op '{cast_res['label']}'.", {"cast": cast_res})

            raise ValueError("Onbekende HoloStage-actie")

        try:
            message_nl, extra_out, stage_payload = await asyncio.to_thread(_execute_sync)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return JSONResponse(
            {"message": message_nl, "stage": stage_payload, **extra_out},
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/holostage/turn-stream")
    async def holostage_turn_stream(request: Request) -> StreamingResponse:
        """Fase 4C — Low-latency (~250ms first clause) multimodal turn stream with strict bystander & ambient isolation."""
        mgr = _resolve_holostage_manager(runtime)
        viewer = _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        _enforce_post_csrf_and_framing(request, used_cookie=viewer.used_cookie)
        payload = await read_small_json_request(request, 524_288, "holostage_turn")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Ongeldig verzoek")

        raw_text = str(payload.get("text") or "").strip()
        if not 1 <= len(raw_text) <= 4096:
            raise HTTPException(status_code=422, detail="Berichttekst moet tussen 1 en 4096 tekens zijn")
        req_mode = _parse_mode(str(payload.get("mode") or viewer.form_factor), viewer.form_factor)
        pcm_b64 = payload.get("audio_pcm_b64")

        def _prepare_turn_sync() -> tuple[dict[str, Any] | None, bool, list[str]]:
            sp_match: dict[str, Any] | None = None
            if viewer.kind in ("owner", "private_stage") and isinstance(pcm_b64, str) and pcm_b64.strip():
                try:
                    pcm_bytes = base64.b64decode(pcm_b64, validate=True)
                    if 6400 <= len(pcm_bytes) <= MAX_PCM_BYTES:
                        sp_match = mgr.biometrics.observe_voice_pcm(pcm_bytes)
                except Exception:
                    sp_match = None

            sh_active, _ = mgr.biometrics.is_privacy_shield_active()
            sp_label = (
                str(sp_match["display_name"])
                if viewer.kind in ("owner", "private_stage")
                and isinstance(sp_match, dict)
                and sp_match.get("status") == "household_voice_candidate"
                else viewer.label
            )
            t_fx = time.monotonic()
            effects = mgr.apply_spoken_stage_side_effects(
                raw_text,
                speaker_name=sp_label,
                allow_private=(viewer.kind in ("owner", "private_stage") and not sh_active),
            )
            fx_ms = (time.monotonic() - t_fx) * 1000.0
            return sp_match, sh_active, effects, fx_ms

        speaker_match, shield_active, side_effects, reflex_ms = await asyncio.to_thread(_prepare_turn_sync)

        can_use_owner_runtime = (
            (viewer.kind == "owner" or viewer.allow_owner_voice)
            and _has_explicit_owner_authority(access, runtime)
            and not shield_active
            and hasattr(runtime, "dashboard_turn")
        )

        obs_ledger = getattr(runtime, "_household_observation", None)
        if obs_ledger is not None:
            def _record_holostage_obs() -> None:
                try:
                    if side_effects:
                        obs_ledger.record_reflex_hit(latency_ms=reflex_ms)
                    if shield_active:
                        return
                    matched_member: str | None = None
                    if (
                        isinstance(speaker_match, dict)
                        and speaker_match.get("status") == "household_voice_candidate"
                        and speaker_match.get("member_id") in ("jaap", "lola")
                    ):
                        matched_member = (
                            "owner-jaap" if speaker_match["member_id"] == "jaap" else "family-lola"
                        )
                    elif viewer.kind == "owner" or viewer.allow_owner_voice:
                        matched_member = "owner-jaap"
                    elif viewer.kind == "private_stage" and req_mode == "pocket":
                        matched_member = "family-lola"
                    if matched_member is not None:
                        obs_ledger.record_turn(
                            principal_id=matched_member,
                            channel="holostage",
                            used_model=can_use_owner_runtime,
                        )
                except Exception:
                    pass

            await asyncio.to_thread(_record_holostage_obs)

        queue: SimpleQueue[dict[str, Any] | None] = SimpleQueue()

        def _produce_owner_stream() -> None:
            t_start = time.monotonic()
            chunker = SentenceBoundaryChunker()
            turn: ConversationTurn | None = None
            stage_name = "respond"
            shield_tripped_midstream = False
            try:
                if speaker_match is not None and not mgr.biometrics.is_privacy_shield_active()[0]:
                    queue.put({"type": "speaker_candidate", "match": speaker_match})
                if side_effects:
                    queue.put(
                        {
                            "type": "stage_state",
                            "effects": side_effects,
                            "stage": mgr.build_stage_state(
                                viewer_kind=viewer.kind,
                                viewer_label=viewer.label,
                                form_factor=req_mode,
                            ),
                        }
                    )

                def _on_delta(delta: str) -> None:
                    nonlocal shield_tripped_midstream
                    if shield_tripped_midstream or mgr.biometrics.is_privacy_shield_active()[0]:
                        shield_tripped_midstream = True
                        return
                    queue.put({"type": "stage_delta", "text": delta})
                    for clause in chunker.feed(delta):
                        queue.put(
                            {
                                "type": "speech_clause",
                                "text": clause,
                                "elapsed_ms": round((time.monotonic() - t_start) * 1000),
                            }
                        )

                with silent_completion_scope(), stream_conversation_text(_on_delta):
                    turn_res, reply_res = runtime.dashboard_turn(raw_text)
                if not isinstance(turn_res, ConversationTurn) or not isinstance(reply_res, GroundedReply):
                    raise ValueError("owner conversation result is invalid")
                turn = turn_res

                if shield_tripped_midstream or mgr.biometrics.is_privacy_shield_active()[0]:
                    chunker.flush()
                    shield_notice = (
                        "Het privéschild is zojuist geactiveerd doordat een omstander in beeld of gehoor kwam. "
                        "Het persoonlijke antwoord is direct afgeschermd."
                    )
                    queue.put(
                        {
                            "type": "final",
                            "text": shield_notice,
                            "shield_interrupted": True,
                            "stage": mgr.build_stage_state(
                                viewer_kind=viewer.kind,
                                viewer_label=viewer.label,
                                form_factor=req_mode,
                            ),
                        }
                    )
                else:
                    mgr.record_stage_utterance(
                        eva_text=reply_res.text,
                        speaker_text=raw_text,
                        privacy_level="private_household",
                    )
                    for rem_clause in chunker.flush():
                        queue.put(
                            {
                                "type": "speech_clause",
                                "text": rem_clause,
                                "elapsed_ms": round((time.monotonic() - t_start) * 1000),
                            }
                        )
                    queue.put(
                        {
                            "type": "final",
                            "text": reply_res.text,
                            "reply": grounded_reply_wire(reply_res),
                            "stage": mgr.build_stage_state(
                                viewer_kind=viewer.kind,
                                viewer_label=viewer.label,
                                form_factor=req_mode,
                            ),
                        }
                    )
                stage_name = "after_response"
                runtime.after_response(turn_res, reply_res)
            except Exception as exc:
                if stage_name == "respond":
                    queue.put({"type": "error", "code": "holostage_turn_failed"})
                if obs_ledger is not None:
                    try:
                        obs_ledger.record_runtime_fault(critical=False)
                    except Exception:
                        pass
                try:
                    record_conversation_failure(exc, turn, stage_name)
                except Exception:
                    pass
            finally:
                queue.put(None)

        def _produce_shared_stage_stream() -> None:
            try:
                st = mgr.build_stage_state(
                    viewer_kind=viewer.kind,
                    viewer_label=viewer.label,
                    form_factor=req_mode,
                )
                if side_effects:
                    spoken = f"Staat erop! Ik heb {', '.join(e.lstrip('+- ') for e in side_effects)} bijgewerkt."
                elif "boodschap" in raw_text.lower():
                    unchecked = [x["label"] for x in st["cards"]["shopping_list"] if not x.get("checked")]
                    spoken = (
                        f"Op de boodschappenlijst staat nu: {', '.join(unchecked)}."
                        if unchecked
                        else "De boodschappenlijst is op dit moment leeg."
                    )
                elif shield_active:
                    spoken = (
                        "Het privéschild staat nu aan omdat er een omstander of onbekende stem aanwezig is. "
                        "Gedeelde kaarten zoals de boodschappenlijst werken gewoon; privézaken blijven afgeschermd."
                    )
                else:
                    spoken = (
                        "Dit scherm staat in gedeelde huiskamermodus. Zeg bijvoorbeeld wat ik op de boodschappenlijst "
                        "mag zetten, of gebruik Start gesprek op je pc voor een volledig gesprek."
                    )
                mgr.record_stage_utterance(eva_text=spoken, speaker_text=raw_text, privacy_level="shared")
                st = mgr.build_stage_state(
                    viewer_kind=viewer.kind,
                    viewer_label=viewer.label,
                    form_factor=req_mode,
                )
                queue.put({"type": "stage_delta", "text": spoken})
                queue.put({"type": "speech_clause", "text": spoken, "elapsed_ms": 4})
                queue.put({"type": "stage_state", "effects": side_effects, "stage": st})
                queue.put({"type": "final", "text": spoken, "stage": st})
            finally:
                queue.put(None)

        target_fn = _produce_owner_stream if can_use_owner_runtime else _produce_shared_stage_stream

        def _ndjson_iter() -> Iterator[bytes]:
            Thread(target=target_fn, name="eva-holostage-stream", daemon=True).start()
            while True:
                item = queue.get()
                if item is None:
                    return
                yield json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"

        return StreamingResponse(
            _ndjson_iter(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )


__all__ = ["HOLOSTAGE_HTML", "install_dashboard_holostage"]
