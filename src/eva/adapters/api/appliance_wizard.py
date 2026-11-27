"""User-friendly, LAN/loopback-gated wizard API & UI for Eva's character, modules, and appliance setup."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)

from eva.adapters.api.immersive_stage_html import (
    IMMERSIVE_STAGE_HTML,
    MEMBER_WELCOME_HTML,
)
from eva.application.appliance_setup import ApplianceSetupPort


_SESSION_COOKIE = "eva_appliance_session"
_WELCOME_COOKIE = "eva_welcome_stage"
_CORTEX_STATIC_DIR = Path(__file__).resolve().parent / "cortex_static"
_STAGE_ASSETS: dict[str, str] = {
    "eva.glb": "model/gltf-binary",
    "three.module.min.js": "application/javascript",
    "three.core.min.js": "application/javascript",
    "GLTFLoader.js": "application/javascript",
    "BufferGeometryUtils.js": "application/javascript",
}
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; connect-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_STAGE_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self' data: blob:; "
        "worker-src 'self' blob:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_PAIR_HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eva · Koppelen</title>
<style>
  :root{color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#07111c;color:#edf6ff;padding:1.5rem}
  main{max-width:460px;width:100%;background:#102337;border:1px solid #2b4a66;border-radius:20px;padding:2rem;box-shadow:0 24px 60px rgba(0,0,0,.45)}
  .badge{display:inline-block;padding:.25rem .65rem;border-radius:999px;background:#163854;color:#8ad0ff;font-size:.8rem;font-weight:600;margin-bottom:.75rem}
  h1{margin:.2rem 0 .6rem;font-size:1.55rem}
  p{color:#b7cde0;line-height:1.5}
  label{display:block;margin-top:1.25rem;font-weight:600;font-size:.92rem}
  input{box-sizing:border-box;width:100%;margin-top:.45rem;padding:.78rem .95rem;border-radius:10px;border:1px solid #486b8a;background:#081523;color:#fff;font-size:1rem;letter-spacing:.04em}
  button{width:100%;margin-top:1rem;padding:.82rem 1rem;border:0;border-radius:10px;background:#2ea877;color:#041b12;font-weight:700;font-size:1rem;cursor:pointer}
  button:hover{background:#3bc08a}
  #pair-msg{margin-top:.9rem;min-height:1.3rem;color:#ffca78;font-size:.92rem}
</style>
</head>
<body>
<main>
  <span class="badge">EVA · INSTALLER &amp; BEHEER</span>
  <h1>Koppel met je Eva-installatie</h1>
  <p>Alleen apparaten op jouw eigen thuisnetwerk mogen verbinden. Gebruik de eenmalige koppel-link of vul je koppelcode in.</p>
  <form id="pair-form">
    <label for="pair-code">Koppelcode</label>
    <input id="pair-code" autocomplete="off" spellcheck="false" placeholder="EVA-XXXX-XXXX-XXXX" required>
    <button type="submit">Open Eva Wizard</button>
  </form>
  <button id="pair-local-btn" type="button" hidden style="margin-top:.65rem;background:#1d4ed8;color:#eff6ff">Direct openen op deze pc</button>
  <p id="pair-msg" role="status"></p>
</main>
<script>
(() => {
  const msg = document.getElementById('pair-msg');
  const input = document.getElementById('pair-code');
  const form = document.getElementById('pair-form');
  const localBtn = document.getElementById('pair-local-btn');
  async function tryLocalLoopbackPair() {
    msg.textContent = 'Lokale pc herkend — Eva direct openen…';
    const res = await fetch('/internal/v1/appliance/pair-local', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: '{}'
    });
    if (res.ok) {
      location.replace('/');
      return true;
    }
    return false;
  }
  async function submitCode(code) {
    msg.textContent = 'Koppelcode controleren…';
    const res = await fetch('/internal/v1/appliance/pair', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code})
    });
    if (res.ok) {
      location.replace('/');
      return;
    }
    let detail = 'Koppelcode geweigerd.';
    try { const data = await res.json(); if (data.detail) detail = data.detail; } catch {}
    msg.textContent = detail;
  }
  const hashCode = decodeURIComponent(location.hash.slice(1)).trim();
  const isLoopbackHost = location.hostname === '127.0.0.1' || location.hostname === 'localhost' || location.hostname === '[::1]';
  if (isLoopbackHost) {
    localBtn.hidden = false;
    localBtn.addEventListener('click', () => { tryLocalLoopbackPair().catch(() => {}); });
  }
  if (hashCode) {
    history.replaceState(null, '', '/pair');
    input.value = hashCode;
    submitCode(hashCode).catch(() => { msg.textContent = 'Verbinding met Eva mislukt.'; });
  } else if (isLoopbackHost) {
    tryLocalLoopbackPair().catch(() => { msg.textContent = 'Klik op "Direct openen op deze pc" of vul je koppelcode in.'; });
  }
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    submitCode(input.value.trim()).catch(() => { msg.textContent = 'Verbinding met Eva mislukt.'; });
  });
})();
</script>
</body>
</html>"""


_APP_HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eva · Instellen &amp; Beheren</title>
<style>
  :root{color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;--bg:#07111c;--card:#0f2033;--card2:#142a42;--border:#2b4a66;--text:#edf6ff;--muted:#9fb9d0;--accent:#36be86;--accentText:#042014;--warn:#ffca78;--err:#ff8e8e}
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(circle at top right,#102b46,var(--bg) 55%);color:var(--text);min-height:100vh}
  header{border-bottom:1px solid var(--border);background:rgba(9,20,34,.92);backdrop-filter:blur(8px);position:sticky;top:0;z-index:10}
  .topbar{max-width:1040px;margin:0 auto;padding:.9rem 1.25rem;display:flex;flex-wrap:wrap;gap:.8rem;align-items:center;justify-content:space-between}
  .brand{display:flex;align-items:center;gap:.65rem;font-weight:700;font-size:1.1rem}
  .dot{width:11px;height:11px;border-radius:50%;background:var(--warn)}
  .dot.ok{background:var(--accent);box-shadow:0 0 12px var(--accent)}
  .pills{display:flex;flex-wrap:wrap;gap:.45rem;align-items:center}
  .pill{font-size:.82rem;padding:.3rem .65rem;border-radius:999px;background:#16314c;border:1px solid var(--border);color:var(--muted)}
  .pill.ok{color:#9ff7cf;border-color:#2c7a5b;background:#0e3326}
  .btn-link{display:inline-flex;align-items:center;gap:.35rem;text-decoration:none;font-size:.84rem;font-weight:700;padding:.4rem .8rem;border-radius:9px;background:var(--accent);color:var(--accentText)}
  .btn-link.alt{background:#183754;color:#edf6ff;border:1px solid #3c6790}
  main{max-width:1080px;margin:0 auto;padding:1.4rem 1.25rem 3rem}
  .tabs{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:.5rem;margin-bottom:1.35rem}
  @media(max-width:920px){.tabs{grid-template-columns:repeat(3,1fr)}}
  @media(max-width:580px){.tabs{grid-template-columns:repeat(2,1fr)}}
  .tab{padding:.75rem .7rem;border-radius:12px;border:1px solid var(--border);background:var(--card);color:var(--muted);font-weight:600;font-size:.9rem;cursor:pointer;text-align:left}
  .tab.active{background:#183754;border-color:#5ea8e6;color:#fff}
  .tab small{display:block;font-weight:400;font-size:.76rem;color:var(--muted);margin-top:.15rem}
  .panel{display:none;background:var(--card);border:1px solid var(--border);border-radius:18px;padding:1.5rem}
  .panel.active{display:block}
  h2{margin:0 0 .35rem;font-size:1.35rem}
  .lead{margin:0 0 1.2rem;color:var(--muted);line-height:1.5}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:1.1rem}
  @media(max-width:780px){.grid2{grid-template-columns:1fr}}
  .field{margin-bottom:.95rem}
  .field label{display:block;font-weight:600;font-size:.9rem;margin-bottom:.35rem}
  .field input,.field select,.field textarea{width:100%;padding:.68rem .82rem;border-radius:10px;border:1px solid #3c5f80;background:#081523;color:#fff;font:inherit}
  .field textarea{min-height:84px;resize:vertical}
  .presets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem;margin-bottom:1.1rem}
  @media(max-width:650px){.presets{grid-template-columns:1fr}}
  .preset{padding:.85rem;border-radius:12px;border:1px solid var(--border);background:var(--card2);cursor:pointer;text-align:left;color:var(--text)}
  .preset.selected{border-color:var(--accent);background:#10322b}
  .preset strong{display:block;margin-bottom:.25rem}
  .preset span{font-size:.84rem;color:var(--muted);line-height:1.35;display:block}
  .preview-box{background:#091726;border:1px solid #2e5273;border-radius:14px;padding:1.1rem;height:100%}
  .bubble{padding:.75rem .95rem;border-radius:12px;margin:.55rem 0;line-height:1.45;font-size:.93rem}
  .bubble.user{background:#193552;margin-left:1.5rem}
  .bubble.eva{background:#12372c;border:1px solid #266c55;margin-right:1.5rem}
  .module-card{border:1px solid var(--border);background:var(--card2);border-radius:14px;padding:1rem 1.1rem;margin-bottom:.85rem}
  .module-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem}
  .switch-wrap{display:flex;align-items:center;gap:.5rem;font-weight:600;white-space:nowrap}
  .module-body{margin-top:.9rem;padding-top:.9rem;border-top:1px solid #294661;display:none}
  .module-card.enabled .module-body{display:block}
  .btn-row{display:flex;flex-wrap:wrap;gap:.65rem;margin-top:1.1rem;align-items:center}
  button.primary{padding:.72rem 1.2rem;border:0;border-radius:10px;background:var(--accent);color:var(--accentText);font-weight:700;font-size:.95rem;cursor:pointer}
  button.secondary{padding:.68rem 1rem;border:1px solid #4d7599;border-radius:10px;background:#132a42;color:#edf6ff;font-weight:600;font-size:.9rem;cursor:pointer}
  button:disabled{opacity:.45;cursor:not-allowed}
  .status-msg{font-size:.9rem;color:#9ff7cf;min-height:1.2rem;margin-top:.55rem}
  .status-msg.err{color:var(--err)}
  .device-box{background:#091726;border:1px dashed #4da5e8;border-radius:12px;padding:.95rem;margin-top:.75rem}
  .code-big{font:700 1.4rem ui-monospace,monospace;letter-spacing:.12em;color:#8de8ff;margin:.35rem 0}
  .step-list{list-style:none;padding:0;margin:.8rem 0}
  .step-list li{padding:.5rem .7rem;border-radius:8px;background:#0a1929;margin-bottom:.4rem;border:1px solid #233f59;font-size:.9rem}
  .muted{color:var(--muted);font-size:.88rem}
  .qr-box{display:grid;grid-template-columns:196px 1fr;gap:1.1rem;align-items:center;margin-top:.85rem;padding:1rem;border-radius:14px;background:#081625;border:1px solid #2f5578}
  @media(max-width:680px){.qr-box{grid-template-columns:1fr;justify-items:center}}
  .qr-box img{width:184px;height:184px;border-radius:10px;background:#fff;padding:6px;display:block}
  .chip-row{display:flex;flex-wrap:wrap;gap:.45rem;margin:.55rem 0}
  .chip{border:1px solid #355b7d;background:#112941;color:#d6ebff;border-radius:999px;padding:.35rem .75rem;font-size:.84rem;cursor:pointer}
  .chip:hover{border-color:#69b8f7;background:#183a5b}
  .chat-live{background:#071320;border:1px solid #264663;border-radius:14px;padding:1rem;min-height:110px;margin:.7rem 0;white-space:pre-wrap;line-height:1.5}
  .grant-list{list-style:none;padding:0;margin:.75rem 0 0}
  .grant-list li{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:.5rem;padding:.55rem .75rem;border-radius:9px;background:#0a1929;border:1px solid #223e58;margin-bottom:.4rem;font-size:.9rem}
  details{margin-top:.9rem;border:1px solid #26435e;border-radius:10px;padding:.65rem .85rem;background:#0b1a2b}
  summary{cursor:pointer;font-weight:600;color:#b7d5ef}
</style>
</head>
<body>
<header>
  <div class="topbar">
    <div class="brand"><span id="hdr-dot" class="dot"></span><span>Eva Setup, Karakter &amp; Contact</span></div>
    <div class="pills">
      <span id="pill-db" class="pill">Database: …</span>
      <span id="pill-mem" class="pill">Geheugen: …</span>
      <span id="pill-model" class="pill">AI-Model: …</span>
      <span id="pill-tg" class="pill" hidden>Telegram: gekoppeld</span>
      <a class="btn-link" href="/">✨ Levende Eva (3D)</a>
      <a id="open-chat" class="btn-link alt" href="/internal/v1/dashboard/conversation" target="_blank" rel="noopener" hidden>Praat met Eva</a>
      <a id="open-stage" class="btn-link alt" href="/internal/v1/dashboard/holostage" target="_blank" rel="noopener" hidden>3D HoloStage</a>
      <a id="open-dash" class="btn-link alt" href="/internal/v1/dashboard" target="_blank" rel="noopener" hidden>Dashboard</a>
    </div>
  </div>
</header>

<main>
  <nav class="tabs" aria-label="Stappen">
    <button class="tab active" data-tab="0">1. Jij &amp; Hardware<small>Gezin &amp; pc</small></button>
    <button class="tab" data-tab="1">2. Eva's Karakter<small>Toon &amp; humor</small></button>
    <button class="tab" data-tab="2">3. AI-Brein<small>15+ modellen</small></button>
    <button class="tab" data-tab="3">4. Modules<small>Huis, Spraak, Mail…</small></button>
    <button class="tab" data-tab="4">5. Activeren<small>Opslaan &amp; back-up</small></button>
    <button class="tab" data-tab="5">6. Contact &amp; Telefoon<small>QR, Telegram &amp; test</small></button>
  </nav>

  <!-- TAB 1: Jij & Hardware -->
  <section class="panel active" data-panel="0">
    <h2>1. Wie woont hier &amp; wat voor pc is dit?</h2>
    <p class="lead">Eva stemt hiermee haar aanspreking en geheugengebruik automatisch af op jouw hardware.</p>
    <div class="grid2">
      <div>
        <div class="field"><label for="owner-name">Jouw naam</label><input id="owner-name" maxlength="80" placeholder="Jaap"></div>
        <div class="field"><label for="family-members">Gezinsleden (optioneel, komma-gescheiden)</label><input id="family-members" placeholder="bijv. Lola"></div>
        <div class="field"><label for="install-name">Naam van deze plek</label><input id="install-name" maxlength="80" value="Eva thuis"></div>
        <div class="grid2">
          <div class="field"><label for="profile-lang">Taal</label><input id="profile-lang" value="nl-NL"></div>
          <div class="field"><label for="profile-tz">Tijdzone</label><input id="profile-tz" value="Europe/Amsterdam"></div>
        </div>
      </div>
      <div>
        <div class="field">
          <label>Gedetecteerde hardware</label>
          <p id="hw-detected" class="muted">Laden…</p>
        </div>
        <div class="field">
          <label for="hw-tier">Prestatieprofiel</label>
          <select id="hw-tier"></select>
        </div>
        <p id="hw-tier-desc" class="muted"></p>
      </div>
    </div>
    <div class="btn-row">
      <button id="save-profile" class="primary" type="button">Bewaar &amp; naar Karakter →</button>
      <span id="msg-profile" class="status-msg"></span>
    </div>
  </section>

  <!-- TAB 2: Eva's Karakter -->
  <section class="panel" data-panel="1">
    <h2>2. Eva's karakter samenstellen</h2>
    <p class="lead">Kies een basiskarakter en schaaf haar naam, toon en initiatief bij. Elke opslag wordt cryptografisch ondertekend (met 1-klik terugzetten naar de vorige versie).</p>
    <div id="char-presets" class="presets"></div>
    <div class="grid2">
      <div>
        <div class="grid2">
          <div class="field"><label for="char-name">Naam</label><input id="char-name" maxlength="64" value="Eva"></div>
          <div class="field"><label for="char-pronouns">Voornaamwoorden</label><input id="char-pronouns" value="zij, haar"></div>
        </div>
        <div class="field"><label for="char-temp">Karaker-eigenschappen (komma-gescheiden)</label><input id="char-temp"></div>
        <div class="field"><label for="char-humour">Humor</label><input id="char-humour"></div>
        <div class="field"><label for="char-voice">Spreekstijl</label><input id="char-voice"></div>
        <div class="field">
          <label for="char-proactivity">Eigen initiatief</label>
          <select id="char-proactivity">
            <option value="quiet">Rustig · wacht tot je iets vraagt</option>
            <option value="balanced">Gebalanceerd · denkt mee bij duidelijke aanleiding</option>
            <option value="proactive">Ondernemend · geeft uit zichzelf seintjes en suggesties</option>
          </select>
        </div>
        <div class="field"><label for="char-rules">Extra huisafspraken (optioneel)</label><textarea id="char-rules" placeholder="Bijv. houd berichten op werkdagen kort; spreek buiten werktijd informeel."></textarea></div>
      </div>
      <div>
        <div class="preview-box">
          <strong>Live voorbeeldgesprek</strong>
          <p class="muted" style="margin:.2rem 0 .8rem">Zo klinkt jouw huidige keuze in de praktijk:</p>
          <div id="prev-user" class="bubble user"></div>
          <div id="prev-eva" class="bubble eva"></div>
          <p id="prev-init" class="muted"></p>
          <p id="char-version" class="muted"></p>
        </div>
        <details>
          <summary>Geavanceerd: SOUL.md importeren of downloaden</summary>
          <div class="field" style="margin-top:.7rem"><label for="soul-raw">Plak een bestaande SOUL.md (laat leeg om bovenstaande velden te gebruiken)</label><textarea id="soul-raw" placeholder="# Eva&#10;&#10;## Temperament&#10;warm, attent"></textarea></div>
          <a class="secondary" style="display:inline-block;text-decoration:none;padding:.45rem .8rem;border-radius:8px" href="/internal/v1/appliance/character/soul-export" download="SOUL.md">Download huidige SOUL.md</a>
        </details>
      </div>
    </div>
    <div class="btn-row">
      <button id="save-char" class="primary" type="button">Karakter opslaan &amp; naar AI-Brein →</button>
      <button id="rollback-char" class="secondary" type="button" disabled>Vorige karakter terugzetten</button>
      <span id="msg-char" class="status-msg"></span>
    </div>
  </section>

  <!-- TAB 3: AI-Brein -->
  <section class="panel" data-panel="2">
    <h2>3. AI-Brein &amp; Kostenplafond</h2>
    <p class="lead">Koppel Eva en haar geheugen aan ChatGPT (Codex-abonnement) of een eigen API-sleutel.</p>
    <div class="grid2">
      <div class="module-card">
        <strong>Optie A · ChatGPT / OpenAI Codex-abonnement (aanbevolen)</strong>
        <p class="muted">Gebruikt jouw bestaande abonnement voor zowel Eva als Hindsight. Geen losse API-kosten.</p>
        <p id="codex-state" class="status-msg"></p>
        <div class="btn-row">
          <button id="codex-start" class="primary" type="button">Inloggen met ChatGPT</button>
        </div>
        <div id="codex-device" class="device-box" hidden>
          <p style="margin:0">1. Open onderstaande link in je browser:</p>
          <p style="margin:.35rem 0"><a id="codex-url" href="#" target="_blank" rel="noopener" style="color:#8de8ff;font-weight:700"></a></p>
          <p style="margin:.4rem 0 0">2. Vul deze code in:</p>
          <div id="codex-code" class="code-big"></div>
          <p class="muted" style="margin:0">Deze pagina merkt automatisch zodra het inloggen klaar is.</p>
        </div>
      </div>

      <div class="module-card">
        <strong>Optie B · 14 Cloud- &amp; Lokale Modellen (Gemini, Claude, OpenRouter, Ollama…)</strong>
        <p id="api-hint" class="muted">Handig als extra model of als je zonder ChatGPT-abonnement wilt draaien.</p>
        <div class="field" style="margin-top:.7rem"><label for="api-preset">Provider</label><select id="api-preset"></select></div>
        <div class="grid2">
          <div class="field"><label for="api-model">Model</label><input id="api-model"></div>
          <div class="field"><label for="api-endpoint">Endpoint</label><input id="api-endpoint"></div>
        </div>
        <div id="api-key-wrap" class="field"><label for="api-key">API-sleutel</label><input id="api-key" type="password" autocomplete="new-password" placeholder="Plak API-sleutel"></div>
        <button id="api-save" class="secondary" type="button">Test &amp; sla model op</button>
        <p id="msg-api" class="status-msg"></p>
      </div>
    </div>

    <div class="module-card" style="margin-top:1rem">
      <div class="grid2">
        <div class="field"><label for="spend-cap">Maandelijks plafond voor losse API-uitgaven (€)</label><input id="spend-cap" type="number" min="0" max="10000" step="1" value="0"></div>
        <div class="field"><label for="reasoning-effort">Denkdiepte</label>
          <select id="reasoning-effort">
            <option value="low">Snel (low)</option>
            <option value="medium">Gebalanceerd (medium)</option>
            <option value="high">Diep nadenken (high)</option>
          </select>
        </div>
      </div>
      <div class="btn-row">
        <button id="save-budget" class="primary" type="button">Bewaar AI-instelling &amp; naar Modules →</button>
        <span id="msg-budget" class="status-msg"></span>
      </div>
    </div>
  </section>

  <!-- TAB 4: Modules -->
  <section class="panel" data-panel="3">
    <h2>4. Modules &amp; Koppelingen</h2>
    <p class="lead">Zet alleen aan wat je wilt gebruiken. Je kunt later altijd terugkomen om extra modules toe te voegen.</p>
    <div class="module-card enabled">
      <div class="module-head">
        <div><strong>Altijd aan · Webdashboard &amp; Gelaagd Geheugen (PostgreSQL + Hindsight)</strong><div class="muted">De basis voor gesprekken, afspraken, geheugen en het eigenaardashboard.</div></div>
        <span class="pill ok">Inbegrepen</span>
      </div>
    </div>
    <div id="modules-container"></div>
    <div class="btn-row">
      <button id="to-apply" class="primary" type="button">Naar Overzicht &amp; Installeren →</button>
    </div>
  </section>

  <!-- TAB 5: Installeren & Beheer -->
  <section class="panel" data-panel="4">
    <h2>5. Overzicht, Activeren &amp; Profiel Overzetten</h2>
    <p class="lead">Controleer je keuzes. Klik op <strong>Eva opslaan &amp; activeren</strong> om alles actief te maken en ga daarna direct naar <strong>6. Contact &amp; Telefoon</strong>.</p>
    <div id="summary-box" class="module-card"></div>
    <div class="btn-row">
      <button id="apply-btn" class="primary" type="button">Eva opslaan &amp; activeren</button>
      <button id="to-contact-btn" class="secondary" type="button">Naar 6. Contact &amp; Telefoon →</button>
      <a class="secondary" style="text-decoration:none;padding:.72rem 1rem;border-radius:10px;border:1px solid var(--border);color:var(--text)" href="/internal/v1/appliance/export" download="eva-appliance-profiel.json">Exporteer profiel (.json)</a>
      <label class="secondary" style="padding:.72rem 1rem;border-radius:10px;border:1px solid var(--border);cursor:pointer">Importeer profiel (.json)<input id="import-file" type="file" accept=".json,application/json" hidden></label>
      <span id="msg-apply" class="status-msg"></span>
    </div>
    <ul id="apply-steps" class="step-list"></ul>
  </section>

  <!-- TAB 6: Contact & Telefoon (QR, Telegram & Live Gesprekstest) -->
  <section class="panel" data-panel="5">
    <h2>6. Contact maken met Eva — Telefoon, Telegram &amp; Live Test</h2>
    <p class="lead">Geen gedoe met gebruikers-ID's of poorten: test hier meteen het gesprek met Eva en koppel je telefoon of gezinsleden in één QR-scan.</p>

    <!-- 6A: Directe Live Gesprekstest -->
    <div class="module-card enabled" style="margin-bottom:1.1rem">
      <strong>A · Zeg meteen gedag tegen Eva (Live Contacttest)</strong>
      <p class="muted" style="margin:.25rem 0 .5rem">Stuurt een écht bericht via Eva's levende kern en toont haar antwoord direct terwijl ze schrijft.</p>
      <div class="chip-row">
        <button class="chip quick-prompt" type="button" data-prompt="Hoi Eva! Stel jezelf kort voor en vertel voor wie je hier in huis klaarstaat.">Hoi Eva! Stel jezelf voor</button>
        <button class="chip quick-prompt" type="button" data-prompt="Zet volkorenpasta, verse basilicum en parmezaan op de boodschappenlijst.">Test boodschappenlijst</button>
        <button class="chip quick-prompt" type="button" data-prompt="Hoe kunnen wij hier in huis het fijnst met jou samenwerken?">Hoe werken we fijn samen?</button>
      </div>
      <div class="grid2" style="grid-template-columns:1fr auto;align-items:end">
        <div class="field" style="margin:0"><label for="live-chat-in">Jouw bericht aan Eva</label><input id="live-chat-in" placeholder="Typ een bericht aan Eva…"></div>
        <button id="live-chat-send" class="primary" type="button">Verstuur live</button>
      </div>
      <div id="live-chat-out" class="chat-live muted">Klik op een voorbeeldzin of typ zelf een bericht om direct contact met Eva te testen.</div>
      <div class="btn-row" style="margin-top:.5rem">
        <a class="secondary" style="text-decoration:none;padding:.58rem .95rem;border-radius:9px;border:1px solid var(--border);color:var(--text)" href="/internal/v1/dashboard/conversation" target="_blank" rel="noopener">Open Volledige Chat &amp; Spraak →</a>
        <a class="secondary" style="text-decoration:none;padding:.58rem .95rem;border-radius:9px;border:1px solid var(--border);color:var(--text)" href="/internal/v1/dashboard/holostage?mode=cockpit" target="_blank" rel="noopener">Open 3D Eva Podium (HoloStage) →</a>
        <span id="msg-live-chat" class="status-msg"></span>
      </div>
    </div>

    <div class="grid2">
      <!-- 6B: Eva op je Telefoon / Tablet / TV (QR) -->
      <div class="module-card">
        <strong>B · Eva op je Telefoon, Tablet of TV (1 QR-scan)</strong>
        <p class="muted" style="margin:.3rem 0 .7rem">Scan met de camera van je iPhone of Android voor fullscreen 3D Eva, spraak en live gezinskaarten (<em>Eva Pocket</em>). Kies in Safari/Chrome <strong>Zet op beginscherm</strong> om haar als app te installeren.</p>
        <div class="field">
          <label for="dev-mode">Soort scherm / apparaat</label>
          <select id="dev-mode">
            <option value="pocket">Mijn Telefoon — Eva Pocket (spraak, camera &amp; privékaarten)</option>
            <option value="unifi_screen">Wandtablet / Keukenscherm (altijd-aan kamerportaal)</option>
            <option value="tv_cast">Woonkamer TV / Scherm (gedeeld gezichtspunt)</option>
            <option value="owner_browser">Tweede Laptop / Telefoon — Volledig Eigenaar-Beheer</option>
          </select>
        </div>
        <div class="field">
          <label for="dev-label">Naam van dit apparaat</label>
          <input id="dev-label" value="Telefoon van Jaap" maxlength="80">
        </div>
        <button id="dev-qr-btn" class="primary" type="button">Maak QR-code voor apparaat</button>
        <span id="msg-dev" class="status-msg"></span>

        <div id="dev-qr-wrap" class="qr-box" hidden>
          <img id="dev-qr-img" alt="Apparaat QR-code">
          <div>
            <strong id="dev-qr-title">Scan met je telefooncamera</strong>
            <p class="muted" style="margin:.35rem 0">Zorg dat je telefoon op hetzelfde thuisnetwerk (wifi) zit en scan deze code, of open de link:</p>
            <p style="word-break:break-all;margin:.35rem 0"><a id="dev-qr-url" href="#" target="_blank" rel="noopener" style="color:#8de8ff;font-size:.88rem"></a></p>
          </div>
        </div>

        <ul id="dev-grants-list" class="grant-list"></ul>
      </div>

      <!-- 6C: Telegram 1-Scan Zonder Gebruikers-ID -->
      <div class="module-card">
        <strong>C · Privé Telegram voor jou &amp; je gezin (zonder gebruikers-ID's)</strong>
        <p class="muted" style="margin:.3rem 0 .7rem">Onbekenden worden 100% genegeerd. Plak eenmalig je <code>@BotFather</code>-token, scan de QR-code en tik op <strong>Start</strong> in Telegram — Eva herkent je automatisch en stuurt direct een bericht terug.</p>
        <div class="field">
          <label for="tg-contact-token" id="tg-token-lbl">BotFather-token</label>
          <input id="tg-contact-token" type="password" autocomplete="off" placeholder="123456789:ABCdef...">
        </div>
        <div class="grid2">
          <div class="field">
            <label for="tg-person-role">Wie koppelen?</label>
            <select id="tg-person-role">
              <option value="owner">Mijzelf (Eigenaar)</option>
              <option value="household">Gezinslid (gescheiden privécontext)</option>
            </select>
          </div>
          <div class="field">
            <label for="tg-person-name">Naam</label>
            <input id="tg-person-name" placeholder="bijv. Jaap of Lola" maxlength="60">
          </div>
        </div>
        <div id="tg-family-chips" class="chip-row"></div>
        <button id="tg-magic-btn" class="primary" type="button">Genereer 1-scan Telegram QR</button>
        <span id="msg-tg-magic" class="status-msg"></span>

        <div id="tg-qr-wrap" class="qr-box" hidden>
          <img id="tg-qr-img" alt="Telegram Koppel QR">
          <div>
            <strong id="tg-qr-title">Scan &amp; tik op 'Start' in Telegram</strong>
            <p id="tg-qr-status" class="muted" style="margin:.35rem 0">Wachten tot je op Start tikt in Telegram…</p>
            <p style="word-break:break-all;margin:.35rem 0"><a id="tg-qr-url" href="#" target="_blank" rel="noopener" style="color:#8de8ff;font-weight:700">Open direct in Telegram →</a></p>
          </div>
        </div>

        <ul id="tg-contacts-list" class="grant-list"></ul>
      </div>
    </div>
  </section>
</main>

<script>
(() => {
  let state = null;
  let codexTimer = null;
  let tgPollTimer = null;

  const $ = (id) => document.getElementById(id);
  function svgToDataUri(svg) {
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  }
  function showMsg(el, text, isErr=false) {
    el.textContent = text || '';
    el.classList.toggle('err', !!isErr);
  }
  async function api(path, method='GET', body=null) {
    const opts = {method, credentials:'same-origin', cache:'no-store', headers:{}};
    if (body !== null) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Actie mislukt');
    return data;
  }
  function setTab(idx) {
    document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', i === idx));
    document.querySelectorAll('.panel').forEach((p, i) => p.classList.toggle('active', i === idx));
    if (idx === 4 && state) renderSummary();
    if (idx === 5) loadContactHub().catch(() => {});
  }
  document.querySelectorAll('.tab').forEach((btn, idx) => btn.addEventListener('click', () => setTab(idx)));

  function renderTopbar(rt) {
    $('hdr-dot').classList.toggle('ok', !!rt.core_ready);
    const dbOk = rt.database === 'ready';
    const memOk = rt.hindsight_healthy || rt.memory_projection === 'ready';
    const modOk = rt.model_provider === 'ready';
    $('pill-db').textContent = 'Database: ' + (dbOk ? 'gereed' : rt.database);
    $('pill-db').className = 'pill ' + (dbOk ? 'ok' : '');
    $('pill-mem').textContent = 'Geheugen: ' + (memOk ? 'gereed' : rt.memory_projection);
    $('pill-mem').className = 'pill ' + (memOk ? 'ok' : '');
    $('pill-model').textContent = 'AI-Model: ' + (modOk ? 'gereed' : rt.model_provider);
    $('pill-model').className = 'pill ' + (modOk ? 'ok' : '');
    const tgPill = $('pill-tg');
    tgPill.hidden = !rt.telegram_ready;
    tgPill.className = 'pill ok';

    const showLinks = !!rt.dashboard_proxy_ready;
    $('open-chat').hidden = !showLinks;
    $('open-stage').hidden = !showLinks;
    $('open-dash').hidden = !showLinks;
    if (rt.conversation_url) $('open-chat').href = rt.conversation_url;
    if (rt.holostage_url) $('open-stage').href = rt.holostage_url;
    if (rt.dashboard_url) $('open-dash').href = rt.dashboard_url;
  }

  function renderProfile(snap) {
    $('owner-name').value = snap.profile.owner_name || '';
    $('family-members').value = (snap.profile.family_members || []).join(', ');
    $('install-name').value = snap.profile.installation_name || 'Eva thuis';
    $('profile-lang').value = snap.profile.language || 'nl-NL';
    $('profile-tz').value = snap.profile.time_zone || 'Europe/Amsterdam';
    const hw = snap.hardware;
    $('hw-detected').textContent = `${hw.hostname} · ${hw.architecture} · ${hw.cpu_count} cores · ${hw.ram_gib} GB RAM · ${hw.disk_free_gib} GB vrije schijf (aanbevolen: ${hw.recommended_tier})`;
    const sel = $('hw-tier');
    sel.replaceChildren();
    hw.tiers.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.tier;
      opt.textContent = t.label;
      sel.append(opt);
    });
    sel.value = hw.selected_tier;
    const updateTierDesc = () => {
      const found = hw.tiers.find(t => t.tier === sel.value);
      $('hw-tier-desc').textContent = found ? `${found.summary} (Postgres shared_buffers=${found.postgres_shared_buffers_mb}MB, Hindsight threads=${found.hindsight_threads})` : '';
    };
    sel.onchange = updateTierDesc;
    updateTierDesc();
  }

  function applyPresetToFields(preset) {
    $('char-temp').value = preset.temperament.join(', ');
    $('char-humour').value = preset.humour;
    $('char-voice').value = preset.voice_qualities.join(', ');
    $('char-proactivity').value = preset.proactivity;
    document.querySelectorAll('#char-presets .preset').forEach(el => {
      el.classList.toggle('selected', el.dataset.preset === preset.preset_id);
    });
    updateCharacterPreview();
  }

  let previewTimer = null;
  function updateCharacterPreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      const selected = document.querySelector('#char-presets .preset.selected')?.dataset.preset || 'warm_huisgenoot';
      try {
        const pv = await api('/internal/v1/appliance/character/preview', 'POST', {
          preset_id: selected,
          display_name: $('char-name').value,
          owner_name: $('owner-name').value,
          proactivity: $('char-proactivity').value,
          temperament: $('char-temp').value,
          humour: $('char-humour').value
        });
        $('prev-user').textContent = pv.user_prompt;
        $('prev-eva').textContent = pv.assistant_reply;
        $('prev-init').textContent = pv.initiative_note;
      } catch {}
    }, 180);
  }

  function renderCharacter(snap) {
    const ch = snap.character;
    const box = $('char-presets');
    box.replaceChildren();
    ch.presets.forEach(p => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'preset' + (p.preset_id === ch.preset_id ? ' selected' : '');
      btn.dataset.preset = p.preset_id;
      btn.innerHTML = `<strong>${p.label}</strong><span>${p.tagline}</span>`;
      btn.addEventListener('click', () => applyPresetToFields(p));
      box.append(btn);
    });
    $('char-name').value = ch.display_name || 'Eva';
    $('char-pronouns').value = (ch.pronouns || ['zij','haar']).join(', ');
    $('char-temp').value = (ch.temperament || []).join(', ');
    $('char-humour').value = ch.humour || '';
    $('char-voice').value = (ch.voice_qualities || []).join(', ');
    $('char-proactivity').value = ch.proactivity || 'balanced';
    $('char-rules').value = ch.custom_rules || '';
    $('rollback-char').disabled = !ch.can_rollback;
    $('char-version').textContent = `Ondertekend IdentityPack versie ${ch.pack_version}`;
    if (ch.preview) {
      $('prev-user').textContent = ch.preview.user_prompt;
      $('prev-eva').textContent = ch.preview.assistant_reply;
      $('prev-init').textContent = ch.preview.initiative_note;
    }
    ['char-name','char-temp','char-humour','char-proactivity','owner-name'].forEach(id => {
      $(id).oninput = updateCharacterPreview;
      $(id).onchange = updateCharacterPreview;
    });
  }

  function renderModel(snap) {
    const cx = snap.model.codex;
    showMsg($('codex-state'), cx.detail + (cx.account_id ? ` (${cx.account_id})` : ''), !cx.logged_in);
    $('codex-start').textContent = cx.logged_in ? 'Opnieuw inloggen met ChatGPT' : 'Inloggen met ChatGPT';
    const sel = $('api-preset');
    sel.replaceChildren();
    snap.model.api_presets.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.preset_id;
      opt.textContent = p.label;
      sel.append(opt);
    });
    const syncPreset = () => {
      const p = snap.model.api_presets.find(x => x.preset_id === sel.value);
      if (p) {
        $('api-model').value = p.default_model;
        $('api-endpoint').value = p.default_endpoint;
        $('api-hint').textContent = p.hint_nl || '';
        $('api-key-wrap').hidden = p.requires_api_key === false;
      }
    };
    sel.onchange = syncPreset;
    syncPreset();
    $('spend-cap').value = snap.hardware.monthly_spend_eur ?? 0;
    $('reasoning-effort').value = snap.model.reasoning_effort || 'medium';
  }

  function renderModules(snap) {
    const wrap = $('modules-container');
    wrap.replaceChildren();
    snap.modules.forEach(m => {
      const card = document.createElement('div');
      card.className = 'module-card' + (m.config.enabled ? ' enabled' : '');
      const head = document.createElement('div');
      head.className = 'module-head';
      head.innerHTML = `<div><strong>${m.title}</strong> <span class="muted">(${m.ram_hint})</span><div class="muted">${m.summary}</div></div>`;
      const sw = document.createElement('label');
      sw.className = 'switch-wrap';
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = !!m.config.enabled;
      chk.addEventListener('change', () => card.classList.toggle('enabled', chk.checked));
      sw.append(chk, document.createTextNode('Aanzetten'));
      head.append(sw);
      card.append(head);

      const body = document.createElement('div');
      body.className = 'module-body';
      const fields = {};
      function addField(key, label, type='text', value='', placeholder='') {
        const div = document.createElement('div'); div.className = 'field';
        const lbl = document.createElement('label'); lbl.textContent = label;
        const inp = document.createElement('input'); inp.type = type; inp.value = value || ''; inp.placeholder = placeholder; inp.autocomplete = 'off';
        div.append(lbl, inp); body.append(div); fields[key] = inp;
      }

      if (m.module_id === 'telegram') {
        addField('bot_token', m.config.secret_present ? 'Bot-token (al opgeslagen; leeg laten om te behouden)' : 'Bot-token (van @BotFather)', 'password', '', '123456:ABC...');
        const tip = document.createElement('p');
        tip.className = 'muted';
        tip.style.margin = '.2rem 0 .6rem';
        tip.textContent = 'Geen zin om een numeriek ID op te zoeken? Bewaar je bot-token en koppel jezelf + gezinsleden met 1 QR-scan bij stap 6 (Contact & Telefoon).';
        body.append(tip);
      } else if (m.module_id === 'smart_home') {
        addField('url', 'Home Assistant URL', 'url', m.config.url || 'http://homeassistant.local:8123', 'http://homeassistant.local:8123');
        addField('token', m.config.secret_present ? 'Long-Lived Token (al opgeslagen; leeg laten = behouden)' : 'Long-Lived Access Token', 'password', '', 'Plak token');
        const effWrap = document.createElement('label'); effWrap.className = 'switch-wrap'; effWrap.style.marginBottom = '.6rem';
        const effChk = document.createElement('input'); effChk.type = 'checkbox'; effChk.checked = !!m.config.allow_effects;
        effWrap.append(effChk, document.createTextNode('Apparaten ook echt laten bedienen (uit = alleen meekijken)'));
        body.append(effWrap);
        fields.allow_effects = effChk;
      } else if (m.module_id === 'voice') {
        const div = document.createElement('div'); div.className = 'field';
        const lbl = document.createElement('label'); lbl.textContent = 'Spraakroute';
        const sel = document.createElement('select');
        sel.innerHTML = '<option value="browser_and_phone">Browser &amp; Telefoon</option><option value="household_speakers">Ook huiskamerspeakers</option>';
        sel.value = m.config.speaker_mode || 'browser_and_phone';
        div.append(lbl, sel); body.append(div); fields.speaker_mode = sel;
      } else if (m.module_id === 'holostage') {
        addField('device_label', 'Scherm- of apparaatnaam', 'text', m.config.device_label || 'Woonkamer / Tablet', 'Woonkamer tablet');
      } else if (m.module_id === 'digital_work') {
        const webWrap = document.createElement('label'); webWrap.className = 'switch-wrap'; webWrap.style.marginBottom = '.6rem';
        const webChk = document.createElement('input'); webChk.type = 'checkbox'; webChk.checked = m.config.allow_public_web !== false;
        webWrap.append(webChk, document.createTextNode('Openbare HTTPS-websites mogen bezoeken voor opdrachten'));
        body.append(webWrap);
        fields.allow_public_web = webChk;
      } else if (m.module_id === 'mail') {
        addField('inbox_id', 'Inbox-adres', 'text', m.config.inbox_id || '', 'eva@...');
        addField('api_key', m.config.secret_present ? 'API-sleutel (al opgeslagen; leeg laten = behouden)' : 'API-sleutel', 'password', '', 'Plak sleutel');
      }

      const row = document.createElement('div'); row.className = 'btn-row';
      const saveBtn = document.createElement('button'); saveBtn.type = 'button'; saveBtn.className = 'secondary'; saveBtn.textContent = 'Module bewaren';
      const msg = document.createElement('span'); msg.className = 'status-msg';
      function collectPayload() {
        const p = {enabled: chk.checked};
        Object.entries(fields).forEach(([k, el]) => {
          p[k] = el.type === 'checkbox' ? el.checked : el.value;
        });
        return p;
      }
      saveBtn.onclick = async () => {
        try {
          await api(`/internal/v1/appliance/modules/${m.module_id}`, 'POST', collectPayload());
          showMsg(msg, chk.checked ? 'Opgeslagen (ingeschakeld).' : 'Opgeslagen (uitgeschakeld).');
          await reloadStatus(false);
        } catch (e) { showMsg(msg, e.message, true); }
      };
      chk.addEventListener('change', () => {
        if (!chk.checked) {
          api(`/internal/v1/appliance/modules/${m.module_id}`, 'POST', {enabled: false}).catch(() => {});
        }
      });
      row.append(saveBtn);
      if (m.has_connection_test) {
        const testBtn = document.createElement('button'); testBtn.type = 'button'; testBtn.className = 'primary'; testBtn.textContent = 'Test verbinding';
        testBtn.onclick = async () => {
          showMsg(msg, 'Verbinding testen…');
          try {
            const res = await api(`/internal/v1/appliance/modules/${m.module_id}/test`, 'POST', collectPayload());
            showMsg(msg, res.detail || 'Verbinding geslaagd!');
            await reloadStatus(false);
          } catch (e) { showMsg(msg, e.message, true); }
        };
        row.append(testBtn);
      }
      row.append(msg);
      body.append(row);
      card.append(body);
      wrap.append(card);
    });
  }

  function renderSummary() {
    const activeMods = state.modules.filter(m => m.config.enabled).map(m => m.title);
    const box = $('summary-box');
    box.replaceChildren();
    const lines = [
      ['Eigenaar & gezin', `${state.profile.owner_name}` + (state.profile.family_members?.length ? ` (met ${state.profile.family_members.join(', ')})` : '')],
      ['Hardwareprofiel', `${state.hardware.selected_tier} (${state.hardware.ram_gib} GB RAM)`],
      ['Karakter', `${state.character.display_name} · ${(state.character.temperament || []).join(', ')}`],
      ['AI-Model', (state.model.codex.logged_in ? 'ChatGPT / Codex gekoppeld' : 'Nog niet ingelogd') + ` · maandplafond € ${state.hardware.monthly_spend_eur ?? 0}`],
      ['Actieve modules', activeMods.length ? activeMods.join(', ') : 'Alleen basis (Webdashboard & Geheugen)'],
    ];
    lines.forEach(([k, v]) => {
      const p = document.createElement('p'); p.style.margin = '.35rem 0';
      const b = document.createElement('strong'); b.textContent = k + ': ';
      p.append(b, document.createTextNode(v));
      box.append(p);
    });
  }

  async function reloadStatus(rerenderAll=true) {
    state = await api('/internal/v1/appliance/status');
    renderTopbar(state.runtime);
    if (rerenderAll) {
      renderProfile(state);
      renderCharacter(state);
      renderModel(state);
      renderModules(state);
      renderSummary();
    }
  }

  $('save-profile').onclick = async () => {
    try {
      await api('/internal/v1/appliance/profile', 'POST', {
        owner_name: $('owner-name').value,
        family_members: $('family-members').value,
        installation_name: $('install-name').value,
        language: $('profile-lang').value,
        time_zone: $('profile-tz').value,
        tier: $('hw-tier').value,
      });
      showMsg($('msg-profile'), 'Opgeslagen!');
      await reloadStatus(false);
      setTab(1);
    } catch (e) { showMsg($('msg-profile'), e.message, true); }
  };

  $('save-char').onclick = async () => {
    const selected = document.querySelector('#char-presets .preset.selected')?.dataset.preset || 'warm_huisgenoot';
    try {
      const res = await api('/internal/v1/appliance/character', 'POST', {
        preset_id: selected,
        display_name: $('char-name').value,
        pronouns: $('char-pronouns').value,
        language: $('profile-lang').value,
        temperament: $('char-temp').value,
        humour: $('char-humour').value,
        voice_qualities: $('char-voice').value,
        proactivity: $('char-proactivity').value,
        custom_rules: $('char-rules').value,
        soul_markdown: $('soul-raw').value,
      });
      showMsg($('msg-char'), 'Karakter ondertekend en opgeslagen!');
      $('rollback-char').disabled = !res.character?.can_rollback;
      await reloadStatus(false);
      setTab(2);
    } catch (e) { showMsg($('msg-char'), e.message, true); }
  };

  $('rollback-char').onclick = async () => {
    try {
      await api('/internal/v1/appliance/character/rollback', 'POST', {});
      showMsg($('msg-char'), 'Vorig karakter teruggezet.');
      await reloadStatus(true);
    } catch (e) { showMsg($('msg-char'), e.message, true); }
  };

  $('codex-start').onclick = async () => {
    showMsg($('codex-state'), 'Login starten…');
    try {
      const res = await api('/internal/v1/appliance/model/codex/start', 'POST', {});
      if (res.status === 'logged_in') {
        $('codex-device').hidden = true;
        showMsg($('codex-state'), res.detail);
        await reloadStatus(false);
        return;
      }
      $('codex-device').hidden = false;
      $('codex-url').href = res.verification_url || 'https://auth.openai.com/codex/device';
      $('codex-url').textContent = res.verification_url || 'Open OpenAI loginpagina';
      $('codex-code').textContent = res.user_code || '(zie loginpagina)';
      showMsg($('codex-state'), 'Wacht op afronding van de browser-login…');
      clearInterval(codexTimer);
      codexTimer = setInterval(async () => {
        const poll = await api('/internal/v1/appliance/model/codex/status');
        if (poll.logged_in) {
          clearInterval(codexTimer);
          $('codex-device').hidden = true;
          showMsg($('codex-state'), poll.detail);
          await reloadStatus(false);
        }
      }, 2500);
    } catch (e) { showMsg($('codex-state'), e.message, true); }
  };

  $('api-save').onclick = async () => {
    const p = state.model.api_presets.find(x => x.preset_id === $('api-preset').value) || {};
    showMsg($('msg-api'), 'API-verbinding testen…');
    try {
      const res = await api('/internal/v1/appliance/model/api', 'POST', {
        kind: p.kind || 'openai_compatible',
        label: p.label || 'API-model',
        model: $('api-model').value,
        endpoint: $('api-endpoint').value,
        api_key: $('api-key').value,
      });
      $('api-key').value = '';
      showMsg($('msg-api'), res.detail || 'API-model opgeslagen!');
      await reloadStatus(false);
    } catch (e) { showMsg($('msg-api'), e.message, true); }
  };

  $('save-budget').onclick = async () => {
    try {
      await api('/internal/v1/appliance/model/budget', 'POST', {
        monthly_spend_eur: Number($('spend-cap').value || 0),
        reasoning_effort: $('reasoning-effort').value,
      });
      showMsg($('msg-budget'), 'AI-instellingen bewaard!');
      await reloadStatus(false);
      setTab(3);
    } catch (e) { showMsg($('msg-budget'), e.message, true); }
  };

  $('to-apply').onclick = () => setTab(4);
  $('to-contact-btn').onclick = () => setTab(5);

  $('import-file').onchange = async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      await api('/internal/v1/appliance/import', 'POST', parsed);
      showMsg($('msg-apply'), 'Profiel & karakter ingeladen! Klik op Eva opslaan & activeren.');
      await reloadStatus(true);
    } catch (e) {
      showMsg($('msg-apply'), 'Importeren mislukt: ' + e.message, true);
    } finally {
      ev.target.value = '';
    }
  };

  $('apply-btn').onclick = async () => {
    const btn = $('apply-btn');
    btn.disabled = true;
    showMsg($('msg-apply'), 'Bezig met opslaan en toepassen…');
    $('apply-steps').replaceChildren();
    try {
      const res = await api('/internal/v1/appliance/apply', 'POST', {});
      (res.steps || []).forEach(s => {
        const li = document.createElement('li');
        li.textContent = `[${s.status}] ${s.step}: ${s.detail}`;
        $('apply-steps').append(li);
      });
      showMsg($('msg-apply'), 'Klaar! Klik op "Naar 6. Contact & Telefoon" om Eva direct te testen of je telefoon te koppelen.');
      await reloadStatus(false);
    } catch (e) {
      showMsg($('msg-apply'), e.message, true);
    } finally {
      btn.disabled = false;
    }
  };

  // --- TAB 6: Contact Hub (Live Chat, Eva Pocket QR & 1-Scan Telegram) ---
  function renderDeviceGrants(grants) {
    const ul = $('dev-grants-list');
    ul.replaceChildren();
    (grants || []).forEach(g => {
      const li = document.createElement('li');
      const left = document.createElement('span');
      left.textContent = `${g.label} · ${g.form_factor}`;
      const rev = document.createElement('button');
      rev.type = 'button';
      rev.className = 'secondary';
      rev.style.padding = '.3rem .65rem';
      rev.style.fontSize = '.8rem';
      rev.textContent = 'Intrekken';
      rev.onclick = async () => {
        try {
          const res = await api(`/internal/v1/appliance/contact/device/${encodeURIComponent(g.grant_id)}/revoke`, 'POST', {});
          renderDeviceGrants(res.device_grants);
        } catch (e) { showMsg($('msg-dev'), e.message, true); }
      };
      li.append(left, rev);
      ul.append(li);
    });
  }

  function renderTelegramContacts(contacts) {
    const ul = $('tg-contacts-list');
    ul.replaceChildren();
    (contacts || []).forEach(c => {
      const li = document.createElement('li');
      const left = document.createElement('span');
      const roleLbl = c.role === 'owner' ? 'Eigenaar' : 'Gezin';
      left.textContent = `${c.name} (${roleLbl})` + (c.username ? ` · @${c.username}` : '');
      const rem = document.createElement('button');
      rem.type = 'button';
      rem.className = 'secondary';
      rem.style.padding = '.3rem .65rem';
      rem.style.fontSize = '.8rem';
      rem.textContent = 'Ontkoppel';
      rem.onclick = async () => {
        try {
          const res = await api(`/internal/v1/appliance/contact/telegram/${c.telegram_user_id}/remove`, 'POST', {});
          renderTelegramContacts(res.contacts);
          await reloadStatus(false);
        } catch (e) { showMsg($('msg-tg-magic'), e.message, true); }
      };
      li.append(left, rem);
      ul.append(li);
    });
  }

  async function loadContactHub() {
    const hub = await api('/internal/v1/appliance/contact');
    const owner = hub.owner_name || 'Jaap';
    if (!$('dev-label').dataset.touched) {
      $('dev-label').value = `Telefoon van ${owner}`;
    }
    if (!$('tg-person-name').value) {
      $('tg-person-name').value = owner;
    }
    $('tg-token-lbl').textContent = hub.telegram.token_saved
      ? `BotFather-token (opgeslagen${hub.telegram.bot_username ? ' · @' + hub.telegram.bot_username : ''}; leeg laten = behouden)`
      : 'BotFather-token (eenmalig plakken van @BotFather)';

    const chips = $('tg-family-chips');
    chips.replaceChildren();
    const mkChip = (name, role) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = role === 'owner' ? `Koppel ${name} (Eigenaar)` : `Nodig ${name} uit`;
      b.onclick = () => {
        $('tg-person-role').value = role;
        $('tg-person-name').value = name;
      };
      chips.append(b);
    };
    mkChip(owner, 'owner');
    (hub.family_members || []).forEach(fm => mkChip(fm, 'household'));

    renderDeviceGrants(hub.device_grants);
    renderTelegramContacts(hub.telegram.contacts);
  }

  $('dev-label').oninput = () => { $('dev-label').dataset.touched = '1'; };
  $('dev-mode').onchange = () => {
    const owner = state?.profile?.owner_name || 'Jaap';
    const map = {
      pocket: `Telefoon van ${owner}`,
      unifi_screen: 'Wandtablet Woonkamer',
      tv_cast: 'Woonkamer TV',
      owner_browser: `Tweede scherm van ${owner}`,
    };
    $('dev-label').value = map[$('dev-mode').value] || 'Mijn Apparaat';
  };

  $('dev-qr-btn').onclick = async () => {
    showMsg($('msg-dev'), 'QR-code aanmaken…');
    try {
      const res = await api('/internal/v1/appliance/contact/device', 'POST', {
        mode: $('dev-mode').value,
        label: $('dev-label').value,
      });
      $('dev-qr-img').src = svgToDataUri(res.qr_svg);
      $('dev-qr-url').href = res.url;
      $('dev-qr-url').textContent = res.url;
      $('dev-qr-wrap').hidden = false;
      if (res.device_grants) renderDeviceGrants(res.device_grants);
      showMsg($('msg-dev'), 'Klaar! Scan de QR-code met je camera.');
    } catch (e) { showMsg($('msg-dev'), e.message, true); }
  };

  $('tg-person-role').onchange = () => {
    const owner = state?.profile?.owner_name || 'Jaap';
    const fam = state?.profile?.family_members?.[0] || '';
    $('tg-person-name').value = $('tg-person-role').value === 'owner' ? owner : fam;
  };

  $('tg-magic-btn').onclick = async () => {
    showMsg($('msg-tg-magic'), 'Telegram-koppeling voorbereiden…');
    try {
      const res = await api('/internal/v1/appliance/contact/telegram/start', 'POST', {
        bot_token: $('tg-contact-token').value,
        role: $('tg-person-role').value,
        person_name: $('tg-person-name').value,
      });
      $('tg-contact-token').value = '';
      $('tg-qr-img').src = svgToDataUri(res.qr_svg);
      $('tg-qr-url').href = res.deep_link;
      $('tg-qr-status').textContent = `Wachten tot ${res.person_name} op 'Start' tikt in @${res.bot_username}…`;
      $('tg-qr-wrap').hidden = false;
      showMsg($('msg-tg-magic'), '');
      clearInterval(tgPollTimer);
      tgPollTimer = setInterval(async () => {
        try {
          const st = await api('/internal/v1/appliance/contact/telegram/status');
          renderTelegramContacts(st.contacts);
          if (st.status === 'linked') {
            clearInterval(tgPollTimer);
            $('tg-qr-status').textContent = `Gelukt! ${st.linked_contact?.name || res.person_name} is gekoppeld en heeft een welkomstbericht ontvangen op Telegram.`;
            showMsg($('msg-tg-magic'), 'Telegram-contact actief!');
            await reloadStatus(false);
          } else if (st.status === 'expired') {
            clearInterval(tgPollTimer);
            $('tg-qr-status').textContent = 'Uitnodiging verlopen (10 min). Klik opnieuw om een verse QR te maken.';
          }
        } catch {}
      }, 2000);
    } catch (e) { showMsg($('msg-tg-magic'), e.message, true); }
  };

  async function sendLiveTestMessage(promptText) {
    const text = (promptText || $('live-chat-in').value || '').trim();
    if (!text) { $('live-chat-in').focus(); return; }
    const btn = $('live-chat-send');
    const out = $('live-chat-out');
    btn.disabled = true;
    out.classList.remove('muted');
    out.textContent = 'Eva luistert en schrijft…';
    showMsg($('msg-live-chat'), '');
    const t0 = performance.now();
    try {
      const response = await fetch('/internal/v1/dashboard/conversation/respond', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/x-ndjson'},
        body: JSON.stringify({text}),
      });
      if (!response.ok || !response.body) {
        const fallback = await api('/internal/v1/appliance/contact/test', 'POST', {text});
        out.textContent = fallback.reply_text;
        showMsg($('msg-live-chat'), `Antwoord ontvangen in ${fallback.latency_ms} ms (${fallback.provider})`);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buf = '', draft = '', firstTokenMs = null;
      while (true) {
        const {value, done} = await reader.read();
        buf += decoder.decode(value, {stream: !done});
        let nl;
        while ((nl = buf.indexOf('\\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.contract === 'EvaTextDelta/v1' && typeof ev.text === 'string') {
            if (firstTokenMs === null) firstTokenMs = Math.round(performance.now() - t0);
            draft += ev.text;
            out.textContent = draft + ' ▍';
          } else if (ev.contract === 'EvaInternalGroundedReply/v1') {
            out.textContent = ev.text || draft;
            const totalMs = Math.round(performance.now() - t0);
            const ft = firstTokenMs !== null ? `eerste woord na ${firstTokenMs} ms · ` : '';
            showMsg($('msg-live-chat'), `Live contact geslaagd (${ft}totaal ${totalMs} ms · ${ev.provider || 'actief'})`);
          } else if (ev.contract === 'EvaTextStreamError/v1') {
            throw new Error('Model nog niet gereed (' + (ev.code || 'controleer stap 3 & 5') + ')');
          }
        }
        if (done) break;
      }
    } catch (e) {
      out.textContent = 'Kon nog geen live antwoord ophalen: ' + e.message;
      showMsg($('msg-live-chat'), 'Controleer stap 3 (AI-Brein) en stap 5 (Activeren).', true);
    } finally {
      btn.disabled = false;
    }
  }

  $('live-chat-send').onclick = () => sendLiveTestMessage();
  $('live-chat-in').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); sendLiveTestMessage(); }
  });
  document.querySelectorAll('.quick-prompt').forEach(btn => {
    btn.onclick = () => {
      $('live-chat-in').value = btn.dataset.prompt || '';
      sendLiveTestMessage(btn.dataset.prompt);
    };
  });

  reloadStatus(true).catch(() => {});
})();
</script>
</body>
</html>"""


def create_appliance_wizard_app(service: ApplianceSetupPort) -> FastAPI:
    """Create the FastAPI application for the 1-click Eva appliance & character wizard."""
    app = FastAPI(title="Eva Appliance Wizard", docs_url=None, redoc_url=None, openapi_url=None)

    def _client_host(request: Request) -> str | None:
        return request.client.host if request.client else None

    def _require_lan(request: Request) -> str:
        host = _client_host(request)
        if not service.client_permitted(host):
            raise HTTPException(
                status_code=403,
                detail="alleen toegankelijk vanaf dit apparaat of je eigen thuisnetwerk",
            )
        return host or "unknown"

    def _require_same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        host_header = request.headers.get("host")
        if origin is not None and host_header and origin != f"{request.url.scheme}://{host_header}":
            raise HTTPException(status_code=403, detail="herkomst geweigerd")

    def _require_session(request: Request) -> None:
        _require_lan(request)
        if not service.is_paired_session(request.cookies.get(_SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="koppeling vereist")

    async def _read_json(request: Request, *, max_bytes: int = 32_768) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > max_bytes:
            raise HTTPException(status_code=413, detail="invoer te groot")
        try:
            data = json.loads(raw.decode("utf-8") if raw else "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="ongeldige JSON-invoer") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="JSON-object vereist")
        return data

    def _call(fn, *args, **kwargs) -> JSONResponse:
        try:
            payload = fn(*args, **kwargs)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int) and 400 <= status_code < 600:
                raise HTTPException(status_code=status_code, detail=str(exc)) from exc
            raise
        return JSONResponse(payload, headers=_SECURITY_HEADERS)

    @app.exception_handler(HTTPException)
    async def _error_handler(_request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"detail": error.detail},
            status_code=error.status_code,
            headers=_SECURITY_HEADERS,
        )

    def _request_origin(request: Request) -> str:
        host = request.headers.get("host") or "127.0.0.1:8765"
        return f"{request.url.scheme}://{host}"

    @app.options("/internal/v1/appliance/ping")
    def options_ping(request: Request) -> Response:
        _require_lan(request)
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Cache-Control": "no-store",
            },
        )

    @app.get("/internal/v1/appliance/ping")
    def get_ping(request: Request) -> JSONResponse:
        _require_lan(request)
        return JSONResponse(
            service.public_ping(),
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/pair", response_class=HTMLResponse)
    def pair_page(request: Request) -> HTMLResponse:
        _require_lan(request)
        return HTMLResponse(_PAIR_HTML, headers=_SECURITY_HEADERS)

    @app.get("/", response_class=HTMLResponse)
    def root_page(request: Request) -> HTMLResponse:
        _require_lan(request)
        if not service.is_paired_session(request.cookies.get(_SESSION_COOKIE)):
            return HTMLResponse(_PAIR_HTML, status_code=401, headers=_SECURITY_HEADERS)
        return HTMLResponse(IMMERSIVE_STAGE_HTML, headers=_STAGE_SECURITY_HEADERS)

    @app.get("/beheer", response_class=HTMLResponse)
    def beheer_page(request: Request) -> HTMLResponse:
        _require_lan(request)
        if not service.is_paired_session(request.cookies.get(_SESSION_COOKIE)):
            return HTMLResponse(_PAIR_HTML, status_code=401, headers=_SECURITY_HEADERS)
        return HTMLResponse(_APP_HTML, headers=_SECURITY_HEADERS)

    @app.get("/internal/v1/appliance/stage-assets/{asset_name}")
    def get_stage_asset(request: Request, asset_name: str) -> Response:
        _require_lan(request)
        has_owner = service.is_paired_session(request.cookies.get(_SESSION_COOKIE))
        has_welcome = request.cookies.get(_WELCOME_COOKIE) == "1"
        if not has_owner and not has_welcome:
            raise HTTPException(status_code=401, detail="koppeling vereist")
        media_type = _STAGE_ASSETS.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="stage-bestand niet gevonden")
        asset_path = _CORTEX_STATIC_DIR / asset_name
        if not asset_path.is_file():
            raise HTTPException(status_code=404, detail="stage-bestand niet beschikbaar")
        return FileResponse(
            path=asset_path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/internal/v1/appliance/pair-local")
    async def pair_local_loopback(request: Request) -> Response:
        client_key = _require_lan(request)
        _require_same_origin(request)
        host_hdr = (request.headers.get("host") or "").split(":", 1)[0].strip().lower()
        if host_hdr not in {"127.0.0.1", "localhost", "[::1]"}:
            raise HTTPException(status_code=403, detail="alleen rechtstreeks op deze pc toegestaan")
        try:
            session_id = service.exchange_loopback_session(remote_host=client_key)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 403)
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        response = Response(status_code=204, headers=_SECURITY_HEADERS)
        response.set_cookie(
            key=_SESSION_COOKIE,
            value=session_id,
            max_age=12 * 3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @app.post("/internal/v1/appliance/pair")
    async def pair_exchange(request: Request) -> Response:
        client_key = _require_lan(request)
        _require_same_origin(request)
        body = await _read_json(request)
        code = str(body.get("code", ""))
        try:
            session_id = service.exchange_pairing_code(code, client_key=client_key)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 401)
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        response = Response(status_code=204, headers=_SECURITY_HEADERS)
        response.set_cookie(
            key=_SESSION_COOKIE,
            value=session_id,
            max_age=12 * 3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @app.get("/internal/v1/appliance/status")
    def get_status(request: Request) -> JSONResponse:
        _require_session(request)
        return _call(service.status_snapshot)

    @app.post("/internal/v1/appliance/profile")
    async def post_profile(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.save_profile_and_hardware, await _read_json(request))

    @app.post("/internal/v1/appliance/character/preview")
    async def post_char_preview(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.preview_character, await _read_json(request))

    @app.post("/internal/v1/appliance/character")
    async def post_character(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.save_character, await _read_json(request))

    @app.post("/internal/v1/appliance/character/rollback")
    async def post_character_rollback(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.rollback_character)

    @app.get("/internal/v1/appliance/character/soul-export")
    def get_soul_export(request: Request) -> PlainTextResponse:
        _require_session(request)
        headers = {
            **_SECURITY_HEADERS,
            "Content-Disposition": 'attachment; filename="SOUL.md"',
        }
        return PlainTextResponse(service.export_soul_markdown(), headers=headers)

    @app.post("/internal/v1/appliance/model/codex/start")
    async def post_codex_start(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.start_codex_login)

    @app.get("/internal/v1/appliance/model/codex/status")
    def get_codex_status(request: Request) -> JSONResponse:
        _require_session(request)
        return _call(service.poll_codex_login)

    @app.post("/internal/v1/appliance/model/api")
    async def post_model_api(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.save_and_test_api_model, await _read_json(request))

    @app.post("/internal/v1/appliance/model/budget")
    async def post_model_budget(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.save_model_budget, await _read_json(request))

    @app.post("/internal/v1/appliance/modules/{module_id}")
    async def post_module(request: Request, module_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.save_module, module_id, await _read_json(request))

    @app.post("/internal/v1/appliance/modules/{module_id}/test")
    async def post_module_test(request: Request, module_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.test_module, module_id, await _read_json(request))

    @app.post("/internal/v1/appliance/apply")
    async def post_apply(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.apply_installation)

    @app.get("/internal/v1/appliance/export")
    def get_export(request: Request) -> JSONResponse:
        _require_session(request)
        bundle = service.export_portable_bundle()
        headers = {
            **_SECURITY_HEADERS,
            "Content-Disposition": 'attachment; filename="eva-appliance-profiel.json"',
        }
        return JSONResponse(bundle, headers=headers)

    @app.post("/internal/v1/appliance/import")
    async def post_import(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        raw = await request.body()
        if len(raw) > 262_144:
            raise HTTPException(status_code=413, detail="profielbestand te groot")
        try:
            payload = json.loads(raw.decode("utf-8") if raw else "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="ongeldige JSON in profielbestand") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="profielbestand moet een JSON-object zijn")
        return _call(service.import_portable_bundle, payload)

    @app.get("/internal/v1/appliance/contact")
    def get_contact_hub(request: Request) -> JSONResponse:
        _require_session(request)
        return _call(service.contact_hub_snapshot, base_origin=_request_origin(request))

    @app.post("/internal/v1/appliance/contact/device")
    async def post_contact_device(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(
            service.create_device_pairing,
            await _read_json(request),
            base_origin=_request_origin(request),
        )

    @app.post("/internal/v1/appliance/contact/device/{grant_id}/revoke")
    async def post_contact_device_revoke(request: Request, grant_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.revoke_device_pairing, grant_id)

    @app.post("/internal/v1/appliance/contact/telegram/start")
    async def post_contact_tg_start(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.start_telegram_magic_link, await _read_json(request))

    @app.get("/internal/v1/appliance/contact/telegram/status")
    def get_contact_tg_status(request: Request) -> JSONResponse:
        _require_session(request)
        return _call(service.poll_telegram_magic_link)

    @app.post("/internal/v1/appliance/contact/telegram/{telegram_user_id}/remove")
    async def post_contact_tg_remove(request: Request, telegram_user_id: int) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.remove_telegram_contact, telegram_user_id)

    @app.post("/internal/v1/appliance/contact/test")
    async def post_contact_test(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.test_live_contact, await _read_json(request))

    @app.get("/internal/v1/appliance/living/state")
    def get_living_state(request: Request) -> JSONResponse:
        _require_session(request)
        return _call(service.living_stage_snapshot, base_origin=_request_origin(request))

    @app.post("/internal/v1/appliance/living/turn")
    async def post_living_turn(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        body = await _read_json(request, max_bytes=524_288)
        return _call(service.handle_living_turn, body, base_origin=_request_origin(request))

    @app.post("/internal/v1/appliance/living/undo/{change_id}")
    async def post_living_undo(request: Request, change_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.undo_living_change, change_id)

    @app.post("/internal/v1/appliance/living/rules/{rule_index}/remove")
    async def post_living_remove_rule(request: Request, rule_index: int) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.remove_household_rule, rule_index)

    @app.post("/internal/v1/appliance/living/guest-mode")
    async def post_living_guest_mode(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        body = await _read_json(request)
        return _call(service.set_guest_mode, enabled=bool(body.get("enabled", False)))

    @app.post("/internal/v1/appliance/living/lan-scan")
    async def post_living_lan_scan(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.scan_lan_devices)

    @app.post("/internal/v1/appliance/living/lan-device/{device_id}")
    async def post_living_lan_device(request: Request, device_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        body = await _read_json(request)
        return _call(
            service.set_lan_device_consent,
            device_id,
            approved=bool(body.get("approved", True)),
        )

    @app.post("/internal/v1/appliance/living/member-welcome")
    async def post_living_member_welcome(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(
            service.create_member_welcome,
            await _read_json(request),
            base_origin=_request_origin(request),
        )

    @app.post("/internal/v1/appliance/living/speech")
    async def post_living_speech(request: Request) -> Response:
        _require_session(request)
        _require_same_origin(request)
        body = await _read_json(request)
        synth = service.synthesize_living_speech(str(body.get("text", "")))
        if synth is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        audio_bytes, media_type = synth
        return Response(
            content=audio_bytes,
            media_type=media_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/appliance/living/voice-enroll")
    async def post_living_voice_enroll(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        body = await _read_json(request, max_bytes=524_288)
        return _call(service.enroll_living_voice, body)

    @app.post("/internal/v1/appliance/living/voice-check")
    async def post_living_voice_check(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        body = await _read_json(request, max_bytes=524_288)
        return _call(service.check_living_voice, body)

    @app.post("/internal/v1/appliance/living/wizard-state")
    async def post_living_wizard_state(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.set_onboarding_wizard_state, await _read_json(request))

    @app.post("/internal/v1/appliance/living/smart-home/connect")
    async def post_living_smart_home_connect(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.connect_smart_home, await _read_json(request))

    @app.post("/internal/v1/appliance/living/integrations/connect")
    async def post_living_integrations_connect(request: Request) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.connect_universal_service, await _read_json(request))

    @app.post("/internal/v1/appliance/living/integrations/{service_id}/remove")
    async def post_living_integrations_remove(request: Request, service_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.remove_universal_service, service_id)

    @app.post("/internal/v1/appliance/living/capabilities/{capability_id}/remove")
    async def post_living_capability_remove(request: Request, capability_id: str) -> JSONResponse:
        _require_session(request)
        _require_same_origin(request)
        return _call(service.delete_custom_capability, capability_id)

    @app.get("/welkom/{token}", response_class=HTMLResponse)
    def get_member_welcome_page(request: Request, token: str) -> HTMLResponse:
        _require_lan(request)
        try:
            service.get_member_welcome(token)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 404)
            msg = str(exc) if isinstance(status_code, int) else "Deze welkomstlink is niet geldig."
            err_html = (
                "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>Eva · Welkom</title></head>"
                "<body style='background:#050914;color:#f8fafc;font-family:system-ui,sans-serif;"
                "display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center'>"
                f"<div><h2>Uitnodiging niet geldig</h2><p style='color:#94a3b8'>{html.escape(msg)}</p></div></body></html>"
            )
            return HTMLResponse(err_html, status_code=int(status_code), headers=_STAGE_SECURITY_HEADERS)
        resp = HTMLResponse(MEMBER_WELCOME_HTML, headers=_STAGE_SECURITY_HEADERS)
        resp.set_cookie(
            key=_WELCOME_COOKIE,
            value="1",
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return resp

    @app.get("/internal/v1/appliance/welcome/{token}")
    def get_member_welcome_api(request: Request, token: str) -> JSONResponse:
        _require_lan(request)
        return _call(service.get_member_welcome, token)

    @app.post("/internal/v1/appliance/welcome/{token}/complete")
    async def post_member_welcome_complete(request: Request, token: str) -> JSONResponse:
        _require_lan(request)
        _require_same_origin(request)
        body = await _read_json(request, max_bytes=524_288)
        return _call(
            service.complete_member_welcome,
            token,
            body,
            base_origin=_request_origin(request),
        )

    async def _proxy_dashboard(request: Request) -> Response:
        _require_lan(request)
        is_owner = service.is_paired_session(request.cookies.get(_SESSION_COOKIE))
        path = request.url.path
        is_holostage_subpath = path == "/internal/v1/dashboard/holostage" or path.startswith(
            "/internal/v1/dashboard/holostage/"
        )
        if not is_owner and not is_holostage_subpath:
            if request.method == "GET" and path in {
                "/internal/v1/dashboard",
                "/internal/v1/dashboard/",
                "/internal/v1/dashboard/conversation",
            }:
                return RedirectResponse(url="/pair", status_code=302, headers=_SECURITY_HEADERS)
            raise HTTPException(status_code=401, detail="koppeling vereist")
        if request.method not in {"GET", "HEAD"}:
            _require_same_origin(request)
        raw = await request.body()
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="invoer te groot")
        try:
            status_code, resp_headers, body_or_iter = service.open_dashboard_stream(
                method=request.method,
                path=path,
                query=request.url.query,
                headers=dict(request.headers),
                body=raw,
                owner_authenticated=is_owner,
            )
        except Exception as exc:
            err_status = getattr(exc, "status_code", None)
            if isinstance(err_status, int) and 400 <= err_status < 600:
                raise HTTPException(status_code=err_status, detail=str(exc)) from exc
            raise
        if isinstance(body_or_iter, (bytes, bytearray)):
            return Response(content=bytes(body_or_iter), status_code=status_code, headers=resp_headers)
        return StreamingResponse(body_or_iter, status_code=status_code, headers=resp_headers)

    @app.api_route(
        "/internal/v1/dashboard",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_dashboard_root(request: Request) -> Response:
        return await _proxy_dashboard(request)

    @app.api_route(
        "/internal/v1/dashboard/{subpath:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_dashboard_subpath(request: Request, subpath: str) -> Response:  # noqa: ARG001
        return await _proxy_dashboard(request)

    return app


__all__ = ["create_appliance_wizard_app"]
