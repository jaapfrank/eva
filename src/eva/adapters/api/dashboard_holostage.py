"""Fase 4 — Eva HoloStage Full-Screen 3D Stage, LiveKit Continuous Voice, Camera Co-Pilot & Conjure-On-Demand Cards.

Exposes:
- GET  /internal/v1/dashboard/holostage                  -> Full-screen 3D Eva (`cockpit`, `unifi_screen`, `tv_cast`, `pocket`)
- POST /internal/v1/dashboard/holostage/session          -> Exchange `#stg_<token>` pairing fragment for `HttpOnly; SameSite=Strict` cookie
- GET  /internal/v1/dashboard/holostage/manifest.json    -> `Eva Pocket` mobile PWA manifest
- GET  /internal/v1/dashboard/holostage/assets/{name}    -> Stage-authenticated `eva.glb` & Three.js ES modules
- GET  /internal/v1/dashboard/holostage/state            -> Privacy-redacted `EvaHoloStageState/v1` JSON
- POST /internal/v1/dashboard/holostage/speech-audio     -> Neural Dutch TTS audio for ambient Cast TVs & text replies
- POST /internal/v1/dashboard/holostage/action           -> Conjure/dismiss cards, shopping list, biometrics & Cast V2 (`DashCast 84912283`)
- POST /internal/v1/dashboard/holostage/turn-stream      -> Sub-300ms clause-chunked NDJSON stream + live stage card updates
- POST /internal/v1/dashboard/holostage/vision-turn      -> Zero-retention camera co-pilot (`Eva Ziet Echt Mee`) + live voice context
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
        parsed_origin = urlsplit(origin)
        origin_host = parsed_origin.netloc.lower()
        req_host = (request.headers.get("host") or "").lower()
        if parsed_origin.scheme == "https" and req_host.endswith(":443") and not origin_host.endswith(":443"):
            req_host = req_host[:-4]
        elif parsed_origin.scheme == "http" and req_host.endswith(":80") and not origin_host.endswith(":80"):
            req_host = req_host[:-3]
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
<title>Eva HoloStage — Apparaatkoppeling</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;width:100%;min-height:100dvh;background:radial-gradient(circle at 50% 38%,rgba(56,189,248,.14),transparent 55%),#060b16;color:#f8fafc;font:15px/1.55 system-ui,-apple-system,sans-serif}
body{display:flex;align-items:center;justify-content:center;padding:20px}
.box{width:100%;max-width:460px;padding:28px;border:1px solid rgba(56,189,248,.32);border-radius:20px;background:rgba(15,25,45,.94);box-shadow:0 22px 50px rgba(0,0,0,.55);text-align:left}
h1{margin:0 0 10px;font-size:1.28rem;letter-spacing:-.01em}
p{color:#cbd5e1;margin:0 0 12px}
.hint{padding:11px 13px;border-radius:12px;background:rgba(8,14,28,.78);border:1px solid rgba(148,163,184,.16);font-size:.86rem;color:#94a3b8;margin-bottom:14px}
.hint a{color:#38bdf8;text-decoration:none;font-weight:600}
.hint a:hover{text-decoration:underline}
form{display:flex;gap:8px;margin-top:10px}
input{flex:1;min-width:0;padding:10px 12px;border-radius:11px;border:1px solid rgba(148,163,184,.28);background:#060b16;color:#f8fafc;font-size:.9rem}
button{padding:10px 15px;border-radius:11px;border:1px solid rgba(110,231,183,.5);background:linear-gradient(135deg,#10b981,#059669);color:#031c14;font-weight:700;font-size:.88rem;cursor:pointer;white-space:nowrap}
button:disabled{opacity:.55;cursor:default}
.err{color:#fda4af;font-size:.86rem;margin-top:10px}
</style>
</head><body><div class="box">
  <h1 id="hdr">🔒 Apparaat nog niet gekoppeld</h1>
  <p id="msg">Dit publieke adres is beveiligd zodat vreemden op internet niet bij Eva kunnen. Koppel deze browser eenmalig (blijft 30 dagen bewaard).</p>
  <div class="hint" id="pc-hint">
    💻 <strong>Zit je op je eigen pc?</strong> Open daar rechtstreeks de <a href="http://localhost:44711/internal/v1/dashboard/holostage">Gezinshoofd Cockpit (localhost:44711)</a> — zonder koppelingslink.
  </div>
  <form id="pair-form">
    <input id="pair-in" type="password" autocomplete="off" placeholder="Plak je #stg_ link of koppelcode…" />
    <button id="pair-btn" type="submit">Koppel scherm</button>
  </form>
  <div id="err" class="err" hidden></div>
</div>
<script>
(()=>{
  const hdr=document.getElementById('hdr');
  const msg=document.getElementById('msg');
  const errEl=document.getElementById('err');
  const form=document.getElementById('pair-form');
  const inp=document.getElementById('pair-in');
  const btn=document.getElementById('pair-btn');
  function extractToken(val){
    const s=String(val||'').trim();
    const m=s.match(/stg_[A-Za-z0-9_-]{40,48}/);
    return m?m[0]:'';
  }
  async function pairWithToken(tok){
    errEl.hidden=true;
    hdr.textContent='⏳ Eva HoloStage koppelen…';
    msg.textContent='Bezig met veilige apparaatauthenticatie.';
    btn.disabled=true;
    try{
      const res=await fetch('/internal/v1/dashboard/holostage/session',{
        method:'POST',
        headers:{'content-type':'application/json','x-holostage-client':'1'},
        body:JSON.stringify({token:tok})
      });
      if(!res.ok)throw new Error('rejected');
      if(location.hash){history.replaceState(null,'',location.pathname+location.search);}
      location.reload();
    }catch(_e){
      btn.disabled=false;
      hdr.textContent='🔒 Koppeling niet gelukt';
      msg.textContent='Deze koppelcode is ongeldig of ingetrokken. Maak een nieuwe link aan via ⚙️ Beheer & TV op je pc of gebruik de nieuwste link uit Telegram.';
      errEl.textContent='Controleer of de volledige #stg_ link of code is geplakt.';
      errEl.hidden=false;
    }
  }
  form.addEventListener('submit',(e)=>{
    e.preventDefault();
    const tok=extractToken(inp.value);
    if(!tok){
      errEl.textContent='Plak een geldige #stg_ koppelingslink of stg_ code.';
      errEl.hidden=false;
      return;
    }
    pairWithToken(tok);
  });
  const hashTok=extractToken(location.hash||'');
  if(hashTok){pairWithToken(hashTok);}
})();
</script></body></html>
"""

HOLOSTAGE_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
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
      margin: 0; padding: 0; width: 100vw; height: 100vh; height: 100dvh; overflow: hidden;
      background:
        radial-gradient(circle at 50% 42%, rgba(56, 189, 248, 0.16), transparent 56%),
        radial-gradient(circle at 50% 88%, rgba(16, 185, 129, 0.09), transparent 48%),
        var(--bg);
      color: var(--text);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    #eva-canvas {
      position: fixed; inset: 0; width: 100vw; height: 100vh; height: 100dvh; z-index: 1;
    }

    /* Top bar — subtle floating glass, safe-area aware */
    .top-hud {
      position: fixed;
      top: calc(14px + env(safe-area-inset-top, 0px));
      left: calc(16px + env(safe-area-inset-left, 0px));
      right: calc(16px + env(safe-area-inset-right, 0px));
      z-index: 10;
      display: flex; justify-content: space-between; align-items: flex-start;
      gap: 10px; pointer-events: none;
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
    .pill.vision-on {
      border-color: rgba(34, 211, 238, 0.72);
      background: rgba(8, 51, 68, 0.68);
      color: #cffafe;
      box-shadow: 0 0 20px rgba(34, 211, 238, 0.24);
      cursor: pointer;
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
      position: relative; overflow: hidden;
      background: var(--card);
      border: 1px solid rgba(56, 189, 248, 0.38);
      border-radius: 20px; padding: 16px 18px;
      box-shadow: 0 20px 48px rgba(2, 6, 23, 0.62), inset 0 1px 0 rgba(255, 255, 255, 0.10);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      animation: holoMaterialize 0.34s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .holo-card::before {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background: linear-gradient(125deg, rgba(56, 189, 248, 0.14) 0%, transparent 38%, rgba(16, 185, 129, 0.08) 100%);
    }
    .holo-card.theme-leaderboard {
      border-color: rgba(34, 211, 238, 0.58);
      box-shadow: 0 22px 54px rgba(2, 6, 23, 0.72), 0 0 28px rgba(34, 211, 238, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.14);
    }
    .lb-list { list-style: none; padding: 0; margin: 10px 0 0 0; display: flex; flex-direction: column; gap: 6px; }
    .lb-row {
      display: flex; align-items: center; gap: 10px;
      padding: 8px 11px; border-radius: 12px;
      background: rgba(8, 16, 32, 0.68);
      border: 1px solid rgba(148, 163, 184, 0.14);
      font-size: 0.91rem; font-weight: 500;
      opacity: 0; transform: translateX(14px);
      animation: lbRowSlide 0.38s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }
    @keyframes lbRowSlide {
      to { opacity: 1; transform: translateX(0); }
    }
    .lb-rank {
      min-width: 28px; height: 26px; padding: 0 6px; border-radius: 8px;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 0.78rem; font-weight: 800; letter-spacing: -0.02em;
      background: rgba(51, 65, 85, 0.75); color: #e2e8f0;
    }
    .lb-row.pos-1 { border-color: rgba(251, 191, 36, 0.48); background: linear-gradient(90deg, rgba(251, 191, 36, 0.16), rgba(8, 16, 32, 0.72)); }
    .lb-row.pos-1 .lb-rank { background: linear-gradient(135deg, #fbbf24, #d97706); color: #0f172a; }
    .lb-row.pos-2 { border-color: rgba(203, 213, 225, 0.38); background: linear-gradient(90deg, rgba(203, 213, 225, 0.12), rgba(8, 16, 32, 0.72)); }
    .lb-row.pos-2 .lb-rank { background: linear-gradient(135deg, #e2e8f0, #94a3b8); color: #0f172a; }
    .lb-row.pos-3 { border-color: rgba(217, 119, 6, 0.42); background: linear-gradient(90deg, rgba(217, 119, 6, 0.14), rgba(8, 16, 32, 0.72)); }
    .lb-row.pos-3 .lb-rank { background: linear-gradient(135deg, #f59e0b, #b45309); color: #0f172a; }
    body[data-mode="tv_cast"] .conjured-overlay {
      top: 56px; right: 42px; bottom: 96px;
      width: min(540px, 46vw);
    }
    body[data-mode="tv_cast"] .holo-card { padding: 20px 22px; }
    body[data-mode="tv_cast"] .holo-card-head h2 { font-size: 1.22rem; }
    body[data-mode="tv_cast"] .lb-row { font-size: 1.02rem; padding: 10px 14px; }

    /* Atmospheric 3D Stage Choreography Glow Layer */
    .stage-fx-layer {
      position: fixed; inset: 0; z-index: 2; pointer-events: none;
      opacity: 0; transition: opacity 0.85s ease;
    }
    .stage-fx-layer[data-fx="aurora_reveal"],
    .stage-fx-layer[data-fx="wave_greeting"] {
      opacity: 1;
      background:
        radial-gradient(ellipse 68% 42% at 50% 14%, rgba(34, 211, 238, 0.24), transparent 70%),
        radial-gradient(ellipse 55% 38% at 28% 24%, rgba(16, 185, 129, 0.19), transparent 68%),
        radial-gradient(ellipse 52% 35% at 74% 22%, rgba(168, 85, 247, 0.16), transparent 68%);
      animation: fxAuroraPulse 4.2s ease-in-out infinite alternate;
    }
    .stage-fx-layer[data-fx="cinema_spotlight"] {
      opacity: 1;
      background:
        conic-gradient(from 165deg at 36% -8%, transparent 0deg, rgba(56, 189, 248, 0.20) 18deg, transparent 38deg),
        radial-gradient(ellipse 60% 48% at 72% 48%, rgba(34, 211, 238, 0.16), transparent 72%);
      animation: fxAuroraPulse 3.6s ease-in-out infinite alternate;
    }
    .stage-fx-layer[data-fx="celebration"],
    .stage-fx-layer[data-fx="pulse_energy"] {
      opacity: 1;
      background:
        radial-gradient(ellipse 64% 46% at 50% 22%, rgba(251, 191, 36, 0.20), transparent 70%),
        radial-gradient(ellipse 58% 44% at 35% 65%, rgba(16, 185, 129, 0.18), transparent 70%);
      animation: fxAuroraPulse 2.8s ease-in-out infinite alternate;
    }
    @keyframes fxAuroraPulse {
      from { transform: scale(1.0) translateY(0); filter: hue-rotate(0deg); }
      to   { transform: scale(1.05) translateY(-8px); filter: hue-rotate(14deg); }
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

    /* Bottom floating conversation, camera co-pilot & voice dock */
    .bottom-dock {
      position: fixed; left: 50%;
      bottom: calc(16px + env(safe-area-inset-bottom, 0px));
      transform: translateX(-50%);
      width: min(780px, calc(100vw - 24px)); z-index: 10;
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
      width: 100%; display: flex; align-items: center; gap: 8px;
      padding: 8px 11px; border-radius: 999px;
      background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      box-shadow: 0 16px 38px rgba(2, 6, 23, 0.5);
    }
    .dock-controls form {
      flex: 1; min-width: 0; display: flex; gap: 7px; align-items: center; margin: 0;
    }
    .dock-controls input {
      min-width: 0; border-radius: 999px; background: rgba(6, 11, 22, 0.65);
      border-color: rgba(148, 163, 184, 0.14); padding: 9px 14px;
    }
    button.btn-cam-toggle {
      border-radius: 999px; padding: 9px 13px; font-size: 0.92rem;
      border-color: rgba(56, 189, 248, 0.34);
      background: rgba(14, 29, 54, 0.82);
      white-space: nowrap;
    }
    button.btn-cam-toggle.cam-active {
      border-color: rgba(34, 211, 238, 0.85);
      background: rgba(8, 66, 88, 0.88);
      box-shadow: 0 0 18px rgba(34, 211, 238, 0.28);
    }

    /* Floating Camera Co-Pilot PIP ("Eva Ziet Echt Mee") */
    .camera-pip {
      position: fixed;
      left: calc(16px + env(safe-area-inset-left, 0px));
      bottom: calc(96px + env(safe-area-inset-bottom, 0px));
      width: min(276px, calc(100vw - 32px));
      z-index: 11;
      background: rgba(10, 18, 36, 0.92);
      border: 1px solid rgba(34, 211, 238, 0.45);
      border-radius: 18px;
      padding: 10px;
      box-shadow: 0 20px 44px rgba(2, 6, 23, 0.68), 0 0 24px rgba(34, 211, 238, 0.14);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      animation: holoMaterialize 0.22s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .camera-pip[hidden] { display: none !important; }
    .camera-pip-head {
      display: flex; justify-content: space-between; align-items: center;
      font-size: 0.78rem; font-weight: 700; color: #cffafe; margin-bottom: 7px;
    }
    .camera-pip-view {
      position: relative; width: 100%; aspect-ratio: 4 / 3;
      border-radius: 12px; overflow: hidden; background: #020617;
      border: 1px solid rgba(56, 189, 248, 0.24);
    }
    .camera-pip-view video {
      width: 100%; height: 100%; object-fit: cover; display: block;
    }
    .camera-pip-flash {
      position: absolute; inset: 0; background: #e0f2fe; opacity: 0;
      pointer-events: none; transition: opacity 0.22s ease;
    }
    .camera-pip-flash.flash-on { opacity: 0.72; }
    .camera-pip-actions {
      display: flex; gap: 6px; margin-top: 8px;
    }
    .camera-pip-actions button.btn-snap {
      flex: 1; border-radius: 11px; padding: 8px 10px; font-size: 0.8rem; font-weight: 700;
      background: linear-gradient(135deg, rgba(6, 182, 212, 0.95), rgba(16, 185, 129, 0.92));
      border-color: rgba(165, 243, 252, 0.6); color: #021c24;
    }

    /* Ambient TV Cast mode: 100% pure 3D Eva + clean cinema subtitle + conjured cards only */
    body[data-mode="tv_cast"] .top-right,
    body[data-mode="tv_cast"] .dock-controls,
    body[data-mode="tv_cast"] .camera-pip,
    body[data-mode="tv_cast"] .admin-drawer {
      display: none !important;
    }
    body[data-mode="tv_cast"] .transcript-strip {
      font-size: 1.25rem; text-align: center; max-height: none;
      background: rgba(6, 11, 22, 0.78);
    }
    @media (max-width: 640px) {
      button.btn-call-start, button.btn-call-end { padding: 10px 14px; font-size: 0.85rem; }
      .dock-controls input { padding: 8px 11px; font-size: 0.84rem; }
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
    #remote-audio, #stage-tts-audio { display: none; }
  </style>
</head>
<body data-mode="cockpit">
  <canvas id="eva-canvas" aria-label="Eva 3D Huiskamer"></canvas>
  <div id="remote-audio"></div>
  <audio id="stage-tts-audio" preload="auto" playsinline></audio>

  <header class="top-hud">
    <div class="top-left">
      <span class="pill" id="voice-pill">🎙️ Klik op Start gesprek om te praten</span>
      <span class="pill vision-on" id="vision-pill" title="Klik om cameracontext te wissen" hidden>👁️ Eva ziet camerabeeld</span>
      <span class="pill shield-on" id="shield-pill" hidden>🛡️ Privéschild actief</span>
    </div>
    <div class="top-right">
      <button type="button" id="btn-clear-cards" class="btn-sm" hidden>✕ Kaarten verbergen</button>
      <button type="button" id="btn-toggle-admin" class="btn-sm" hidden>⚙️ Beheer &amp; TV</button>
    </div>
  </header>

  <!-- Floating Camera Co-Pilot PIP (strictly opt-in, zero disk retention) -->
  <aside class="camera-pip" id="camera-pip" hidden>
    <div class="camera-pip-head">
      <span>📷 Laat Eva meekijken</span>
      <div style="display:flex;gap:5px">
        <button type="button" id="btn-camera-flip" class="btn-sm" title="Draai camera">🔄</button>
        <button type="button" id="btn-camera-close" class="btn-sm" title="Sluit camera">✕</button>
      </div>
    </div>
    <div class="camera-pip-view">
      <video id="camera-video" autoplay playsinline muted></video>
      <div class="camera-pip-flash" id="camera-flash"></div>
    </div>
    <div class="camera-pip-actions">
      <button type="button" id="btn-camera-snap" class="btn-snap">👁️ Toon aan Eva</button>
    </div>
  </aside>

  <!-- Conjure-on-demand floating hologram cards (hidden when conjured_cards == []) -->
  <div class="stage-fx-layer" id="stage-fx-layer" data-fx=""></div>
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

    <div class="drawer-sec">
      <h3>📊 Gezinsstabiliteit (7d) &amp; HTTPS</h3>
      <div id="obs-summary-box" class="muted">Laden…</div>
    </div>
  </aside>

  <!-- Bottom Floating Voice & Chat Dock -->
  <section class="bottom-dock">
    <div class="transcript-strip" id="transcript-strip" hidden aria-live="polite"></div>
    <div class="dock-controls">
      <button type="button" id="btn-voice-start" class="btn-call-start">🎙️ Start gesprek</button>
      <button type="button" id="btn-voice-end" class="btn-call-end" hidden>⏹️ Beëindig gesprek</button>
      <button type="button" id="btn-camera-toggle" class="btn-cam-toggle" title="Laat Eva meekijken via je camera" hidden>📷</button>
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
    let ttsSpeaking = false;
    let voiceEnergy = 0.0;
    let vowelFormant = 0.5;
    let cameraTargetX = 0.0;
    let lastSpokenUtteranceId = null;
    let lastStageAnimationId = '';
    let activeStageFx = '';
    let stageFxStartedAt = 0;
    let stageFxTimer = null;

    function triggerStageChoreography(effectName, durationMs = 11000) {
      const allowedFx = ['aurora_reveal', 'cinema_spotlight', 'celebration', 'pulse_energy', 'wave_greeting'];
      const fx = allowedFx.includes(effectName) ? effectName : 'aurora_reveal';
      activeStageFx = fx;
      stageFxStartedAt = performance.now();
      const layer = $('stage-fx-layer');
      if (layer) layer.dataset.fx = fx;
      if (stageFxTimer) clearTimeout(stageFxTimer);
      stageFxTimer = setTimeout(() => {
        activeStageFx = '';
        if (layer) layer.dataset.fx = '';
      }, Math.max(2500, Number(durationMs) || 11000));
    }
    if (currentMode === 'tv_cast') {
      triggerStageChoreography('aurora_reveal', 9500);
    }
    let activeRemoteTrack = null;
    let cameraStream = null;
    let cameraOpenSeq = 0;
    let cameraFacingMode = 'environment';
    let visionSnapBusy = false;

    // WebAudio Analyser + WebRTC RTP AudioLevel wired to Eva's voice & TTS streams
    let audioCtx = null;
    let silentSink = null;
    let activeStreamSource = null;
    let activeStreamAnalyser = null;
    let activeAnalyser = null;
    let freqBins = null;
    let timeBins = null;
    const attachedAudioElements = new WeakMap();

    function disconnectStreamAnalyser() {
      if (activeStreamSource) {
        try { activeStreamSource.disconnect(); } catch (_e) {}
        activeStreamSource = null;
      }
      if (activeStreamAnalyser) {
        try { activeStreamAnalyser.disconnect(); } catch (_e) {}
        if (activeAnalyser === activeStreamAnalyser) activeAnalyser = null;
        activeStreamAnalyser = null;
      }
    }

    function ensureAudioContext() {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!audioCtx) {
        audioCtx = new Ctx();
        silentSink = audioCtx.createGain();
        silentSink.gain.value = 0.0;
        silentSink.connect(audioCtx.destination);
      }
      if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
      return audioCtx;
    }

    function attachStreamAnalyser(mediaStreamTrack) {
      try {
        disconnectStreamAnalyser();
        const ctx = ensureAudioContext();
        if (!ctx || !mediaStreamTrack) return;
        const stream = new MediaStream([mediaStreamTrack]);
        const src = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.62;
        src.connect(analyser);
        // Connect through zero-gain sink so Chromium/Safari pull WebRTC audio frames
        if (silentSink) analyser.connect(silentSink);
        activeStreamSource = src;
        activeStreamAnalyser = analyser;
        activeAnalyser = analyser;
        freqBins = new Uint8Array(analyser.frequencyBinCount);
        timeBins = new Uint8Array(analyser.fftSize);
      } catch (_e) {}
    }

    function attachElementAnalyser(audioEl) {
      try {
        const ctx = ensureAudioContext();
        if (!ctx || !audioEl) return;
        let analyser = attachedAudioElements.get(audioEl);
        if (!analyser) {
          const src = ctx.createMediaElementSource(audioEl);
          analyser = ctx.createAnalyser();
          analyser.fftSize = 256;
          analyser.smoothingTimeConstant = 0.62;
          src.connect(analyser);
          analyser.connect(ctx.destination);
          attachedAudioElements.set(audioEl, analyser);
        }
        activeAnalyser = analyser;
        freqBins = new Uint8Array(analyser.frequencyBinCount);
        timeBins = new Uint8Array(analyser.fftSize);
      } catch (_e) {}
    }

    let lastRtpSampleAt = 0.0;
    let cachedRtpLevel = 0.0;

    function readWebRtcRtpAudioLevel() {
      const nowPerf = performance.now();
      if ((nowPerf - lastRtpSampleAt) < 45.0) return cachedRtpLevel;
      lastRtpSampleAt = nowPerf;
      try {
        let best = 0.0;
        const primary = activeRemoteTrack?.receiver;
        if (primary && typeof primary.getSynchronizationSources === 'function') {
          const sources = primary.getSynchronizationSources() || [];
          for (const s of sources) {
            if (typeof s.audioLevel === 'number' && (!s.timestamp || (nowPerf - s.timestamp) < 400)) {
              const mapped = Math.min(1.0, Math.pow(Math.max(0, s.audioLevel * 3.2), 0.72));
              if (mapped > best) best = mapped;
            }
          }
        }
        if (best < 0.01) {
          const subPc = liveSession?.room?.engine?.pcManager?.subscriber?.pc;
          if (subPc && typeof subPc.getReceivers === 'function') {
            for (const r of subPc.getReceivers()) {
              if (r === primary || r?.track?.kind !== 'audio' || typeof r.getSynchronizationSources !== 'function') continue;
              const sources = r.getSynchronizationSources() || [];
              for (const s of sources) {
                if (typeof s.audioLevel === 'number' && (!s.timestamp || (nowPerf - s.timestamp) < 400)) {
                  const mapped = Math.min(1.0, Math.pow(Math.max(0, s.audioLevel * 3.2), 0.72));
                  if (mapped > best) best = mapped;
                }
              }
            }
          }
        }
        cachedRtpLevel = best;
        return best;
      } catch (_e) {
        cachedRtpLevel = 0.0;
        return 0.0;
      }
    }

    // --- 1. Full-Screen Three.js 3D Eva Stage (Natural Standing Posture & 60fps Lip-Sync) ---
    function buildSphereContouredMouth(THREE) {
      // Head bone local space: 1 unit = 1 cm (Armature scale = 0.01).
      // Painted mouth center on eva.glb is (-0.16, 14.39, 18.05), R_face ~= 15 cm.
      const rig = new THREE.Group();
      rig.position.set(-0.12, 14.39, 18.18);
      rig.rotation.x = 0.03;

      const zContour = (dx, dy) => -((dx * dx + dy * dy) / 30.0);

      // 1. Inner oral cavity (curved ellipse flush with face shell)
      const segU = 20, segV = 12;
      const cavPositions = [];
      const cavIndices = [];
      for (let iv = 0; iv <= segV; iv++) {
        const r = iv / segV;
        for (let iu = 0; iu <= segU; iu++) {
          const th = (iu / segU) * Math.PI * 2;
          const dx = Math.cos(th) * 1.65 * r;
          const dy = Math.sin(th) * 1.05 * r;
          cavPositions.push(dx, dy, zContour(dx, dy));
        }
      }
      for (let iv = 0; iv < segV; iv++) {
        for (let iu = 0; iu < segU; iu++) {
          const a = iv * (segU + 1) + iu;
          const b = a + segU + 1;
          cavIndices.push(a, b, a + 1, b, b + 1, a + 1);
        }
      }
      const cavGeo = new THREE.BufferGeometry();
      cavGeo.setAttribute('position', new THREE.Float32BufferAttribute(cavPositions, 3));
      cavGeo.setIndex(cavIndices);
      const cavMat = new THREE.MeshBasicMaterial({
        color: 0x26121b, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2
      });
      const cavity = new THREE.Mesh(cavGeo, cavMat);
      cavity.scale.set(0.85, 0.02, 1.0);
      cavity.visible = false;
      rig.add(cavity);

      // 2. Subtle warm tongue contour inside lower cavity
      const tngPositions = [];
      const tngIndices = [];
      for (let iv = 0; iv <= 6; iv++) {
        const r = iv / 6;
        for (let iu = 0; iu <= 14; iu++) {
          const th = Math.PI + (iu / 14) * Math.PI;
          const dx = Math.cos(th) * 1.05 * r;
          const dy = -0.18 + Math.sin(th) * 0.62 * r;
          tngPositions.push(dx, dy, zContour(dx, dy) + 0.02);
        }
      }
      for (let iv = 0; iv < 6; iv++) {
        for (let iu = 0; iu < 14; iu++) {
          const a = iv * 15 + iu;
          const b = a + 15;
          tngIndices.push(a, b, a + 1, b, b + 1, a + 1);
        }
      }
      const tngGeo = new THREE.BufferGeometry();
      tngGeo.setAttribute('position', new THREE.Float32BufferAttribute(tngPositions, 3));
      tngGeo.setIndex(tngIndices);
      const tongue = new THREE.Mesh(tngGeo, new THREE.MeshBasicMaterial({
        color: 0xb5546a, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -3
      }));
      tongue.visible = false;
      rig.add(tongue);

      // 3. Articulated upper & lower curved lip arcs matching Eva's charcoal eye palette
      const lipMat = new THREE.MeshBasicMaterial({
        color: 0x2b313a,
        polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4
      });
      function makeLipArc(signY, archSign) {
        const pts = [];
        for (let i = 0; i <= 12; i++) {
          const u = (i / 12) * 2 - 1; // -1 .. +1
          const dx = u * 1.62;
          const cornerLift = 0.22 * (u * u);
          const arch = archSign * 0.16 * (1 - u * u);
          const dy = cornerLift + arch + signY * 0.04;
          pts.push(new THREE.Vector3(dx, dy, zContour(dx, dy) + 0.05));
        }
        const curve = new THREE.CatmullRomCurve3(pts);
        return new THREE.Mesh(new THREE.TubeGeometry(curve, 20, 0.17, 8, false), lipMat);
      }
      const upperLip = makeLipArc(1, 1);
      const lowerLip = makeLipArc(-1, -1);
      rig.add(upperLip);
      rig.add(lowerLip);

      return { rig, cavity, tongue, upperLip, lowerLip };
    }

    function buildContouredEyeRig(THREE, skinnedMesh, headBone) {
      const bonesArr = skinnedMesh?.skeleton?.bones;
      const inverses = skinnedMesh?.skeleton?.boneInverses;
      const headIdx = Array.isArray(bonesArr) ? bonesArr.indexOf(headBone) : -1;
      const headInv = (headIdx >= 0 && inverses && inverses[headIdx]) ? inverses[headIdx] : null;
      const pos = skinnedMesh?.geometry?.attributes?.position;
      if (!headInv || !pos) return null;

      // Sample front-head vertices once to conform eyelids & iris glints to Eva's recessed eye sockets
      const samples = [];
      for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
        if (z > 0.15 && y >= 1.33 && y <= 1.51 && Math.abs(x) <= 0.20) {
          samples.push(x, y, z);
        }
      }
      function surfaceZ(mx, my) {
        let bestZ = 0.232 - 0.14 * Math.hypot(mx, my - 1.418);
        let bestD2 = 1e9;
        for (let i = 0; i < samples.length; i += 3) {
          const dx = samples[i] - mx;
          const dy = samples[i + 1] - my;
          const d2 = dx * dx + dy * dy;
          if (d2 < bestD2) {
            bestD2 = d2;
            bestZ = samples[i + 2];
          }
        }
        return Math.max(0.205, Math.min(0.262, bestZ));
      }

      const bindMat = skinnedMesh.bindMatrix || null;
      const tmpV = new THREE.Vector3();
      function writeHeadLocal(arr, idx, mx, my, mz) {
        tmpV.set(mx, my, mz);
        if (bindMat) tmpV.applyMatrix4(bindMat);
        tmpV.applyMatrix4(headInv);
        arr[idx * 3] = tmpV.x;
        arr[idx * 3 + 1] = tmpV.y;
        arr[idx * 3 + 2] = tmpV.z;
      }

      const lidMat = new THREE.MeshStandardMaterial({
        color: 0xfbfdff,
        roughness: 0.24,
        metalness: 0.04,
        side: THREE.DoubleSide
      });
      const glintMat = new THREE.MeshBasicMaterial({
        color: 0x67e8f9,
        transparent: true,
        opacity: 0.58,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide
      });

      const group = new THREE.Group();
      const lidMeshes = [];
      const glints = [];
      const cols = 12;
      const rows = 4;

      function makeLidMesh(cx, cy, rx, ry, isUpper) {
        const sign = isUpper ? 1 : -1;
        const seamY = cy - 0.004; // upper lid covers ~66%, lower lid ~34% for natural human blink
        const vCount = (cols + 1) * (rows + 1);
        const openArr = new Float32Array(vCount * 3);
        const closedArr = new Float32Array(vCount * 3);
        const indices = [];

        let vi = 0;
        for (let r = 0; r <= rows; r++) {
          const vFrac = r / rows; // 0 = outer socket rim, 1 = leading lash edge
          for (let c = 0; c <= cols; c++) {
            const u = (c / cols) * 2 - 1; // -1 .. +1
            const mx = cx + u * rx;
            const arc = Math.sqrt(Math.max(0.02, 1 - u * u));
            const rimY = cy + sign * ry * arc;
            const openY = rimY - sign * (0.0025 * vFrac);
            const closedTargetY = seamY + (isUpper ? -0.0012 : 0.0012) * arc;
            const closedY = rimY + (closedTargetY - rimY) * vFrac;
            const domeBulge = Math.sin(vFrac * Math.PI) * 0.0032 * arc;

            writeHeadLocal(openArr, vi, mx, openY, surfaceZ(mx, openY) + 0.0038);
            writeHeadLocal(closedArr, vi, mx, closedY, surfaceZ(mx, closedY) + 0.0044 + domeBulge);
            vi++;
          }
        }
        for (let r = 0; r < rows; r++) {
          for (let c = 0; c < cols; c++) {
            const a = r * (cols + 1) + c;
            const b = a + 1;
            const d = a + (cols + 1);
            const e = d + 1;
            indices.push(a, d, b, b, d, e);
          }
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(openArr, 3));
        geo.morphAttributes.position = [new THREE.BufferAttribute(closedArr, 3)];
        geo.setIndex(indices);
        geo.computeVertexNormals();
        const mesh = new THREE.Mesh(geo, lidMat);
        mesh.updateMorphTargets();
        return mesh;
      }

      function makeIrisGlint(cx, cy) {
        const segs = 18;
        const vCount = (segs + 1) * 2;
        const arr = new Float32Array(vCount * 3);
        const idx = [];
        const baseZ = surfaceZ(cx, cy) + 0.0022;
        let vi = 0;
        for (let i = 0; i <= segs; i++) {
          const ang = 0.35 + (i / segs) * (Math.PI * 1.15);
          const ca = Math.cos(ang), sa = Math.sin(ang);
          writeHeadLocal(arr, vi++, cx + ca * 0.022, cy + sa * 0.024, baseZ);
          writeHeadLocal(arr, vi++, cx + ca * 0.033, cy + sa * 0.035, baseZ);
        }
        for (let i = 0; i < segs; i++) {
          const a = i * 2, b = a + 1, c = a + 2, d = a + 3;
          idx.push(a, c, b, b, c, d);
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(arr, 3));
        geo.setIndex(idx);
        const m = new THREE.Mesh(geo, glintMat);
        const centerLocal = new THREE.Vector3(cx, cy, baseZ).applyMatrix4(headInv);
        return { mesh: m, restPos: centerLocal };
      }

      for (const cx of [-0.108, 0.108]) {
        const upper = makeLidMesh(cx, 1.418, 0.049, 0.052, true);
        const lower = makeLidMesh(cx, 1.418, 0.049, 0.052, false);
        lidMeshes.push({ upper, lower });
        group.add(upper);
        group.add(lower);
        const glint = makeIrisGlint(cx, 1.418);
        glints.push(glint);
        group.add(glint.mesh);
      }

      headBone.add(group);
      return { group, lidMeshes, glints, glintMat };
    }

    function buildLiquidNeuralField(THREE, isPocketInit) {
      const count = isPocketInit ? 38 : 56;
      const positions = new Float32Array(count * 3);
      const seeds = new Float32Array(count * 4);
      for (let i = 0; i < count; i++) {
        const ang = (i / count) * Math.PI * 2 + (i % 5) * 0.37;
        const rad = 0.44 + (i % 7) * 0.065;
        const y = 0.24 + ((i * 0.618034) % 1) * 1.46;
        positions[i * 3] = Math.cos(ang) * rad;
        positions[i * 3 + 1] = y;
        positions[i * 3 + 2] = Math.sin(ang) * rad - 0.12;
        seeds[i * 4] = ang;
        seeds[i * 4 + 1] = rad;
        seeds[i * 4 + 2] = y;
        seeds[i * 4 + 3] = 0.45 + (i % 9) * 0.11;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));

      const sprCanvas = document.createElement('canvas');
      sprCanvas.width = 64;
      sprCanvas.height = 64;
      const g2d = sprCanvas.getContext('2d');
      if (g2d) {
        const grad = g2d.createRadialGradient(32, 32, 2, 32, 32, 30);
        grad.addColorStop(0.0, 'rgba(255,255,255,1)');
        grad.addColorStop(0.35, 'rgba(186,230,253,0.75)');
        grad.addColorStop(1.0, 'rgba(56,189,248,0)');
        g2d.fillStyle = grad;
        g2d.fillRect(0, 0, 64, 64);
      }
      const tex = new THREE.CanvasTexture(sprCanvas);
      const mat = new THREE.PointsMaterial({
        size: isPocketInit ? 0.042 : 0.048,
        map: tex,
        color: 0x38bdf8,
        transparent: true,
        opacity: 0.52,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      });
      const points = new THREE.Points(geo, mat);
      return { points, geo, mat, seeds, count };
    }

    function init3DStage() {
      const canvas = $('eva-canvas');
      const isPocketInit = currentMode === 'pocket' || window.innerWidth < 768;
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({
          canvas,
          antialias: !isPocketInit,
          alpha: true,
          powerPreference: 'high-performance'
        });
      } catch (_err) {
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isPocketInit ? 1.35 : 1.75));
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

      const neuralField = buildLiquidNeuralField(THREE, isPocketInit);
      scene.add(neuralField.points);

      let avatarRoot = null;
      let baseFloorY = 0.04;
      const bones = {};
      const restQuats = {};
      const jawMeshes = [];
      let mouthParts = null;
      let eyeParts = null;

      const loader = new GLTFLoader();
      loader.load('/internal/v1/dashboard/holostage/assets/eva.glb', gltf => {
        avatarRoot = gltf.scene;
        // Keep Eva's symmetric standing rest pose (feet planted flat, NO walking_man loop!)
        avatarRoot.traverse(node => {
          if (node.isBone && node.name) {
            bones[node.name] = node;
            restQuats[node.name] = node.quaternion.clone();
          }
          if (node.isSkinnedMesh && node.geometry?.attributes?.position) {
            const pos = node.geometry.attributes.position;
            const jawArr = new Float32Array(pos.array.length);
            jawArr.set(pos.array);
            for (let i = 0; i < pos.count; i++) {
              const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
              if (z > 0.08 && y >= 1.20 && y <= 1.315) {
                const wx = Math.max(0, 1 - Math.abs(x) / 0.14);
                const wy = Math.sin(Math.PI * Math.max(0, Math.min(1, (y - 1.20) / 0.115)));
                const w = wx * wy;
                jawArr[i * 3 + 1] = y - 0.017 * w;
                jawArr[i * 3 + 2] = z - 0.005 * w;
              }
            }
            node.geometry.morphAttributes.position = [new THREE.BufferAttribute(jawArr, 3)];
            node.updateMorphTargets();
            jawMeshes.push(node);
          }
        });

        if (bones.Head) {
          mouthParts = buildSphereContouredMouth(THREE);
          bones.Head.add(mouthParts.rig);
          if (jawMeshes.length > 0) {
            eyeParts = buildContouredEyeRig(THREE, jawMeshes[0], bones.Head);
          }
        }

        const box = new THREE.Box3().setFromObject(avatarRoot);
        const size = box.getSize(new THREE.Vector3());
        const scale = 1.68 / Math.max(size.y, 0.01);
        avatarRoot.scale.setScalar(scale);
        const centered = new THREE.Box3().setFromObject(avatarRoot);
        baseFloorY = -centered.min.y + 0.04;
        avatarRoot.position.y = baseFloorY;
        scene.add(avatarRoot);
      });

      const resize = () => {
        const w = window.innerWidth;
        const h = window.innerHeight;
        const pocketNow = currentMode === 'pocket' || w < 768;
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, pocketNow ? 1.35 : 1.75));
        renderer.setSize(w, h, false);
        camera.aspect = w / Math.max(h, 1);
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', resize);
      resize();

      const tmpEuler = new THREE.Euler();
      const tmpQuat = new THREE.Quaternion();
      const targetQuat = new THREE.Quaternion();
      function poseBone(name, rx, ry, rz, alpha) {
        const b = bones[name];
        const rest = restQuats[name];
        if (!b || !rest) return;
        tmpEuler.set(rx, ry, rz, 'XYZ');
        tmpQuat.setFromEuler(tmpEuler);
        targetQuat.copy(rest).multiply(tmpQuat);
        b.quaternion.slerp(targetQuat, alpha);
      }

      let lookX = 0.0;
      let mouthOpen = 0.0;
      let mouthWidth = 1.0;
      let speakActivity = 0.0;
      let nextBlinkAt = 2.1;
      let blinkStart = -10.0;
      let blinkDuration = 0.19;
      let queuedDoubleBlink = false;
      let lastHeadYaw = 0.0;
      let nextSaccadeAt = 1.2;
      let saccadeTargetX = 0.0;
      let saccadeTargetY = 0.0;
      let saccadeX = 0.0;
      let saccadeY = 0.0;
      const clock = new THREE.Clock();

      function animate() {
        requestAnimationFrame(animate);
        const dt = Math.min(clock.getDelta(), 0.1);
        const t = clock.elapsedTime;
        const smoothFast = Math.min(1, dt * 18.0);
        const smoothPose = Math.min(1, dt * 6.5);

        // --- Multi-source real-time voice energy (WebAudio + WebRTC RTP + speech cadence fallback) ---
        let analyserLevel = 0.0;
        if (activeAnalyser && freqBins) {
          activeAnalyser.getByteFrequencyData(freqBins);
          let lowSum = 0, midHighSum = 0;
          for (let i = 2; i < 12; i++) lowSum += freqBins[i];
          for (let i = 12; i < 40; i++) midHighSum += freqBins[i];
          const totalAvg = (lowSum + midHighSum) / (38 * 140);
          analyserLevel = Math.min(1.0, totalAvg);
          if (timeBins && analyserLevel < 0.02) {
            activeAnalyser.getByteTimeDomainData(timeBins);
            let sq = 0;
            for (let i = 0; i < timeBins.length; i++) {
              const d = (timeBins[i] - 128) / 128;
              sq += d * d;
            }
            const rms = Math.sqrt(sq / timeBins.length);
            if (rms > 0.012) analyserLevel = Math.min(1.0, rms * 3.8);
          }
          if (lowSum + midHighSum > 60) {
            const ratio = (midHighSum / 28) / Math.max(1, lowSum / 10);
            vowelFormant += (Math.max(0.0, Math.min(1.0, ratio * 0.75)) - vowelFormant) * Math.min(1, dt * 12.0);
          }
        }

        const rtpLevel = liveSession ? readWebRtcRtpAudioLevel() : 0.0;
        const measuredLevel = Math.max(analyserLevel, rtpLevel);
        const stateSpeaking = (liveVoiceState === 'speaking') || ttsSpeaking;
        let instantEnergy = measuredLevel;
        if (stateSpeaking && measuredLevel < 0.02) {
          // Fallback syllable cadence when mobile OS mutes WebAudio tap on hardware AEC stream
          const syll = Math.max(0,
            Math.sin(t * 11.4) * 0.52 +
            Math.sin(t * 7.1 + 0.9) * 0.44 +
            Math.sin(t * 17.3 + 2.1) * 0.22
          );
          instantEnergy = Math.min(0.88, syll * 0.78);
          vowelFormant = 0.5 + 0.35 * Math.sin(t * 6.2);
        }

        const attack = instantEnergy > voiceEnergy ? Math.min(1, dt * 24.0) : Math.min(1, dt * 13.0);
        voiceEnergy += (instantEnergy - voiceEnergy) * attack;
        evaSpeaking = stateSpeaking || voiceEnergy > 0.055;
        speakActivity += ((evaSpeaking ? 1.0 : 0.0) - speakActivity) * Math.min(1, dt * 5.0);

        // Articulated syllable mouth opening & vowel width ('oh' narrow vs 'aa'/'ee' wider)
        const syllableFlutter = evaSpeaking ? (0.84 + 0.16 * Math.sin(t * 18.5)) : 0.0;
        const targetMouthOpen = evaSpeaking
          ? Math.min(1.0, Math.max(0.04, (voiceEnergy * 1.28 - 0.03) * syllableFlutter))
          : 0.0;
        mouthOpen += (targetMouthOpen - mouthOpen) * smoothFast;
        const smileIdle = (userVoiceState === 'speaking') ? 1.06 : 1.0;
        const targetWidth = evaSpeaking
          ? (0.78 + 0.34 * vowelFormant - 0.12 * mouthOpen)
          : smileIdle;
        mouthWidth += (targetWidth - mouthWidth) * Math.min(1, dt * 14.0);

        if (mouthParts) {
          const openCm = mouthOpen * 1.05; // up to ~2.1cm total lip separation in Head space
          mouthParts.upperLip.position.y = openCm * 0.38;
          mouthParts.lowerLip.position.y = -openCm * 0.66;
          mouthParts.upperLip.scale.set(mouthWidth, 1.0, 1.0);
          mouthParts.lowerLip.scale.set(mouthWidth * 0.96, 1.0, 1.0);
          const showInside = mouthOpen > 0.045;
          mouthParts.cavity.visible = showInside;
          mouthParts.tongue.visible = mouthOpen > 0.10;
          if (showInside) {
            mouthParts.cavity.scale.set(mouthWidth * 0.92, Math.max(0.06, mouthOpen * 0.95), 1.0);
            mouthParts.tongue.scale.set(mouthWidth * 0.84, Math.max(0.10, mouthOpen * 0.82), 1.0);
            mouthParts.tongue.position.y = -openCm * 0.24;
          }
        }
        for (const m of jawMeshes) {
          if (m.morphTargetInfluences && m.morphTargetInfluences.length > 0) {
            m.morphTargetInfluences[0] = mouthOpen * 0.85;
          }
        }

        // Smooth camera pan & cinematic 3D stage choreography (Aurora / Spotlight / Celebration)
        const fxAge = activeStageFx ? Math.max(0, (performance.now() - stageFxStartedAt) * 0.001) : 999.0;
        const fxEnv = fxAge < 9.5 ? Math.sin(Math.min(1.0, fxAge / 0.85) * (Math.PI * 0.5)) * Math.max(0, 1.0 - Math.max(0, fxAge - 5.5) / 4.0) : 0.0;
        lookX += (cameraTargetX - lookX) * Math.min(1, dt * 5.0);
        const cineArcX = fxEnv * Math.sin(fxAge * 0.85) * 0.14;
        const cineZoomZ = 2.75 - fxEnv * (activeStageFx === 'cinema_spotlight' ? 0.22 : 0.34);
        camera.position.x = lookX * 0.55 + cineArcX;
        camera.position.y = 1.38 + fxEnv * 0.04;
        camera.position.z += (cineZoomZ - camera.position.z) * Math.min(1, dt * 3.8);
        camera.lookAt(lookX, 1.24 + fxEnv * 0.03, 0);
        rimLight.intensity = 1.15 + fxEnv * 1.15 + voiceEnergy * 0.35;

        // --- Natural Humanoid Standing, Breathing, Eye Contact & Conversational Gestures ---
        if (avatarRoot) {
          const breath = Math.sin(t * 1.55) * 0.5 + 0.5;
          const sway = Math.sin(t * 0.43) * 0.65 + Math.sin(t * 0.27 + 1.1) * 0.35;
          const userTalking = (userVoiceState === 'speaking');
          const thinking = (liveVoiceState === 'thinking');

          // Feet stay calmly planted; gentle natural hip weight-shift only (never marching/spinning!)
          avatarRoot.position.x = sway * 0.012;
          avatarRoot.position.y = baseFloorY + breath * 0.0025;
          avatarRoot.rotation.y = sway * 0.032 - lookX * 0.12;

          // Spine breathing & subtle contrapposto counterbalance
          poseBone('Spine02', 0.0, sway * 0.015, -sway * 0.022, smoothPose);
          poseBone('Spine01', -breath * 0.024, sway * 0.018, 0.0, smoothPose);
          poseBone('Spine', -breath * 0.028 - speakActivity * 0.022, speakActivity * Math.sin(t * 1.35) * 0.025, 0.0, smoothPose);

          // Neck & Head: lift rest pose (-27.8 deg) up by -0.17 rad into warm eye contact with viewer
          const listenNod = userTalking ? (Math.sin(t * 3.9) * 0.034 - 0.02) : 0.0;
          const listenTilt = userTalking ? 0.068 : (thinking ? -0.055 : 0.0);
          const thinkYaw = thinking ? 0.085 : 0.0;
          const speakNod = speakActivity * (-mouthOpen * 0.048 + Math.sin(t * 4.8) * 0.022);
          const speakTurn = speakActivity * Math.sin(t * 1.65) * 0.052;
          const idleGazeY = Math.sin(t * 0.52 + 0.4) * 0.035;
          const idleTiltZ = Math.sin(t * 0.37) * 0.022;

          const headYaw = (idleGazeY + speakTurn + thinkYaw) * 0.75;
          poseBone('neck', -0.045 - breath * 0.01, (idleGazeY + speakTurn + thinkYaw) * 0.35, (idleTiltZ + listenTilt) * 0.3, smoothPose);
          poseBone(
            'Head',
            -0.165 + listenNod + speakNod + breath * 0.012,
            headYaw,
            idleTiltZ + listenTilt + speakActivity * Math.sin(t * 1.18) * 0.032,
            smoothPose
          );

          // Lifelike 3D eye-blinking (randomized cadence + double-blinks + gaze-shift blink + attentive squint)
          if (eyeParts) {
            if (Math.abs(headYaw - lastHeadYaw) > 0.058 && (t - blinkStart) > 0.55) {
              blinkStart = t;
              blinkDuration = 0.17;
              nextBlinkAt = t + 2.8 + Math.random() * 2.6;
            }
            lastHeadYaw = headYaw;
            if (t >= nextBlinkAt) {
              blinkStart = t;
              blinkDuration = 0.18 + Math.random() * 0.04;
              if (queuedDoubleBlink) {
                queuedDoubleBlink = false;
                nextBlinkAt = t + 2.7 + Math.random() * 2.9;
              } else if (Math.random() < 0.18) {
                queuedDoubleBlink = true;
                nextBlinkAt = t + 0.24;
              } else {
                nextBlinkAt = t + 2.6 + Math.random() * 3.0;
              }
            }
            let blinkVal = 0.0;
            const elapsedBlink = t - blinkStart;
            if (elapsedBlink >= 0 && elapsedBlink < blinkDuration) {
              const closeFrac = 0.36;
              const p = elapsedBlink / blinkDuration;
              if (p < closeFrac) {
                const u = p / closeFrac;
                blinkVal = u * u * (3 - 2 * u);
              } else {
                const u = 1.0 - (p - closeFrac) / (1.0 - closeFrac);
                blinkVal = u * u;
              }
            }
            const warmSquint = (userTalking || thinking) ? 0.11 : (evaSpeaking ? 0.05 : 0.0);
            const upperClose = Math.min(1.0, Math.max(blinkVal, warmSquint * 0.65));
            const lowerClose = Math.min(1.0, Math.max(blinkVal, warmSquint * 1.35));
            for (const pair of eyeParts.lidMeshes) {
              if (pair.upper.morphTargetInfluences) pair.upper.morphTargetInfluences[0] = upperClose;
              if (pair.lower.morphTargetInfluences) pair.lower.morphTargetInfluences[0] = lowerClose;
            }

            // Living iris micro-saccades (Head local space: 1 unit = 1 cm)
            if (t >= nextSaccadeAt) {
              nextSaccadeAt = t + (thinking ? 0.45 : 0.95) + Math.random() * 1.45;
              const amp = thinking ? 0.34 : 0.22;
              saccadeTargetX = (Math.random() * 2 - 1) * amp - lookX * 0.32;
              saccadeTargetY = (Math.random() * 2 - 1) * (amp * 0.72) + (thinking ? 0.16 : 0.0);
            }
            saccadeX += (saccadeTargetX - saccadeX) * Math.min(1, dt * 16.0);
            saccadeY += (saccadeTargetY - saccadeY) * Math.min(1, dt * 16.0);
            const glintVisible = upperClose < 0.78;
            const visualActive = !!(cameraStream || stageState?.visual_copilot?.active);
            eyeParts.glintMat.color.setHex(
              stageState?.privacy_shield?.active ? 0xfbbf24 : (visualActive ? 0x22d3ee : (evaSpeaking ? 0x34d399 : 0x67e8f9))
            );
            eyeParts.glintMat.opacity = glintVisible ? (0.46 + voiceEnergy * 0.34 + (visualActive ? 0.16 : 0.0)) : 0.0;
            for (const g of eyeParts.glints) {
              g.mesh.visible = glintVisible;
              g.mesh.position.set(saccadeX, saccadeY, 0.0);
            }
          }

          // Relaxed shoulders & arms at sides + expressive alternating forearm/hand gestures + stage greeting wave!
          const gEnv = speakActivity * (0.38 + 0.62 * Math.min(1, voiceEnergy * 1.4));
          const gLeft = Math.max(0, Math.sin(t * 2.15) * 0.65 + Math.sin(t * 1.15 + 0.5) * 0.35) * gEnv;
          const gRight = Math.max(0, Math.sin(t * 1.85 + 1.9) * 0.65 + Math.sin(t * 1.05 + 2.4) * 0.35) * gEnv;
          const waveActive = (fxAge < 4.2 && (activeStageFx === 'aurora_reveal' || activeStageFx === 'wave_greeting' || activeStageFx === 'celebration'))
            ? Math.sin(Math.min(1.0, fxAge / 0.55) * (Math.PI * 0.5)) * Math.max(0, 1.0 - Math.max(0, fxAge - 2.8) / 1.4)
            : 0.0;
          const presentActive = (fxAge < 5.5 && activeStageFx === 'cinema_spotlight')
            ? Math.sin(Math.min(1.0, fxAge / 0.65) * (Math.PI * 0.5)) * Math.max(0, 1.0 - Math.max(0, fxAge - 3.8) / 1.7)
            : 0.0;
          const waveOsc = Math.sin(t * 7.2) * waveActive;

          poseBone('LeftShoulder', 0.0, 0.0, -breath * 0.016, smoothPose);
          poseBone('RightShoulder', 0.0, 0.0, breath * 0.016 + waveActive * 0.08, smoothPose);

          // +localZ on LeftArm / -localZ on RightArm relaxes arms inward toward body; -localX swings slightly forward
          poseBone('LeftArm', -0.03 - gLeft * 0.16, gLeft * 0.08, 0.11 - breath * 0.014 - gLeft * 0.04, smoothPose);
          poseBone(
            'RightArm',
            -0.03 - gRight * 0.16 - waveActive * 0.68 - presentActive * 0.36,
            -gRight * 0.08 - presentActive * 0.24,
            -0.11 + breath * 0.014 + gRight * 0.04 - waveActive * 0.22 - presentActive * 0.18,
            smoothPose
          );

          // -localX bends elbows naturally forward & upward during speech, wave, or spotlight presentation
          poseBone('LeftForeArm', -0.07 - breath * 0.015 - gLeft * 0.42, gLeft * 0.14, 0.02, smoothPose);
          poseBone(
            'RightForeArm',
            -0.07 - breath * 0.015 - gRight * 0.44 - waveActive * 1.12 - presentActive * 0.62,
            -gRight * 0.14 + waveOsc * 0.28,
            -0.02 + waveOsc * 0.22,
            smoothPose
          );

          poseBone('LeftHand', -gLeft * 0.12, gLeft * 0.18, gLeft * 0.15, smoothPose);
          poseBone('RightHand', -gRight * 0.12 - presentActive * 0.18, -gRight * 0.18 + waveOsc * 0.32, -gRight * 0.15 + waveOsc * 0.26, smoothPose);
        }

        // Liquid-Core orbital neural field (`dx/dt` coupled harmonic drift modulated by voice, camera & stage choreography)
        const shieldNow = !!stageState?.privacy_shield?.active;
        const camNow = !!(cameraStream || stageState?.visual_copilot?.active);
        const pArr = neuralField.geo.attributes.position.array;
        const orbitSpeed = 0.22 + voiceEnergy * 0.85 + fxEnv * 1.15 + (liveVoiceState === 'thinking' ? 0.45 : 0.0);
        const radialTighten = camNow ? 0.86 : (liveVoiceState === 'thinking' ? 0.90 : (1.0 + voiceEnergy * 0.14 + fxEnv * 0.22));
        for (let i = 0; i < neuralField.count; i++) {
          const s0 = neuralField.seeds[i * 4];
          const baseR = neuralField.seeds[i * 4 + 1] * radialTighten;
          const baseY = neuralField.seeds[i * 4 + 2];
          const w = neuralField.seeds[i * 4 + 3];
          const theta = s0 + t * orbitSpeed * w + Math.sin(t * 0.7 + s0) * 0.18;
          const rNow = baseR + Math.sin(t * (1.4 * w) + s0 * 2.0) * (0.035 + voiceEnergy * 0.06 + fxEnv * 0.08);
          const yNow = baseY + Math.sin(t * (0.95 * w) + s0) * (0.045 + voiceEnergy * 0.05 + fxEnv * 0.12);
          pArr[i * 3] = Math.cos(theta) * rNow;
          pArr[i * 3 + 1] = yNow;
          pArr[i * 3 + 2] = Math.sin(theta) * rNow - 0.10;
        }
        neuralField.geo.attributes.position.needsUpdate = true;
        const fxColor = activeStageFx === 'celebration' ? 0xfbbf24 : (activeStageFx ? 0x22d3ee : null);
        neuralField.mat.color.setHex(
          shieldNow ? 0xf59e0b : (fxColor || (camNow ? 0x22d3ee : (evaSpeaking || liveSession ? 0x10b981 : 0x38bdf8)))
        );
        neuralField.mat.opacity = (liveSession || camNow || evaSpeaking || fxEnv > 0.05) ? Math.min(0.95, 0.48 + voiceEnergy * 0.34 + fxEnv * 0.36) : 0.32;

        const pulse = 1.0 + (voiceEnergy * 0.30) + fxEnv * 0.34 + Math.sin(t * 2.1) * 0.02;
        halo.scale.setScalar(pulse);
        haloMat.color.setHex(
          shieldNow ? 0xf59e0b : (camNow ? 0x22d3ee : (evaSpeaking || liveSession ? 0x10b981 : 0x38bdf8))
        );
        haloMat.opacity = (liveSession || camNow) ? (0.46 + voiceEnergy * 0.36) : 0.34;
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

    // --- 3. Neural Audio Queue for TV Cast & Streaming Typed Chat ---
    const ttsQueue = [];
    let ttsDraining = false;
    let ttsUnlocked = false;
    let ttsGeneration = 0;
    let activeTtsCancel = null;

    function unlockStageAudio() {
      try {
        ensureAudioContext();
        const el = $('stage-tts-audio');
        if (!el || ttsUnlocked) return;
        ttsUnlocked = true;
        // Tiny 40ms silent WAV unlocks persistent HTMLAudioElement inside user tap gesture on iOS/Android
        el.src = 'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
        el.muted = true;
        const p = el.play();
        if (p && typeof p.then === 'function') {
          p.then(() => {
            el.muted = false;
            if (!ttsSpeaking && el.src && el.src.startsWith('data:')) {
              el.pause();
              el.removeAttribute('src');
            }
          }).catch(() => { el.muted = false; });
        } else {
          el.muted = false;
        }
      } catch (_e) {}
    }

    function stopNeuralSpeechQueue() {
      ttsGeneration += 1;
      for (const it of ttsQueue.splice(0)) {
        if (it?.urlPromise) {
          it.urlPromise.then(u => { if (u) URL.revokeObjectURL(u); }).catch(() => {});
        }
      }
      ttsSpeaking = false;
      const el = $('stage-tts-audio');
      if (el) {
        try { el.pause(); } catch (_e) {}
      }
      if (activeTtsCancel) {
        const cancel = activeTtsCancel;
        activeTtsCancel = null;
        cancel();
      }
    }

    async function fetchSpeechBlobUrl(cleanText) {
      const res = await fetch('/internal/v1/dashboard/holostage/speech-audio', {
        method: 'POST', headers: hdrs, body: JSON.stringify({ text: cleanText })
      });
      if (!res.ok) return null;
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    }

    async function drainNeuralSpeechQueue() {
      if (ttsDraining) return;
      ttsDraining = true;
      try {
        while (ttsQueue.length > 0) {
          const item = ttsQueue.shift();
          const url = await item.urlPromise.catch(() => null);
          if (!url || item.gen !== ttsGeneration) {
            if (url) URL.revokeObjectURL(url);
            continue;
          }
          const el = $('stage-tts-audio') || new Audio();
          el.muted = false;
          el.src = url;
          try {
            if ('preservesPitch' in el) el.preservesPitch = true;
            if ('webkitPreservesPitch' in el) el.webkitPreservesPitch = true;
            el.playbackRate = 1.09;
          } catch (_e) {}
          attachElementAnalyser(el);
          ttsSpeaking = true;
          evaSpeaking = true;
          await new Promise(resolve => {
            let settled = false;
            const done = () => {
              if (settled) return;
              settled = true;
              el.onended = null;
              el.onerror = null;
              el.onpause = null;
              if (activeTtsCancel === done) activeTtsCancel = null;
              URL.revokeObjectURL(url);
              resolve();
            };
            activeTtsCancel = done;
            el.onended = done;
            el.onerror = done;
            el.onpause = done;
            const p = el.play();
            if (p && typeof p.catch === 'function') p.catch(done);
          });
        }
      } finally {
        ttsDraining = false;
        if (ttsQueue.length === 0) {
          ttsSpeaking = false;
          if (!liveSession) evaSpeaking = false;
        }
      }
    }

    function playNeuralSpeech(text, reset = false) {
      const clean = String(text || '')
        .replace(/\\[([^\\]]+)\\]\\(https?:\\/\\/[^)]+\\)/g, '$1')
        .replace(/\\(\\s*https?:\\/\\/[^\\s)]+\\s*\\)/g, '')
        .replace(/https?:\\/\\/\\S+/g, '')
        .replace(/\\*\\*([^*]+)\\*\\*/g, '$1')
        .replace(/\\*(\\S(?:[^*]*?\\S)?)\\*/g, '$1')
        .replace(/\\s+/g, ' ')
        .trim();
      if (!clean) return;
      if (reset) stopNeuralSpeechQueue();
      ttsQueue.push({ gen: ttsGeneration, urlPromise: fetchSpeechBlobUrl(clean) });
      drainNeuralSpeechQueue();
    }

    // --- 4. Real Continuous Full-Duplex LiveKit + Single-Core Eva Voice Session ---
    const VOICE_BASE = '/internal/v1/dashboard/holostage/voice';
    let liveSession = null;
    let liveVoiceState = 'idle';
    let rtcInferredSpeaking = false;
    let userVoiceState = 'idle';
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
      rtcInferredSpeaking = false;
      userVoiceState = 'idle';
      activeRemoteTrack = null;
      evaSpeaking = false;
      disconnectStreamAnalyser();
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

      stopNeuralSpeechQueue();
      ensureAudioContext();
      const attempt = { closedServer: false };
      liveSession = attempt;
      syncVoiceButtons();
      setVoicePill('⏳ Spraakverbinding openen…', true);

      try {
        const room = attempt.room = new window.LivekitClient.Room({ adaptiveStream: true, dynacast: true });
        // Unlock mobile Safari/Chrome WebAudio synchronously inside user tap gesture
        room.startAudio().catch(() => {});

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

        room.on(window.LivekitClient.RoomEvent.TrackSubscribed, track => {
          if (liveSession !== attempt || track.kind !== 'audio') return;
          activeRemoteTrack = track;
          const el = track.attach();
          el.autoplay = true;
          el.playsInline = true;
          $('remote-audio').append(el);
          if (typeof el.play === 'function') el.play().catch(() => {});
          if (track.mediaStreamTrack) attachStreamAnalyser(track.mediaStreamTrack);
        });

        function applyEvaAgentState(rawState, fromRtcInference = false) {
          const mapped = rawState === 'initializing' ? 'listening' : (rawState || 'listening');
          const prev = liveVoiceState;
          liveVoiceState = mapped;
          rtcInferredSpeaking = (mapped === 'speaking' && fromRtcInference);
          if (mapped === 'speaking') userVoiceState = 'listening';
          const labels = {
            listening: '🟢 Eva luistert — praat gewoon door',
            thinking: '💭 Eva denkt even na…',
            speaking: '🗣️ Eva spreekt — je kunt haar gewoon onderbreken'
          };
          setVoicePill(labels[mapped] || '🟢 Gesprek actief', true);
          if ((prev === 'thinking' && mapped === 'speaking') || (prev === 'speaking' && mapped === 'listening')) {
            refreshState();
          }
        }

        room.on(window.LivekitClient.RoomEvent.TranscriptionReceived, (segments, participant) => {
          if (liveSession !== attempt) return;
          const isEva = participant?.identity === 'eva';
          for (const seg of segments) {
            upsertTranscript(seg.id, isEva ? 'eva' : 'user', seg.text);
            if (seg.final && seg.text && !seenFinalSegments.has(seg.id)) {
              seenFinalSegments.add(seg.id);
              if (!isEva) {
                postAction({ action: 'spoken_side_effects', text: seg.text }, true).catch(() => {});
              } else {
                refreshState();
              }
            }
          }
        });

        if (window.LivekitClient.RoomEvent.ParticipantAttributesChanged) {
          room.on(window.LivekitClient.RoomEvent.ParticipantAttributesChanged, (changed, participant) => {
            if (liveSession !== attempt || participant?.identity !== 'eva') return;
            const lkState = changed?.['lk.agent.state'] || participant?.attributes?.['lk.agent.state'];
            if (lkState) applyEvaAgentState(lkState, false);
          });
        }

        if (window.LivekitClient.RoomEvent.ActiveSpeakersChanged) {
          room.on(window.LivekitClient.RoomEvent.ActiveSpeakersChanged, speakers => {
            if (liveSession !== attempt || !Array.isArray(speakers)) return;
            const evaActive = speakers.some(p => p?.identity === 'eva');
            const localActive = speakers.some(p => p?.isLocal || (p?.identity && p.identity === room.localParticipant?.identity));
            userVoiceState = localActive ? 'speaking' : 'listening';
            if (evaActive && liveVoiceState !== 'speaking') {
              applyEvaAgentState('speaking', true);
            } else if (!evaActive && rtcInferredSpeaking && liveVoiceState === 'speaking') {
              applyEvaAgentState('listening', false);
            } else if (localActive && !evaActive && liveVoiceState === 'listening') {
              setVoicePill('👂 Eva luistert naar je…', true);
            } else if (!localActive && !evaActive && liveVoiceState === 'listening') {
              setVoicePill('🟢 Eva luistert — praat gewoon door', true);
            }
          });
        }

        room.on(window.LivekitClient.RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
          if (liveSession !== attempt || topic !== 'eva.work' || participant?.identity !== 'eva') return;
          try {
            const ev = JSON.parse(new TextDecoder().decode(payload));
            if (ev.type === 'voice_state') {
              applyEvaAgentState(ev.state || 'listening');
            } else if (ev.type === 'user_voice_state') {
              userVoiceState = ev.state || 'listening';
              if (userVoiceState === 'speaking') {
                setVoicePill('👂 Eva luistert naar je…', true);
              }
            } else if (ev.type === 'stt_final' || ev.type === 'work_started') {
              if (liveVoiceState !== 'speaking') {
                applyEvaAgentState('thinking');
              }
            } else if (ev.type === 'work_finished') {
              refreshState();
            }
          } catch (_e) {}
        });

        if (window.LivekitClient.RoomEvent.Reconnecting) {
          room.on(window.LivekitClient.RoomEvent.Reconnecting, () => {
            if (liveSession === attempt) setVoicePill('🔄 Verbinding even herstellen…', true);
          });
        }
        if (window.LivekitClient.RoomEvent.Reconnected) {
          room.on(window.LivekitClient.RoomEvent.Reconnected, () => {
            if (liveSession === attempt) setVoicePill('🟢 Verbinding hersteld — praat gerust verder', true);
          });
        }
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
        await room.startAudio().catch(() => {});
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

    let lastCardsSig = '';
    let lastAdminSig = '';

    function renderConjuredCards(st) {
      const overlay = $('conjured-overlay');
      const conjured = Array.isArray(st.cards?.conjured_cards) ? st.cards.conjured_cards : [];

      if (conjured.length === 0) {
        $('btn-clear-cards').hidden = true;
        cameraTargetX = 0.0;
        if (lastCardsSig !== 'empty') {
          lastCardsSig = 'empty';
          overlay.hidden = true;
          overlay.replaceChildren();
        }
        return;
      }

      const cardsSig = JSON.stringify({ c: conjured, d: st.cards, v: st.viewer?.kind, wide: window.innerWidth >= 860 });
      if (cardsSig === lastCardsSig) return;
      lastCardsSig = cardsSig;

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
            const itemsArr = sp.items || [];
            const isLeaderboard = sp.theme === 'leaderboard' || (
              itemsArr.length >= 2 && itemsArr.filter(x => /^\\s*#?\\d+[.):\\s-]/.test(String(x))).length >= 2
            );
            let listHtml = '';
            if (itemsArr.length) {
              if (isLeaderboard) {
                listHtml = '<ul class="lb-list">' + itemsArr.map((rawRow, idx) => {
                  const sRow = String(rawRow).trim();
                  const mRank = sRow.match(/^\\s*#?(\\d+)[.):\\s-]+\\s*(.*)$/);
                  const rankNum = mRank ? parseInt(mRank[1], 10) : (idx + 1);
                  const rowText = mRank ? mRank[2] : sRow;
                  const posCls = rankNum <= 3 ? `pos-${rankNum}` : '';
                  return `<li class="lb-row ${posCls}" style="animation-delay:${idx * 55}ms">
                    <span class="lb-rank">#${rankNum}</span>
                    <span style="flex:1">${esc(rowText)}</span>
                  </li>`;
                }).join('') + '</ul>';
              } else {
                listHtml = '<ul style="margin:8px 0 0 18px;padding:0">' + itemsArr.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>';
              }
            }
            const themeCls = isLeaderboard ? 'theme-leaderboard' : '';
            const icon = isLeaderboard ? '🏆' : (sp.theme === 'climate' ? '🌡️' : '✨');
            cardsHtml.push(`
              <article class="holo-card ${themeCls}">
                <div class="holo-card-head">
                  <h2>${icon} ${esc(sp.title)}</h2>
                  ${currentMode === 'tv_cast' ? '' : `<button type="button" class="btn-sm" data-dismiss-spot="${esc(sp.card_id)}">Sluit ✕</button>`}
                </div>
                <div style="font-size:0.92rem;line-height:1.48">${esc(sp.body_nl)}${listHtml}</div>
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
      overlay.hidden = false;
      $('btn-clear-cards').hidden = false;
      cameraTargetX = window.innerWidth >= 860 ? 0.42 : 0.0;
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

      const adminSig = JSON.stringify({
        c: st.cards?.home_overview?.cast_targets,
        v: st.biometrics_admin?.enrolled_members || st.speaker_biometrics?.enrolled_members,
        p: st.public_stage_origin,
        o: st.household_observation_summary
      });
      if (adminSig === lastAdminSig) return;
      lastAdminSig = adminSig;

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

      const obsBox = $('obs-summary-box');
      if (obsBox) {
        const pubOrigin = st.public_stage_origin || '';
        const obs = st.household_observation_summary;
        const lines = [];
        lines.push(pubOrigin
          ? `🔒 Publieke HTTPS: ${pubOrigin}`
          : '🏠 Alleen thuisnetwerk (nog geen publieke HTTPS ingesteld)');
        if (obs && obs.release_window) {
          const w = obs.release_window;
          const t = obs.totals || {};
          lines.push(
            `⏱️ Venster: ${w.contiguous_hours ?? 0}/${w.required_contiguous_hours ?? 168}u · ` +
            `${w.distinct_active_days ?? 0}/${w.required_active_days ?? 7} actieve dagen · ` +
            `kritieke fouten: ${w.unresolved_critical_errors ?? 0}`
          );
          lines.push(
            `👨‍👩‍👧 Gebruik: Jaap ${t.turns_jaap ?? 0} · Lola ${t.turns_lola ?? 0} · gedeeld ${t.turns_shared_household ?? 0}`
          );
          lines.push(w.f2_2_owner_accepted
            ? '✅ Eigenaarsacceptatie (F2.2) geregistreerd'
            : (w.seven_day_gate_met ? '🟢 Technisch 7d-doel bereikt — klaar voor acceptatie' : '🔵 Veilige gezinsobservatie actief'));
        }
        obsBox.innerHTML = lines.map(l => `<div style="padding:2px 0">${esc(l)}</div>`).join('');
      }
    }

    function closeCameraPip(clearServerContext = false) {
      cameraOpenSeq += 1;
      const pip = $('camera-pip');
      const vid = $('camera-video');
      const btn = $('btn-camera-toggle');
      if (cameraStream) {
        for (const tr of cameraStream.getTracks()) {
          try { tr.stop(); } catch (_e) {}
        }
        cameraStream = null;
      }
      if (vid) vid.srcObject = null;
      if (pip) pip.hidden = true;
      if (btn) btn.classList.remove('cam-active');
      if (clearServerContext) {
        postAction({ action: 'clear_visual_context' }, true).catch(() => {});
      }
    }

    function renderState(st) {
      stageState = st;
      const sh = st.privacy_shield || {};
      $('shield-pill').hidden = !sh.active;
      if (sh.active) {
        closeCameraPip(false);
      }

      const canCam = !!(st.viewer?.can_use_camera) && currentMode !== 'tv_cast' && !sh.active;
      const camBtn = $('btn-camera-toggle');
      if (camBtn) camBtn.hidden = !canCam;

      const vis = st.visual_copilot || {};
      const visPill = $('vision-pill');
      if (visPill) {
        if (vis.active && !sh.active) {
          const rem = typeof vis.remaining_seconds === 'number' ? ` (${vis.remaining_seconds}s)` : '';
          visPill.textContent = `👁️ Eva ziet je beeld${rem} ✕`;
          visPill.hidden = false;
        } else {
          visPill.hidden = true;
        }
      }

      renderConjuredCards(st);
      renderAdminDrawer(st);

      const anim = st.stage_animation;
      if (anim && anim.animation_id && anim.animation_id !== lastStageAnimationId) {
        lastStageAnimationId = anim.animation_id;
        triggerStageChoreography(anim.effect || 'aurora_reveal', 11500);
      }

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

      // Stop any previous speech first, then unlock persistent audio element inside submit gesture
      stopNeuralSpeechQueue();
      unlockStageAudio();

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
        let queuedClauses = 0;
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
            } else if (msg.type === 'speech_clause' && msg.text) {
              queuedClauses += 1;
              playNeuralSpeech(msg.text, false);
            } else if (msg.type === 'stage_state' && msg.stage) {
              renderState(msg.stage);
            } else if (msg.type === 'final') {
              if (msg.shield_interrupted) {
                stopNeuralSpeechQueue();
              }
              finalReply = msg.text || acc;
              upsertTranscript(turnId + ':e', 'eva', finalReply);
              if (msg.stage) renderState(msg.stage);
            }
          }
        }
        if (finalReply && queuedClauses === 0) playNeuralSpeech(finalReply, true);
      } catch (_e) {}
    };

    // --- 6B. Live Multimodal Camera Co-Pilot ("Eva Ziet Echt Mee", Zero-Retention) ---
    async function openCameraPip(facing = cameraFacingMode) {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setVoicePill('⚠️ Camera vereist een beveiligde HTTPS-link (Tailscale Funnel) of localhost', !!liveSession);
        return;
      }
      if (document.hidden || stageState?.privacy_shield?.active) {
        if (stageState?.privacy_shield?.active) {
          setVoicePill('🛡️ Privéschild is actief — camera blijft gesloten', !!liveSession);
        }
        return;
      }
      closeCameraPip(false);
      const mySeq = ++cameraOpenSeq;
      cameraFacingMode = facing;
      let stream = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 960 } },
          audio: false
        });
        if (mySeq !== cameraOpenSeq || document.hidden || stageState?.privacy_shield?.active) {
          for (const tr of stream.getTracks()) {
            try { tr.stop(); } catch (_e) {}
          }
          return;
        }
        cameraStream = stream;
        const vid = $('camera-video');
        vid.srcObject = stream;
        await vid.play().catch(() => {});
        if (mySeq !== cameraOpenSeq || document.hidden || stageState?.privacy_shield?.active) {
          closeCameraPip(false);
          return;
        }
        $('camera-pip').hidden = false;
        $('btn-camera-toggle').classList.add('cam-active');
      } catch (_e) {
        if (stream) {
          for (const tr of stream.getTracks()) {
            try { tr.stop(); } catch (_e) {}
          }
        }
        if (mySeq === cameraOpenSeq) {
          closeCameraPip(false);
          setVoicePill('⚠️ Kon camera niet openen — controleer toestemming van je browser', !!liveSession);
        }
      }
    }

    function captureCameraJpegBase64() {
      const vid = $('camera-video');
      if (!vid || !vid.videoWidth || !vid.videoHeight) return '';
      const maxDim = 1024;
      const scale = Math.min(1.0, maxDim / Math.max(vid.videoWidth, vid.videoHeight));
      const w = Math.max(64, Math.round(vid.videoWidth * scale));
      const h = Math.max(64, Math.round(vid.videoHeight * scale));
      const snapCanvas = document.createElement('canvas');
      snapCanvas.width = w;
      snapCanvas.height = h;
      const ctx = snapCanvas.getContext('2d');
      if (!ctx) return '';
      ctx.drawImage(vid, 0, 0, w, h);
      const dataUrl = snapCanvas.toDataURL('image/jpeg', 0.82);
      const comma = dataUrl.indexOf(',');
      return comma >= 0 ? dataUrl.slice(comma + 1) : '';
    }

    async function snapVisionFrame() {
      if (visionSnapBusy || !cameraStream) return;
      const image_b64 = captureCameraJpegBase64();
      if (!image_b64) {
        setVoicePill('⏳ Camera stelt nog scherp — probeer nog eens', !!liveSession);
        return;
      }
      visionSnapBusy = true;
      const snapBtn = $('btn-camera-snap');
      const flashEl = $('camera-flash');
      snapBtn.disabled = true;
      snapBtn.textContent = '👁️ Eva kijkt mee…';
      if (flashEl) {
        flashEl.classList.add('flash-on');
        setTimeout(() => flashEl.classList.remove('flash-on'), 210);
      }

      const inp = $('chat-input');
      const typedPrompt = inp.value.trim();
      if (typedPrompt) inp.value = '';
      const inLiveVoice = !!(liveSession?.room);
      if (!inLiveVoice) {
        stopNeuralSpeechQueue();
        unlockStageAudio();
      }

      try {
        const res = await fetch('/internal/v1/dashboard/holostage/vision-turn', {
          method: 'POST',
          headers: hdrs,
          body: JSON.stringify({
            image_b64,
            media_type: 'image/jpeg',
            text: typedPrompt,
            mode: currentMode,
            source_label: currentMode === 'pocket' ? 'Eva Pocket camera' : 'HoloStage camera',
            context_only: inLiveVoice
          })
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          setVoicePill('⚠️ ' + (errData.detail || 'Kon camerabeeld niet analyseren'), !!liveSession);
          return;
        }

        if (inLiveVoice) {
          const data = await res.json();
          if (data.stage) renderState(data.stage);
          const askText = typedPrompt || 'Kijk even mee naar wat ik je nu via mijn camera laat zien en reageer kort en behulpzaam.';
          upsertTranscript('cam-' + Date.now(), 'user', typedPrompt ? `📷 ${typedPrompt}` : '📷 [Camerabeeld getoond]');
          await liveSession.room.localParticipant.sendText(askText, { topic: 'lk.chat' });
          return;
        }

        if (!res.body) return;
        const turnId = 'vis-' + Date.now();
        upsertTranscript(turnId + ':u', 'user', typedPrompt ? `📷 ${typedPrompt}` : '📷 [Camerabeeld getoond]');
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let acc = '';
        let finalReply = '';
        let queuedClauses = 0;
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split('\\n');
          buf = lines.pop() || '';
          for (const ln of lines) {
            if (!ln.trim()) continue;
            const msg = JSON.parse(ln);
            if (msg.type === 'vision_ack' && msg.stage) {
              renderState(msg.stage);
            } else if (msg.type === 'stage_delta') {
              acc += msg.text;
              upsertTranscript(turnId + ':e', 'eva', acc);
            } else if (msg.type === 'speech_clause' && msg.text) {
              queuedClauses += 1;
              playNeuralSpeech(msg.text, false);
            } else if (msg.type === 'final') {
              if (msg.shield_interrupted) stopNeuralSpeechQueue();
              finalReply = msg.text || acc;
              upsertTranscript(turnId + ':e', 'eva', finalReply);
              if (msg.stage) renderState(msg.stage);
            }
          }
        }
        if (finalReply && queuedClauses === 0) playNeuralSpeech(finalReply, true);
      } catch (_e) {
        setVoicePill('⚠️ Camerabeeld kon niet worden verwerkt', !!liveSession);
      } finally {
        visionSnapBusy = false;
        snapBtn.disabled = false;
        snapBtn.textContent = '👁️ Toon aan Eva';
      }
    }

    $('btn-camera-toggle').onclick = () => {
      if (cameraStream) closeCameraPip(false);
      else openCameraPip(cameraFacingMode);
    };
    $('btn-camera-close').onclick = () => closeCameraPip(false);
    $('btn-camera-flip').onclick = () => {
      const nextFacing = cameraFacingMode === 'environment' ? 'user' : 'environment';
      openCameraPip(nextFacing);
    };
    $('btn-camera-snap').onclick = () => snapVisionFrame();
    $('vision-pill').onclick = () => postAction({ action: 'clear_visual_context' });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && cameraStream) closeCameraPip(false);
    });
    window.addEventListener('pagehide', () => {
      if (cameraStream) closeCameraPip(false);
    });

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
    setInterval(() => {
      if (currentMode !== 'tv_cast' && (evaSpeaking || liveVoiceState === 'speaking' || liveVoiceState === 'thinking')) {
        return;
      }
      refreshState();
    }, currentMode === 'tv_cast' ? 2500 : 8000);
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
    async def holostage_state(request: Request, mode: str = "cockpit") -> JSONResponse:
        mgr = _resolve_holostage_manager(runtime)
        req_mode = _parse_mode(mode)
        viewer = _authenticate_stage_viewer(
            request, access=access, runtime=runtime, mgr=mgr, requested_mode=req_mode
        )
        can_include_obs = viewer.kind == "owner" and _has_explicit_owner_authority(access, runtime)

        def _build_sync() -> dict[str, Any]:
            payload = mgr.build_stage_state(
                viewer_kind=viewer.kind,
                viewer_label=viewer.label,
                form_factor=req_mode,
                allow_owner_voice=viewer.allow_owner_voice,
            )
            if can_include_obs:
                obs_ledger = getattr(runtime, "_household_observation", None)
                if obs_ledger is not None:
                    try:
                        snap = obs_ledger.snapshot(include_external_probe=False)
                        metrics = snap.get("symbiotic_metrics") or {}
                        rel_win = snap.get("release_window") or {}
                        payload["household_observation_summary"] = {
                            "release_window": rel_win,
                            "owner_accepted": bool(rel_win.get("f2_2_owner_accepted", False)),
                            "totals": {
                                "turns_jaap": int(metrics.get("turns_jaap", 0)),
                                "turns_lola": int(metrics.get("turns_lola", 0)),
                                "turns_shared_household": int(metrics.get("turns_shared_household", 0)),
                            },
                        }
                    except Exception:
                        payload["household_observation_summary"] = None
            return payload

        body = await asyncio.to_thread(_build_sync)
        return JSONResponse(
            body,
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
                    allow_owner_voice=viewer.allow_owner_voice,
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
                    theme=str(payload.get("theme") or "spotlight"),
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

            if action == "clear_visual_context":
                if not (viewer.kind == "owner" or viewer.allow_owner_voice):
                    raise PermissionError("Alleen het persoonlijke apparaat van het gezinshoofd beheert cameracontext")
                mgr.clear_ephemeral_visual_context()
                return _done("👁️ Tijdelijke cameracontext gewist.")

            if action == "set_privacy_shield":
                locked_flag = bool(payload.get("locked", False))
                mgr.biometrics.set_manual_privacy_shield(locked_flag)
                if locked_flag:
                    mgr.clear_ephemeral_visual_context()
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
                                allow_owner_voice=viewer.allow_owner_voice,
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
                                allow_owner_voice=viewer.allow_owner_voice,
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
                                allow_owner_voice=viewer.allow_owner_voice,
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
                    allow_owner_voice=viewer.allow_owner_voice,
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
                    allow_owner_voice=viewer.allow_owner_voice,
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

    @app.post("/internal/v1/dashboard/holostage/vision-turn", response_model=None)
    async def holostage_vision_turn(request: Request) -> Response:
        """Fase 4B(3) — Zero-retention camera co-pilot (`Eva Ziet Echt Mee`) grounded in Eva's Single Core."""
        mgr = _resolve_holostage_manager(runtime)
        viewer = _authenticate_stage_viewer(request, access=access, runtime=runtime, mgr=mgr)
        _enforce_post_csrf_and_framing(request, used_cookie=viewer.used_cookie)

        if (
            not (viewer.kind == "owner" or viewer.allow_owner_voice)
            or viewer.form_factor == "tv_cast"
            or not _has_explicit_owner_authority(access, runtime)
        ):
            raise HTTPException(
                status_code=403,
                detail="Live camerameekijken is alleen toegestaan op het persoonlijke Eva Pocket-apparaat of de Cockpit van Jaap",
            )

        payload = await read_small_json_request(request, 2_097_152, "holostage_vision_turn")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Ongeldig verzoek")

        req_mode = _parse_mode(str(payload.get("mode") or viewer.form_factor), viewer.form_factor)
        if req_mode == "tv_cast":
            raise HTTPException(status_code=403, detail="Gedeelde tv-schermen ondersteunen geen camerabeeld")

        shield_active, _ = await asyncio.to_thread(mgr.biometrics.is_privacy_shield_active)
        if shield_active:
            await asyncio.to_thread(mgr.clear_ephemeral_visual_context)
            raise HTTPException(
                status_code=403,
                detail="Privéschild is actief: camerabeeld wordt niet verwerkt zolang er een omstander aanwezig is",
            )

        raw_b64 = payload.get("image_b64")
        if not isinstance(raw_b64, str) or not 64 <= len(raw_b64.strip()) <= 1_750_000:
            raise HTTPException(status_code=422, detail="Ongeldig of te groot camerabeeld")
        try:
            frame_bytes = base64.b64decode(raw_b64.strip(), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Ongeldige base64-afbeelding") from exc
        if not 64 <= len(frame_bytes) <= 1_250_000:
            raise HTTPException(status_code=422, detail="Camerabeeld valt buiten de toegestane grootte (max 1.25 MB)")

        media_type = str(payload.get("media_type") or "image/jpeg").strip().lower()
        if media_type not in ("image/jpeg", "image/png"):
            raise HTTPException(status_code=422, detail="Alleen JPEG- en PNG-camerabeelden worden ondersteund")

        user_text = str(payload.get("text") or "").strip()[:2000]
        source_label = str(payload.get("source_label") or "Eva Pocket camera").strip()[:64] or "Eva Pocket camera"
        context_only = payload.get("context_only") is True

        vision_bridge = getattr(runtime, "_holostage_vision", None)
        if vision_bridge is None or not getattr(vision_bridge, "available", False):
            raise HTTPException(status_code=503, detail="Visuele waarneming is niet geconfigureerd")

        try:
            receipt = await asyncio.to_thread(
                vision_bridge.analyze_frame,
                frame_bytes,
                media_type=media_type,
                focus_hint=user_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except RuntimeError as exc:
            safe_detail = getattr(exc, "detail_nl", None)
            raise HTTPException(
                status_code=502,
                detail=(
                    str(safe_detail)
                    if isinstance(safe_detail, str) and safe_detail.strip()
                    else "Beeldanalyse tijdelijk niet beschikbaar."
                ),
            ) from None
        finally:
            frame_bytes = b""

        def _store_and_check_sync() -> tuple[dict[str, Any] | None, list[str], float, dict[str, Any]]:
            if mgr.biometrics.is_privacy_shield_active()[0]:
                mgr.clear_ephemeral_visual_context()
                return None, [], 0.0, {}
            try:
                vis_ctx = mgr.record_ephemeral_visual_context(
                    summary_nl=receipt.summary_nl,
                    source_label=source_label,
                    image_sha256=receipt.image_sha256,
                    ttl_seconds=90,
                )
            except PermissionError:
                mgr.clear_ephemeral_visual_context()
                return None, [], 0.0, {}
            t_fx = time.monotonic()
            effects = (
                mgr.apply_spoken_stage_side_effects(
                    user_text,
                    speaker_name=viewer.label,
                    allow_private=True,
                )
                if user_text
                else []
            )
            fx_ms = (time.monotonic() - t_fx) * 1000.0
            st = mgr.build_stage_state(
                viewer_kind=viewer.kind,
                viewer_label=viewer.label,
                form_factor=req_mode,
                allow_owner_voice=viewer.allow_owner_voice,
            )
            return vis_ctx, effects, fx_ms, st

        vis_ctx, side_effects, reflex_ms, initial_stage = await asyncio.to_thread(_store_and_check_sync)
        if vis_ctx is None:
            raise HTTPException(
                status_code=403,
                detail="Privéschild geactiveerd tijdens camerawaarneming — beeldcontext direct gewist",
            )

        obs_ledger = getattr(runtime, "_household_observation", None)
        if obs_ledger is not None:
            def _record_vis_obs() -> None:
                try:
                    if side_effects:
                        obs_ledger.record_reflex_hit(latency_ms=reflex_ms)
                    obs_ledger.record_turn(
                        principal_id="owner-jaap",
                        channel="holostage",
                        used_model=True,
                    )
                except Exception:
                    pass

            await asyncio.to_thread(_record_vis_obs)

        if context_only:
            return JSONResponse(
                {
                    "contract": "EvaHoloStageVisionTurn/v1",
                    "context_only": True,
                    "observation_id": vis_ctx["observation_id"],
                    "expires_at": vis_ctx["expires_at"],
                    "vision_backend": receipt.backend,
                    "vision_latency_ms": receipt.latency_ms,
                    "stage": initial_stage,
                },
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

        if not hasattr(runtime, "dashboard_turn"):
            raise HTTPException(status_code=503, detail="Eigenaarsgesprek is niet beschikbaar")

        turn_prompt = (
            user_text
            if user_text
            else "Kijk even mee naar wat ik je nu via mijn camera laat zien en reageer kort en behulpzaam."
        )
        queue: SimpleQueue[dict[str, Any] | None] = SimpleQueue()

        def _produce_vision_stream() -> None:
            t_start = time.monotonic()
            chunker = SentenceBoundaryChunker()
            turn: ConversationTurn | None = None
            stage_name = "respond"
            shield_tripped_midstream = False
            try:
                queue.put(
                    {
                        "type": "vision_ack",
                        "observation_id": vis_ctx["observation_id"],
                        "expires_at": vis_ctx["expires_at"],
                        "vision_backend": receipt.backend,
                        "vision_latency_ms": receipt.latency_ms,
                        "effects": side_effects,
                        "stage": initial_stage,
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
                    turn_res, reply_res = runtime.dashboard_turn(turn_prompt)
                if not isinstance(turn_res, ConversationTurn) or not isinstance(reply_res, GroundedReply):
                    raise ValueError("owner vision conversation result is invalid")
                turn = turn_res

                if shield_tripped_midstream or mgr.biometrics.is_privacy_shield_active()[0]:
                    chunker.flush()
                    mgr.clear_ephemeral_visual_context()
                    shield_notice = (
                        "Het privéschild is zojuist geactiveerd doordat een omstander in beeld of gehoor kwam. "
                        "Het antwoord en de cameracontext zijn direct afgeschermd."
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
                                allow_owner_voice=viewer.allow_owner_voice,
                            ),
                        }
                    )
                else:
                    mgr.record_stage_utterance(
                        eva_text=reply_res.text,
                        speaker_text=user_text or "[Camerabeeld getoond]",
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
                                allow_owner_voice=viewer.allow_owner_voice,
                            ),
                        }
                    )
                stage_name = "after_response"
                runtime.after_response(turn_res, reply_res)
            except Exception as exc:
                if stage_name == "respond":
                    queue.put({"type": "error", "code": "holostage_vision_turn_failed"})
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

        def _ndjson_vision_iter() -> Iterator[bytes]:
            Thread(target=_produce_vision_stream, name="eva-holostage-vision-stream", daemon=True).start()
            while True:
                item = queue.get()
                if item is None:
                    return
                yield json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"

        return StreamingResponse(
            _ndjson_vision_iter(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )


__all__ = ["HOLOSTAGE_HTML", "install_dashboard_holostage"]
