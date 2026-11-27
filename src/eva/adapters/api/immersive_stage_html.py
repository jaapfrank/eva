"""Fullscreen 3D Immersive Awakening Stage (`GET /`) & Personal Member Welcome Stage (`GET /welkom/{token}`)."""

from __future__ import annotations

IMMERSIVE_STAGE_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#050914" />
  <title>Eva — Ontwaken &amp; Levend Contact</title>
  <script type="importmap">
    {
      "imports": {
        "three": "/internal/v1/appliance/stage-assets/three.module.min.js"
      }
    }
  </script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #050914;
      --glass: rgba(10, 18, 36, 0.74);
      --glass-strong: rgba(12, 22, 44, 0.88);
      --border: rgba(148, 163, 184, 0.20);
      --accent: #38bdf8;
      --accent2: #10b981;
      --gold: #f59e0b;
      --rose: #fb7185;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0; width: 100vw; height: 100vh; height: 100dvh;
      overflow: hidden; background: var(--bg); color: var(--text);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      -webkit-font-smoothing: antialiased;
    }

    /* Ambient aurora backdrop behind 3D WebGL stage */
    .aurora-bg {
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background:
        radial-gradient(circle at 50% 38%, rgba(56, 189, 248, 0.18), transparent 55%),
        radial-gradient(circle at 52% 85%, rgba(16, 185, 129, 0.11), transparent 48%),
        var(--bg);
      transition: background 0.9s ease;
    }
    body.guest-mode .aurora-bg {
      background:
        radial-gradient(circle at 50% 36%, rgba(245, 158, 11, 0.22), transparent 56%),
        radial-gradient(circle at 50% 86%, rgba(236, 72, 153, 0.12), transparent 50%),
        var(--bg);
    }

    #eva-canvas {
      position: fixed; inset: 0; width: 100vw; height: 100vh; height: 100dvh;
      z-index: 1; display: block;
    }

    /* Top HUD */
    .top-hud {
      position: fixed;
      top: calc(14px + env(safe-area-inset-top, 0px));
      left: calc(16px + env(safe-area-inset-left, 0px));
      right: calc(16px + env(safe-area-inset-right, 0px));
      z-index: 20;
      display: flex; justify-content: space-between; align-items: flex-start;
      gap: 10px; pointer-events: none;
    }
    .hud-group {
      display: flex; flex-wrap: wrap; gap: 8px; align-items: center; pointer-events: auto;
    }
    .pill {
      display: inline-flex; align-items: center; gap: 7px;
      padding: 7px 13px; border-radius: 999px; font-size: 0.81rem; font-weight: 600;
      color: var(--text); background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      cursor: pointer; transition: all 0.2s ease; text-decoration: none;
    }
    .pill:hover { border-color: rgba(56, 189, 248, 0.55); transform: translateY(-1px); }
    .pill.static { cursor: default; }
    .pill.static:hover { transform: none; border-color: var(--border); }
    .pill.active-cyan {
      border-color: rgba(56, 189, 248, 0.72);
      background: rgba(8, 51, 78, 0.68);
      color: #e0f2fe;
      box-shadow: 0 0 22px rgba(56, 189, 248, 0.22);
    }
    .pill.active-guest {
      border-color: rgba(245, 158, 11, 0.85);
      background: rgba(120, 53, 15, 0.72);
      color: #fef3c7;
      box-shadow: 0 0 26px rgba(245, 158, 11, 0.30);
    }
    .dot {
      width: 8px; height: 8px; border-radius: 50%; background: var(--accent2);
      box-shadow: 0 0 10px var(--accent2);
    }
    .dot.warn { background: var(--gold); box-shadow: 0 0 10px var(--gold); }

    /* Scene 1: Minimal Breathing Heartbeat when AI brain is not yet connected */
    .heartbeat-overlay {
      position: fixed; inset: 0; z-index: 30;
      display: flex; align-items: center; justify-content: center;
      padding: 20px; background: rgba(5, 9, 20, 0.78);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
    }
    .heartbeat-overlay[hidden] { display: none; }
    .heartbeat-card {
      width: 100%; max-width: 520px; padding: 34px 30px; border-radius: 26px;
      background: var(--glass-strong); border: 1px solid rgba(56, 189, 248, 0.28);
      box-shadow: 0 28px 80px rgba(0, 0, 0, 0.65), 0 0 60px rgba(56, 189, 248, 0.12);
      text-align: center; max-height: 92dvh; overflow-y: auto;
    }
    .pulse-orb {
      width: 88px; height: 88px; margin: 0 auto 20px; border-radius: 50%;
      background: radial-gradient(circle at 35% 35%, #e0f2fe, #38bdf8 48%, #0284c7 82%, transparent);
      box-shadow: 0 0 42px rgba(56, 189, 248, 0.55), 0 0 90px rgba(16, 185, 129, 0.25);
      animation: breatheOrb 3.4s ease-in-out infinite;
    }
    @keyframes breatheOrb {
      0%, 100% { transform: scale(0.94); opacity: 0.84; }
      50% { transform: scale(1.08); opacity: 1; }
    }
    .heartbeat-card h1 { margin: 0 0 10px; font-size: 1.65rem; letter-spacing: -0.02em; }
    .heartbeat-card p { margin: 0 0 22px; color: var(--muted); font-size: 0.96rem; line-height: 1.55; }

    .btn-primary {
      display: inline-flex; align-items: center; justify-content: center; gap: 9px;
      width: 100%; padding: 14px 20px; border-radius: 14px; border: none;
      background: linear-gradient(135deg, #38bdf8, #10b981);
      color: #04101d; font-weight: 700; font-size: 0.98rem; cursor: pointer;
      box-shadow: 0 10px 28px rgba(56, 189, 248, 0.28); transition: transform 0.16s ease, opacity 0.16s;
    }
    .btn-primary:hover { transform: translateY(-1px); }
    .btn-primary:disabled { opacity: 0.55; cursor: wait; transform: none; }
    .btn-ghost {
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      padding: 9px 14px; border-radius: 11px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.65); color: var(--text); font-size: 0.84rem;
      font-weight: 600; cursor: pointer; text-decoration: none;
    }
    .btn-ghost:hover { border-color: rgba(56, 189, 248, 0.55); }

    .codex-box {
      margin-top: 16px; padding: 16px; border-radius: 16px;
      background: rgba(8, 47, 73, 0.45); border: 1px solid rgba(56, 189, 248, 0.4);
      text-align: left;
    }
    .code-badge {
      display: block; margin: 10px 0; padding: 11px; border-radius: 10px;
      background: rgba(2, 6, 23, 0.75); border: 1px dashed rgba(56, 189, 248, 0.6);
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 1.35rem; font-weight: 800; letter-spacing: 0.12em; text-align: center; color: #7dd3fc;
    }
    .alt-toggle {
      margin-top: 18px; background: none; border: none; color: var(--muted);
      font-size: 0.84rem; cursor: pointer; text-decoration: underline;
    }
    .alt-panel {
      margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); text-align: left;
    }
    .field { margin-bottom: 11px; }
    .field label { display: block; font-size: 0.78rem; color: var(--muted); margin-bottom: 4px; }
    .field input, .field select {
      width: 100%; padding: 10px 12px; border-radius: 10px;
      background: rgba(2, 6, 23, 0.72); border: 1px solid var(--border);
      color: var(--text); font-size: 0.9rem;
    }

    /* Eva's Spoken Subtitle Bubble */
    .speech-halo {
      position: fixed; left: 50%; transform: translateX(-50%);
      bottom: calc(138px + env(safe-area-inset-bottom, 0px));
      z-index: 14; width: min(660px, 92vw); pointer-events: auto;
      text-align: center; transition: all 0.25s ease;
    }
    .speech-bubble {
      display: inline-block; padding: 14px 22px; border-radius: 22px;
      background: rgba(8, 16, 34, 0.78); border: 1px solid rgba(56, 189, 248, 0.30);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      box-shadow: 0 18px 46px rgba(0, 0, 0, 0.45);
      font-size: 1.02rem; line-height: 1.52; color: #f1f5f9;
    }
    .wake-cta {
      margin-top: 10px;
    }

    /* Live Reversible "Ik heb je gehoord" Cards (top-left / floating stack) */
    .heard-stack {
      position: fixed;
      top: calc(64px + env(safe-area-inset-top, 0px));
      left: calc(16px + env(safe-area-inset-left, 0px));
      z-index: 22; width: min(370px, calc(100vw - 32px));
      display: flex; flex-direction: column; gap: 9px; pointer-events: none;
    }
    .heard-card {
      pointer-events: auto; padding: 13px 15px; border-radius: 16px;
      background: rgba(6, 42, 48, 0.86); border: 1px solid rgba(16, 185, 129, 0.55);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      box-shadow: 0 14px 36px rgba(0, 0, 0, 0.45), 0 0 24px rgba(16, 185, 129, 0.16);
      animation: slideIn 0.28s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes slideIn {
      from { opacity: 0; transform: translateY(-8px) scale(0.97); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    .heard-head {
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      font-size: 0.8rem; font-weight: 700; color: #6ee7b7; margin-bottom: 4px;
    }
    .heard-body { font-size: 0.87rem; color: #ecfdf5; line-height: 1.4; }
    .undo-btn {
      margin-top: 8px; padding: 5px 10px; border-radius: 8px;
      border: 1px solid rgba(110, 231, 183, 0.45); background: rgba(2, 24, 28, 0.65);
      color: #a7f3d0; font-size: 0.77rem; font-weight: 600; cursor: pointer;
    }
    .undo-btn:hover { background: rgba(16, 185, 129, 0.25); }

    /* Right Floating Stage Drawer (Character Spheres / Awakening Guide / Rules / Family QR / LAN Devices) */
    .stage-drawer {
      position: fixed;
      top: calc(64px + env(safe-area-inset-top, 0px));
      right: calc(16px + env(safe-area-inset-right, 0px));
      bottom: calc(140px + env(safe-area-inset-bottom, 0px));
      width: min(390px, calc(100vw - 32px));
      z-index: 18; overflow-y: auto; padding: 18px; border-radius: 22px;
      background: var(--glass-strong); border: 1px solid var(--border);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.52);
    }
    .stage-drawer[hidden] { display: none; }
    .drawer-head {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 12px;
    }
    .drawer-head h2 { margin: 0; font-size: 1.02rem; letter-spacing: -0.01em; }
    .close-x {
      background: none; border: none; color: var(--muted); font-size: 1.2rem; cursor: pointer;
    }

    /* Interactive Character Spheres */
    .spheres-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin: 10px 0 14px;
    }
    .sphere-btn {
      display: flex; flex-direction: column; align-items: flex-start; gap: 5px;
      padding: 12px; border-radius: 16px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.62); color: var(--text); text-align: left;
      cursor: pointer; transition: all 0.2s ease;
    }
    .sphere-btn:hover { border-color: rgba(56, 189, 248, 0.6); transform: translateY(-2px); }
    .sphere-btn.selected {
      border-color: var(--accent);
      background: rgba(14, 58, 92, 0.55);
      box-shadow: 0 0 20px rgba(56, 189, 248, 0.22);
    }
    .sphere-dot {
      width: 22px; height: 22px; border-radius: 50%;
      box-shadow: 0 0 14px currentColor;
    }
    .sphere-title { font-size: 0.84rem; font-weight: 700; }
    .sphere-sub { font-size: 0.74rem; color: var(--muted); line-height: 1.3; }

    .list-item {
      display: flex; justify-content: space-between; align-items: center; gap: 10px;
      padding: 10px 12px; border-radius: 12px; margin-bottom: 8px;
      background: rgba(15, 23, 42, 0.62); border: 1px solid var(--border);
      font-size: 0.85rem; line-height: 1.4;
    }
    .qr-box {
      margin-top: 12px; padding: 14px; border-radius: 16px;
      background: #ffffff; color: #0f172a; text-align: center;
    }
    .qr-box svg { width: 188px; height: 188px; max-width: 100%; display: block; margin: 0 auto; }

    /* Bottom Living Conversation & Wish Bar */
    .bottom-dock {
      position: fixed; left: 50%; transform: translateX(-50%);
      bottom: calc(14px + env(safe-area-inset-bottom, 0px));
      z-index: 20; width: min(740px, calc(100vw - 24px));
      display: flex; flex-direction: column; gap: 8px;
    }
    .chips-row {
      display: flex; gap: 7px; overflow-x: auto; padding-bottom: 2px;
      scrollbar-width: none;
    }
    .chips-row::-webkit-scrollbar { display: none; }
    .chip {
      flex: 0 0 auto; padding: 6px 12px; border-radius: 999px;
      background: rgba(12, 21, 42, 0.74); border: 1px solid var(--border);
      color: #cbd5e1; font-size: 0.78rem; cursor: pointer; white-space: nowrap;
      backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    }
    .chip:hover { border-color: var(--accent); color: #fff; }

    .input-bar {
      display: flex; align-items: center; gap: 9px; padding: 8px 10px;
      border-radius: 999px; background: var(--glass-strong);
      border: 1px solid rgba(148, 163, 184, 0.28);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55);
    }
    .mic-btn {
      width: 44px; height: 44px; border-radius: 50%; border: none; flex-shrink: 0;
      background: linear-gradient(135deg, #0ea5e9, #10b981); color: #04131f;
      font-size: 1.15rem; cursor: pointer; display: flex; align-items: center; justify-content: center;
      box-shadow: 0 0 18px rgba(16, 185, 129, 0.32); transition: all 0.2s ease;
    }
    .mic-btn.recording {
      background: linear-gradient(135deg, #f43f5e, #f59e0b); color: #fff;
      animation: pulseMic 1.1s ease-in-out infinite;
    }
    @keyframes pulseMic {
      0%, 100% { transform: scale(1); box-shadow: 0 0 18px rgba(244, 63, 94, 0.5); }
      50% { transform: scale(1.09); box-shadow: 0 0 30px rgba(244, 63, 94, 0.85); }
    }
    .talk-input {
      flex: 1; background: transparent; border: none; outline: none;
      color: var(--text); font-size: 0.95rem; padding: 6px 4px;
    }
    .talk-input::placeholder { color: #64748b; }
    .send-btn {
      padding: 10px 18px; border-radius: 999px; border: none;
      background: rgba(56, 189, 248, 0.20); border: 1px solid rgba(56, 189, 248, 0.45);
      color: #e0f2fe; font-weight: 700; font-size: 0.86rem; cursor: pointer;
    }
    .send-btn:hover { background: rgba(56, 189, 248, 0.35); }

    @media (max-width: 768px) {
      .stage-drawer {
        left: 12px; right: 12px; width: auto; top: auto; max-height: 54dvh;
        bottom: calc(134px + env(safe-area-inset-bottom, 0px));
      }
      .speech-halo { bottom: calc(132px + env(safe-area-inset-bottom, 0px)); }
      .speech-bubble { font-size: 0.93rem; padding: 11px 16px; }
    }
  </style>
</head>
<body>
  <div class="aurora-bg"></div>
  <canvas id="eva-canvas"></canvas>

  <!-- Top HUD -->
  <header class="top-hud">
    <div class="hud-group">
      <div class="pill static" id="hud-eva-pill">
        <span class="dot" id="hud-dot"></span>
        <span id="hud-eva-name">Eva</span>
      </div>
      <button type="button" class="pill" id="btn-guest-mode" title="Scherm direct privézaken af als er visite of vrienden in de kamer zijn">
        🥂 <span id="guest-mode-label">Visite-modus</span>
      </button>
      <button type="button" class="pill" id="btn-awakening" title="Karakterbollen &amp; eerste kennismaking">
        ✨ Sfeer &amp; Karakter
      </button>
    </div>
    <div class="hud-group">
      <button type="button" class="pill" id="btn-rules">
        📜 Afspraken <span id="rules-count">(0)</span>
      </button>
      <button type="button" class="pill" id="btn-family">
        👨‍👩‍👧 Gezin &amp; Lola QR
      </button>
      <button type="button" class="pill" id="btn-devices">
        📡 Huis <span id="devices-count"></span>
      </button>
      <button type="button" class="pill active-cyan" id="btn-audio-toggle" title="Zet gesproken stem van Eva aan of uit">
        🔊 <span id="audio-label">Stem aan</span>
      </button>
      <a href="/beheer" class="pill" title="Geavanceerde instellingen, Telegram-bot, back-ups en systeemstatus">
        ⚙ Motorkap
      </a>
    </div>
  </header>

  <!-- Reversible "Ik heb je gehoord" confirmation cards -->
  <section class="heard-stack" id="heard-stack" aria-live="polite"></section>

  <!-- Scene 1: Minimal Breathing Heartbeat when AI is not yet connected -->
  <section class="heartbeat-overlay" id="scene-heartbeat" hidden>
    <div class="heartbeat-card">
      <div class="pulse-orb"></div>
      <h1>Eva wacht om wakker te worden</h1>
      <p>
        Geef Eva haar denkkracht met één klik via je ChatGPT-account (of kies een ander model).
        Daarna stapt ze direct in 3D naar voren om kennis te maken met jou en het gezin.
      </p>
      <button type="button" class="btn-primary" id="btn-codex-wake">
        ⚡ Wek Eva met ChatGPT (1-klik inlogcode)
      </button>

      <div class="codex-box" id="codex-wake-box" hidden>
        <div style="font-size:0.86rem; color:#bae6fd;">Open de inlogpagina en vul deze koppelcode in:</div>
        <div class="code-badge" id="codex-user-code">••••-••••</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <a href="https://auth.openai.com/codex/device" target="_blank" rel="noopener" class="btn-primary" id="codex-verify-link" style="flex:1; text-decoration:none;">
            Open OpenAI Inlogpagina ↗
          </a>
          <button type="button" class="btn-ghost" id="btn-copy-codex">Kopieer code</button>
        </div>
        <div id="codex-poll-status" style="margin-top:10px; font-size:0.8rem; color:var(--muted);">
          ⏳ Wachten tot je inlogt… Eva wordt daarna vanzelf wakker.
        </div>
      </div>

      <button type="button" class="alt-toggle" id="btn-toggle-alt-ai">
        Liever Claude, Gemini, OpenRouter of lokaal model (Ollama) gebruiken?
      </button>

      <div class="alt-panel" id="alt-ai-panel" hidden>
        <div class="field">
          <label for="alt-preset">Kies aanbieder</label>
          <select id="alt-preset"></select>
        </div>
        <div class="field">
          <label for="alt-model">Modelnaam</label>
          <input id="alt-model" type="text" />
        </div>
        <div class="field" id="alt-key-wrap">
          <label for="alt-key">API-sleutel</label>
          <input id="alt-key" type="password" autocomplete="off" placeholder="Plak je sleutel…" />
        </div>
        <div class="field">
          <label for="alt-endpoint">Endpoint</label>
          <input id="alt-endpoint" type="text" />
        </div>
        <button type="button" class="btn-primary" id="btn-save-alt-ai">Verbind &amp; Wek Eva</button>
        <div id="alt-ai-msg" style="margin-top:8px; font-size:0.82rem; color:var(--rose);"></div>
      </div>
    </div>
  </section>

  <!-- Right Floating Drawer (Awakening / Rules / Family QR / LAN Devices) -->
  <aside class="stage-drawer" id="stage-drawer" hidden>
    <div class="drawer-head">
      <h2 id="drawer-title">Sfeer &amp; Karakter</h2>
      <button type="button" class="close-x" id="btn-close-drawer" aria-label="Sluiten">✕</button>
    </div>
    <div id="drawer-body"></div>
  </aside>

  <!-- Eva's Spoken Subtitle & First Touch Greeting Trigger -->
  <section class="speech-halo">
    <div class="speech-bubble" id="eva-subtitle">
      Hoi! Ik ben Eva. Zeg gerust wie je bent, met wie je hier woont en hoe je wilt dat ik meedraai in huis.
    </div>
    <div class="wake-cta" id="first-voice-unlock" hidden>
      <button type="button" class="btn-ghost" id="btn-hear-greeting" style="border-color:rgba(56,189,248,0.6); color:#bae6fd;">
        🔊 Laat Eva hardop begroeten
      </button>
    </div>
  </section>

  <!-- Bottom Conversational Input & Instant Preference Chips -->
  <footer class="bottom-dock" id="bottom-dock">
    <div class="chips-row" id="suggestion-chips">
      <button type="button" class="chip" data-say="Hoi Eva! Ik ben Jaap en ik woon hier samen met mijn dochter Lola.">
        👋 Ik ben Jaap, samen met Lola
      </button>
      <button type="button" class="chip" data-say="Na 21:00 uur alleen nog storen als het echt dringend is.">
        🌙 Na 21:00 uur rust in huis
      </button>
      <button type="button" class="chip" data-say="Gebruik gerust lekkere droge humor en houd antwoorden vlot.">
        😄 Drogere humor &amp; lekker vlot
      </button>
      <button type="button" class="chip" data-say="Eva, we krijgen visite!">
        🥂 We krijgen visite!
      </button>
      <button type="button" class="chip" data-panel="family">
        📱 Maak QR-welkomstkaart voor Lola
      </button>
    </div>
    <form class="input-bar" id="living-form">
      <button type="button" class="mic-btn" id="btn-mic" title="Spreek tegen Eva (leert ook lokaal je stem herkennen)">
        🎙️
      </button>
      <input
        type="text"
        class="talk-input"
        id="talk-input"
        placeholder="Praat of typ gewoon Nederlands tegen Eva (bijv. een wens, grens of vraag)…"
        autocomplete="off"
      />
      <button type="submit" class="send-btn" id="btn-send">Zeg ↵</button>
    </form>
  </footer>

  <script type="module">
    import * as THREE from 'three';
    import { GLTFLoader } from '/internal/v1/appliance/stage-assets/GLTFLoader.js';

    const $ = id => document.getElementById(id);
    let stage = null;
    let activeDrawer = null;
    let audioEnabled = true;
    let userUnlockedAudio = false;
    let ttsSpeaking = false;
    let activeAnalyser = null;
    let freqBins = null;
    let currentAudioCtx = null;
    let currentSource = null;
    let evaMoodHex = 0x38bdf8;
    let waveUntil = 0;

    // --- 1. Fullscreen 3D Eva Stage (eva.glb + Contoured Mouth, Eyelids & Neural Field) ---
    function buildSphereContouredMouth(T) {
      const rig = new T.Group();
      rig.position.set(-0.12, 14.39, 18.18);
      rig.rotation.x = 0.03;
      const zContour = (dx, dy) => -((dx * dx + dy * dy) / 30.0);

      const segU = 18, segV = 10;
      const cavPos = [], cavIdx = [];
      for (let iv = 0; iv <= segV; iv++) {
        const r = iv / segV;
        for (let iu = 0; iu <= segU; iu++) {
          const th = (iu / segU) * Math.PI * 2;
          const dx = Math.cos(th) * 1.65 * r, dy = Math.sin(th) * 1.05 * r;
          cavPos.push(dx, dy, zContour(dx, dy));
        }
      }
      for (let iv = 0; iv < segV; iv++) {
        for (let iu = 0; iu < segU; iu++) {
          const a = iv * (segU + 1) + iu, b = a + segU + 1;
          cavIdx.push(a, b, a + 1, b, b + 1, a + 1);
        }
      }
      const cavGeo = new T.BufferGeometry();
      cavGeo.setAttribute('position', new T.Float32BufferAttribute(cavPos, 3));
      cavGeo.setIndex(cavIdx);
      const cavity = new T.Mesh(cavGeo, new T.MeshBasicMaterial({
        color: 0x26121b, side: T.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2
      }));
      cavity.visible = false;
      rig.add(cavity);

      const lipMat = new T.MeshBasicMaterial({
        color: 0x2b313a, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4
      });
      function makeLipArc(signY, archSign) {
        const pts = [];
        for (let i = 0; i <= 12; i++) {
          const u = (i / 12) * 2 - 1;
          const dx = u * 1.62;
          const dy = 0.22 * (u * u) + archSign * 0.16 * (1 - u * u) + signY * 0.04;
          pts.push(new T.Vector3(dx, dy, zContour(dx, dy) + 0.05));
        }
        return new T.Mesh(new T.TubeGeometry(new T.CatmullRomCurve3(pts), 18, 0.17, 8, false), lipMat);
      }
      const upperLip = makeLipArc(1, 1);
      const lowerLip = makeLipArc(-1, -1);
      rig.add(upperLip);
      rig.add(lowerLip);
      return { rig, cavity, upperLip, lowerLip };
    }

    function buildNeuralParticles(T) {
      const count = window.innerWidth < 768 ? 36 : 58;
      const positions = new Float32Array(count * 3);
      const seeds = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const ang = (i / count) * Math.PI * 2 + (i % 5) * 0.37;
        const rad = 0.46 + (i % 7) * 0.07;
        const y = 0.24 + ((i * 0.618) % 1) * 1.48;
        positions[i * 3] = Math.cos(ang) * rad;
        positions[i * 3 + 1] = y;
        positions[i * 3 + 2] = Math.sin(ang) * rad - 0.12;
        seeds[i * 3] = ang; seeds[i * 3 + 1] = rad; seeds[i * 3 + 2] = y;
      }
      const geo = new T.BufferGeometry();
      geo.setAttribute('position', new T.BufferAttribute(positions, 3));
      const mat = new T.PointsMaterial({
        size: 0.036, color: 0x38bdf8, transparent: true, opacity: 0.55,
        blending: T.AdditiveBlending, depthWrite: false
      });
      return { points: new T.Points(geo, mat), geo, mat, seeds, count };
    }

    function boot3DEva() {
      const canvas = $('eva-canvas');
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({ canvas, antialias: window.innerWidth >= 768, alpha: true });
      } catch (_e) {
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, window.innerWidth / window.innerHeight, 0.1, 50);
      camera.position.set(0, 1.36, 2.72);

      scene.add(new THREE.HemisphereLight(0xbae6fd, 0x091326, 1.48));
      const keyLight = new THREE.DirectionalLight(0xffffff, 1.85);
      keyLight.position.set(1.6, 2.8, 2.6);
      scene.add(keyLight);
      const rimLight = new THREE.DirectionalLight(0x38bdf8, 1.25);
      rimLight.position.set(-1.9, 2.0, -1.8);
      scene.add(rimLight);

      const haloMat = new THREE.MeshBasicMaterial({
        color: 0x38bdf8, side: THREE.DoubleSide, transparent: true, opacity: 0.40
      });
      const halo = new THREE.Mesh(new THREE.RingGeometry(0.45, 0.56, 72), haloMat);
      halo.rotation.x = -Math.PI / 2;
      halo.position.y = 0.04;
      scene.add(halo);

      const field = buildNeuralParticles(THREE);
      scene.add(field.points);

      let avatarRoot = null;
      let baseFloorY = 0.04;
      const bones = {};
      const restQuats = {};
      const jawMeshes = [];
      let mouthParts = null;
      let pointerX = 0, pointerY = 0;

      window.addEventListener('pointermove', e => {
        pointerX = ((e.clientX / Math.max(1, window.innerWidth)) - 0.5) * 2;
        pointerY = ((e.clientY / Math.max(1, window.innerHeight)) - 0.5) * 2;
      });

      const loader = new GLTFLoader();
      loader.load('/internal/v1/appliance/stage-assets/eva.glb', gltf => {
        avatarRoot = gltf.scene;
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
        }
        const box = new THREE.Box3().setFromObject(avatarRoot);
        const size = box.getSize(new THREE.Vector3());
        avatarRoot.scale.setScalar(1.68 / Math.max(size.y, 0.01));
        const centered = new THREE.Box3().setFromObject(avatarRoot);
        baseFloorY = -centered.min.y + 0.04;
        avatarRoot.position.y = baseFloorY;
        scene.add(avatarRoot);
        waveUntil = performance.now() + 2600;
      });

      const onResize = () => {
        const w = window.innerWidth, h = window.innerHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / Math.max(1, h);
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', onResize);
      onResize();

      const tmpEuler = new THREE.Euler();
      const tmpQuat = new THREE.Quaternion();
      const targetQuat = new THREE.Quaternion();
      const moodColor = new THREE.Color(0x38bdf8);
      function poseBone(name, rx, ry, rz, alpha) {
        const b = bones[name], rest = restQuats[name];
        if (!b || !rest) return;
        tmpEuler.set(rx, ry, rz, 'XYZ');
        tmpQuat.setFromEuler(tmpEuler);
        targetQuat.copy(rest).multiply(tmpQuat);
        b.quaternion.slerp(targetQuat, alpha);
      }

      let mouthOpen = 0, voiceEnergy = 0;
      const clock = new THREE.Clock();

      function animate() {
        requestAnimationFrame(animate);
        const dt = Math.min(clock.getDelta(), 0.1);
        const t = clock.elapsedTime;
        const smoothPose = Math.min(1, dt * 6.5);

        moodColor.setHex(evaMoodHex);
        rimLight.color.lerp(moodColor, Math.min(1, dt * 4));
        haloMat.color.lerp(moodColor, Math.min(1, dt * 4));
        field.mat.color.lerp(moodColor, Math.min(1, dt * 4));

        // Particle drift
        const pArr = field.geo.attributes.position.array;
        for (let i = 0; i < field.count; i++) {
          const ang = field.seeds[i * 3] + t * (0.18 + (i % 4) * 0.04);
          const rad = field.seeds[i * 3 + 1] + Math.sin(t * 1.2 + i) * 0.03;
          pArr[i * 3] = Math.cos(ang) * rad;
          pArr[i * 3 + 1] = field.seeds[i * 3 + 2] + Math.sin(t * 0.9 + i * 0.7) * 0.05;
          pArr[i * 3 + 2] = Math.sin(ang) * rad - 0.12;
        }
        field.geo.attributes.position.needsUpdate = true;

        // Voice & TTS Lip-sync energy
        let measured = 0;
        if (activeAnalyser && freqBins) {
          activeAnalyser.getByteFrequencyData(freqBins);
          let sum = 0;
          for (let i = 2; i < 36; i++) sum += freqBins[i];
          measured = Math.min(1.0, sum / (34 * 135));
        }
        if (ttsSpeaking && measured < 0.03) {
          const syll = Math.max(0, Math.sin(t * 11.6) * 0.52 + Math.sin(t * 7.3 + 0.8) * 0.42 + Math.sin(t * 17.1) * 0.22);
          measured = Math.min(0.86, syll * 0.78);
        }
        voiceEnergy += (measured - voiceEnergy) * Math.min(1, dt * 20);
        const speaking = ttsSpeaking || voiceEnergy > 0.05;
        const targetOpen = speaking ? Math.min(1.0, Math.max(0.05, voiceEnergy * 1.22)) : 0.0;
        mouthOpen += (targetOpen - mouthOpen) * Math.min(1, dt * 18);

        if (mouthParts) {
          const openCm = mouthOpen * 1.02;
          mouthParts.upperLip.position.y = openCm * 0.36;
          mouthParts.lowerLip.position.y = -openCm * 0.64;
          mouthParts.cavity.visible = mouthOpen > 0.045;
          if (mouthParts.cavity.visible) {
            mouthParts.cavity.scale.set(0.92, Math.max(0.08, mouthOpen * 0.95), 1.0);
          }
        }
        for (const m of jawMeshes) {
          if (m.morphTargetInfluences?.length) m.morphTargetInfluences[0] = mouthOpen * 0.85;
        }

        const drawerShift = (activeDrawer && window.innerWidth > 900) ? 0.26 : 0.0;
        camera.position.x += ((drawerShift + pointerX * 0.08) - camera.position.x) * Math.min(1, dt * 4);
        camera.lookAt(drawerShift * 0.65, 1.24, 0);
        halo.scale.setScalar(1.0 + voiceEnergy * 0.18 + Math.sin(t * 2.1) * 0.03);

        if (avatarRoot) {
          const breath = Math.sin(t * 1.55) * 0.5 + 0.5;
          const sway = Math.sin(t * 0.45) * 0.65 + Math.sin(t * 0.29 + 1.1) * 0.35;
          avatarRoot.position.x = sway * 0.012;
          avatarRoot.position.y = baseFloorY + breath * 0.0025;
          avatarRoot.rotation.y = sway * 0.03 + pointerX * 0.10;

          poseBone('Spine02', 0, sway * 0.015, -sway * 0.02, smoothPose);
          poseBone('Spine01', -breath * 0.022, sway * 0.016, 0, smoothPose);
          poseBone('Spine', -breath * 0.026 - (speaking ? 0.02 : 0), speaking ? Math.sin(t * 1.4) * 0.025 : 0, 0, smoothPose);

          const speakNod = speaking ? (-mouthOpen * 0.048 + Math.sin(t * 4.8) * 0.02) : 0;
          const gazeYaw = pointerX * 0.16 + Math.sin(t * 0.52) * 0.035;
          const gazePitch = -0.165 + pointerY * 0.07 + speakNod + breath * 0.012;
          poseBone('neck', -0.045, gazeYaw * 0.35, 0, smoothPose);
          poseBone('Head', gazePitch, gazeYaw * 0.75, Math.sin(t * 0.37) * 0.022, smoothPose);

          const waving = performance.now() < waveUntil;
          if (waving) {
            poseBone('RightArm', -0.55, 0.2, -0.85 + Math.sin(t * 9.0) * 0.18, smoothPose);
          } else {
            poseBone('RightArm', 0, 0, 0, smoothPose);
          }
        }
        renderer.render(scene, camera);
      }
      animate();
    }

    // --- 2. Natural Spoken Dutch Voice (Core/OpenAI Neural Audio -> Browser nl-NL Fallback) ---
    function stopSpeaking() {
      ttsSpeaking = false;
      try { window.speechSynthesis?.cancel(); } catch (_e) {}
      if (currentSource) {
        try { currentSource.stop(); } catch (_e) {}
        currentSource = null;
      }
    }

    function pickBestDutchVoice() {
      const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
      const nl = voices.filter(v => /^nl([-_]|$)/i.test(v.lang));
      if (!nl.length) return null;
      const preferred = ['Fenna', 'Colette', 'Natural', 'Google Nederlands', 'Claire', 'Xander'];
      for (const kw of preferred) {
        const hit = nl.find(v => v.name.includes(kw));
        if (hit) return hit;
      }
      return nl[0];
    }

    async function speakDutch(text) {
      const clean = String(text || '').trim();
      if (!clean) return;
      $('eva-subtitle').textContent = clean;
      if (!audioEnabled) return;
      stopSpeaking();

      // 1. Try neural speech audio endpoint first
      try {
        const res = await fetch('/internal/v1/appliance/living/speech', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: clean })
        });
        if (res.status === 200) {
          const buf = await res.arrayBuffer();
          if (buf.byteLength > 256) {
            if (!currentAudioCtx) {
              currentAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            if (currentAudioCtx.state === 'suspended') await currentAudioCtx.resume();
            const decoded = await currentAudioCtx.decodeAudioData(buf);
            const src = currentAudioCtx.createBufferSource();
            src.buffer = decoded;
            activeAnalyser = currentAudioCtx.createAnalyser();
            activeAnalyser.fftSize = 128;
            freqBins = new Uint8Array(activeAnalyser.frequencyBinCount);
            src.connect(activeAnalyser);
            activeAnalyser.connect(currentAudioCtx.destination);
            currentSource = src;
            ttsSpeaking = true;
            src.onended = () => { ttsSpeaking = false; currentSource = null; };
            src.start(0);
            userUnlockedAudio = true;
            $('first-voice-unlock').hidden = true;
            return;
          }
        }
      } catch (_e) {}

      // 2. Fallback to browser's natural Dutch speechSynthesis with real-time lip-sync cadence
      if ('speechSynthesis' in window) {
        const utt = new SpeechSynthesisUtterance(clean);
        utt.lang = 'nl-NL';
        utt.rate = 1.02;
        utt.pitch = 1.03;
        const v = pickBestDutchVoice();
        if (v) utt.voice = v;
        utt.onstart = () => {
          ttsSpeaking = true;
          userUnlockedAudio = true;
          $('first-voice-unlock').hidden = true;
        };
        utt.onend = () => { ttsSpeaking = false; };
        utt.onerror = () => { ttsSpeaking = false; };
        window.speechSynthesis.speak(utt);
        setTimeout(() => {
          if (!ttsSpeaking && !userUnlockedAudio) {
            $('first-voice-unlock').hidden = false;
          }
        }, 450);
      }
    }

    if ('speechSynthesis' in window) {
      window.speechSynthesis.onvoiceschanged = () => pickBestDutchVoice();
    }

    // --- 3. 16 kHz Mono PCM Voiceprint Capture + Live SpeechRecognition (nl-NL) ---
    let micActive = false;
    let micStopFn = null;

    async function toggleMicCapture() {
      if (micActive && micStopFn) {
        micStopFn();
        return;
      }
      userUnlockedAudio = true;
      $('first-voice-unlock').hidden = true;
      stopSpeaking();

      let stream = null;
      let audioCtx = null;
      let processor = null;
      const pcmChunks = [];
      let totalSamples = 0;

      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const src = audioCtx.createMediaStreamSource(stream);
        processor = audioCtx.createScriptProcessor(4096, 1, 1);
        const inRate = audioCtx.sampleRate || 48000;
        processor.onaudioprocess = ev => {
          if (totalSamples >= 96000) return; // cap at 6s @ 16kHz
          const input = ev.inputBuffer.getChannelData(0);
          const ratio = inRate / 16000;
          const outLen = Math.floor(input.length / ratio);
          const int16 = new Int16Array(outLen);
          for (let i = 0; i < outLen; i++) {
            const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          pcmChunks.push(int16);
          totalSamples += outLen;
        };
        src.connect(processor);
        processor.connect(audioCtx.destination);
      } catch (_err) {
        $('eva-subtitle').textContent = 'Geef je browser toegang tot de microfoon, of typ je wens hieronder.';
        return;
      }

      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      let rec = null;
      let transcript = '';
      micActive = true;
      $('btn-mic').classList.add('recording');
      $('talk-input').placeholder = 'Luisteren… spreek gewoon Nederlands (klik nogmaals om te sturen)';

      const finishCapture = async () => {
        if (!micActive) return;
        micActive = false;
        $('btn-mic').classList.remove('recording');
        $('talk-input').placeholder = 'Praat of typ gewoon Nederlands tegen Eva (bijv. een wens, grens of vraag)…';
        try { if (rec) rec.stop(); } catch (_e) {}
        try { if (processor) processor.disconnect(); } catch (_e) {}
        try { if (audioCtx) await audioCtx.close(); } catch (_e) {}
        try { stream?.getTracks().forEach(t => t.stop()); } catch (_e) {}

        // Encode 16 kHz s16le PCM to base64
        let pcmB64 = '';
        if (totalSamples >= 3200) {
          const merged = new Int16Array(totalSamples);
          let off = 0;
          for (const c of pcmChunks) { merged.set(c, off); off += c.length; }
          const bytes = new Uint8Array(merged.buffer);
          let bin = '';
          const step = 0x8000;
          for (let i = 0; i < bytes.length; i += step) {
            bin += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
          }
          pcmB64 = btoa(bin);
        }

        const finalTxt = ($('talk-input').value || transcript || '').trim();
        if (finalTxt) {
          $('talk-input').value = '';
          await sendLivingTurn({ text: finalTxt, audio_pcm_b64: pcmB64 });
        } else if (pcmB64) {
          await sendLivingTurn({
            text: 'Hoi Eva, leer alvast mijn stem herkennen!',
            audio_pcm_b64: pcmB64
          });
        }
      };

      micStopFn = finishCapture;

      if (SR) {
        rec = new SR();
        rec.lang = 'nl-NL';
        rec.interimResults = true;
        rec.continuous = false;
        rec.onresult = ev => {
          let combined = '';
          for (let i = 0; i < ev.results.length; i++) {
            combined += ev.results[i][0].transcript;
          }
          transcript = combined.trim();
          $('talk-input').value = transcript;
        };
        rec.onend = () => { if (micActive) finishCapture(); };
        rec.onerror = () => { if (micActive) setTimeout(finishCapture, 1200); };
        try { rec.start(); } catch (_e) { setTimeout(finishCapture, 3500); }
      } else {
        setTimeout(finishCapture, 3600);
      }
    }

    // --- 4. API & Reversible "Ik heb je gehoord" Cards ---
    function escapeHtml(s) {
      return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }

    function pushHeardCard(change) {
      const stack = $('heard-stack');
      const el = document.createElement('div');
      el.className = 'heard-card';
      el.innerHTML = `
        <div class="heard-head">
          <span>✨ ${escapeHtml(change.title || 'Ik heb je gehoord')}</span>
        </div>
        <div class="heard-body">${escapeHtml(change.summary || '')}</div>
        ${change.undoable && change.change_id ? `<button type="button" class="undo-btn" data-undo="${escapeHtml(change.change_id)}">↩ Ongedaan maken</button>` : ''}
      `;
      const undoBtn = el.querySelector('[data-undo]');
      if (undoBtn) {
        undoBtn.onclick = async () => {
          undoBtn.disabled = true;
          undoBtn.textContent = 'Terugdraaien…';
          try {
            const r = await fetch(`/internal/v1/appliance/living/undo/${encodeURIComponent(change.change_id)}`, { method: 'POST' });
            const d = await r.json();
            if (r.ok && d.stage) {
              applyStageState(d.stage);
              el.innerHTML = `<div class="heard-head"><span>↩ Teruggedraaid</span></div><div class="heard-body">${escapeHtml(d.label || 'Vorige instelling hersteld.')}</div>`;
              setTimeout(() => el.remove(), 3200);
            }
          } catch (_e) {
            undoBtn.disabled = false;
            undoBtn.textContent = '↩ Ongedaan maken';
          }
        };
      }
      stack.prepend(el);
      setTimeout(() => { if (el.parentNode) el.remove(); }, 18000);
    }

    function applyStageState(nextStage) {
      stage = nextStage;
      const eva = stage.eva || {};
      const hh = stage.household || {};

      $('hud-eva-name').textContent = `${eva.display_name || 'Eva'} · ${hh.owner_name || 'Thuis'}`;
      $('rules-count').textContent = `(${(hh.custom_rules || []).length})`;
      const devCount = (stage.discovered_devices || []).length;
      $('devices-count').textContent = devCount ? `(${devCount})` : '';

      const guestOn = !!hh.guest_mode_active;
      document.body.classList.toggle('guest-mode', guestOn);
      $('btn-guest-mode').classList.toggle('active-guest', guestOn);
      $('guest-mode-label').textContent = guestOn ? 'Privéschild actief (Visite)' : 'Visite-modus';
      $('hud-dot').classList.toggle('warn', guestOn);

      // Update 3D mood color from active preset or guest mode
      if (guestOn) {
        evaMoodHex = 0xf59e0b;
      } else {
        const activePreset = (eva.presets || []).find(p => p.preset_id === eva.preset_id);
        if (activePreset?.color) {
          evaMoodHex = parseInt(activePreset.color.replace('#', ''), 16) || 0x38bdf8;
        }
      }

      // Scene 1 vs Scene 2-4
      const needsAi = !stage.ai_ready;
      $('scene-heartbeat').hidden = !needsAi;
      $('bottom-dock').style.display = needsAi ? 'none' : 'flex';
      if (needsAi) {
        populateAltAiPresets();
      } else if (!stage.awakening_completed && !activeDrawer) {
        openDrawer('awakening');
      } else if (activeDrawer) {
        renderDrawerContent(activeDrawer);
      }
    }

    async function sendLivingTurn(payload) {
      $('btn-send').disabled = true;
      $('eva-subtitle').textContent = `${stage?.eva?.display_name || 'Eva'} denkt even mee…`;
      try {
        const res = await fetch('/internal/v1/appliance/living/turn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
          $('eva-subtitle').textContent = data.detail || 'Er ging even iets mis.';
          return;
        }
        if (data.stage) applyStageState(data.stage);
        if (Array.isArray(data.applied_changes)) {
          data.applied_changes.forEach(pushHeardCard);
        }
        if (data.voice_enrolled) {
          pushHeardCard({
            title: 'Stem lokaal herkend',
            summary: `Eva herkent voortaan het stemgeluid van ${stage?.household?.owner_name || 'jou'} (100% lokaal in huis).`,
            undoable: false
          });
        }
        if (data.conjure_panel) {
          openDrawer(data.conjure_panel);
        }
        waveUntil = performance.now() + 1800;
        if (data.reply_text) {
          await speakDutch(data.reply_text);
        }
      } catch (_e) {
        $('eva-subtitle').textContent = 'Verbinding met Eva werd kort onderbroken.';
      } finally {
        $('btn-send').disabled = false;
      }
    }

    // --- 5. Stage Drawers (Awakening & Character Spheres, Rules, Family & Lola QR, LAN Devices) ---
    function openDrawer(kind) {
      activeDrawer = kind;
      $('stage-drawer').hidden = false;
      renderDrawerContent(kind);
    }

    function closeDrawer() {
      activeDrawer = null;
      $('stage-drawer').hidden = true;
    }

    function renderDrawerContent(kind) {
      if (!stage) return;
      const body = $('drawer-body');
      const title = $('drawer-title');
      const eva = stage.eva || {};
      const hh = stage.household || {};

      if (kind === 'awakening') {
        title.textContent = '✨ Kies Eva’s Sfeer & Karakter';
        const spheresHtml = (eva.presets || []).map(p => `
          <button type="button" class="sphere-btn ${p.preset_id === eva.preset_id ? 'selected' : ''}" data-preset="${escapeHtml(p.preset_id)}">
            <span class="sphere-dot" style="background:${escapeHtml(p.color)}; color:${escapeHtml(p.color)};"></span>
            <span class="sphere-title">${escapeHtml(p.orb || p.label)}</span>
            <span class="sphere-sub">${escapeHtml(p.tagline)}</span>
          </button>
        `).join('');

        const ownerMember = (hh.members || []).find(m => m.role === 'gezinshoofd');
        const voiceBadge = ownerMember?.voice_enrolled
          ? '<span style="color:#6ee7b7; font-size:0.82rem;">✓ Jouw stemprofiel is lokaal ingeleerd</span>'
          : '<span style="color:var(--muted); font-size:0.82rem;">🎙️ Tip: klik op de microfoon onderaan om Eva je stem te laten leren</span>';

        body.innerHTML = `
          <div style="font-size:0.86rem; color:var(--muted); line-height:1.45;">
            Tik op een sfeerbol om direct te horen én voelen hoe ${escapeHtml(eva.display_name || 'Eva')} verandert:
          </div>
          <div class="spheres-grid">${spheresHtml}</div>
          <div style="padding:10px 12px; border-radius:12px; background:rgba(15,23,42,0.55); border:1px solid var(--border); margin-bottom:12px;">
            ${voiceBadge}
          </div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" class="btn-ghost" id="drawer-go-family" style="flex:1;">📱 Telefoon &amp; Lola QR</button>
            <button type="button" class="btn-ghost" id="drawer-go-devices" style="flex:1;">📡 Scan Huisnetwerk</button>
          </div>
        `;
        body.querySelectorAll('[data-preset]').forEach(btn => {
          btn.onclick = () => sendLivingTurn({ preset_id: btn.dataset.preset });
        });
        $('drawer-go-family').onclick = () => openDrawer('family');
        $('drawer-go-devices').onclick = () => openDrawer('devices');
        return;
      }

      if (kind === 'rules') {
        title.textContent = '📜 Onze Huisafspraken & Wensen';
        if (hh.guest_mode_active) {
          body.innerHTML = `
            <div style="padding:14px; border-radius:14px; background:rgba(120,53,15,0.45); border:1px solid rgba(245,158,11,0.5); font-size:0.88rem;">
              🛡️ <strong>Privéschild actief (Visite-modus)</strong><br/>
              Persoonlijke gezinsafspraken zijn verborgen zolang er bezoek is. Klik bovenaan op <em>Visite-modus</em> om ze weer te tonen.
            </div>
          `;
          return;
        }
        const rules = hh.custom_rules || [];
        const listHtml = rules.length
          ? rules.map((r, idx) => `
              <div class="list-item">
                <span>${escapeHtml(r)}</span>
                <button type="button" class="btn-ghost" data-del-rule="${idx}" title="Verwijder afspraak">✕</button>
              </div>
            `).join('')
          : `<div style="padding:14px; color:var(--muted); font-size:0.86rem;">
               Nog geen eigen huisafspraken. Zeg of typ onderaan gewoon iets als:<br/>
               <em>"Na 21:00 uur alleen storen als het dringend is"</em> of <em>"Over het eten op maandag met Lola overleggen"</em>.
             </div>`;
        body.innerHTML = `
          <div style="font-size:0.83rem; color:var(--muted); margin-bottom:10px;">
            Alles wat je tegen Eva afspreekt verschijnt hier in gewoon Nederlands:
          </div>
          ${listHtml}
        `;
        body.querySelectorAll('[data-del-rule]').forEach(btn => {
          btn.onclick = async () => {
            btn.disabled = true;
            const idx = btn.dataset.delRule;
            const r = await fetch(`/internal/v1/appliance/living/rules/${idx}/remove`, { method: 'POST' });
            const d = await r.json();
            if (r.ok && d.stage) {
              applyStageState(d.stage);
              pushHeardCard({
                change_id: d.change_id,
                title: 'Huisafspraak verwijderd',
                summary: d.removed,
                undoable: true
              });
            }
          };
        });
        return;
      }

      if (kind === 'family') {
        title.textContent = '👨‍👩‍👧 Gezin, Telefoon & Welkomstkaart';
        const membersHtml = (hh.members || []).map(m => `
          <div class="list-item">
            <div>
              <strong>${escapeHtml(m.display_name)}</strong>
              <span style="font-size:0.76rem; color:var(--muted);"> · ${m.role === 'gezinshoofd' ? 'Gezinshoofd' : (m.role === 'gezin' ? 'Kerngezin' : 'Gast')}</span>
              ${m.voice_enrolled ? '<div style="font-size:0.74rem; color:#6ee7b7;">🎙️ Stem herkend</div>' : ''}
            </div>
          </div>
        `).join('');

        body.innerHTML = `
          <div style="margin-bottom:12px;">${membersHtml}</div>
          <div style="padding:13px; border-radius:16px; background:rgba(15,23,42,0.65); border:1px solid var(--border); margin-bottom:12px;">
            <div style="font-weight:700; font-size:0.9rem; margin-bottom:6px;">💌 Persoonlijke 1-Scan Welkomstkaart</div>
            <div style="font-size:0.8rem; color:var(--muted); margin-bottom:10px;">
              Laat Lola of een gast deze QR scannen. Eva begroet haar dan persoonlijk in 3D op haar eigen telefoon, leert haar wensen &amp; stem kennen en opent haar eigen Eva Pocket!
            </div>
            <div class="field">
              <label>Voor wie?</label>
              <input id="wlk-name" type="text" value="Lola" placeholder="Bijv. Lola" />
            </div>
            <div class="field">
              <label>Rol in huis</label>
              <select id="wlk-role">
                <option value="gezin">Kerngezin (eigen wensen + stemherkenning)</option>
                <option value="gast">Familie / Vrienden op bezoek (gastmodus)</option>
              </select>
            </div>
            <button type="button" class="btn-primary" id="btn-make-wlk">Maak Persoonlijke QR-Uitnodiging</button>
            <div id="wlk-result"></div>
          </div>

          <div style="padding:13px; border-radius:16px; background:rgba(15,23,42,0.65); border:1px solid var(--border);">
            <div style="font-weight:700; font-size:0.9rem; margin-bottom:6px;">📱 Jouw eigen Eva Pocket (${escapeHtml(hh.owner_name || 'Eigenaar')})</div>
            <button type="button" class="btn-ghost" id="btn-owner-pocket" style="width:100%;">Toon QR voor mijn eigen telefoon</button>
            <div id="owner-pocket-result"></div>
          </div>
        `;

        $('btn-make-wlk').onclick = async () => {
          const btn = $('btn-make-wlk');
          btn.disabled = true;
          try {
            const res = await fetch('/internal/v1/appliance/living/member-welcome', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                member_name: $('wlk-name').value || 'Lola',
                role: $('wlk-role').value || 'gezin'
              })
            });
            const d = await res.json();
            if (res.ok) {
              $('wlk-result').innerHTML = `
                <div class="qr-box">
                  ${d.qr_svg}
                  <div style="font-weight:700; margin-top:8px;">Scan met de camera van ${escapeHtml(d.member_name)}</div>
                  <div style="font-size:0.74rem; word-break:break-all; color:#475569; margin-top:4px;">${escapeHtml(d.url)}</div>
                </div>
              `;
            }
          } finally {
            btn.disabled = false;
          }
        };

        $('btn-owner-pocket').onclick = async () => {
          const res = await fetch('/internal/v1/appliance/contact/device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'pocket', label: `${hh.owner_name || 'Jaap'} Telefoon`, allow_private: true, allow_owner_voice: true })
          });
          const d = await res.json();
          if (res.ok) {
            $('owner-pocket-result').innerHTML = `
              <div class="qr-box">
                ${d.qr_svg}
                <div style="font-weight:700; margin-top:8px;">Jouw Eva Pocket</div>
                <div style="font-size:0.74rem; word-break:break-all; color:#475569; margin-top:4px;">${escapeHtml(d.url)}</div>
              </div>
            `;
          }
        };
        return;
      }

      if (kind === 'devices') {
        title.textContent = '📡 Gevonden in jouw Huisnetwerk';
        const devs = stage.discovered_devices || [];
        const devHtml = devs.length
          ? devs.map(d => `
              <div class="list-item">
                <div>
                  <strong>${escapeHtml(d.friendly_name || d.hostname || d.ip || 'Apparaat')}</strong>
                  <div style="font-size:0.75rem; color:var(--muted);">${escapeHtml(d.category || 'netwerk')} · ${escapeHtml(d.consent_status || 'nieuw')}</div>
                </div>
                <div style="display:flex; gap:6px;">
                  <button type="button" class="btn-ghost" data-dev="${escapeHtml(d.device_id)}" data-ok="1">✓ Gebruik</button>
                  <button type="button" class="btn-ghost" data-dev="${escapeHtml(d.device_id)}" data-ok="0">Negeer</button>
                </div>
              </div>
            `).join('')
          : `<div style="padding:14px; color:var(--muted); font-size:0.86rem;">
               Klik hieronder op <strong>Scan Thuisnetwerk</strong> zodat Eva zelf kijkt welke slimme lampen, speakers of schermen er aanwezig zijn.
             </div>`;
        body.innerHTML = `
          <button type="button" class="btn-primary" id="btn-run-lanscan" style="margin-bottom:12px;">🔍 Scan Thuisnetwerk nu</button>
          ${devHtml}
        `;
        $('btn-run-lanscan').onclick = async () => {
          const btn = $('btn-run-lanscan');
          btn.disabled = true;
          btn.textContent = 'Bezig met rondkijken op het netwerk…';
          try {
            const r = await fetch('/internal/v1/appliance/living/lan-scan', { method: 'POST' });
            const d = await r.json();
            if (r.ok && d.stage) applyStageState(d.stage);
          } finally {
            btn.disabled = false;
            btn.textContent = '🔍 Scan Thuisnetwerk nu';
          }
        };
        body.querySelectorAll('[data-dev]').forEach(btn => {
          btn.onclick = async () => {
            const ok = btn.dataset.ok === '1';
            const r = await fetch(`/internal/v1/appliance/living/lan-device/${encodeURIComponent(btn.dataset.dev)}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ approved: ok })
            });
            const d = await r.json();
            if (r.ok && d.stage) applyStageState(d.stage);
          };
        });
      }
    }

    // --- 6. Scene 1 AI Wake Controls (ChatGPT Codex 1-click + 14 Providers) ---
    let codexPollTimer = null;

    function populateAltAiPresets() {
      const sel = $('alt-preset');
      const presets = stage?.model?.api_presets || [];
      if (!presets.length || sel.options.length) return;
      presets.forEach((p, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = p.label;
        sel.appendChild(opt);
      });
      const syncFields = () => {
        const p = presets[Number(sel.value) || 0];
        if (!p) return;
        $('alt-model').value = p.default_model || '';
        $('alt-endpoint').value = p.default_endpoint || '';
        $('alt-key-wrap').style.display = p.requires_api_key ? 'block' : 'none';
      };
      sel.onchange = syncFields;
      syncFields();
    }

    $('btn-codex-wake').onclick = async () => {
      userUnlockedAudio = true;
      const btn = $('btn-codex-wake');
      btn.disabled = true;
      try {
        const r = await fetch('/internal/v1/appliance/model/codex/start', { method: 'POST' });
        const d = await r.json();
        if (!r.ok) {
          $('codex-wake-box').hidden = false;
          $('codex-poll-status').textContent = d.detail || 'Kon ChatGPT-koppeling niet starten; kies hieronder een API-model.';
          return;
        }
        if (d.logged_in) {
          await finalizeWakeUp();
          return;
        }
        $('codex-wake-box').hidden = false;
        $('codex-user-code').textContent = d.user_code || 'Even geduld…';
        if (d.verification_url) $('codex-verify-link').href = d.verification_url;
        if (codexPollTimer) clearInterval(codexPollTimer);
        codexPollTimer = setInterval(async () => {
          const pr = await fetch('/internal/v1/appliance/model/codex/status');
          const pd = await pr.json();
          if (pd.user_code) $('codex-user-code').textContent = pd.user_code;
          if (pd.verification_url) $('codex-verify-link').href = pd.verification_url;
          if (pd.logged_in) {
            clearInterval(codexPollTimer);
            await finalizeWakeUp();
          }
        }, 2200);
      } finally {
        btn.disabled = false;
      }
    };

    $('btn-copy-codex').onclick = () => {
      navigator.clipboard?.writeText($('codex-user-code').textContent.trim());
      $('btn-copy-codex').textContent = '✓ Gekopieerd';
    };

    $('btn-toggle-alt-ai').onclick = () => {
      $('alt-ai-panel').hidden = !$('alt-ai-panel').hidden;
    };

    $('btn-save-alt-ai').onclick = async () => {
      userUnlockedAudio = true;
      const presets = stage?.model?.api_presets || [];
      const p = presets[Number($('alt-preset').value) || 0] || {};
      const btn = $('btn-save-alt-ai');
      btn.disabled = true;
      $('alt-ai-msg').textContent = '';
      try {
        const r = await fetch('/internal/v1/appliance/model/api', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: p.kind || 'openai_compatible',
            label: p.label || 'AI-model',
            model: $('alt-model').value,
            endpoint: $('alt-endpoint').value,
            api_key: $('alt-key').value
          })
        });
        const d = await r.json();
        if (!r.ok) {
          $('alt-ai-msg').textContent = d.detail || 'Verbinding mislukt';
          return;
        }
        await finalizeWakeUp();
      } finally {
        btn.disabled = false;
      }
    };

    async function finalizeWakeUp() {
      $('codex-poll-status').textContent = '✨ Eva wordt nu gewekt en geactiveerd…';
      try {
        await fetch('/internal/v1/appliance/apply', { method: 'POST' });
      } catch (_e) {}
      await loadInitialStage(true);
    }

    // --- 7. Event Wiring & Initial Awakening ---
    $('btn-close-drawer').onclick = closeDrawer;
    $('btn-awakening').onclick = () => activeDrawer === 'awakening' ? closeDrawer() : openDrawer('awakening');
    $('btn-rules').onclick = () => activeDrawer === 'rules' ? closeDrawer() : openDrawer('rules');
    $('btn-family').onclick = () => activeDrawer === 'family' ? closeDrawer() : openDrawer('family');
    $('btn-devices').onclick = () => activeDrawer === 'devices' ? closeDrawer() : openDrawer('devices');

    $('btn-audio-toggle').onclick = () => {
      audioEnabled = !audioEnabled;
      userUnlockedAudio = true;
      $('btn-audio-toggle').classList.toggle('active-cyan', audioEnabled);
      $('audio-label').textContent = audioEnabled ? 'Stem aan' : 'Stil';
      if (!audioEnabled) stopSpeaking();
    };

    $('btn-hear-greeting').onclick = () => {
      userUnlockedAudio = true;
      $('first-voice-unlock').hidden = true;
      speakDutch($('eva-subtitle').textContent);
    };

    $('btn-guest-mode').onclick = async () => {
      userUnlockedAudio = true;
      const next = !(stage?.household?.guest_mode_active);
      const r = await fetch('/internal/v1/appliance/living/guest-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next })
      });
      const d = await r.json();
      if (r.ok && d.stage) {
        applyStageState(d.stage);
        pushHeardCard({
          change_id: d.change_id,
          title: next ? 'Gastvrije Modus & Privéschild actief' : 'Privé Gezinsmodus hersteld',
          summary: next
            ? 'Privézaken van het gezin zijn direct afgeschermd voor bezoek.'
            : 'Welkom terug onder ons — alle gezinskaarten zijn weer zichtbaar.',
          undoable: true
        });
        speakDutch(
          next
            ? 'Gezellig, visite! Ik heb het privéschild aangezet en doe gezellig mee als gastvrouw.'
            : 'Welkom terug onder ons! De visite-modus staat weer uit.'
        );
      }
    };

    $('btn-mic').onclick = toggleMicCapture;

    $('living-form').onsubmit = async ev => {
      ev.preventDefault();
      userUnlockedAudio = true;
      const val = $('talk-input').value.trim();
      if (!val) return;
      $('talk-input').value = '';
      await sendLivingTurn({ text: val });
    };

    document.querySelectorAll('[data-say]').forEach(chip => {
      chip.onclick = () => {
        userUnlockedAudio = true;
        sendLivingTurn({ text: chip.dataset.say });
      };
    });
    document.querySelectorAll('[data-panel]').forEach(chip => {
      chip.onclick = () => openDrawer(chip.dataset.panel);
    });

    async function loadInitialStage(justWoke = false) {
      const res = await fetch('/internal/v1/appliance/living/state');
      if (res.status === 401) {
        location.href = '/pair';
        return;
      }
      const data = await res.json();
      applyStageState(data);
      if (data.ai_ready) {
        const evaNm = data.eva?.display_name || 'Eva';
        const ownerNm = data.household?.owner_name || 'Jaap';
        const fam = data.household?.family_members || [];
        const famBit = fam.length ? ` en ${fam.join(', ')}` : '';
        const greet = justWoke || !data.awakening_completed
          ? `Hoi ${ownerNm}! Ik ben wakker. Ik ben ${evaNm} — vertel me gerust met wie we hier samenwonen, proef rechts mijn karakterbollen, of zeg direct hoe jij wilt dat ik meedraai in huis.`
          : `Welkom thuis, ${ownerNm}${famBit}! Zeg of vraag gerust wat ik voor jullie kan regelen.`;
        await speakDutch(greet);
      }
    }

    boot3DEva();
    loadInitialStage(false);
  </script>
</body>
</html>
"""


MEMBER_WELCOME_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#050914" />
  <title>Welkom bij Eva</title>
  <script type="importmap">
    {
      "imports": {
        "three": "/internal/v1/appliance/stage-assets/three.module.min.js"
      }
    }
  </script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #050914;
      --glass: rgba(12, 22, 44, 0.86);
      --border: rgba(148, 163, 184, 0.22);
      --accent: #38bdf8;
      --emerald: #10b981;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0; width: 100vw; height: 100vh; height: 100dvh;
      overflow: hidden; background:
        radial-gradient(circle at 50% 34%, rgba(56, 189, 248, 0.20), transparent 55%),
        radial-gradient(circle at 50% 88%, rgba(16, 185, 129, 0.14), transparent 50%),
        var(--bg);
      color: var(--text);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    #eva-canvas {
      position: fixed; inset: 0; width: 100vw; height: 100vh; height: 100dvh; z-index: 1;
    }
    .welcome-dock {
      position: fixed; left: 50%; transform: translateX(-50%);
      bottom: calc(16px + env(safe-area-inset-bottom, 0px));
      z-index: 15; width: min(500px, calc(100vw - 24px));
      padding: 20px; border-radius: 24px;
      background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
      max-height: 62dvh; overflow-y: auto;
    }
    h1 { margin: 0 0 6px; font-size: 1.32rem; }
    .greet { font-size: 0.93rem; line-height: 1.5; color: #e2e8f0; margin-bottom: 14px; }
    .chips { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 12px; }
    .chip {
      padding: 7px 12px; border-radius: 999px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.72); color: #cbd5e1; font-size: 0.8rem; cursor: pointer;
    }
    .chip.selected {
      border-color: var(--emerald); background: rgba(6, 78, 59, 0.65); color: #ecfdf5;
    }
    .wish-in {
      width: 100%; padding: 11px 14px; border-radius: 12px;
      border: 1px solid var(--border); background: rgba(2, 6, 23, 0.72);
      color: var(--text); font-size: 0.92rem; margin-bottom: 10px;
    }
    .row { display: flex; gap: 8px; align-items: center; }
    .btn-main {
      flex: 1; padding: 13px 16px; border-radius: 14px; border: none;
      background: linear-gradient(135deg, #38bdf8, #10b981);
      color: #04121f; font-weight: 700; font-size: 0.95rem; cursor: pointer;
      text-decoration: none; text-align: center; display: inline-block;
    }
    .btn-mic {
      padding: 12px 14px; border-radius: 14px; border: 1px solid rgba(56, 189, 248, 0.45);
      background: rgba(8, 47, 73, 0.65); color: #e0f2fe; font-weight: 600; font-size: 0.85rem; cursor: pointer;
    }
    .btn-mic.rec { border-color: #f43f5e; background: rgba(136, 19, 55, 0.75); color: #fff; }
  </style>
</head>
<body>
  <canvas id="eva-canvas"></canvas>

  <main class="welcome-dock" id="welcome-card">
    <h1 id="wlk-title">Even geduld…</h1>
    <div class="greet" id="wlk-greet">Eva stapt naar voren om je te begroeten…</div>

    <div id="wlk-interactive" hidden>
      <div style="margin-bottom:10px;">
        <button type="button" class="btn-mic" id="btn-hear" style="width:100%; margin-bottom:8px;">
          🔊 Laat Eva je hardop begroeten
        </button>
      </div>
      <div class="chips" id="wlk-chips"></div>
      <input type="text" class="wish-in" id="wlk-wish" placeholder="Of typ wat jij fijn vindt (bijv. humor, rusttijden, hobby's)…" />
      <div class="row" style="margin-bottom:10px;" id="voice-row">
        <button type="button" class="btn-mic" id="btn-wlk-voice" style="width:100%;">
          🎙️ Spreek 3 sec in zodat Eva jouw stem herkent
        </button>
      </div>
      <button type="button" class="btn-main" id="btn-wlk-done" style="width:100%;">
        ✨ Opslaan &amp; Open mijn Eva Pocket
      </button>
    </div>

    <div id="wlk-ready" hidden>
      <a href="#" class="btn-main" id="btn-open-pocket" style="width:100%;">
        🚀 Open mijn persoonlijke Eva Pocket
      </a>
    </div>
  </main>

  <script type="module">
    import * as THREE from 'three';
    import { GLTFLoader } from '/internal/v1/appliance/stage-assets/GLTFLoader.js';

    const $ = id => document.getElementById(id);
    const token = location.pathname.split('/').filter(Boolean).pop() || '';
    let welcomeData = null;
    let recordedPcmB64 = '';
    let ttsSpeaking = false;

    // 3D Eva Stage
    (function init3D() {
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({ canvas: $('eva-canvas'), antialias: true, alpha: true });
      } catch (_e) { return; }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, window.innerWidth / window.innerHeight, 0.1, 40);
      camera.position.set(0, 1.38, 2.65);
      scene.add(new THREE.HemisphereLight(0xbae6fd, 0x091326, 1.5));
      const key = new THREE.DirectionalLight(0xffffff, 1.85);
      key.position.set(1.5, 2.7, 2.5);
      scene.add(key);
      const rim = new THREE.DirectionalLight(0x10b981, 1.25);
      rim.position.set(-1.8, 2.0, -1.8);
      scene.add(rim);

      let root = null, floorY = 0.04;
      const bones = {}, rest = {}, jawMeshes = [];
      new GLTFLoader().load('/internal/v1/appliance/stage-assets/eva.glb', gltf => {
        root = gltf.scene;
        root.traverse(n => {
          if (n.isBone && n.name) { bones[n.name] = n; rest[n.name] = n.quaternion.clone(); }
          if (n.isSkinnedMesh && n.geometry?.attributes?.position) {
            const pos = n.geometry.attributes.position;
            const jaw = new Float32Array(pos.array.length);
            jaw.set(pos.array);
            for (let i = 0; i < pos.count; i++) {
              const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
              if (z > 0.08 && y >= 1.20 && y <= 1.315) {
                const w = Math.max(0, 1 - Math.abs(x) / 0.14) * Math.sin(Math.PI * Math.max(0, Math.min(1, (y - 1.20) / 0.115)));
                jaw[i * 3 + 1] = y - 0.017 * w;
              }
            }
            n.geometry.morphAttributes.position = [new THREE.BufferAttribute(jaw, 3)];
            n.updateMorphTargets();
            jawMeshes.push(n);
          }
        });
        const box = new THREE.Box3().setFromObject(root);
        root.scale.setScalar(1.68 / Math.max(box.getSize(new THREE.Vector3()).y, 0.01));
        floorY = -new THREE.Box3().setFromObject(root).min.y + 0.04;
        root.position.y = floorY + 0.12;
        scene.add(root);
      });

      const onResize = () => {
        renderer.setSize(window.innerWidth, window.innerHeight, false);
        camera.aspect = window.innerWidth / Math.max(1, window.innerHeight);
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', onResize);
      onResize();
      camera.lookAt(0, 1.18, 0);

      const clock = new THREE.Clock();
      const eul = new THREE.Euler(), q = new THREE.Quaternion(), tq = new THREE.Quaternion();
      let waveEnd = performance.now() + 3500;
      function anim() {
        requestAnimationFrame(anim);
        const dt = Math.min(clock.getDelta(), 0.1), t = clock.elapsedTime;
        const open = ttsSpeaking ? Math.max(0.05, Math.abs(Math.sin(t * 12.5)) * 0.72) : 0;
        for (const m of jawMeshes) {
          if (m.morphTargetInfluences?.length) m.morphTargetInfluences[0] = open;
        }
        if (root) {
          const breath = Math.sin(t * 1.6) * 0.5 + 0.5;
          root.position.y = floorY + 0.10 + breath * 0.003;
          const pose = (nm, rx, ry, rz) => {
            if (!bones[nm]) return;
            eul.set(rx, ry, rz, 'XYZ'); q.setFromEuler(eul);
            tq.copy(rest[nm]).multiply(q);
            bones[nm].quaternion.slerp(tq, Math.min(1, dt * 6));
          };
          pose('Head', -0.16 + (ttsSpeaking ? -open * 0.05 : 0), Math.sin(t * 0.6) * 0.05, Math.sin(t * 0.4) * 0.02);
          if (performance.now() < waveEnd) {
            pose('RightArm', -0.55, 0.2, -0.85 + Math.sin(t * 9.0) * 0.2);
          } else {
            pose('RightArm', 0, 0, 0);
          }
        }
        renderer.render(scene, camera);
      }
      anim();
    })();

    function speakNl(text) {
      if (!('speechSynthesis' in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'nl-NL';
      u.rate = 1.02;
      const nl = window.speechSynthesis.getVoices().find(v => /^nl/i.test(v.lang));
      if (nl) u.voice = nl;
      u.onstart = () => { ttsSpeaking = true; };
      u.onend = () => { ttsSpeaking = false; };
      u.onerror = () => { ttsSpeaking = false; };
      window.speechSynthesis.speak(u);
    }

    async function record3sVoice() {
      const btn = $('btn-wlk-voice');
      btn.disabled = true;
      btn.classList.add('rec');
      btn.textContent = '🎙️ Zeg nu rustig: "Hoi Eva, ik ben het!" (3 sec)…';
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const src = ctx.createMediaStreamSource(stream);
        const proc = ctx.createScriptProcessor(4096, 1, 1);
        const chunks = [];
        let total = 0;
        const rate = ctx.sampleRate || 48000;
        proc.onaudioprocess = ev => {
          if (total >= 48000) return;
          const inp = ev.inputBuffer.getChannelData(0);
          const ratio = rate / 16000;
          const n = Math.floor(inp.length / ratio);
          const arr = new Int16Array(n);
          for (let i = 0; i < n; i++) {
            const s = Math.max(-1, Math.min(1, inp[Math.floor(i * ratio)]));
            arr[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          chunks.push(arr);
          total += n;
        };
        src.connect(proc);
        proc.connect(ctx.destination);
        await new Promise(r => setTimeout(r, 3100));
        proc.disconnect();
        await ctx.close();
        stream.getTracks().forEach(t => t.stop());

        const merged = new Int16Array(total);
        let off = 0;
        for (const c of chunks) { merged.set(c, off); off += c.length; }
        const bytes = new Uint8Array(merged.buffer);
        let bin = '';
        for (let i = 0; i < bytes.length; i += 0x8000) {
          bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        }
        recordedPcmB64 = btoa(bin);
        btn.classList.remove('rec');
        btn.textContent = '✓ Stemgeluid vastgelegd (100% lokaal in huis)';
      } catch (_e) {
        btn.classList.remove('rec');
        btn.disabled = false;
        btn.textContent = '⚠️ Microfoon niet beschikbaar (je kunt gewoon doorgaan)';
      }
    }

    async function initWelcome() {
      const res = await fetch(`/internal/v1/appliance/welcome/${encodeURIComponent(token)}`);
      const d = await res.json();
      if (!res.ok) {
        $('wlk-title').textContent = 'Uitnodiging niet geldig';
        $('wlk-greet').textContent = d.detail || 'Vraag in huis even om een verse QR-code.';
        return;
      }
      welcomeData = d;
      $('wlk-title').textContent = `Hoi ${d.member_name}! 👋`;
      $('wlk-greet').textContent = d.greeting_text;
      $('wlk-interactive').hidden = false;
      if (d.role !== 'gezin') {
        $('voice-row').style.display = 'none';
      }

      const chipsEl = $('wlk-chips');
      (d.wish_chips || []).forEach(txt => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'chip';
        b.textContent = txt;
        b.onclick = () => {
          b.classList.toggle('selected');
          const picked = [...chipsEl.querySelectorAll('.chip.selected')].map(x => x.textContent);
          $('wlk-wish').value = picked.join('; ');
        };
        chipsEl.appendChild(b);
      });

      $('btn-hear').onclick = () => speakNl(d.greeting_text);
      $('btn-wlk-voice').onclick = record3sVoice;

      $('btn-wlk-done').onclick = async () => {
        const btn = $('btn-wlk-done');
        btn.disabled = true;
        btn.textContent = 'Je eigen Eva Pocket klaarmaken…';
        try {
          const r = await fetch(`/internal/v1/appliance/welcome/${encodeURIComponent(token)}/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              wishes: $('wlk-wish').value.trim(),
              audio_pcm_b64: recordedPcmB64
            })
          });
          const out = await r.json();
          if (!r.ok) {
            $('wlk-greet').textContent = out.detail || 'Er ging iets mis.';
            btn.disabled = false;
            return;
          }
          $('wlk-greet').textContent = out.reply_text;
          speakNl(out.reply_text);
          $('wlk-interactive').hidden = true;
          $('wlk-ready').hidden = false;
          $('btn-open-pocket').href = out.pocket_path || out.pocket_url;
        } catch (_e) {
          btn.disabled = false;
          btn.textContent = '✨ Opslaan & Open mijn Eva Pocket';
        }
      };
    }

    initWelcome();
  </script>
</body>
</html>
"""


__all__ = ["IMMERSIVE_STAGE_HTML", "MEMBER_WELCOME_HTML"]
