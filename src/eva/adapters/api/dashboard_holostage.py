"""Fase 4 — Eva HoloStage 3D Multimodal Stage Adapter, Biometric Controls & Sub-300ms Clause Voice Stream.

Exposes:
- GET  /internal/v1/dashboard/holostage                  -> 4-mode 3D Eva HoloStage (`cockpit`, `unifi_screen`, `tv_cast`, `pocket`)
- POST /internal/v1/dashboard/holostage/session          -> Exchange `#stg_<token>` pairing fragment for `HttpOnly; SameSite=Strict` cookie
- GET  /internal/v1/dashboard/holostage/manifest.json    -> `Eva Pocket` mobile PWA manifest
- GET  /internal/v1/dashboard/holostage/assets/{name}    -> Stage-authenticated `eva.glb` & Three.js ES modules
- GET  /internal/v1/dashboard/holostage/state            -> Privacy-redacted `EvaHoloStageState/v1` JSON
- POST /internal/v1/dashboard/holostage/action           -> Shopping list, spotlights, camera tracklets, voice enrollment & Cast V2
- POST /internal/v1/dashboard/holostage/turn-stream      -> Sub-300ms clause-chunked NDJSON speech + live stage card updates
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
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
            )

    # 3. Master Owner `DashboardAccess` (only when Authorization is valid or local dev mode is explicitly owner-wired)
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
    "connect-src 'self' blob: data:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: data:; "
    "worker-src 'self' blob:; "
    "base-uri 'none'; frame-ancestors 'self'"
)

_PAIRING_BOOTSTRAP_HTML = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eva HoloStage — Schermkoppeling</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#070d19;color:#f8fafc;font:16px/1.5 system-ui,sans-serif}.box{max-width:420px;padding:28px;border:1px solid rgba(56,189,248,.3);border-radius:18px;background:#0f192d;text-align:center}h1{margin:0 0 8px;font-size:1.35rem}p{color:#94a3b8;margin:0}</style>
</head><body><div class="box"><h1>Eva HoloStage koppelen…</h1><p id="msg">Bezig met veilige schermauthenticatie.</p></div>
<script>
(async()=>{
  const msg=document.getElementById('msg');
  const raw=(location.hash||'').replace(/^#/,'').trim();
  if(raw){history.replaceState(null,'',location.pathname+location.search);}
  if(!/^stg_[A-Za-z0-9_-]{40,48}$/.test(raw)){
    msg.textContent='Open deze pagina vanuit het Gezinshoofd-dashboard of gebruik een geldige eenmalige #stg_ koppelingslink.';
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
    msg.textContent='Schermkoppeling is ongeldig of verlopen. Maak een nieuwe link aan in het dashboard.';
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
  <meta name="theme-color" content="#070d19" />
  <title>Eva HoloStage — 3D Huiskamer, Pocket & Spraakpodium</title>
  <script type="importmap">
    {
      "imports": {
        "three": "/internal/v1/dashboard/holostage/assets/three.module.min.js"
      }
    }
  </script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #070d19;
      --panel: rgba(15, 24, 42, 0.86);
      --card: rgba(20, 31, 53, 0.92);
      --border: rgba(148, 163, 184, 0.16);
      --cyan: #38bdf8;
      --emerald: #10b981;
      --amber: #f59e0b;
      --rose: #fb7185;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0; min-height: 100vh;
      background: radial-gradient(circle at 30% 45%, rgba(56,189,248,0.16), transparent 55%), var(--bg);
      color: var(--text);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    .stage-layout {
      display: grid;
      grid-template-columns: minmax(310px, 38vw) minmax(0, 1fr);
      min-height: 100vh;
    }
    body[data-mode="pocket"] .stage-layout {
      grid-template-columns: 1fr;
    }
    body[data-mode="tv_cast"] { font-size: 19px; }
    .avatar-column {
      position: relative;
      min-height: 420px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: 20px 24px;
      border-right: 1px solid var(--border);
      overflow: hidden;
    }
    #eva-canvas {
      position: absolute; inset: 0; width: 100%; height: 100%; z-index: 1;
    }
    .stage-overlay-top, .stage-overlay-bottom {
      position: relative; z-index: 3;
    }
    .pill-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 5px 11px; border-radius: 999px; font-size: 0.78rem; font-weight: 650;
      background: rgba(15, 23, 42, 0.82); border: 1px solid var(--border); backdrop-filter: blur(8px);
    }
    .pill.shield-on { border-color: var(--amber); color: #fde68a; background: rgba(120, 53, 15, 0.55); }
    .pill.gaze-on { border-color: var(--emerald); color: #a7f3d0; background: rgba(6, 78, 59, 0.55); }
    .mode-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .mode-btn, button {
      border: 1px solid var(--border); border-radius: 10px;
      background: rgba(30, 41, 59, 0.85); color: var(--text);
      padding: 7px 12px; font-size: 0.83rem; font-weight: 600; cursor: pointer;
    }
    .mode-btn.active, button.primary {
      background: linear-gradient(135deg, #0284c7, #2563eb); border-color: var(--cyan);
    }
    button.emerald { background: #059669; border-color: #34d399; }
    .speech-bubble {
      background: rgba(11, 19, 35, 0.88); border: 1px solid rgba(56, 189, 248, 0.32);
      border-radius: 16px; padding: 14px 16px; backdrop-filter: blur(10px);
      min-height: 68px; font-size: 0.96rem; line-height: 1.45;
    }
    .talk-bar {
      display: grid; grid-template-columns: auto 1fr auto; gap: 8px; margin-top: 10px;
    }
    .talk-bar input {
      border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
      background: rgba(9, 15, 28, 0.92); color: var(--text); font: inherit;
    }
    .cards-column {
      padding: 22px 26px; display: grid; gap: 18px; align-content: start;
      max-height: 100vh; overflow-y: auto;
    }
    body[data-mode="pocket"] .cards-column { max-height: none; }
    .card-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(295px, 1fr)); gap: 16px;
    }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 16px; padding: 16px 18px;
    }
    .card h2 { margin: 0 0 10px; font-size: 1.02rem; display: flex; justify-content: space-between; align-items: center; }
    .shop-list { list-style: none; padding: 0; margin: 0 0 12px; display: grid; gap: 7px; }
    .shop-item {
      display: flex; justify-content: space-between; align-items: center;
      padding: 7px 10px; border-radius: 10px; background: rgba(15, 23, 42, 0.72);
    }
    .shop-item.checked span { text-decoration: line-through; color: var(--muted); }
    .inline-form { display: flex; gap: 7px; flex-wrap: wrap; }
    .inline-form input, .inline-form select {
      flex: 1; min-width: 120px; border: 1px solid var(--border); border-radius: 9px;
      padding: 7px 10px; background: #09101f; color: var(--text); font: inherit;
    }
    .muted { color: var(--muted); font-size: 0.85rem; }
    .bar-track { height: 7px; border-radius: 99px; background: rgba(148,163,184,0.16); overflow: hidden; margin-top: 4px; }
    .bar-fill { height: 100%; background: linear-gradient(90deg, var(--cyan), var(--emerald)); }
    .cam-preview { width: 100%; max-height: 160px; object-fit: cover; border-radius: 10px; display: none; margin-top: 8px; }
    .status-toast { font-size: 0.82rem; color: var(--cyan); min-height: 1.2em; }
  </style>
</head>
<body data-mode="cockpit">
  <div class="stage-layout">
    <section class="avatar-column">
      <canvas id="eva-canvas"></canvas>
      <div class="stage-overlay-top">
        <div class="pill-row">
          <span class="pill" id="eva-name-pill">✨ Eva HoloStage</span>
          <span class="pill" id="candidate-pill">Aanwezigheid: luisterend</span>
          <span class="pill" id="shield-pill" hidden>🛡️ Privéschild actief</span>
          <span class="pill gaze-on" id="gaze-pill" hidden>👁️ Gaze-to-Talk</span>
        </div>
        <div class="mode-bar" role="tablist" aria-label="Schermmodus">
          <button type="button" class="mode-btn" data-set-mode="cockpit">💻 Cockpit</button>
          <button type="button" class="mode-btn" data-set-mode="unifi_screen">🖼️ UniFi Screen</button>
          <button type="button" class="mode-btn" data-set-mode="tv_cast">📺 Google TV / Cast</button>
          <button type="button" class="mode-btn" data-set-mode="pocket">📱 Eva Pocket</button>
          <a href="/internal/v1/dashboard/cortex" style="color:var(--cyan);font-size:.82rem;padding:7px 8px;text-decoration:none">← Cortex</a>
        </div>
      </div>

      <div class="stage-overlay-bottom">
        <div class="speech-bubble" id="speech-bubble">Hoi! Zeg bijv. “Zet melk en avocado’s op de boodschappenlijst” of kies een kaart hiernaast.</div>
        <form class="talk-bar" id="talk-form">
          <button type="button" id="mic-btn" class="emerald" title="Spreek direct met Eva (~250ms eerste zin)">🎙️ Spreek</button>
          <input id="talk-input" maxlength="2000" placeholder="Vraag iets of tover op het scherm…" autocomplete="off" />
          <button type="submit" class="primary">Stuur</button>
        </form>
        <div class="pill-row" style="margin-top:8px">
          <button type="button" id="cam-btn">📷 Meekijk-camera / Gaze aan</button>
          <span class="status-toast" id="toast"></span>
        </div>
        <video id="local-video" class="cam-preview" autoplay playsinline muted></video>
      </div>
    </section>

    <main class="cards-column">
      <div class="card-grid">
        <section class="card" id="card-shopping">
          <h2><span>🛒 Boodschappenlijst</span> <button type="button" id="read-shop-btn">🔊 Lees voor</button></h2>
          <ul class="shop-list" id="shop-list"></ul>
          <form class="inline-form" id="shop-form">
            <input id="shop-input" maxlength="110" placeholder="Bijv. melk, sushi-rijst…" required />
            <input id="shop-qty" maxlength="24" placeholder="Aantal (opt.)" style="max-width:110px" />
            <button type="submit" class="primary">+ Zet erop</button>
            <button type="button" id="shop-clear-btn">Streep afgevinkt weg</button>
          </form>
        </section>

        <section class="card" id="card-household">
          <h2><span>🏡 Gezinsdag — Jaap & Lola</span></h2>
          <div id="household-body" class="muted">Laden…</div>
        </section>

        <section class="card" id="card-spotlights">
          <h2><span>✨ Op het scherm getoverd</span></h2>
          <div id="spotlights-body" class="muted">Nog geen extra podiumkaarten actief.</div>
          <form class="inline-form" id="spotlight-form" style="margin-top:10px">
            <input id="spot-title" maxlength="80" placeholder="Kaarttitel…" required />
            <input id="spot-body" maxlength="300" placeholder="Inhoud op het scherm…" required />
            <button type="submit">+ Tover kaart</button>
          </form>
        </section>

        <section class="card" id="card-home">
          <h2><span>📡 Kamers & Google Cast TV</span></h2>
          <div id="rooms-body"></div>
          <div id="cast-body" style="margin-top:12px"></div>
        </section>

        <section class="card" id="card-visitors" hidden>
          <h2><span>🚪 Geziene bezoekers (rustig overzicht)</span></h2>
          <p class="muted" style="margin-top:0">Geen ‘Wie is dit?’-spam: alleen scherpe, blijvende bezoekers bij de deur.</p>
          <div id="visitors-body"></div>
        </section>

        <section class="card" id="card-owner-admin" hidden>
          <h2><span>🔐 Stemherkenning (Jaap & Lola) & Schermkoppeling</span></h2>
          <div id="voice-enroll-body" class="muted"></div>
          <div class="inline-form" style="margin-top:8px">
            <select id="enroll-member">
              <option value="owner-jaap">Jaap (Gezinshoofd)</option>
              <option value="family-lola">Lola (Gezin)</option>
            </select>
            <button type="button" id="enroll-voice-btn" class="emerald">🎙️ Neem 3s stemprofiel op (lokaal CPU)</button>
            <button type="button" id="shield-toggle-btn">🛡️ Privéschild testen</button>
          </div>
          <hr style="border:0;border-top:1px solid var(--border);margin:14px 0" />
          <form class="inline-form" id="grant-form">
            <input id="grant-label" maxlength="60" placeholder="Naam scherm (bijv. Woonkamer TV)" required />
            <select id="grant-mode">
              <option value="unifi_screen">UniFi Screen</option>
              <option value="tv_cast">Google TV / Cast</option>
              <option value="pocket">Eva Pocket (iPhone/Android)</option>
            </select>
            <button type="submit">🔗 Maak 1-klik koppelingslink</button>
          </form>
          <div id="grant-output" class="muted" style="margin-top:8px;word-break:break-all"></div>
        </section>
      </div>
    </main>
  </div>

  <script type="module">
    import * as THREE from 'three';
    import { GLTFLoader } from '/internal/v1/dashboard/holostage/assets/GLTFLoader.js';

    const params = new URLSearchParams(location.search);
    let currentMode = params.get('mode') || 'cockpit';
    const setMode = (m) => {
      currentMode = m;
      document.body.dataset.mode = m;
      document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.setMode === m));
    };
    setMode(currentMode);
    document.querySelectorAll('.mode-btn').forEach(b => b.addEventListener('click', () => setMode(b.dataset.setMode)));

    const toastEl = document.getElementById('toast');
    const speechBubble = document.getElementById('speech-bubble');
    let speakingEnergy = 0;
    let latestState = null;

    function toast(msg) { toastEl.textContent = msg; }

    async function apiPost(path, body) {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-holostage-client': '1' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Actie mislukt');
      return data;
    }

    // Sub-300ms Clause Speech Synthesizer (Web Speech API + speaking halo pulse)
    function speakClause(text) {
      if (!text || !('speechSynthesis' in window)) return;
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'nl-NL';
      u.rate = 1.03;
      u.onstart = () => { speakingEnergy = 1.0; };
      u.onend = () => { speakingEnergy = 0.0; };
      u.onerror = () => { speakingEnergy = 0.0; };
      window.speechSynthesis.speak(u);
    }

    function renderStage(st) {
      latestState = st;
      document.getElementById('eva-name-pill').textContent = '✨ ' + (st.eva?.display_name || 'Eva') + ' HoloStage';
      const shield = st.privacy_shield || {};
      const shieldPill = document.getElementById('shield-pill');
      shieldPill.hidden = !shield.active;
      shieldPill.classList.toggle('shield-on', !!shield.active);
      shieldPill.textContent = shield.active ? '🛡️ Privéschild actief (' + (shield.reason || 'omstander') + ')' : '';

      const gazePill = document.getElementById('gaze-pill');
      gazePill.hidden = !shield.gaze_to_talk_active;

      const cand = shield.household_candidate;
      document.getElementById('candidate-pill').textContent = cand
        ? ('Herkenning: ' + cand.display_name + ' (kandidaat)')
        : (shield.active ? 'Omstander gedetecteerd — privé verborgen' : 'Aanwezigheid: rustig');

      // Shopping list
      const shopUl = document.getElementById('shop-list');
      shopUl.replaceChildren();
      const items = st.cards?.shopping_list || [];
      if (!items.length) {
        const li = document.createElement('li');
        li.className = 'muted';
        li.textContent = 'De boodschappenlijst is leeg.';
        shopUl.append(li);
      } else {
        for (const item of items) {
          const li = document.createElement('li');
          li.className = 'shop-item' + (item.checked ? ' checked' : '');
          const label = document.createElement('span');
          label.textContent = item.label + (item.quantity ? ' (' + item.quantity + ')' : '');
          const actions = document.createElement('div');
          actions.style.display = 'flex'; actions.style.gap = '6px';
          const toggleBtn = document.createElement('button');
          toggleBtn.type = 'button';
          toggleBtn.textContent = item.checked ? '↩' : '✓';
          toggleBtn.onclick = async () => {
            const r = await apiPost('/internal/v1/dashboard/holostage/action', { action: 'shopping_toggle', target: item.item_id });
            renderStage(r.stage);
          };
          const delBtn = document.createElement('button');
          delBtn.type = 'button';
          delBtn.textContent = '✕';
          delBtn.onclick = async () => {
            const r = await apiPost('/internal/v1/dashboard/holostage/action', { action: 'shopping_remove', target: item.item_id });
            renderStage(r.stage);
          };
          actions.append(toggleBtn, delBtn);
          li.append(label, actions);
          shopUl.append(li);
        }
      }

      // Household Today (server-redacted when shield active or ambient viewer)
      const hhBody = document.getElementById('household-body');
      hhBody.replaceChildren();
      if (!st.cards?.household_today) {
        hhBody.textContent = '🔒 Privé-gezinsagenda is afgeschermd (Privéschild of gedeeld tv-scherm).';
      } else {
        const h = st.cards.household_today;
        const p = document.createElement('p');
        p.style.margin = '0 0 8px'; p.style.color = '#f8fafc'; p.style.fontWeight = '650';
        p.textContent = h.headline_nl;
        hhBody.append(p);
        for (const n of (h.notes || [])) {
          const d = document.createElement('div');
          d.style.padding = '6px 0';
          d.textContent = '• ' + n.title + ': ' + n.detail_nl;
          hhBody.append(d);
        }
      }

      // Spotlights
      const spBody = document.getElementById('spotlights-body');
      spBody.replaceChildren();
      const spots = st.cards?.spotlights || [];
      if (!spots.length) {
        spBody.textContent = 'Geen tijdelijke kaarten actief.';
      } else {
        for (const sp of spots) {
          const wrap = document.createElement('div');
          wrap.style.padding = '8px 10px'; wrap.style.marginBottom = '8px';
          wrap.style.borderRadius = '10px'; wrap.style.background = 'rgba(15,23,42,0.75)';
          const strong = document.createElement('strong');
          strong.textContent = sp.title + ' ';
          const close = document.createElement('button');
          close.type = 'button'; close.textContent = 'Sluit'; close.style.float = 'right';
          close.onclick = async () => {
            const r = await apiPost('/internal/v1/dashboard/holostage/action', { action: 'dismiss_spotlight', card_id: sp.card_id });
            renderStage(r.stage);
          };
          const bodyP = document.createElement('div');
          bodyP.textContent = sp.body_nl;
          wrap.append(close, strong, bodyP);
          spBody.append(wrap);
        }
      }

      // Rooms & Cast targets
      const roomsEl = document.getElementById('rooms-body');
      roomsEl.replaceChildren();
      const rooms = st.cards?.home_overview?.rooms || [];
      if (!rooms.length) {
        roomsEl.innerHTML = '<span class="muted">Ruimtelijk huisveld actief in rust (0 tokens).</span>';
      } else {
        for (const r of rooms) {
          const pct = Math.round((r.occupancy_probability || 0) * 100);
          const row = document.createElement('div');
          row.style.marginBottom = '8px';
          const lbl = document.createElement('div');
          lbl.className = 'muted';
          lbl.textContent = r.room_id + ' · aanwezigheid ' + pct + '%';
          const track = document.createElement('div'); track.className = 'bar-track';
          const fill = document.createElement('div'); fill.className = 'bar-fill'; fill.style.width = pct + '%';
          track.append(fill); row.append(lbl, track); roomsEl.append(row);
        }
      }

      const castEl = document.getElementById('cast-body');
      castEl.replaceChildren();
      for (const c of (st.cards?.home_overview?.cast_targets || [])) {
        const row = document.createElement('div');
        row.className = 'shop-item'; row.style.marginTop = '6px';
        const span = document.createElement('span');
        span.textContent = '📺 ' + c.label + ' (' + c.consent_status + ')';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Cast podium';
        btn.onclick = async () => {
          try {
            const r = await apiPost('/internal/v1/dashboard/holostage/action', {
              action: 'cast_launch',
              device_id: c.device_id,
              stage_url: location.origin + '/internal/v1/dashboard/holostage?mode=tv_cast'
            });
            toast(r.message || 'Cast-verzoek verzonden');
          } catch (err) { toast(err.message); }
        };
        row.append(span, btn);
        castEl.append(row);
      }

      // Visitors Review (only when visible)
      const visCard = document.getElementById('card-visitors');
      const visitors = st.cards?.visitors_review || [];
      visCard.hidden = st.viewer?.kind !== 'owner' && !visitors.length;
      const visBody = document.getElementById('visitors-body');
      visBody.replaceChildren();
      if (!visitors.length) {
        visBody.innerHTML = '<span class="muted">Geen onbekende bezoekers bij de deur geregistreerd.</span>';
      } else {
        for (const v of visitors) {
          const d = document.createElement('div');
          d.className = 'shop-item'; d.style.marginBottom = '6px';
          const sp = document.createElement('span');
          sp.textContent = v.room_id + ' · ' + v.dwell_seconds + 's in beeld (' + v.classification + ')';
          const b1 = document.createElement('button');
          b1.type = 'button'; b1.textContent = 'Buur/Bekende';
          b1.onclick = async () => {
            const r = await apiPost('/internal/v1/dashboard/holostage/action', {
              action: 'classify_visitor', visitor_id: v.visitor_id, classification: 'bekende_buur'
            });
            renderStage(r.stage);
          };
          d.append(sp, b1);
          visBody.append(d);
        }
      }

      // Owner Biometrics & Device Grants
      const ownerCard = document.getElementById('card-owner-admin');
      ownerCard.hidden = st.viewer?.kind !== 'owner';
      if (st.viewer?.kind === 'owner' && st.biometrics_admin) {
        const vb = document.getElementById('voice-enroll-body');
        vb.textContent = (st.biometrics_admin.members || []).map(
          m => m.display_name + ': ' + (m.enrolled ? ('✓ ingeleerd (' + m.sample_count + ' opnames)') : 'nog niet ingeleerd')
        ).join(' · ');
      }
    }

    async function refreshState() {
      const res = await fetch('/internal/v1/dashboard/holostage/state?mode=' + encodeURIComponent(currentMode));
      if (res.ok) renderStage(await res.json());
    }

    // Record short 16kHz mono PCM snippet via WebAudio
    async function recordPcm16Seconds(seconds = 2.6) {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }
      });
      const ctx = new AudioContext({ sampleRate: 16000 });
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      proc.onaudioprocess = (e) => {
        const f32 = e.inputBuffer.getChannelData(0);
        const i16 = new Int16Array(f32.length);
        for (let i = 0; i < f32.length; i++) {
          const s = Math.max(-1, Math.min(1, f32[i]));
          i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        chunks.push(new Uint8Array(i16.buffer));
      };
      src.connect(proc); proc.connect(ctx.destination);
      await new Promise(r => setTimeout(r, seconds * 1000));
      proc.disconnect(); src.disconnect();
      stream.getTracks().forEach(t => t.stop());
      await ctx.close();
      const total = chunks.reduce((acc, c) => acc + c.length, 0);
      const merged = new Uint8Array(Math.min(total, 160000));
      let offset = 0;
      for (const c of chunks) {
        const slice = c.subarray(0, Math.min(c.length, merged.length - offset));
        merged.set(slice, offset);
        offset += slice.length;
        if (offset >= merged.length) break;
      }
      let bin = '';
      for (let i = 0; i < merged.length; i += 8192) {
        bin += String.fromCharCode(...merged.subarray(i, i + 8192));
      }
      return btoa(bin);
    }

    async function sendStageTurn(text, audioPcmB64 = null) {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      speechBubble.textContent = 'Eva luistert & denkt…';
      const t0 = performance.now();
      let firstClauseMs = null;
      let fullText = '';
      const res = await fetch('/internal/v1/dashboard/holostage/turn-stream', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-holostage-client': '1' },
        body: JSON.stringify({ text, mode: currentMode, audio_pcm_b64: audioPcmB64 }),
      });
      if (!res.ok || !res.body) {
        speechBubble.textContent = 'Kon antwoord niet starten.';
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        buf += decoder.decode(value || new Uint8Array(), { stream: !done });
        let nl;
        while ((nl = buf.indexOf('\\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === 'stage_delta') {
            fullText += ev.text;
            speechBubble.textContent = fullText;
          } else if (ev.type === 'speech_clause') {
            if (firstClauseMs === null) {
              firstClauseMs = Math.round(performance.now() - t0);
              toast('⚡ Eerste spraakzin na ' + firstClauseMs + ' ms');
            }
            speakClause(ev.text);
          } else if (ev.type === 'stage_state') {
            renderStage(ev.stage);
          } else if (ev.type === 'final') {
            speechBubble.textContent = ev.text || fullText;
          }
        }
        if (done) break;
      }
    }

    document.getElementById('talk-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const inp = document.getElementById('talk-input');
      const txt = inp.value.trim();
      if (!txt) return;
      inp.value = '';
      await sendStageTurn(txt);
    });

    document.getElementById('shop-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const lbl = document.getElementById('shop-input');
      const qty = document.getElementById('shop-qty');
      const r = await apiPost('/internal/v1/dashboard/holostage/action', {
        action: 'shopping_add', label: lbl.value, quantity: qty.value
      });
      lbl.value = ''; qty.value = '';
      renderStage(r.stage);
    });

    document.getElementById('shop-clear-btn').addEventListener('click', async () => {
      const r = await apiPost('/internal/v1/dashboard/holostage/action', { action: 'shopping_clear_checked' });
      renderStage(r.stage);
    });

    document.getElementById('read-shop-btn').addEventListener('click', () => {
      const items = (latestState?.cards?.shopping_list || []).filter(i => !i.checked);
      const line = items.length
        ? ('Op de boodschappenlijst staat: ' + items.map(i => i.label).join(', ') + '.')
        : 'De boodschappenlijst is helemaal leeg.';
      speechBubble.textContent = line;
      speakClause(line);
    });

    document.getElementById('spotlight-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const t = document.getElementById('spot-title');
      const b = document.getElementById('spot-body');
      const r = await apiPost('/internal/v1/dashboard/holostage/action', {
        action: 'summon_spotlight', title: t.value, body_nl: b.value
      });
      t.value = ''; b.value = '';
      renderStage(r.stage);
    });

    document.getElementById('enroll-voice-btn').addEventListener('click', async () => {
      try {
        toast('🎙️ Spreek nu 2,5 seconden een gewone Nederlandse zin in…');
        const pcmB64 = await recordPcm16Seconds(2.6);
        const memberId = document.getElementById('enroll-member').value;
        const r = await apiPost('/internal/v1/dashboard/holostage/action', {
          action: 'enroll_voice_pcm', member_id: memberId, audio_pcm_b64: pcmB64
        });
        toast(r.message || 'Stemprofiel lokaal opgeslagen');
        renderStage(r.stage);
      } catch (err) { toast('Microfoonfout: ' + err.message); }
    });

    document.getElementById('shield-toggle-btn').addEventListener('click', async () => {
      const nextLock = !(latestState?.privacy_shield?.active);
      const r = await apiPost('/internal/v1/dashboard/holostage/action', {
        action: 'set_privacy_shield', locked: nextLock
      });
      renderStage(r.stage);
    });

    document.getElementById('grant-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const lbl = document.getElementById('grant-label');
      const mode = document.getElementById('grant-mode').value;
      const r = await apiPost('/internal/v1/dashboard/holostage/action', {
        action: 'issue_device_token',
        label: lbl.value,
        form_factor: mode,
        allow_private_cards_when_alone: mode !== 'tv_cast'
      });
      lbl.value = '';
      document.getElementById('grant-output').textContent = 'Eenmalige koppelingslink: ' + location.origin + r.issued.pair_path;
      renderStage(r.stage);
    });

    // Browser SpeechRecognition + 16kHz Speaker ID push-to-talk
    document.getElementById('mic-btn').addEventListener('click', async () => {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) { toast('Gebruik het tekstveld of Chrome/Edge/Safari voor spraakherkenning.'); return; }
      toast('🎙️ Ik luister…');
      const rec = new SR();
      rec.lang = 'nl-NL';
      rec.interimResults = false;
      rec.onresult = async (ev) => {
        const transcript = ev.results[0][0].transcript;
        await sendStageTurn(transcript);
      };
      rec.onerror = () => toast('Spraakinvoer gestopt.');
      rec.start();
    });

    // Optional Camera Preview (Eva Pocket back-camera / UniFi Screen Gaze-to-Talk)
    let camActive = false;
    document.getElementById('cam-btn').addEventListener('click', async () => {
      const vid = document.getElementById('local-video');
      if (camActive) {
        (vid.srcObject?.getTracks() || []).forEach(t => t.stop());
        vid.srcObject = null; vid.style.display = 'none'; camActive = false;
        toast('Camera gestopt.');
        return;
      }
      try {
        const facing = currentMode === 'pocket' ? { ideal: 'environment' } : 'user';
        const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing, width: 640, height: 480 } });
        vid.srcObject = stream; vid.style.display = 'block'; camActive = true;
        toast(currentMode === 'pocket' ? '📱 Eva Pocket meekijk-camera actief' : '👁️ On-device Gaze-to-Talk camera actief');
      } catch (err) { toast('Camera niet beschikbaar: ' + err.message); }
    });

    // 3D Eva GLB Renderer
    function boot3DEva() {
      const canvas = document.getElementById('eva-canvas');
      const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 50);
      camera.position.set(0, 1.38, 2.35);

      scene.add(new THREE.HemisphereLight(0x93c5fd, 0x0f172a, 1.55));
      const keyLight = new THREE.DirectionalLight(0xe0f2fe, 1.9);
      keyLight.position.set(1.8, 2.6, 2.4);
      scene.add(keyLight);

      const haloGeo = new THREE.RingGeometry(0.46, 0.52, 64);
      const haloMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8, side: THREE.DoubleSide, transparent: true, opacity: 0.42 });
      const halo = new THREE.Mesh(haloGeo, haloMat);
      halo.position.set(0, 1.32, -0.28);
      scene.add(halo);

      let avatarRoot = null;
      const loader = new GLTFLoader();
      loader.load('/internal/v1/dashboard/holostage/assets/eva.glb', (gltf) => {
        avatarRoot = gltf.scene;
        scene.add(avatarRoot);
      });

      const resize = () => {
        const w = canvas.clientWidth || 360, h = canvas.clientHeight || 480;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', resize);
      resize();

      const clock = new THREE.Clock();
      function animate() {
        requestAnimationFrame(animate);
        const t = clock.getElapsedTime();
        const pulse = 1.0 + Math.sin(t * (speakingEnergy > 0 ? 11.0 : 2.1)) * (speakingEnergy > 0 ? 0.08 : 0.02);
        halo.scale.setScalar(pulse);
        haloMat.opacity = speakingEnergy > 0 ? 0.78 : 0.36;
        if (avatarRoot) {
          avatarRoot.rotation.y = Math.sin(t * 0.55) * 0.08;
          avatarRoot.position.y = Math.sin(t * 1.4) * 0.012;
        }
        renderer.render(scene, camera);
      }
      animate();
    }

    refreshState();
    boot3DEva();
    setInterval(refreshState, 6000);
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
    @app.get("/internal/v1/dashboard/holostage")
    def holostage_page(request: Request, mode: str = "cockpit") -> HTMLResponse:
        mgr = _resolve_holostage_manager(runtime)
        req_mode = _parse_mode(mode)
        try:
            _authenticate_stage_viewer(
                request, access=access, runtime=runtime, mgr=mgr, requested_mode=req_mode
            )
            return HTMLResponse(
                HOLOSTAGE_HTML,
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": _CSP_HEADER,
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except HTTPException:
            # Allow a dedicated stage mode (`unifi_screen`, `tv_cast`, `pocket`) to exchange `#stg_...` fragment
            # via zero-data bootstrap without exposing any state, assets, or master Basic prompt on TVs.
            if mode in ("unifi_screen", "tv_cast", "pocket"):
                return HTMLResponse(
                    _PAIRING_BOOTSTRAP_HTML,
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Security-Policy": _CSP_HEADER,
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            access.require(request)
            raise

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
        is_https = (request.url.scheme == "https") or (
            request.headers.get("x-forwarded-proto", "").lower() == "https"
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
                "background_color": "#070d19",
                "theme_color": "#070d19",
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

    @app.post("/internal/v1/dashboard/holostage/action")
    async def holostage_action(request: Request) -> JSONResponse:
        mgr = _resolve_holostage_manager(runtime)
        viewer = _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        _enforce_post_csrf_and_framing(request, used_cookie=viewer.used_cookie)
        # Allow up to 480 KB JSON payload for 2.5s 16kHz PCM voice enrollment base64
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
                allow_priv = (
                    viewer.kind in ("owner", "private_stage")
                    and not mgr.biometrics.is_privacy_shield_active()[0]
                )
                mgr.dismiss_spotlight_card(str(payload.get("card_id") or ""), allow_private=allow_priv)
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
                # Never return unredacted biometric snapshot to non-owner viewers (HIGH-01)
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
                    ttl_hours=ttl_h if isinstance(ttl_h, int) and not isinstance(ttl_h, bool) else 720,
                )
                return _done("🔗 Eenmalige schermkoppeling aangemaakt.", {"issued": issued})

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
                stage_url = str(payload.get("stage_url") or "").strip() if action == "cast_launch" else None
                app_id = str(payload.get("app_id") or DEFAULT_MEDIA_RECEIVER_APP_ID)
                cast_res = query_or_launch_approved_cast_device(
                    mgr.proprioception,
                    device_id=str(payload.get("device_id") or ""),
                    stage_url=stage_url,
                    app_id=app_id,
                )
                return _done(f"📺 Cast-status ontvangen van '{cast_res['label']}'.", {"cast": cast_res})

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
            # Only trusted/private viewers may evaluate voice biometrics (prevents ambient voiceprint oracle)
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
            effects = mgr.apply_spoken_stage_side_effects(raw_text, speaker_name=sp_label)
            return sp_match, sh_active, effects

        speaker_match, shield_active, side_effects = await asyncio.to_thread(_prepare_turn_sync)

        # SECURITY INVARIANT:
        # Owner private memory/runtime (`runtime.dashboard_turn`) is invoked ONLY for authenticated `owner`
        # when NO bystander privacy shield is active! Ambient screens or shielded rooms stay strictly
        # inside shared stage scope so private emails/memories/schedules can never leak to bystanders.
        can_use_owner_runtime = (
            viewer.kind == "owner"
            and _has_explicit_owner_authority(access, runtime)
            and not shield_active
            and hasattr(runtime, "dashboard_turn")
        )

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
                    spoken = f"Staat erop! Ik heb {', '.join(e.lstrip('+- ') for e in side_effects)} bijgewerkt op de boodschappenlijst."
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
                        "mag zetten, of open de Gezinshoofd-cockpit voor persoonlijke vragen."
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
