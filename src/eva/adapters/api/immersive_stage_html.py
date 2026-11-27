"""Fullscreen 3D Immersive Awakening Stage (`GET /`) with 6-Step Guided Wizard & Member Welcome (`GET /welkom/{token}`)."""

from __future__ import annotations

IMMERSIVE_STAGE_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#050914" />
  <title>Eva — Kennismaking &amp; Levend Contact</title>
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
      --glass: rgba(10, 18, 36, 0.76);
      --glass-strong: rgba(11, 20, 40, 0.92);
      --border: rgba(148, 163, 184, 0.22);
      --accent: #38bdf8;
      --accent2: #10b981;
      --gold: #f59e0b;
      --rose: #fb7185;
      --text: #f8fafc;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    html, body {
      margin: 0; padding: 0; width: 100vw; height: 100vh; height: 100dvh;
      overflow: hidden; background: var(--bg); color: var(--text);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .aurora-bg {
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background:
        radial-gradient(circle at 38% 38%, rgba(56, 189, 248, 0.18), transparent 55%),
        radial-gradient(circle at 50% 86%, rgba(16, 185, 129, 0.11), transparent 48%),
        var(--bg);
      transition: background 0.8s ease;
    }
    body.guest-mode .aurora-bg {
      background:
        radial-gradient(circle at 42% 36%, rgba(245, 158, 11, 0.22), transparent 56%),
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
      top: calc(10px + env(safe-area-inset-top, 0px));
      left: calc(12px + env(safe-area-inset-left, 0px));
      right: calc(12px + env(safe-area-inset-right, 0px));
      z-index: 24;
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none;
      gap: 8px; pointer-events: auto; padding-bottom: 2px;
    }
    .top-hud::-webkit-scrollbar { display: none; }
    .hud-group {
      display: flex; flex-wrap: nowrap; flex-shrink: 0; gap: 6px; align-items: center;
    }
    .pill {
      display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; white-space: nowrap;
      padding: 6px 11px; border-radius: 999px; font-size: 0.77rem; font-weight: 600;
      color: var(--text); background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      cursor: pointer; transition: all 0.18s ease; text-decoration: none;
    }
    .pill:hover { border-color: rgba(56, 189, 248, 0.6); transform: translateY(-1px); }
    .pill.static { cursor: default; }
    .pill.static:hover { transform: none; border-color: var(--border); }
    .pill.active-cyan {
      border-color: rgba(56, 189, 248, 0.75);
      background: rgba(8, 51, 78, 0.72);
      color: #e0f2fe;
      box-shadow: 0 0 20px rgba(56, 189, 248, 0.22);
    }
    .pill.active-emerald {
      border-color: rgba(16, 185, 129, 0.75);
      background: rgba(6, 78, 59, 0.68);
      color: #d1fae5;
    }
    .pill.active-guest {
      border-color: rgba(245, 158, 11, 0.85);
      background: rgba(120, 53, 15, 0.76);
      color: #fef3c7;
      box-shadow: 0 0 24px rgba(245, 158, 11, 0.3);
    }
    .dot {
      width: 8px; height: 8px; border-radius: 50%; background: var(--accent2);
      box-shadow: 0 0 10px var(--accent2);
    }
    .dot.warn { background: var(--gold); box-shadow: 0 0 10px var(--gold); }

    /* Reversible "Ik heb je gehoord" Stack */
    .heard-stack {
      position: fixed;
      top: calc(54px + env(safe-area-inset-top, 0px));
      left: calc(14px + env(safe-area-inset-left, 0px));
      z-index: 25; width: min(355px, calc(100vw - 28px));
      display: flex; flex-direction: column; gap: 8px; pointer-events: none;
    }
    .heard-card {
      pointer-events: auto; padding: 12px 14px; border-radius: 16px;
      background: rgba(6, 42, 48, 0.90); border: 1px solid rgba(16, 185, 129, 0.58);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      box-shadow: 0 14px 36px rgba(0, 0, 0, 0.5);
      animation: slideIn 0.25s ease-out;
    }
    @keyframes slideIn {
      from { opacity: 0; transform: translateY(-6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .heard-head { font-size: 0.79rem; font-weight: 700; color: #6ee7b7; margin-bottom: 3px; }
    .heard-body { font-size: 0.85rem; color: #ecfdf5; line-height: 1.4; }
    .undo-btn {
      margin-top: 7px; padding: 4px 10px; border-radius: 8px;
      border: 1px solid rgba(110, 231, 183, 0.45); background: rgba(2, 24, 28, 0.7);
      color: #a7f3d0; font-size: 0.76rem; font-weight: 600; cursor: pointer;
    }

    /* Right Guided Wizard & Stage Panel */
    .stage-drawer {
      position: fixed;
      top: calc(54px + env(safe-area-inset-top, 0px));
      right: calc(16px + env(safe-area-inset-right, 0px));
      bottom: calc(136px + env(safe-area-inset-bottom, 0px));
      width: min(470px, calc(100vw - 32px));
      z-index: 22; overflow-y: auto; padding: 20px; border-radius: 24px;
      background: var(--glass-strong); border: 1px solid rgba(56, 189, 248, 0.28);
      backdrop-filter: blur(22px); -webkit-backdrop-filter: blur(22px);
      box-shadow: 0 26px 70px rgba(0, 0, 0, 0.62);
    }
    .stage-drawer[hidden] { display: none; }
    .drawer-head {
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      margin-bottom: 12px;
    }
    .drawer-head h2 { margin: 0; font-size: 1.08rem; letter-spacing: -0.01em; }
    .close-x {
      background: rgba(15, 23, 42, 0.7); border: 1px solid var(--border);
      color: var(--muted); border-radius: 9px; padding: 4px 9px; font-size: 0.85rem; cursor: pointer;
    }
    .close-x:hover { color: #fff; border-color: var(--accent); }

    /* 6-Step Wizard Stepper */
    .wiz-steps {
      display: grid; grid-template-columns: repeat(6, 1fr); gap: 5px; margin-bottom: 14px;
    }
    .wiz-step-pill {
      padding: 6px 4px; border-radius: 9px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.65); color: var(--muted);
      font-size: 0.72rem; font-weight: 700; text-align: center; cursor: pointer;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .wiz-step-pill.active {
      border-color: var(--accent); background: rgba(14, 65, 102, 0.72); color: #fff;
    }
    .wiz-step-pill.done {
      border-color: rgba(16, 185, 129, 0.5); color: #6ee7b7;
    }

    .card-sec {
      padding: 13px 14px; border-radius: 16px; margin-bottom: 11px;
      background: rgba(15, 23, 42, 0.62); border: 1px solid var(--border);
    }
    .badge-ok {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 5px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 700;
      background: rgba(6, 78, 59, 0.65); border: 1px solid rgba(16, 185, 129, 0.55); color: #a7f3d0;
    }
    .badge-info {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 5px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
      background: rgba(8, 47, 73, 0.68); border: 1px solid rgba(56, 189, 248, 0.45); color: #bae6fd;
    }

    .field { margin-bottom: 10px; }
    .field label { display: block; font-size: 0.8rem; font-weight: 600; color: #cbd5e1; margin-bottom: 4px; }
    .field input, .field select {
      width: 100%; padding: 9px 12px; border-radius: 10px;
      background: rgba(2, 6, 23, 0.78); border: 1px solid var(--border);
      color: var(--text); font-size: 0.88rem;
    }
    .check-row {
      display: flex; align-items: center; gap: 8px; font-size: 0.83rem; color: #e2e8f0; margin: 8px 0; cursor: pointer;
    }

    .btn-primary {
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      width: 100%; padding: 12px 16px; border-radius: 12px; border: none;
      background: linear-gradient(135deg, #38bdf8, #10b981);
      color: #04101d; font-weight: 700; font-size: 0.91rem; cursor: pointer;
      transition: transform 0.14s ease, opacity 0.14s;
    }
    .btn-primary:hover { transform: translateY(-1px); }
    .btn-primary:disabled { opacity: 0.52; cursor: wait; transform: none; }
    .btn-ghost {
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      padding: 9px 13px; border-radius: 11px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.72); color: var(--text); font-size: 0.82rem;
      font-weight: 600; cursor: pointer; text-decoration: none;
    }
    .btn-ghost:hover { border-color: var(--accent); }
    .wiz-nav {
      display: flex; justify-content: space-between; gap: 8px; margin-top: 14px; padding-top: 12px;
      border-top: 1px solid var(--border);
    }

    /* Character Spheres */
    .spheres-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0 12px;
    }
    .sphere-btn {
      display: flex; flex-direction: column; align-items: flex-start; gap: 4px;
      padding: 11px; border-radius: 14px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.68); color: var(--text); text-align: left; cursor: pointer;
    }
    .sphere-btn.selected {
      border-color: var(--accent); background: rgba(14, 58, 92, 0.62);
      box-shadow: 0 0 18px rgba(56, 189, 248, 0.22);
    }
    .sphere-dot { width: 18px; height: 18px; border-radius: 50%; box-shadow: 0 0 12px currentColor; }
    .sphere-title { font-size: 0.82rem; font-weight: 700; }
    .sphere-sub { font-size: 0.73rem; color: var(--muted); line-height: 1.3; }

    .list-item {
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      padding: 9px 11px; border-radius: 11px; margin-bottom: 7px;
      background: rgba(15, 23, 42, 0.68); border: 1px solid var(--border); font-size: 0.83rem;
    }
    .qr-box {
      margin-top: 10px; padding: 12px; border-radius: 14px;
      background: #ffffff; color: #0f172a; text-align: center;
    }
    .qr-box svg { width: 176px; height: 176px; max-width: 100%; display: block; margin: 0 auto; }

    /* Eva's Spoken Subtitle Bubble (shifts left on wide screens when Wizard is open) */
    .speech-halo {
      position: fixed; left: 50%; transform: translateX(-50%);
      bottom: calc(142px + env(safe-area-inset-bottom, 0px));
      z-index: 15; width: min(620px, 90vw); pointer-events: auto;
      text-align: center; transition: left 0.35s ease, width 0.35s ease;
    }
    body.drawer-open .speech-halo {
      left: calc((100vw - 490px) / 2);
      width: min(520px, calc(100vw - 530px));
    }
    .speech-bubble {
      display: inline-block; padding: 13px 20px; border-radius: 20px;
      background: rgba(8, 16, 34, 0.82); border: 1px solid rgba(56, 189, 248, 0.32);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      box-shadow: 0 18px 46px rgba(0, 0, 0, 0.48);
      font-size: 0.98rem; line-height: 1.5; color: #f1f5f9;
    }

    /* Bottom Living Conversation Dock */
    .bottom-dock {
      position: fixed; left: 50%; transform: translateX(-50%);
      bottom: calc(12px + env(safe-area-inset-bottom, 0px));
      z-index: 23; width: min(740px, calc(100vw - 24px));
      display: flex; flex-direction: column; gap: 6px;
      transition: left 0.35s ease, width 0.35s ease;
    }
    body.drawer-open .bottom-dock {
      left: calc((100vw - 490px) / 2);
      width: min(590px, calc(100vw - 510px));
    }
    .chips-row {
      display: flex; flex-wrap: wrap; justify-content: center; gap: 5px;
      padding-bottom: 2px;
    }
    .chip {
      flex: 0 0 auto; padding: 4px 10px; border-radius: 999px;
      background: rgba(12, 21, 42, 0.78); border: 1px solid var(--border);
      color: #cbd5e1; font-size: 0.74rem; cursor: pointer; white-space: nowrap;
    }
    .chip:hover { border-color: var(--accent); color: #fff; }

    .input-bar {
      display: flex; align-items: center; gap: 8px; padding: 7px 9px;
      border-radius: 999px; background: var(--glass-strong);
      border: 1px solid rgba(148, 163, 184, 0.30);
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55);
    }
    .mic-btn {
      width: 42px; height: 42px; border-radius: 50%; border: none; flex-shrink: 0;
      background: linear-gradient(135deg, #0ea5e9, #10b981); color: #04131f;
      font-size: 1.1rem; cursor: pointer; display: flex; align-items: center; justify-content: center;
    }
    .mic-btn.recording {
      background: linear-gradient(135deg, #f43f5e, #f59e0b); color: #fff;
      animation: pulseMic 1.1s ease-in-out infinite;
    }
    @keyframes pulseMic {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.09); }
    }
    .talk-input {
      flex: 1; background: transparent; border: none; outline: none;
      color: var(--text); font-size: 0.93rem; padding: 5px 4px;
    }
    .send-btn {
      padding: 9px 16px; border-radius: 999px;
      background: rgba(56, 189, 248, 0.22); border: 1px solid rgba(56, 189, 248, 0.48);
      color: #e0f2fe; font-weight: 700; font-size: 0.84rem; cursor: pointer;
    }

    @media (max-width: 1120px) {
      .top-hud { flex-wrap: wrap; }
      .hud-group { flex-wrap: wrap; }
      .stage-drawer, .heard-stack { top: calc(92px + env(safe-area-inset-top, 0px)); }
    }
    @media (max-width: 980px) {
      body.drawer-open .speech-halo,
      body.drawer-open .bottom-dock {
        left: 50%; width: min(700px, calc(100vw - 24px));
      }
      .stage-drawer {
        left: 12px; right: 12px; width: auto; top: calc(92px + env(safe-area-inset-top, 0px));
        bottom: calc(134px + env(safe-area-inset-bottom, 0px));
      }
    }
  </style>
</head>
<body>
  <div class="aurora-bg"></div>
  <canvas id="eva-canvas"></canvas>

  <!-- Top HUD -->
  <header class="top-hud">
    <div class="hud-group">
      <div class="pill static">
        <span class="dot" id="hud-dot"></span>
        <span id="hud-eva-name">Eva</span>
      </div>
      <button type="button" class="pill active-cyan" id="btn-wizard" title="Open de 6-staps Kennismakings- &amp; Installatie-Wizard">
        🪄 <span id="wiz-hud-label">Kennismakings-Wizard</span>
      </button>
      <button type="button" class="pill" id="btn-smart-home" title="Koppel UniFi, Protect, Agenda, E-mail, Home Assistant of bekijk Eva's eigen vaardigheden">
        🔌 <span id="ha-hud-label">Koppelingen &amp; Kluis</span>
      </button>
      <button type="button" class="pill" id="btn-guest-mode" title="Scherm privézaken direct af als er visite is">
        🥂 <span id="guest-mode-label">Visite-modus</span>
      </button>
      <span class="pill static active-emerald" id="hud-speaker-pill" hidden></span>
    </div>
    <div class="hud-group">
      <button type="button" class="pill" id="btn-rules">
        📜 Afspraken <span id="rules-count">(0)</span>
      </button>
      <button type="button" class="pill" id="btn-family">
        👨‍👩‍👧 Gezin &amp; Lola QR
      </button>
      <button type="button" class="pill active-cyan" id="btn-audio-toggle">
        🔊 <span id="audio-label">Stem aan</span>
      </button>
      <a href="/beheer" class="pill" title="Klassiek 6-tab systeemoverzicht &amp; back-ups">
        ⚙ Motorkap
      </a>
    </div>
  </header>

  <!-- Reversible "Ik heb je gehoord" Stack -->
  <section class="heard-stack" id="heard-stack" aria-live="polite"></section>

  <!-- Guided 6-Step Wizard & Quick Drawers -->
  <aside class="stage-drawer" id="stage-drawer" hidden>
    <div class="drawer-head">
      <h2 id="drawer-title">🪄 Kennismaking met Eva</h2>
      <button type="button" class="close-x" id="btn-close-drawer">Verberg ✕</button>
    </div>
    <div id="drawer-body"></div>
  </aside>

  <!-- Eva's Spoken Subtitle -->
  <section class="speech-halo">
    <div class="speech-bubble" id="eva-subtitle">
      Hoi! Ik ben Eva. Laten we rechts stap voor stap kennismaken en jouw huis koppelen.
    </div>
    <div style="margin-top:8px;" id="first-voice-unlock" hidden>
      <button type="button" class="btn-ghost" id="btn-hear-greeting" style="border-color:rgba(56,189,248,0.6); color:#bae6fd;">
        🔊 Laat Eva hardop spreken
      </button>
    </div>
  </section>

  <!-- Bottom Conversational Bar -->
  <footer class="bottom-dock" id="bottom-dock">
    <div class="chips-row">
      <button type="button" class="chip" data-panel="integrations">🔌 Koppelingen &amp; Kluis</button>
      <button type="button" class="chip" data-say="Eva, maak verbinding met mijn UniFi netwerk en UniFi Protect.">📡 Koppel UniFi &amp; Protect</button>
      <button type="button" class="chip" data-say="Eva, maak verbinding met mijn agenda en e-mail.">📅 Koppel Agenda &amp; Mail</button>
      <button type="button" class="chip" data-say="Eva, we krijgen visite!">🥂 We krijgen visite!</button>
      <button type="button" class="chip" data-panel="family">💌 QR-kaart voor Lola</button>
    </div>
    <form class="input-bar" id="living-form">
      <button type="button" class="mic-btn" id="btn-mic" title="Praat Nederlands tegen Eva (herkent jouw ingeleerde stem)">🎙️</button>
      <input
        type="text"
        class="talk-input"
        id="talk-input"
        placeholder="Praat of typ gewoon Nederlands tegen Eva…"
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
    let wizardStep = 1; // 1..6
    let audioEnabled = true;
    let userUnlockedAudio = false;
    let ttsSpeaking = false;
    let activeAnalyser = null;
    let freqBins = null;
    let currentAudioCtx = null;
    let currentSource = null;
    let evaMoodHex = 0x38bdf8;
    let waveUntil = 0;
    let codexPollTimer = null;
    let tgPollTimer = null;

    // --- 1. Fullscreen 3D Eva Stage ---
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

    function boot3DEva() {
      const canvas = $('eva-canvas');
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({ canvas, antialias: window.innerWidth >= 768, alpha: true });
      } catch (_e) { return; }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(34, window.innerWidth / window.innerHeight, 0.1, 50);
      camera.position.set(0, 1.36, 2.75);

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

      let avatarRoot = null, baseFloorY = 0.04;
      const bones = {}, restQuats = {}, jawMeshes = [];
      let mouthParts = null, pointerX = 0, pointerY = 0;

      window.addEventListener('pointermove', e => {
        pointerX = ((e.clientX / Math.max(1, window.innerWidth)) - 0.5) * 2;
        pointerY = ((e.clientY / Math.max(1, window.innerHeight)) - 0.5) * 2;
      });

      new GLTFLoader().load('/internal/v1/appliance/stage-assets/eva.glb', gltf => {
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
        avatarRoot.scale.setScalar(1.68 / Math.max(box.getSize(new THREE.Vector3()).y, 0.01));
        baseFloorY = -new THREE.Box3().setFromObject(avatarRoot).min.y + 0.04;
        avatarRoot.position.y = baseFloorY;
        scene.add(avatarRoot);
        waveUntil = performance.now() + 2500;
      });

      const onResize = () => {
        renderer.setSize(window.innerWidth, window.innerHeight, false);
        camera.aspect = window.innerWidth / Math.max(1, window.innerHeight);
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', onResize);
      onResize();

      const tmpEuler = new THREE.Euler(), tmpQuat = new THREE.Quaternion(), targetQuat = new THREE.Quaternion();
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
        const dt = Math.min(clock.getDelta(), 0.1), t = clock.elapsedTime;
        const smoothPose = Math.min(1, dt * 6.5);

        moodColor.setHex(evaMoodHex);
        rimLight.color.lerp(moodColor, Math.min(1, dt * 4));
        haloMat.color.lerp(moodColor, Math.min(1, dt * 4));

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

        // Shift camera right so 3D Eva stands gracefully on the left half when the Wizard is open
        const shiftX = (activeDrawer && window.innerWidth > 980) ? 0.42 : 0.0;
        camera.position.x += ((shiftX + pointerX * 0.06) - camera.position.x) * Math.min(1, dt * 4.2);
        camera.lookAt(shiftX * 0.82, 1.24, 0);
        halo.scale.setScalar(1.0 + voiceEnergy * 0.18 + Math.sin(t * 2.1) * 0.03);

        if (avatarRoot) {
          const breath = Math.sin(t * 1.55) * 0.5 + 0.5;
          const sway = Math.sin(t * 0.45) * 0.65 + Math.sin(t * 0.29 + 1.1) * 0.35;
          avatarRoot.position.x = sway * 0.012;
          avatarRoot.position.y = baseFloorY + breath * 0.0025;
          avatarRoot.rotation.y = sway * 0.03 + pointerX * 0.09 + (activeDrawer && window.innerWidth > 980 ? 0.10 : 0);

          poseBone('Spine02', 0, sway * 0.015, -sway * 0.02, smoothPose);
          poseBone('Spine01', -breath * 0.022, sway * 0.016, 0, smoothPose);
          poseBone('Spine', -breath * 0.026 - (speaking ? 0.02 : 0), speaking ? Math.sin(t * 1.4) * 0.025 : 0, 0, smoothPose);

          const speakNod = speaking ? (-mouthOpen * 0.048 + Math.sin(t * 4.8) * 0.02) : 0;
          const gazeYaw = pointerX * 0.14 + Math.sin(t * 0.52) * 0.035;
          poseBone('neck', -0.045, gazeYaw * 0.35, 0, smoothPose);
          poseBone('Head', -0.165 + pointerY * 0.06 + speakNod + breath * 0.012, gazeYaw * 0.75, Math.sin(t * 0.37) * 0.02, smoothPose);

          if (performance.now() < waveUntil) {
            poseBone('RightArm', -0.55, 0.2, -0.85 + Math.sin(t * 9.0) * 0.18, smoothPose);
          } else {
            poseBone('RightArm', 0, 0, 0, smoothPose);
          }
        }
        renderer.render(scene, camera);
      }
      animate();
    }

    // --- 2. Spoken Dutch Voice ---
    let currentTtsAbort = null;
    function stopSpeaking() {
      ttsSpeaking = false;
      if (currentTtsAbort) {
        try { currentTtsAbort.abort(); } catch (_e) {}
        currentTtsAbort = null;
      }
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
      for (const kw of ['Fenna', 'Colette', 'Natural', 'Google Nederlands', 'Claire', 'Xander']) {
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

      const ttsCtrl = new AbortController();
      currentTtsAbort = ttsCtrl;
      try {
        const res = await fetch('/internal/v1/appliance/living/speech', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: clean }),
          signal: ttsCtrl.signal
        });
        if (ttsCtrl.signal.aborted) return;
        if (res.status === 200) {
          const buf = await res.arrayBuffer();
          if (buf.byteLength > 256) {
            if (!currentAudioCtx) currentAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
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
          if (!ttsSpeaking && !userUnlockedAudio) $('first-voice-unlock').hidden = false;
        }, 450);
      }
    }

    // --- 3. Fixed-Duration 16 kHz PCM Helper (for Wizard Step 3 Enroll & Live Voice Test) ---
    async function captureFixedPcm16B64(durationMs = 3400) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      let total = 0;
      const inRate = ctx.sampleRate || 48000;
      proc.onaudioprocess = ev => {
        const input = ev.inputBuffer.getChannelData(0);
        const ratio = inRate / 16000;
        const outLen = Math.floor(input.length / ratio);
        const int16 = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) {
          const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
          int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        chunks.push(int16);
        total += outLen;
      };
      src.connect(proc);
      proc.connect(ctx.destination);
      await new Promise(r => setTimeout(r, durationMs));
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
      return btoa(bin);
    }

    // --- 4. Conversational Microphone (Recognizes Speaker + Transcribes Speech) ---
    let micActive = false, micStopFn = null;
    async function toggleMicCapture() {
      if (micActive && micStopFn) { micStopFn(); return; }
      userUnlockedAudio = true;
      stopSpeaking();

      let stream = null, audioCtx = null, processor = null;
      const pcmChunks = [];
      let totalSamples = 0;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const src = audioCtx.createMediaStreamSource(stream);
        processor = audioCtx.createScriptProcessor(4096, 1, 1);
        const inRate = audioCtx.sampleRate || 48000;
        processor.onaudioprocess = ev => {
          if (totalSamples >= 96000) return;
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
      } catch (_e) {
        $('eva-subtitle').textContent = 'Geef je browser microfoontoegang of typ je bericht hieronder.';
        return;
      }

      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      let rec = null, transcript = '';
      micActive = true;
      $('btn-mic').classList.add('recording');
      $('talk-input').placeholder = 'Luisteren… spreek Nederlands (klik nogmaals om te verzenden)';

      const finish = async () => {
        if (!micActive) return;
        micActive = false;
        $('btn-mic').classList.remove('recording');
        $('talk-input').placeholder = 'Praat of typ gewoon Nederlands tegen Eva…';
        try { rec?.stop(); } catch (_e) {}
        try { processor?.disconnect(); } catch (_e) {}
        try { await audioCtx?.close(); } catch (_e) {}
        try { stream?.getTracks().forEach(t => t.stop()); } catch (_e) {}

        let pcmB64 = '';
        if (totalSamples >= 3200) {
          const merged = new Int16Array(totalSamples);
          let off = 0;
          for (const c of pcmChunks) { merged.set(c, off); off += c.length; }
          const bytes = new Uint8Array(merged.buffer);
          let bin = '';
          for (let i = 0; i < bytes.length; i += 0x8000) {
            bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
          }
          pcmB64 = btoa(bin);
        }
        const finalTxt = ($('talk-input').value || transcript || '').trim();
        if (finalTxt) {
          $('talk-input').value = '';
          await sendLivingTurn({ text: finalTxt, audio_pcm_b64: pcmB64 });
        }
      };
      micStopFn = finish;

      if (SR) {
        rec = new SR();
        rec.lang = 'nl-NL';
        rec.interimResults = true;
        rec.onresult = ev => {
          let s = '';
          for (let i = 0; i < ev.results.length; i++) s += ev.results[i][0].transcript;
          transcript = s.trim();
          $('talk-input').value = transcript;
        };
        rec.onend = () => { if (micActive) finish(); };
        rec.onerror = () => { if (micActive) setTimeout(finish, 1000); };
        try { rec.start(); } catch (_e) { setTimeout(finish, 3500); }
      } else {
        setTimeout(finish, 3600);
      }
    }

    // --- 5. Helpers & Reversible Cards ---
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
        <div class="heard-head">✨ ${escapeHtml(change.title || 'Ik heb je gehoord')}</div>
        <div class="heard-body">${escapeHtml(change.summary || '')}</div>
        ${change.undoable && change.change_id ? `<button type="button" class="undo-btn" data-undo="${escapeHtml(change.change_id)}">↩ Ongedaan maken</button>` : ''}
      `;
      const ub = el.querySelector('[data-undo]');
      if (ub) {
        ub.onclick = async () => {
          ub.disabled = true;
          const r = await fetch(`/internal/v1/appliance/living/undo/${encodeURIComponent(change.change_id)}`, { method: 'POST' });
          const d = await r.json();
          if (r.ok && d.stage) {
            applyStageState(d.stage);
            el.innerHTML = `<div class="heard-head">↩ Teruggedraaid</div><div class="heard-body">${escapeHtml(d.label || '')}</div>`;
            setTimeout(() => el.remove(), 3000);
          }
        };
      }
      stack.prepend(el);
      setTimeout(() => { if (el.parentNode) el.remove(); }, 18000);
    }

    function applyStageState(nextStage, { preserveDrawerInputs = false } = {}) {
      stage = nextStage;
      const eva = stage.eva || {};
      const hh = stage.household || {};
      const sh = stage.channels?.smart_home || {};

      $('hud-eva-name').textContent = `${eva.display_name || 'Eva'} · ${hh.owner_name || 'Thuis'}`;
      $('rules-count').textContent = `(${(hh.custom_rules || []).length})`;

      const haConnected = !!(sh.enabled && sh.secret_present);
      const vaultSvcs = stage.integrations?.services || [];
      const customCaps = stage.integrations?.capabilities || [];
      const totalConnected = (haConnected ? 1 : 0) + vaultSvcs.filter(s => s.has_credential).length;
      $('btn-smart-home').classList.toggle('active-emerald', totalConnected > 0);
      $('ha-hud-label').textContent = totalConnected > 0
        ? `Koppelingen (${totalConnected})${customCaps.length ? ' · ' + customCaps.length + ' skills' : ''}`
        : (sh.lan_detected?.reachable || stage.integrations?.unifi_detected?.reachable ? 'Koppelingen (gevonden!)' : 'Koppelingen & Kluis');

      const guestOn = !!hh.guest_mode_active;
      document.body.classList.toggle('guest-mode', guestOn);
      $('btn-guest-mode').classList.toggle('active-guest', guestOn);
      $('guest-mode-label').textContent = guestOn ? 'Privéschild actief (Visite)' : 'Visite-modus';
      $('hud-dot').classList.toggle('warn', guestOn);

      if (guestOn) {
        evaMoodHex = 0xf59e0b;
      } else {
        const activePreset = (eva.presets || []).find(p => p.preset_id === eva.preset_id);
        if (activePreset?.color) evaMoodHex = parseInt(activePreset.color.replace('#', ''), 16) || 0x38bdf8;
      }

      $('wiz-hud-label').textContent = stage.wizard_completed ? 'Wizard' : 'Kennismaking (stap ' + wizardStep + '/6)';

      if (!preserveDrawerInputs && activeDrawer) {
        renderDrawer(activeDrawer);
      }
    }

    async function sendLivingTurn(payload) {
      $('btn-send').disabled = true;
      $('eva-subtitle').textContent = `${stage?.eva?.display_name || 'Eva'} luistert en denkt mee…`;
      try {
        const res = await fetch('/internal/v1/appliance/living/turn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
          $('eva-subtitle').textContent = data.detail || 'Er ging iets mis.';
          return;
        }
        if (data.stage) applyStageState(data.stage, { preserveDrawerInputs: activeDrawer === 'wizard' });
        if (Array.isArray(data.applied_changes)) data.applied_changes.forEach(pushHeardCard);

        // Live speaker recognition feedback in top HUD
        const sp = data.speaker_match;
        if (sp && sp.status === 'household_voice_candidate') {
          const pill = $('hud-speaker-pill');
          pill.hidden = false;
          pill.textContent = `🎙️ Stem herkend: ${sp.display_name} (${sp.similarity_pct}%)`;
        } else if (sp && sp.status === 'unknown_voice') {
          const pill = $('hud-speaker-pill');
          pill.hidden = false;
          pill.textContent = '🛡️ Onbekende stem · Privéschild actief';
        }

        if (data.conjure_preset) {
          selectedIntegrationPreset = data.conjure_preset;
        }
        if (data.conjure_panel && (activeDrawer !== 'wizard' || data.conjure_panel === 'wizard')) {
          openDrawer(data.conjure_panel, null, data.conjure_preset || null);
        }
        waveUntil = performance.now() + 1800;
        if (data.reply_text) await speakDutch(data.reply_text);
      } finally {
        $('btn-send').disabled = false;
      }
    }

    // --- 6. Guided 6-Step 3D Onboarding Wizard & Quick Drawers ---
    function openDrawer(kind, step = null, preset = null) {
      activeDrawer = kind;
      if (step !== null) wizardStep = step;
      if (preset) {
        selectedIntegrationPreset = preset;
      } else if (kind === 'smart_home') {
        selectedIntegrationPreset = 'home_assistant';
      }
      $('stage-drawer').hidden = false;
      document.body.classList.add('drawer-open');
      renderDrawer(kind);
    }

    function closeDrawer() {
      activeDrawer = null;
      $('stage-drawer').hidden = true;
      document.body.classList.remove('drawer-open');
    }

    function buildSmartHomeSectionHtml(sh) {
      const connected = !!(sh.enabled && sh.secret_present);
      const det = sh.lan_detected || {};
      const defaultUrl = sh.url || det.url || 'http://homeassistant.local:8123';
      return `
        <div class="card-sec">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:8px;">
            <strong style="font-size:0.92rem;">🏡 Home Assistant Koppeling</strong>
            ${connected
              ? '<span class="badge-ok">✓ Actief gekoppeld</span>'
              : (det.reachable
                  ? `<span class="badge-info">🟢 Gevonden op ${escapeHtml(det.ip || 'thuisnetwerk')}</span>`
                  : '<span style="font-size:0.76rem; color:var(--muted);">Nog niet gekoppeld</span>')}
          </div>
          <div style="font-size:0.8rem; color:var(--muted); margin-bottom:10px; line-height:1.45;">
            Plak hieronder veilig je <strong>Long-Lived Access Token</strong> (Profiel in Home Assistant → Beveiliging → Langlevende toegangstokens). Dit token gaat rechtstreeks naar Eva's versleutelde kluis en nooit door de chat.
          </div>
          <div class="field">
            <label>Home Assistant adres</label>
            <input id="ha-url-in" type="text" value="${escapeHtml(defaultUrl)}" placeholder="http://homeassistant.local:8123" />
          </div>
          <div class="field">
            <label>Long-Lived Access Token ${sh.secret_present ? '(opgeslagen — leeg laten om huidig token te houden)' : ''}</label>
            <input id="ha-tok-in" type="password" autocomplete="off" placeholder="Plak hier je Home Assistant token…" />
          </div>
          <label class="check-row">
            <input id="ha-eff-in" type="checkbox" ${sh.allow_effects !== false ? 'checked' : ''} />
            <span>Eva mag lampen &amp; schakelaars ook echt bedienen (niet alleen uitlezen)</span>
          </label>
          <button type="button" class="btn-primary" id="btn-connect-ha" style="margin-top:6px;">
            🔌 Verbind &amp; Activeer Home Assistant direct
          </button>
          <div id="ha-status-msg" style="margin-top:8px; font-size:0.82rem;"></div>
        </div>
      `;
    }

    let selectedIntegrationPreset = 'auto';

    function getIntegrationPresets(unifiUrl) {
      return {
        home_assistant: { label: '🏡 Home Assistant' },
        unifi_network: {
          label: '📡 UniFi Netwerk',
          service_id: 'unifi_network',
          title: 'UniFi Netwerk & Wi-Fi',
          service_kind: 'unifi_network',
          base_url: unifiUrl || 'https://192.168.1.1',
          auth_mode: 'api_key',
          hint: 'Maak in UniFi OS → Instellingen → Control Plane → Integrations een API-Key aan (of kies Lokaal account hieronder).'
        },
        unifi_protect: {
          label: '🎥 UniFi Protect',
          service_id: 'unifi_protect',
          title: 'UniFi Protect Camera’s & Deurbel',
          service_kind: 'unifi_protect',
          base_url: unifiUrl || 'https://192.168.1.1',
          auth_mode: 'api_key',
          hint: 'Gebruik een UniFi OS Integration API-Key of een lokaal UniFi OS-account voor camera’s & slimme detecties.'
        },
        calendar: {
          label: '📅 Agenda',
          service_id: 'gezins_agenda',
          title: 'Agenda (CalDAV / iCloud / Google ICS)',
          service_kind: 'calendar',
          base_url: 'https://caldav.icloud.com',
          auth_mode: 'basic_auth',
          hint: 'Vul je CalDAV-adres + app-wachtwoord in, of kies "Geheime ICS-link" voor Google/Outlook agenda’s.'
        },
        email: {
          label: '✉️ E-mail',
          service_id: 'eigen_email',
          title: 'E-mail (IMAP & SMTP)',
          service_kind: 'email',
          base_url: 'imaps://imap.gmail.com:993',
          auth_mode: 'imap_password',
          hint: 'Vul je IMAP-server, e-mailadres en App-wachtwoord in (bijv. Gmail App-wachtwoord of eigen mailserver).'
        },
        rest_api: {
          label: '🌐 Eigen API / Dienst',
          service_id: 'eigen_dienst',
          title: 'Eigen Dienst / API',
          service_kind: 'rest_api',
          base_url: 'https://',
          auth_mode: 'bearer_token',
          hint: 'Koppel elk systeem (zonnepanelen, NAS, P1-meter, webhook of REST-API). Eva bouwt zelf de ontbrekende logica erbij!'
        }
      };
    }

    function buildUniversalIntegrationsHtml() {
      const sh = stage.channels?.smart_home || {};
      const integ = stage.integrations || {};
      const pending = integ.pending_request;
      const unifiDet = integ.unifi_detected || {};
      const presets = getIntegrationPresets(unifiDet.reachable ? unifiDet.url : 'https://192.168.1.1');

      let activeKey = selectedIntegrationPreset;
      if (activeKey === 'auto') {
        if (pending && pending.service_kind && presets[pending.service_kind]) {
          activeKey = pending.service_kind;
        } else if (!(sh.enabled && sh.secret_present) && sh.lan_detected?.reachable) {
          activeKey = 'home_assistant';
        } else if (unifiDet.reachable) {
          activeKey = 'unifi_network';
        } else {
          activeKey = 'home_assistant';
        }
      }

      const presetTabs = Object.entries(presets).map(([k, p]) => `
        <button type="button" class="chip" data-integ-tab="${escapeHtml(k)}" style="${k === activeKey ? 'border-color:#38bdf8;background:rgba(14,65,102,0.82);color:#fff;font-weight:700;' : ''}">
          ${escapeHtml(p.label)}
        </button>
      `).join('');

      const showPendingBanner = !!(pending && (!pending.service_kind || pending.service_kind === activeKey));
      const pendingBanner = showPendingBanner ? `
        <div class="card-sec" style="border-color:rgba(56,189,248,0.68); background:rgba(8,47,73,0.62);">
          <div style="font-size:0.82rem; font-weight:700; color:#7dd3fc; margin-bottom:4px;">
            ✨ Eva staat klaar om te koppelen: ${escapeHtml(pending.title || pending.service_id)}
          </div>
          <div style="font-size:0.79rem; color:#e0f2fe; line-height:1.42;">
            ${escapeHtml(pending.instructions_nl || 'Vul hieronder je gegevens veilig in Eva’s AES-256 Kluis in (buiten de chat om).')}
          </div>
        </div>
      ` : '';

      let formHtml = '';
      if (activeKey === 'home_assistant') {
        formHtml = buildSmartHomeSectionHtml(sh);
      } else {
        const p = presets[activeKey] || presets.rest_api;
        const usePending = !!(pending && pending.service_kind === activeKey);
        const sid = usePending ? pending.service_id : p.service_id;
        const stitle = usePending ? pending.title : p.title;
        const surl = (usePending && pending.default_url) ? pending.default_url : p.base_url;
        const sauth = (usePending && pending.auth_mode) ? pending.auth_mode : p.auth_mode;
        const shint = (usePending && pending.instructions_nl) ? pending.instructions_nl : p.hint;
        const unifiBadge = (activeKey.startsWith('unifi_') && unifiDet.reachable)
          ? `<span class="badge-info">🟢 Gevonden op ${escapeHtml(unifiDet.ip)}</span>`
          : '<span class="badge-info">🔐 AES-256 Kluis</span>';

        formHtml = `
          <div class="card-sec">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:8px;">
              <strong style="font-size:0.92rem;">${escapeHtml(p.label)} koppelen</strong>
              ${unifiBadge}
            </div>
            <div style="font-size:0.79rem; color:var(--muted); margin-bottom:10px; line-height:1.45;">
              ${escapeHtml(shint)}
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
              <div class="field">
                <label>Naam</label>
                <input id="uv-title" type="text" value="${escapeHtml(stitle)}" />
              </div>
              <div class="field">
                <label>Kluis-ID</label>
                <input id="uv-id" type="text" value="${escapeHtml(sid)}" />
              </div>
            </div>
            <input id="uv-kind" type="hidden" value="${escapeHtml(usePending ? pending.service_kind : p.service_kind)}" />
            <div class="field">
              <label>Serveradres / URL</label>
              <input id="uv-url" type="text" value="${escapeHtml(surl)}" placeholder="https://..." />
            </div>
            <div class="field">
              <label>Authenticatie</label>
              <select id="uv-auth">
                <option value="api_key" ${sauth === 'api_key' ? 'selected' : ''}>API-Key (bijv. UniFi X-API-KEY)</option>
                <option value="basic_auth" ${sauth === 'basic_auth' ? 'selected' : ''}>Gebruikersnaam + Wachtwoord (Lokaal UniFi / CalDAV)</option>
                <option value="imap_password" ${sauth === 'imap_password' ? 'selected' : ''}>E-mailadres + App-wachtwoord (IMAP/SMTP)</option>
                <option value="bearer_token" ${sauth === 'bearer_token' ? 'selected' : ''}>Bearer Token (REST / OAuth)</option>
                <option value="ics_url" ${sauth === 'ics_url' ? 'selected' : ''}>Alleen-lezen geheime ICS-link (zonder wachtwoord)</option>
              </select>
            </div>
            <div class="field" id="uv-user-wrap">
              <label>Gebruikersnaam / E-mailadres (alleen indien nodig)</label>
              <input id="uv-user" type="text" autocomplete="off" placeholder="bijv. lokaal account of naam@voorbeeld.nl" />
            </div>
            <div class="field" id="uv-sec-wrap">
              <label>API-sleutel / Wachtwoord (blijft versleuteld buiten LLM)</label>
              <input id="uv-secret" type="password" autocomplete="new-password" placeholder="Plak veilig in Eva’s Kluis…" />
            </div>
            ${activeKey.startsWith('unifi_') ? `
              <label class="check-row">
                <input id="uv-unifi-both" type="checkbox" checked />
                <span>Koppel met deze UniFi OS-sleutel direct zowel <strong>UniFi Netwerk</strong> als <strong>UniFi Protect</strong></span>
              </label>
            ` : ''}
            ${activeKey === 'email' ? `
              <div class="field">
                <label>Optioneel: SMTP-server (voor uitgaande mail, bijv. smtps://smtp.gmail.com:465)</label>
                <input id="uv-smtp" type="text" placeholder="Automatisch afgeleid uit IMAP indien leeg" />
              </div>
            ` : ''}
            <button type="button" class="btn-primary" id="btn-uv-connect" style="margin-top:4px;">
              🔐 Test, Versleutel &amp; Activeer direct
            </button>
            <div id="uv-status-msg" style="margin-top:8px; font-size:0.82rem; line-height:1.42;"></div>
          </div>
        `;
      }

      const svcs = integ.services || [];
      const svcsHtml = svcs.length ? `
        <div class="card-sec">
          <strong style="display:block; font-size:0.86rem; margin-bottom:8px;">🔐 Actieve diensten in Eva's Kluis (${svcs.length})</strong>
          ${svcs.map(s => `
            <div class="list-item" style="align-items:flex-start;">
              <div>
                <div style="font-weight:700;">${escapeHtml(s.title)} <span style="font-size:0.72rem; color:#6ee7b7;">(${escapeHtml(s.service_id)})</span></div>
                <div style="font-size:0.75rem; color:var(--muted);">${escapeHtml(s.last_summary || s.base_url)}</div>
              </div>
              <button type="button" class="btn-ghost" data-del-svc="${escapeHtml(s.service_id)}" style="padding:5px 9px; font-size:0.75rem;">✕</button>
            </div>
          `).join('')}
        </div>
      ` : '';

      const caps = integ.capabilities || [];
      const capsHtml = `
        <div class="card-sec">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <strong style="font-size:0.86rem;">🛠️ Door Eva zelfgebouwde vaardigheden (${caps.length})</strong>
            <span class="badge-info">Autonome Sandbox</span>
          </div>
          <div style="font-size:0.77rem; color:var(--muted); line-height:1.42; margin-bottom:${caps.length ? '8px' : '0'};">
            Vraag je Eva iets dat ze nog niet standaard kan? Dan zoekt ze zelf de documentatie op en programmeert, test en bewaart ze hier een nieuwe Python-vaardigheid.
          </div>
          ${caps.map(c => `
            <div class="list-item" style="align-items:flex-start;">
              <div>
                <div style="font-weight:700;">⚡ ${escapeHtml(c.title)} <span style="font-size:0.71rem; color:#7dd3fc;">(${escapeHtml(c.capability_id)})</span></div>
                <div style="font-size:0.75rem; color:var(--muted);">${escapeHtml(c.description || 'Geverifieerd in geïsoleerde sandbox')}</div>
              </div>
              <button type="button" class="btn-ghost" data-del-cap="${escapeHtml(c.capability_id)}" style="padding:5px 9px; font-size:0.75rem;">✕</button>
            </div>
          `).join('')}
        </div>
      `;

      return `
        ${pendingBanner}
        <div style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:11px;">${presetTabs}</div>
        ${formHtml}
        ${svcsHtml}
        ${capsHtml}
      `;
    }

    function wireUniversalIntegrationsHandlers(rerenderFn) {
      wireSmartHomeHandlers();
      const body = $('drawer-body');
      body.querySelectorAll('[data-integ-tab]').forEach(b => {
        b.onclick = () => {
          selectedIntegrationPreset = b.dataset.integTab;
          rerenderFn();
        };
      });

      const authSel = $('uv-auth');
      if (authSel) {
        const syncAuthVisibility = () => {
          const mode = authSel.value;
          if ($('uv-user-wrap')) $('uv-user-wrap').style.display = (mode === 'basic_auth' || mode === 'imap_password') ? 'block' : 'none';
          if ($('uv-sec-wrap')) $('uv-sec-wrap').style.display = (mode === 'ics_url') ? 'none' : 'block';
        };
        authSel.onchange = syncAuthVisibility;
        syncAuthVisibility();
      }

      const uvBtn = $('btn-uv-connect');
      if (uvBtn) {
        uvBtn.onclick = async () => {
          const msg = $('uv-status-msg');
          uvBtn.disabled = true;
          uvBtn.textContent = '⏳ Testen & versleutelen in Kluis…';
          msg.style.color = '#bae6fd';
          msg.textContent = 'Verbinding live controleren en sleutel AES-256 versleutelen…';
          try {
            const r = await fetch('/internal/v1/appliance/living/integrations/connect', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                service_id: $('uv-id').value.trim(),
                title: $('uv-title').value.trim(),
                service_kind: $('uv-kind').value,
                base_url: $('uv-url').value.trim(),
                auth_mode: $('uv-auth').value,
                username: $('uv-user') ? $('uv-user').value.trim() : '',
                secret: $('uv-secret') ? $('uv-secret').value.trim() : '',
                smtp_url: $('uv-smtp') ? $('uv-smtp').value.trim() : '',
                also_connect_unifi_pair: $('uv-unifi-both') ? $('uv-unifi-both').checked : false
              })
            });
            const d = await r.json();
            if (!r.ok) {
              msg.style.color = '#fb7185';
              msg.textContent = d.detail || 'Koppelen mislukt.';
              return;
            }
            if ($('uv-secret')) $('uv-secret').value = '';
            if (d.stage) applyStageState(d.stage, { preserveDrawerInputs: true });
            rerenderFn();
            pushHeardCard({
              title: '🔐 Gekoppeld in Kluis!',
              summary: d.detail,
              undoable: false
            });
            await speakDutch(`Gelukt! ${d.detail} Je kunt me nu direct vragen om ermee te werken.`);
          } finally {
            if ($('btn-uv-connect')) {
              $('btn-uv-connect').disabled = false;
              $('btn-uv-connect').textContent = '🔐 Test, Versleutel & Activeer direct';
            }
          }
        };
      }

      body.querySelectorAll('[data-del-svc]').forEach(b => {
        b.onclick = async () => {
          const r = await fetch(`/internal/v1/appliance/living/integrations/${encodeURIComponent(b.dataset.delSvc)}/remove`, { method: 'POST' });
          const d = await r.json();
          if (r.ok && d.stage) { applyStageState(d.stage, { preserveDrawerInputs: true }); rerenderFn(); }
        };
      });
      body.querySelectorAll('[data-del-cap]').forEach(b => {
        b.onclick = async () => {
          const r = await fetch(`/internal/v1/appliance/living/capabilities/${encodeURIComponent(b.dataset.delCap)}/remove`, { method: 'POST' });
          const d = await r.json();
          if (r.ok && d.stage) { applyStageState(d.stage, { preserveDrawerInputs: true }); rerenderFn(); }
        };
      });
    }

    function wireSmartHomeHandlers() {
      const btn = $('btn-connect-ha');
      if (!btn) return;
      btn.onclick = async () => {
        const msg = $('ha-status-msg');
        btn.disabled = true;
        btn.textContent = '⏳ Verbinden, apparaten zoeken & Eva activeren…';
        msg.style.color = '#bae6fd';
        msg.textContent = 'Even geduld: verbinding testen en Eva’s Home Assistant-tools activeren…';
        try {
          const r = await fetch('/internal/v1/appliance/living/smart-home/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              url: $('ha-url-in').value.trim(),
              token: $('ha-tok-in').value.trim(),
              allow_effects: $('ha-eff-in').checked
            })
          });
          const d = await r.json();
          if (!r.ok) {
            msg.style.color = '#fb7185';
            msg.textContent = d.detail || 'Koppelen mislukt.';
            return;
          }
          $('ha-tok-in').value = '';
          msg.style.color = '#6ee7b7';
          msg.textContent = '✓ ' + d.detail;
          if (d.stage) applyStageState(d.stage, { preserveDrawerInputs: true });
          pushHeardCard({
            title: 'Home Assistant actief!',
            summary: d.detail,
            undoable: false
          });
          await speakDutch(`Gelukt! Ik ben verbonden met je Home Assistant en zie ${d.discovered?.lights || 0} lampen, ${d.discovered?.switches || 0} schakelaars en ${d.discovered?.sensors || 0} sensoren. Vraag me gerust wat er aanstaat!`);
        } finally {
          btn.disabled = false;
          btn.textContent = '🔌 Verbind & Activeer Home Assistant direct';
        }
      };
    }

    function renderWizardStep() {
      const body = $('drawer-body');
      const eva = stage.eva || {};
      const hh = stage.household || {};
      const hw = stage.hardware || {};
      const sh = stage.channels?.smart_home || {};
      const tg = stage.channels?.telegram || {};
      const ownerMember = (hh.members || []).find(m => m.role === 'gezinshoofd') || {};

      $('drawer-title').textContent = `🪄 Kennismaking · Stap ${wizardStep} van 6`;
      $('wiz-hud-label').textContent = stage.wizard_completed ? 'Wizard' : `Kennismaking (${wizardStep}/6)`;

      const labels = ['1. Brein', '2. Jij', '3. Stem', '4. Sfeer', '5. Huis', '6. Klaar'];
      const stepperHtml = `<div class="wiz-steps">${labels.map((lbl, idx) => {
        const s = idx + 1;
        const cls = s === wizardStep ? 'active' : (s < wizardStep ? 'done' : '');
        return `<button type="button" class="wiz-step-pill ${cls}" data-go-step="${s}">${lbl}</button>`;
      }).join('')}</div>`;

      let contentHtml = '';

      if (wizardStep === 1) {
        const codexOk = stage.model?.codex?.logged_in;
        const apiConns = stage.model?.configured_api_connections || [];
        const readyApi = apiConns.find(c => c.check_state === 'ready');
        contentHtml = `
          <div class="card-sec">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
              <strong>🧠 Stap 1 — Eva's AI-Brein</strong>
              ${stage.ai_ready ? '<span class="badge-ok">✓ Verbonden</span>' : '<span style="color:var(--gold); font-size:0.8rem;">Nog koppelen</span>'}
            </div>
            <p style="margin:0 0 12px; font-size:0.84rem; color:var(--muted); line-height:1.45;">
              ${codexOk
                ? 'Je ChatGPT/Codex-account is al actief gekoppeld! Je kunt direct door naar Stap 2, of hieronder een ander model kiezen.'
                : (readyApi
                    ? `Verbonden via ${escapeHtml(readyApi.label)} (${escapeHtml(readyApi.model)}).`
                    : 'Koppel Eva met één klik aan je ChatGPT-account, of kies een van de 14 andere cloud-/lokale modellen.')}
            </p>
            <button type="button" class="btn-ghost" id="wiz-codex-btn" style="width:100%; margin-bottom:8px;">
              ⚡ ${codexOk ? 'ChatGPT-sessie gecontroleerd (opnieuw koppelen)' : 'Koppel met ChatGPT (1-klik inlogcode)'}
            </button>
            <div id="wiz-codex-box" hidden style="padding:10px; border-radius:12px; background:rgba(8,47,73,0.5); margin-bottom:8px;">
              <div style="font-size:0.8rem;">Code: <strong id="wiz-codex-code" style="font-size:1.1rem; color:#7dd3fc;">…</strong></div>
              <a id="wiz-codex-url" href="https://auth.openai.com/codex/device" target="_blank" rel="noopener" style="color:#38bdf8; font-size:0.82rem;">Open OpenAI inlogpagina ↗</a>
            </div>
            <details style="margin-top:6px;">
              <summary style="font-size:0.81rem; color:var(--muted); cursor:pointer;">Of kies Claude, Gemini, OpenRouter of lokaal model (Ollama)…</summary>
              <div style="margin-top:10px;">
                <div class="field"><label>Aanbieder</label><select id="wiz-api-preset"></select></div>
                <div class="field"><label>Model</label><input id="wiz-api-model" type="text" /></div>
                <div class="field" id="wiz-api-key-wrap"><label>API-sleutel</label><input id="wiz-api-key" type="password" placeholder="Plak API-sleutel…" /></div>
                <div class="field"><label>Endpoint</label><input id="wiz-api-ep" type="text" /></div>
                <button type="button" class="btn-ghost" id="wiz-api-save" style="width:100%;">Test &amp; Bewaar API-model</button>
                <div id="wiz-api-msg" style="font-size:0.8rem; margin-top:6px;"></div>
              </div>
            </details>
          </div>
          <div class="wiz-nav">
            <span></span>
            <button type="button" class="btn-primary" id="wiz-next" style="width:auto; padding:11px 20px;" ${!stage.ai_ready ? 'disabled' : ''}>
              Volgende: Wie ben jij? →
            </button>
          </div>
        `;
      } else if (wizardStep === 2) {
        const famVal = (hh.family_members || []).length ? hh.family_members.join(', ') : 'Lola';
        const detHw = hw.detected || {};
        contentHtml = `
          <div class="card-sec">
            <strong style="display:block; margin-bottom:6px;">👋 Stap 2 — Wie ben jij &amp; wie wonen hier?</strong>
            <p style="margin:0 0 12px; font-size:0.82rem; color:var(--muted); line-height:1.45;">
              Zo weet Eva precies wie het gezinshoofd is, wie bij het kerngezin hoort (zodat hun privézaken beschermd zijn tegenover gasten) en op welke pc ze draait.
            </p>
            <div class="field">
              <label>Jouw voornaam (gezinshoofd)</label>
              <input id="wiz-owner" type="text" value="${escapeHtml(hh.owner_name || 'Jaap')}" />
            </div>
            <div class="field">
              <label>Gezinsleden in huis (komma-gescheiden, bijv. Lola)</label>
              <input id="wiz-family" type="text" value="${escapeHtml(famVal)}" placeholder="Bijv. Lola" />
            </div>
            <div class="field">
              <label>Hoe wil je mij noemen?</label>
              <input id="wiz-eva-name" type="text" value="${escapeHtml(eva.display_name || 'Eva')}" />
            </div>
            <div class="field">
              <label>Gedetecteerde machine (${escapeHtml(detHw.cpu_count || '?')} CPU-cores · ${escapeHtml(detHw.ram_gib || '?')} GB RAM)</label>
              <select id="wiz-tier">
                ${(hw.tiers || []).map(t => `<option value="${escapeHtml(t.tier_id)}" ${t.tier_id === hw.tier ? 'selected' : ''}>${escapeHtml(t.label)} — ${escapeHtml(t.summary)}</option>`).join('')}
              </select>
            </div>
          </div>
          <div class="wiz-nav">
            <button type="button" class="btn-ghost" data-go-step="1">← Terug</button>
            <button type="button" class="btn-primary" id="wiz-save-step2" style="width:auto; padding:11px 20px;">
              Bewaar &amp; Stemherkenning →
            </button>
          </div>
        `;
      } else if (wizardStep === 3) {
        const enrolled = !!ownerMember.voice_enrolled;
        contentHtml = `
          <div class="card-sec">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <strong>🎙️ Stap 3 — Hoe herkent Eva dat jij ${escapeHtml(hh.owner_name || 'het')} bent?</strong>
              ${enrolled ? '<span class="badge-ok">✓ Stem bekend</span>' : '<span style="font-size:0.78rem; color:var(--gold);">Nog opnemen</span>'}
            </div>
            <p style="margin:0 0 12px; font-size:0.81rem; color:var(--muted); line-height:1.48;">
              1. <strong>Op je telefoon &amp; Telegram</strong> herkent Eva jou aan je persoonlijke cryptografische sleutel.<br/>
              2. <strong>In de kamer via de microfoon</strong> herkent ze je aan een lokale 64-D stemafdruk (blijft 100% binnen deze pc). Hoort ze een onbekende stem, dan schiet automatisch het Privéschild aan!
            </p>
            <div style="padding:11px; border-radius:12px; background:rgba(8,47,73,0.45); border:1px solid rgba(56,189,248,0.35); margin-bottom:10px;">
              <div style="font-size:0.8rem; color:#bae6fd; margin-bottom:8px;">
                Klik hieronder en zeg rustig hardop:<br/>
                <em>"Hoi ${escapeHtml(eva.display_name || 'Eva')}, ik ben ${escapeHtml(hh.owner_name || 'Jaap')} en ik woon hier thuis."</em>
              </div>
              <button type="button" class="btn-primary" id="btn-wiz-enroll">
                🎙️ ${enrolled ? 'Stemafdruk opnieuw opnemen (3,5 sec)' : 'Neem nu mijn stemafdruk op (3,5 sec)'}
              </button>
            </div>
            <button type="button" class="btn-ghost" id="btn-wiz-voicetest" style="width:100%;" ${!enrolled ? 'disabled' : ''}>
              🔍 Test nu live of Eva mijn stem herkent (2,5 sec)
            </button>
            <div id="wiz-voice-msg" style="margin-top:10px; font-size:0.84rem; line-height:1.45;"></div>
          </div>
          <div class="wiz-nav">
            <button type="button" class="btn-ghost" data-go-step="2">← Terug</button>
            <button type="button" class="btn-primary" data-go-step="4" style="width:auto; padding:11px 20px;">
              Volgende: Karakter &amp; Regels →
            </button>
          </div>
        `;
      } else if (wizardStep === 4) {
        const spheresHtml = (eva.presets || []).map(p => `
          <button type="button" class="sphere-btn ${p.preset_id === eva.preset_id ? 'selected' : ''}" data-preset="${escapeHtml(p.preset_id)}">
            <span class="sphere-dot" style="background:${escapeHtml(p.color)}; color:${escapeHtml(p.color)};"></span>
            <span class="sphere-title">${escapeHtml(p.orb || p.label)}</span>
            <span class="sphere-sub">${escapeHtml(p.tagline)}</span>
          </button>
        `).join('');
        const rules = hh.custom_rules || [];
        const quickRules = [
          'Na 21:00 uur alleen storen als het echt dringend is',
          'Houd antwoorden vlot, menselijk en met subtiele droge humor',
          'Over het eten op maandag en dinsdag afstemmen met Lola'
        ];
        contentHtml = `
          <div class="card-sec">
            <strong>✨ Stap 4 — Proef Eva's Karakter &amp; Huisafspraken</strong>
            <div style="font-size:0.8rem; color:var(--muted); margin:4px 0 8px;">Klik op een sfeerbol om direct het verschil te horen:</div>
            <div class="spheres-grid">${spheresHtml}</div>
            <div style="font-size:0.8rem; font-weight:600; margin-bottom:6px;">Klik om een huisafspraak toe te voegen:</div>
            <div style="display:flex; flex-direction:column; gap:6px; margin-bottom:10px;">
              ${quickRules.map(qr => {
                const already = rules.some(r => r.toLowerCase().includes(qr.slice(0, 18).toLowerCase()));
                return `<button type="button" class="btn-ghost" data-add-rule="${escapeHtml(qr)}" style="justify-content:flex-start; text-align:left;" ${already ? 'disabled' : ''}>${already ? '✓ ' : '+ '}${escapeHtml(qr)}</button>`;
              }).join('')}
            </div>
            <div style="display:flex; gap:6px;">
              <input id="wiz-custom-rule" type="text" placeholder="Of typ je eigen huisregel…" style="flex:1; padding:8px 11px; border-radius:10px; background:rgba(2,6,23,0.78); border:1px solid var(--border); color:#fff; font-size:0.84rem;" />
              <button type="button" class="btn-ghost" id="btn-wiz-add-rule">Voeg toe</button>
            </div>
          </div>
          <div class="wiz-nav">
            <button type="button" class="btn-ghost" data-go-step="3">← Terug</button>
            <button type="button" class="btn-primary" data-go-step="5" style="width:auto; padding:11px 20px;">
              Volgende: Slim Huis &amp; Kanalen →
            </button>
          </div>
        `;
      } else if (wizardStep === 5) {
        contentHtml = `
          ${buildUniversalIntegrationsHtml()}

          <div class="card-sec">
            <strong style="display:block; margin-bottom:4px;">💌 Persoonlijke 1-Scan Welkomstkaart voor Lola</strong>
            <div style="font-size:0.79rem; color:var(--muted); margin-bottom:8px;">
              Laat Lola deze QR met haar telefoon scannen: 3D Eva begroet haar bij naam, leert Lola's eigen stem &amp; wensen en opent haar Eva Pocket!
            </div>
            <div style="display:flex; gap:6px;">
              <input id="wiz-wlk-name" type="text" value="Lola" style="flex:1; padding:8px 10px; border-radius:10px; background:rgba(2,6,23,0.78); border:1px solid var(--border); color:#fff;" />
              <button type="button" class="btn-ghost" id="btn-wiz-lola-qr">Maak QR</button>
            </div>
            <div id="wiz-lola-qr-out"></div>
          </div>

          <div class="card-sec">
            <strong style="display:block; margin-bottom:6px;">📱 Jouw eigen Telefoon &amp; Telegram</strong>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              <button type="button" class="btn-ghost" id="btn-wiz-owner-qr" style="flex:1;">📱 Mijn Eva Pocket QR</button>
            </div>
            <div id="wiz-owner-qr-out"></div>
            <details style="margin-top:10px;">
              <summary style="font-size:0.8rem; color:var(--muted); cursor:pointer;">✈️ Optioneel: Telegram koppelen zonder gebruikers-ID ${tg.contacts?.length ? '(✓ gekoppeld)' : ''}</summary>
              <div style="margin-top:8px;">
                <div class="field">
                  <label>@BotFather token ${tg.token_saved ? '(al opgeslagen)' : ''}</label>
                  <input id="wiz-tg-tok" type="password" placeholder="Plak BotFather token…" />
                </div>
                <button type="button" class="btn-ghost" id="btn-wiz-tg-qr" style="width:100%;">Genereer Telegram Start-QR</button>
                <div id="wiz-tg-out"></div>
              </div>
            </details>
          </div>

          <div class="wiz-nav">
            <button type="button" class="btn-ghost" data-go-step="4">← Terug</button>
            <button type="button" class="btn-primary" data-go-step="6" style="width:auto; padding:11px 20px;">
              Volgende: Overzicht →
            </button>
          </div>
        `;
      } else {
        const haOk = !!(sh.enabled && sh.secret_present);
        contentHtml = `
          <div class="card-sec">
            <strong style="display:block; font-size:0.98rem; margin-bottom:10px;">🚀 Stap 6 — Klaar voor Levend Contact!</strong>
            <div class="list-item"><span>🧠 AI-Brein</span><strong style="color:#6ee7b7;">${stage.ai_ready ? 'Actief' : 'Nog koppelen'}</strong></div>
            <div class="list-item"><span>👨‍👩‍👧 Gezin</span><strong>${escapeHtml(hh.owner_name || 'Jaap')}${(hh.family_members || []).length ? ' &amp; ' + escapeHtml(hh.family_members.join(', ')) : ''}</strong></div>
            <div class="list-item"><span>🎙️ Jouw stemherkenning</span><strong style="color:${ownerMember.voice_enrolled ? '#6ee7b7' : '#f59e0b'};">${ownerMember.voice_enrolled ? '✓ Ingeleerd (lokaal)' : 'Nog niet opgenomen'}</strong></div>
            <div class="list-item"><span>✨ Karakter &amp; Regels</span><strong>${escapeHtml(eva.display_name || 'Eva')} (${(hh.custom_rules || []).length} afspraken)</strong></div>
            <div class="list-item"><span>🏡 Home Assistant</span><strong style="color:${haOk ? '#6ee7b7' : '#94a3b8'};">${haOk ? '✓ Actief verbonden' : 'Optioneel'}</strong></div>
            <p style="font-size:0.81rem; color:var(--muted); line-height:1.45; margin:10px 0 0;">
              Je kunt deze wizard op elk moment weer openen via <strong>🪄 Kennismaking / Wizard</strong> linksboven, of gewoon hardop tegen Eva zeggen wat je wilt aanpassen.
            </p>
          </div>
          <div class="wiz-nav">
            <button type="button" class="btn-ghost" data-go-step="5">← Terug</button>
            <button type="button" class="btn-primary" id="btn-wiz-finish" style="width:auto; padding:12px 22px;">
              ✨ Start Levend Contact met ${escapeHtml(eva.display_name || 'Eva')}!
            </button>
          </div>
        `;
      }

      body.innerHTML = stepperHtml + contentHtml;

      body.querySelectorAll('[data-go-step]').forEach(b => {
        b.onclick = () => {
          wizardStep = Number(b.dataset.goStep);
          renderWizardStep();
        };
      });

      // Step-specific handlers
      if (wizardStep === 1) {
        const sel = $('wiz-api-preset');
        const presets = stage.model?.api_presets || [];
        presets.forEach((p, idx) => {
          const o = document.createElement('option');
          o.value = idx; o.textContent = p.label;
          sel.appendChild(o);
        });
        const syncP = () => {
          const p = presets[Number(sel.value) || 0];
          if (!p) return;
          $('wiz-api-model').value = p.default_model || '';
          $('wiz-api-ep').value = p.default_endpoint || '';
          $('wiz-api-key-wrap').style.display = p.requires_api_key ? 'block' : 'none';
        };
        sel.onchange = syncP;
        syncP();

        $('wiz-codex-btn').onclick = async () => {
          const r = await fetch('/internal/v1/appliance/model/codex/start', { method: 'POST' });
          const d = await r.json();
          if (d.logged_in) {
            await refreshStage();
            wizardStep = 2;
            renderWizardStep();
            return;
          }
          $('wiz-codex-box').hidden = false;
          $('wiz-codex-code').textContent = d.user_code || '…';
          if (d.verification_url) $('wiz-codex-url').href = d.verification_url;
          clearInterval(codexPollTimer);
          codexPollTimer = setInterval(async () => {
            const pr = await fetch('/internal/v1/appliance/model/codex/status');
            const pd = await pr.json();
            if (pd.user_code) $('wiz-codex-code').textContent = pd.user_code;
            if (pd.logged_in) {
              clearInterval(codexPollTimer);
              await fetch('/internal/v1/appliance/apply', { method: 'POST' });
              await refreshStage();
              wizardStep = 2;
              renderWizardStep();
            }
          }, 2200);
        };

        $('wiz-api-save').onclick = async () => {
          const p = presets[Number(sel.value) || 0] || {};
          const r = await fetch('/internal/v1/appliance/model/api', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              kind: p.kind || 'openai_compatible',
              label: p.label || 'AI',
              model: $('wiz-api-model').value,
              endpoint: $('wiz-api-ep').value,
              api_key: $('wiz-api-key').value
            })
          });
          const d = await r.json();
          if (!r.ok) { $('wiz-api-msg').textContent = d.detail || 'Fout'; return; }
          await fetch('/internal/v1/appliance/apply', { method: 'POST' });
          await refreshStage();
          wizardStep = 2;
          renderWizardStep();
        };

        if ($('wiz-next')) {
          $('wiz-next').onclick = () => { wizardStep = 2; renderWizardStep(); };
        }
      }

      if (wizardStep === 2) {
        $('wiz-save-step2').onclick = async () => {
          userUnlockedAudio = true;
          const btn = $('wiz-save-step2');
          btn.disabled = true;
          try {
            const ownerNm = $('wiz-owner').value.trim() || 'Jaap';
            const famNm = $('wiz-family').value.trim();
            const evaNm = $('wiz-eva-name').value.trim() || 'Eva';
            const tierVal = $('wiz-tier').value;
            const r1 = await fetch('/internal/v1/appliance/profile', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                owner_name: ownerNm,
                family_members: famNm,
                installation_name: `Eva bij ${ownerNm}`,
                tier: tierVal
              })
            });
            if (!r1.ok) return;
            if (evaNm !== (stage.eva?.display_name || 'Eva')) {
              await fetch('/internal/v1/appliance/character', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset_id: stage.eva?.preset_id || 'warm_huisgenoot', display_name: evaNm })
              });
            }
            await refreshStage();
            wizardStep = 3;
            renderWizardStep();
            speakDutch(`Aangenaam ${ownerNm}${famNm ? ' en leuk dat ' + famNm + ' er ook bij hoort' : ''}! Laten we nu in stap 3 kort je stem opnemen zodat ik je op gehoor herken.`);
          } finally {
            btn.disabled = false;
          }
        };
      }

      if (wizardStep === 3) {
        $('btn-wiz-enroll').onclick = async () => {
          userUnlockedAudio = true;
          stopSpeaking();
          const btn = $('btn-wiz-enroll');
          const msg = $('wiz-voice-msg');
          btn.disabled = true;
          btn.textContent = '🔴 Spreek nu 3,5 seconden rustig in…';
          msg.style.color = '#bae6fd';
          msg.textContent = 'Luisteren naar jouw stemklank…';
          try {
            const pcmB64 = await captureFixedPcm16B64(3500);
            const r = await fetch('/internal/v1/appliance/living/voice-enroll', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ audio_pcm_b64: pcmB64 })
            });
            const d = await r.json();
            if (!r.ok) {
              msg.style.color = '#fb7185';
              msg.textContent = d.detail || 'Opname mislukt.';
              return;
            }
            if (d.stage) applyStageState(d.stage, { preserveDrawerInputs: true });
            renderWizardStep();
            $('wiz-voice-msg').style.color = '#6ee7b7';
            $('wiz-voice-msg').innerHTML = `✅ <strong>Stemafdruk van ${escapeHtml(d.enrollment?.display_name || hh.owner_name)} opgeslagen!</strong> Klik nu op <em>Test nu live</em> hierboven om het zelf te zien.`;
            speakDutch(`Top ${hh.owner_name || ''}, ik heb je stemgeluid lokaal opgeslagen! Klik gerust op de testknop om te zien hoe ik je herken.`);
          } catch (_e) {
            msg.style.color = '#fb7185';
            msg.textContent = 'Geef je browser toestemming voor de microfoon.';
          } finally {
            btn.disabled = false;
          }
        };

        $('btn-wiz-voicetest').onclick = async () => {
          userUnlockedAudio = true;
          stopSpeaking();
          const btn = $('btn-wiz-voicetest');
          const msg = $('wiz-voice-msg');
          btn.disabled = true;
          btn.textContent = '🔴 Zeg nu kort iets om te testen (2,5 sec)…';
          try {
            const pcmB64 = await captureFixedPcm16B64(2600);
            const r = await fetch('/internal/v1/appliance/living/voice-check', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ audio_pcm_b64: pcmB64 })
            });
            const d = await r.json();
            if (!r.ok) {
              msg.style.color = '#fb7185';
              msg.textContent = d.detail || 'Test mislukt.';
              return;
            }
            const m = d.match || {};
            if (m.status === 'household_voice_candidate') {
              msg.style.color = '#6ee7b7';
              msg.innerHTML = `🎯 <strong>Herkend als ${escapeHtml(m.display_name)}!</strong> (Stem-overeenkomst: <strong>${m.similarity_pct}%</strong>)`;
              speakDutch(`Ik hoor meteen dat jij het bent, ${m.display_name}!`);
            } else {
              msg.style.color = '#f59e0b';
              msg.innerHTML = `🛡️ <strong>Niet herkend als ingeleerd gezinslid</strong> — als dit een bezoeker is, springt automatisch het Privéschild aan. (Tip: spreek eventueel nog één keer duidelijk 3,5 sec in om je profiel te versterken.)`;
            }
          } finally {
            btn.disabled = false;
            btn.textContent = '🔍 Test nu live of Eva mijn stem herkent (2,5 sec)';
          }
        };
      }

      if (wizardStep === 4) {
        body.querySelectorAll('[data-preset]').forEach(b => {
          b.onclick = async () => {
            userUnlockedAudio = true;
            await sendLivingTurn({ preset_id: b.dataset.preset });
            renderWizardStep();
          };
        });
        body.querySelectorAll('[data-add-rule]').forEach(b => {
          b.onclick = async () => {
            userUnlockedAudio = true;
            b.disabled = true;
            await sendLivingTurn({ text: `Nieuwe huisafspraak: ${b.dataset.addRule}` });
            renderWizardStep();
          };
        });
        $('btn-wiz-add-rule').onclick = async () => {
          const val = $('wiz-custom-rule').value.trim();
          if (!val) return;
          $('wiz-custom-rule').value = '';
          await sendLivingTurn({ text: `Nieuwe huisafspraak: ${val}` });
          renderWizardStep();
        };
      }

      if (wizardStep === 5) {
        wireUniversalIntegrationsHandlers(() => renderWizardStep());
        $('btn-wiz-lola-qr').onclick = async () => {
          const nm = $('wiz-wlk-name').value.trim() || 'Lola';
          const r = await fetch('/internal/v1/appliance/living/member-welcome', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ member_name: nm, role: 'gezin' })
          });
          const d = await r.json();
          if (r.ok) {
            $('wiz-lola-qr-out').innerHTML = `<div class="qr-box">${d.qr_svg}<div style="font-weight:700; margin-top:6px;">Scan met camera van ${escapeHtml(d.member_name)}</div><div style="font-size:0.72rem; word-break:break-all; color:#475569;">${escapeHtml(d.url)}</div></div>`;
          }
        };
        $('btn-wiz-owner-qr').onclick = async () => {
          const r = await fetch('/internal/v1/appliance/contact/device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'pocket', label: `${hh.owner_name || 'Jaap'} Telefoon`, allow_private: true, allow_owner_voice: true })
          });
          const d = await r.json();
          if (r.ok) {
            $('wiz-owner-qr-out').innerHTML = `<div class="qr-box">${d.qr_svg}<div style="font-weight:700; margin-top:6px;">Jouw Eva Pocket</div></div>`;
          }
        };
        $('btn-wiz-tg-qr').onclick = async () => {
          const r = await fetch('/internal/v1/appliance/contact/telegram/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bot_token: $('wiz-tg-tok').value.trim(), role: 'owner', person_name: hh.owner_name || 'Jaap' })
          });
          const d = await r.json();
          if (!r.ok) {
            $('wiz-tg-out').innerHTML = `<div style="color:#fb7185; font-size:0.8rem; margin-top:6px;">${escapeHtml(d.detail || 'Fout')}</div>`;
            return;
          }
          $('wiz-tg-out').innerHTML = `<div class="qr-box">${d.qr_svg}<div style="font-weight:700; margin-top:6px;">Scan &amp; tik Start in @${escapeHtml(d.bot_username)}</div></div>`;
          clearInterval(tgPollTimer);
          tgPollTimer = setInterval(async () => {
            const st = await (await fetch('/internal/v1/appliance/contact/telegram/status')).json();
            if (st.status === 'linked') {
              clearInterval(tgPollTimer);
              $('wiz-tg-out').innerHTML = '<div style="color:#6ee7b7; margin-top:8px; font-weight:700;">✓ Telegram gekoppeld!</div>';
              refreshStage();
            }
          }, 2200);
        };
      }

      if (wizardStep === 6) {
        $('btn-wiz-finish').onclick = async () => {
          userUnlockedAudio = true;
          const btn = $('btn-wiz-finish');
          btn.disabled = true;
          btn.textContent = 'Activeren…';
          const r = await fetch('/internal/v1/appliance/living/wizard-state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ completed: true })
          });
          const d = await r.json();
          if (r.ok && d.stage) applyStageState(d.stage);
          closeDrawer();
          waveUntil = performance.now() + 2600;
          await speakDutch(`Helemaal klaar, ${stage.household?.owner_name || 'Jaap'}! Alles staat ingesteld. Vraag me gerust wat er in huis speelt of zeg wat je wilt regelen.`);
        };
      }
    }

    function renderDrawer(kind) {
      if (!stage) return;
      if (kind === 'wizard') {
        renderWizardStep();
        return;
      }
      const body = $('drawer-body');
      const title = $('drawer-title');
      const hh = stage.household || {};
      const sh = stage.channels?.smart_home || {};

      if (kind === 'smart_home' || kind === 'integrations') {
        if (kind === 'smart_home' && selectedIntegrationPreset === 'auto' && !stage.integrations?.pending_request) {
          selectedIntegrationPreset = 'home_assistant';
        }
        title.textContent = '🔌 Koppelingen, Kluis & Eigen Vaardigheden';
        body.innerHTML = buildUniversalIntegrationsHtml() + `
          <div class="card-sec">
            <button type="button" class="btn-ghost" id="btn-lan-scan" style="width:100%;">🔍 Scan lokaal thuisnetwerk op apparaten (UniFi, Hue, Sonos, Camera’s…)</button>
            <div id="lan-devs-list" style="margin-top:10px;">
              ${(stage.discovered_devices || []).map(d => `<div class="list-item"><span>${escapeHtml(d.display_name || d.friendly_name || d.hostname || d.ip)}</span><span style="color:var(--muted); font-size:0.76rem;">${escapeHtml(d.kind || d.category || '')}</span></div>`).join('')}
            </div>
          </div>
        `;
        wireUniversalIntegrationsHandlers(() => renderDrawer(kind));
        $('btn-lan-scan').onclick = async () => {
          $('btn-lan-scan').textContent = 'Scannen…';
          const r = await fetch('/internal/v1/appliance/living/lan-scan', { method: 'POST' });
          const d = await r.json();
          if (r.ok && d.stage) applyStageState(d.stage);
        };
        return;
      }

      if (kind === 'rules') {
        title.textContent = '📜 Onze Huisafspraken';
        if (hh.guest_mode_active) {
          body.innerHTML = `<div class="card-sec" style="border-color:rgba(245,158,11,0.5);">🛡️ <strong>Privéschild actief</strong> — gezinsafspraken zijn verborgen tijdens visite.</div>`;
          return;
        }
        const rules = hh.custom_rules || [];
        body.innerHTML = rules.length
          ? rules.map((r, i) => `<div class="list-item"><span>${escapeHtml(r)}</span><button type="button" class="btn-ghost" data-del-rule="${i}">✕</button></div>`).join('')
          : `<div class="card-sec" style="color:var(--muted); font-size:0.84rem;">Nog geen eigen huisafspraken. Zeg onderaan bijv. <em>"Na 21:00 uur alleen storen als het dringend is"</em>.</div>`;
        body.querySelectorAll('[data-del-rule]').forEach(b => {
          b.onclick = async () => {
            const r = await fetch(`/internal/v1/appliance/living/rules/${b.dataset.delRule}/remove`, { method: 'POST' });
            const d = await r.json();
            if (r.ok && d.stage) applyStageState(d.stage);
          };
        });
        return;
      }

      if (kind === 'family') {
        title.textContent = '👨‍👩‍👧 Gezin & Lola Welkomst-QR';
        body.innerHTML = `
          <div class="card-sec">
            ${(hh.members || []).map(m => `<div class="list-item"><span><strong>${escapeHtml(m.display_name)}</strong> (${escapeHtml(m.role)})</span><span style="color:${m.voice_enrolled ? '#6ee7b7' : 'var(--muted)'}; font-size:0.76rem;">${m.voice_enrolled ? '🎙️ Stem bekend' : 'Geen stemprofiel'}</span></div>`).join('')}
          </div>
          <div class="card-sec">
            <strong style="display:block; margin-bottom:6px;">💌 Persoonlijke 3D Welkomstkaart</strong>
            <div class="field"><label>Naam</label><input id="fam-wlk-name" type="text" value="Lola" /></div>
            <div class="field">
              <label>Rol</label>
              <select id="fam-wlk-role">
                <option value="gezin">Kerngezin (eigen wensen + stemherkenning)</option>
                <option value="gast">Gast / Visite</option>
              </select>
            </div>
            <button type="button" class="btn-primary" id="btn-fam-wlk">Genereer Persoonlijke QR</button>
            <div id="fam-wlk-out"></div>
          </div>
        `;
        $('btn-fam-wlk').onclick = async () => {
          const r = await fetch('/internal/v1/appliance/living/member-welcome', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ member_name: $('fam-wlk-name').value || 'Lola', role: $('fam-wlk-role').value })
          });
          const d = await r.json();
          if (r.ok) {
            $('fam-wlk-out').innerHTML = `<div class="qr-box">${d.qr_svg}<div style="font-weight:700; margin-top:6px;">Scan met telefoon van ${escapeHtml(d.member_name)}</div><div style="font-size:0.72rem; word-break:break-all; color:#475569;">${escapeHtml(d.url)}</div></div>`;
          }
        };
      }
    }

    // --- 7. Wiring & Startup ---
    $('btn-close-drawer').onclick = closeDrawer;
    $('btn-wizard').onclick = () => activeDrawer === 'wizard' ? closeDrawer() : openDrawer('wizard');
    $('btn-smart-home').onclick = () => (activeDrawer === 'integrations' || activeDrawer === 'smart_home') ? closeDrawer() : openDrawer('integrations');
    $('btn-rules').onclick = () => activeDrawer === 'rules' ? closeDrawer() : openDrawer('rules');
    $('btn-family').onclick = () => activeDrawer === 'family' ? closeDrawer() : openDrawer('family');

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
        speakDutch(next ? 'Visite-modus en privéschild staan aan!' : 'Welkom terug onder ons, de visite-modus is weer uit.');
      }
    };
    $('btn-mic').onclick = toggleMicCapture;
    $('living-form').onsubmit = async ev => {
      ev.preventDefault();
      userUnlockedAudio = true;
      const v = $('talk-input').value.trim();
      if (!v) return;
      $('talk-input').value = '';
      await sendLivingTurn({ text: v });
    };
    document.querySelectorAll('[data-say]').forEach(c => {
      c.onclick = () => { userUnlockedAudio = true; sendLivingTurn({ text: c.dataset.say }); };
    });
    document.querySelectorAll('[data-panel]').forEach(c => {
      c.onclick = () => openDrawer(c.dataset.panel);
    });

    async function refreshStage() {
      const res = await fetch('/internal/v1/appliance/living/state');
      if (res.status === 401) { location.href = '/pair'; return null; }
      const d = await res.json();
      applyStageState(d, { preserveDrawerInputs: activeDrawer === 'wizard' });
      return d;
    }

    async function boot() {
      boot3DEva();
      const d = await refreshStage();
      if (!d) return;
      if (!d.wizard_completed) {
        wizardStep = d.ai_ready ? 2 : 1;
        openDrawer('wizard', wizardStep);
        const greet = d.ai_ready
          ? 'Hoi! Ik ben wakker en ik heb rechts de Kennismakings-Wizard voor je klaargezet. Laten we samen in een paar korte stappen jouw naam, je stem en Home Assistant instellen!'
          : 'Welkom! Koppel rechts in stap 1 eerst mijn AI-brein, dan lopen we samen alle stappen door.';
        await speakDutch(greet);
      } else {
        await speakDutch(`Welkom thuis, ${d.household?.owner_name || 'Jaap'}! Klik gerust op Wizard of Slim Huis bovenaan, of zeg waar ik je mee kan helpen.`);
      }
    }
    boot();
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
    [hidden] { display: none !important; }
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
      bottom: calc(12px + env(safe-area-inset-bottom, 0px));
      z-index: 15; width: min(500px, calc(100vw - 24px));
      padding: 15px 18px; border-radius: 22px;
      background: var(--glass); border: 1px solid var(--border);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
      max-height: min(84dvh, calc(100dvh - 24px)); overflow-y: auto;
    }
    h1 { margin: 0 0 4px; font-size: 1.24rem; }
    .greet { font-size: 0.89rem; line-height: 1.44; color: #e2e8f0; margin-bottom: 10px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 9px; }
    .chip {
      padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.72); color: #cbd5e1; font-size: 0.77rem; cursor: pointer;
    }
    .chip.selected {
      border-color: var(--emerald); background: rgba(6, 78, 59, 0.65); color: #ecfdf5;
    }
    .wish-in {
      width: 100%; padding: 9px 12px; border-radius: 11px;
      border: 1px solid var(--border); background: rgba(2, 6, 23, 0.72);
      color: var(--text); font-size: 0.88rem; margin-bottom: 8px;
    }
    .row { display: flex; gap: 8px; align-items: center; }
    .btn-main {
      flex: 1; padding: 11px 15px; border-radius: 13px; border: none;
      background: linear-gradient(135deg, #38bdf8, #10b981);
      color: #04121f; font-weight: 700; font-size: 0.91rem; cursor: pointer;
      text-decoration: none; text-align: center; display: inline-block;
    }
    .btn-mic {
      padding: 9px 12px; border-radius: 12px; border: 1px solid rgba(56, 189, 248, 0.45);
      background: rgba(8, 47, 73, 0.65); color: #e0f2fe; font-weight: 600; font-size: 0.82rem; cursor: pointer;
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
    let recordedPcmB64 = '';
    let ttsSpeaking = false;

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
      $('wlk-title').textContent = `Hoi ${d.member_name}! 👋`;
      $('wlk-greet').textContent = d.greeting_text;
      $('wlk-interactive').hidden = false;
      if (d.role !== 'gezin') $('voice-row').style.display = 'none';

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
