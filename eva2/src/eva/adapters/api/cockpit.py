"""Self-contained, read-only cockpit for Eva's operator dashboard."""

from __future__ import annotations

from typing import Any


_EVIDENCE_INDEX = "release/eva-cockpit-evidence-index-v1.json"


def _evidence(anchor: str) -> str:
    return f"{_EVIDENCE_INDEX}#{anchor}"


def cockpit_progress() -> dict[str, Any]:
    """Return the versioned, evidence-oriented product view shown in the cockpit."""

    return {
        "contract": "EvaCockpitProgress/v1",
        "read_only": True,
        "updated_on": "2026-08-26",
        "legend": [
            {"id": "live", "label": "Live beproefd op eerdere B"},
            {"id": "tested", "label": "Gebouwd en getest"},
            {"id": "selected", "label": "Proefroute gekozen"},
            {"id": "open", "label": "Nog niet aangesloten"},
        ],
        "capabilities": [
            {
                "id": "living-core",
                "title": "Eva's kern",
                "state": "live",
                "proof": "15 van 15 kerntaken live geslaagd",
                "meaning": "Gesprek, geheugen, doelen en gecontroleerde acties hebben een echte bewijsbasis.",
                "next": "Dezelfde eindversie opnieuw als één complete productrun bewijzen.",
                "evidence": _evidence("capability/living-core"),
            },
            {
                "id": "home-skill",
                "title": "Eerste complete skill",
                "state": "live",
                "proof": "Home Assistant lezen, bedienen, automatiseren en terugcontroleren",
                "meaning": "Home Assistant is de eerste verwisselbare skill, niet Eva's hoofdproduct.",
                "next": "De algemene keten op de eindimage via echt Telegram uitvoeren.",
                "evidence": _evidence("capability/home-skill"),
            },
            {
                "id": "personal-memory",
                "title": "Mensen en geheugen",
                "state": "live",
                "proof": "Persoons- en projectgeheugen blijft na herstart gescheiden",
                "meaning": "Hindsight is het langetermijngeheugen; PostgreSQL bewaakt identiteit en toestemming.",
                "next": "Echte gezins- en groepstest plus het geheugenbeheerscherm koppelen.",
                "evidence": _evidence("capability/personal-memory"),
            },
            {
                "id": "safe-actions",
                "title": "Handelen en herstellen",
                "state": "tested",
                "proof": "75 van 75 algemene actieketentests groen",
                "meaning": "Uitvoering, fout, teruglezing en werkelijk succes zijn aparte toestanden.",
                "next": "Crashen midden in een echte huisactie zonder dubbele provideractie.",
                "evidence": _evidence("capability/safe-actions"),
            },
            {
                "id": "cockpit",
                "title": "Centrale cockpit",
                "state": "tested",
                "proof": "Status, identiteit, voortgang en Mijn kring zijn lokaal en alleen-lezen aangesloten",
                "meaning": "De eerste echte cockpitmodule leest account-, toestemming- en geheugenstatus uit Eva's duurzame bron.",
                "next": "Subagents en hun duurzame werkbord als volgende complete module aansluiten.",
                "evidence": _evidence("capability/cockpit"),
            },
            {
                "id": "child-engine",
                "title": "Geïsoleerde child-engine",
                "state": "tested",
                "proof": "Pydantic/Harness-kandidaten samen 45 van 45 tests en onafhankelijk groen",
                "meaning": "De generieke child-runner wordt hergebruikt; hij is nog geen Eva-runtimefunctie.",
                "next": "De workeromgeving aan een duurzame, geautoriseerde Eva-taak koppelen.",
                "evidence": _evidence("capability/child-engine"),
            },
            {
                "id": "runtime-subagents",
                "title": "Runtime-subagents en werkbord",
                "state": "open",
                "proof": "Contract en database-admission bestaan; spawn, lease, resultaat en Kanban nog niet",
                "meaning": "Een geteste enginekandidaat is nog geen werkende productfunctie.",
                "next": "Portable-v2, workerlease, resultaat, ouderhervatting en read-only Kanban sluiten.",
                "evidence": "reviews/delegated-work-database-admission-d510366.md",
            },
            {
                "id": "browser",
                "title": "Browsercomputer",
                "state": "selected",
                "proof": "Microsoft Playwright MCP 0.0.79 geselecteerd als geïsoleerde sidecar",
                "meaning": "We bouwen geen eigen Chromium- of screenshotmotor.",
                "next": "Exact pinnen en één credentialvrije read-only webtaak uitvoeren.",
            },
            {
                "id": "voice",
                "title": "Onderbreekbare voice",
                "state": "selected",
                "proof": "Pydantic AI Realtime 2.34.0 gekozen als eerste providerroute",
                "meaning": "Dezelfde Eva moet luisteren, streamen en direct kunnen stoppen.",
                "next": "Eerste full-duplex sessie bouwen en de echte spreektijd meten.",
            },
            {
                "id": "presence",
                "title": "Aanwezig in huis",
                "state": "open",
                "proof": "De oude Eva bewijst de gewenste deurbel- en speakerbeleving",
                "meaning": "Eva 2 heeft die spontane aanwezigheid nog niet via haar eindroute.",
                "next": "Deurbel, speaker, timing en stilte als één verticale huisproef aansluiten.",
            },
            {
                "id": "installer",
                "title": "Installeren en bijwerken",
                "state": "tested",
                "proof": "Read-only back-up en runtime-secretpreflight onafhankelijk gereviewd",
                "meaning": "De gegevens- en secretbasis is beschermd; build, update, rollback en wizard blijven aparte open bewijzen.",
                "next": "Installeren, koppelen, updaten en terugrollen vanuit één begeleide route.",
                "evidence": _evidence("capability/installer"),
            },
            {
                "id": "learning",
                "title": "Eerste begrensde leerproef",
                "state": "tested",
                "proof": "Eén zelfgekozen repositoryverbetering live uitgevoerd; geen skillcyclus",
                "meaning": "Deze ene proef werkt; automatisch skills maken, evalueren en terugdraaien nog niet.",
                "next": "Een nieuwe skill maken, apart testen, promoveren en bij verslechtering terugdraaien.",
                "evidence": _evidence("capability/learning"),
            },
            {
                "id": "benchmarks",
                "title": "Vergelijkbare benchmarks",
                "state": "selected",
                "proof": "AgentBench, GAIA en OSWorld hebben een vast publicatiecontract",
                "meaning": "LAB-36 blijft apart; openbare scores krijgen de officiële scorer.",
                "next": "De eerste officiële run en gelijke vijf-frameworkproef uitvoeren.",
            },
        ],
        "dependencies": [
            {
                "name": "Hindsight",
                "version": "0.9.1",
                "role": "Langetermijngeheugen",
                "state": "live",
                "decision": "Behouden; Control Plane wordt de geheugenmodule.",
                "evidence": _evidence("dependency/hindsight"),
            },
            {
                "name": "Pydantic AI",
                "version": "2.34.0 kandidaat",
                "role": "Model- en realtimebasis",
                "state": "selected",
                "decision": "Realtime en productmodelroute zijn gekozen maar nog niet als Eva-functie getest.",
            },
            {
                "name": "Pydantic AI Harness",
                "version": "0.24.0",
                "role": "Geïsoleerde interactieve subagents",
                "state": "tested",
                "decision": "Alleen worker-sidecar; geen tweede taakwaarheid.",
                "evidence": _evidence("dependency/pydantic-ai-harness"),
            },
            {
                "name": "Microsoft Playwright MCP",
                "version": "0.0.79",
                "role": "Browsercomputer",
                "state": "selected",
                "decision": "Sidecar met vaste browserimage, sandbox en egressgrens.",
            },
            {
                "name": "DBOS",
                "version": "2.30.0+eva.1",
                "role": "Wake-up en scheduling",
                "state": "tested",
                "decision": "Smalle rol behouden; geen tweede workflowplatform.",
                "evidence": _evidence("dependency/dbos"),
            },
            {
                "name": "Hindsight Control Plane",
                "version": "0.8.6 kandidaat",
                "role": "Geheugen bekijken en recall testen",
                "state": "selected",
                "decision": "Gekozen voor local-only proef; nog niet als release-input gestart of gekoppeld.",
            },
            {
                "name": "React-Admin + dnd-kit",
                "version": "uitgesteld",
                "role": "Latere beheer- en Kanbanschermen",
                "state": "open",
                "decision": "Pas installeren wanneer echte command-API's maatwerk-UI vervangen.",
            },
            {
                "name": "Renovate + Trivy",
                "version": "nog te pinnen",
                "role": "Updatevoorstellen en beveiligingsscan",
                "state": "open",
                "decision": "Losse CI-jobs; nooit automatische productiepromotie.",
            },
        ],
    }


COCKPIT_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Eva — cockpit</title>
  <style>
    :root{color-scheme:dark;--ink:#eef7ff;--muted:#8da6ba;--line:rgba(141,199,255,.16);--glass:rgba(8,20,34,.74);--cyan:#61e7ff;--violet:#a684ff;--green:#76f2b0;--amber:#ffc86a;--red:#ff7c8f;--depth:0 28px 80px rgba(0,0,0,.38)}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;background:#030912;color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,sans-serif;overflow-x:hidden}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 50% 12%,rgba(73,116,255,.2),transparent 34%),radial-gradient(circle at 16% 52%,rgba(0,224,255,.1),transparent 28%),linear-gradient(125deg,#02060b,#071424 52%,#040813);z-index:-3}
    body:after{content:"";position:fixed;inset:0;pointer-events:none;opacity:.28;background-image:linear-gradient(rgba(114,177,255,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(114,177,255,.06) 1px,transparent 1px);background-size:48px 48px;mask-image:linear-gradient(to bottom,black,transparent 88%);z-index:-2}
    a{color:inherit}.skip{position:fixed;left:1rem;top:-4rem;background:#fff;color:#000;padding:.7rem 1rem;z-index:20}.skip:focus{top:1rem}
    .topbar{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.9rem clamp(1rem,3vw,2.5rem);background:rgba(3,9,18,.72);backdrop-filter:blur(24px);border-bottom:1px solid var(--line)}
    .brand{display:flex;align-items:center;gap:.8rem;font-weight:750;letter-spacing:.08em}.brand-mark{width:30px;height:30px;border:1px solid var(--cyan);border-radius:50%;box-shadow:0 0 24px rgba(97,231,255,.45),inset 0 0 15px rgba(97,231,255,.24);position:relative}.brand-mark:after{content:"";position:absolute;inset:7px;border-radius:50%;background:var(--cyan);box-shadow:0 0 18px var(--cyan)}
    .runtime-pill{display:flex;align-items:center;gap:.55rem;border:1px solid var(--line);border-radius:999px;padding:.45rem .8rem;color:var(--muted);background:rgba(255,255,255,.03)}.runtime-dot{width:8px;height:8px;border-radius:50%;background:var(--amber);box-shadow:0 0 12px currentColor}.runtime-pill.ready .runtime-dot{background:var(--green)}.runtime-pill.not-ready .runtime-dot{background:var(--red)}
    .shell{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:calc(100vh - 61px)}
    nav{position:sticky;top:61px;height:calc(100vh - 61px);padding:1.4rem 1rem;border-right:1px solid var(--line);background:rgba(3,10,18,.42);backdrop-filter:blur(14px);overflow:auto}.nav-label{margin:1rem .75rem .45rem;color:#607c91;font-size:.7rem;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.nav-link{display:flex;align-items:center;gap:.75rem;width:100%;padding:.7rem .8rem;margin:.14rem 0;border:1px solid transparent;border-radius:11px;text-decoration:none;color:#a9bdcc}.nav-link:hover,.nav-link:focus-visible{outline:none;color:var(--ink);background:rgba(97,231,255,.07);border-color:var(--line)}.nav-icon{width:8px;height:8px;border:1px solid currentColor;transform:rotate(45deg)}
    main{min-width:0;padding:clamp(1rem,3vw,2.8rem)}
    .hero{min-height:540px;display:grid;grid-template-columns:minmax(230px,1fr) minmax(300px,1.2fr) minmax(230px,1fr);align-items:center;gap:clamp(1rem,3vw,3rem);perspective:1100px;position:relative}
    .hero:before{content:"";position:absolute;left:50%;top:50%;width:min(70vw,850px);height:min(34vw,390px);transform:translate(-50%,-45%) rotateX(67deg);border:1px solid rgba(97,231,255,.12);border-radius:50%;box-shadow:0 0 90px rgba(97,231,255,.07),inset 0 0 60px rgba(166,132,255,.05);pointer-events:none}
    .intro{transform:rotateY(5deg);transform-origin:right}.eyebrow{color:var(--cyan);font-size:.72rem;font-weight:800;letter-spacing:.18em;text-transform:uppercase}.intro h1{font-size:clamp(2.6rem,6vw,5.8rem);line-height:.9;letter-spacing:-.065em;margin:.55rem 0 1rem}.intro h1 span{display:block;color:transparent;-webkit-text-stroke:1px rgba(238,247,255,.55)}.intro p{max-width:42ch;color:var(--muted);font-size:1.05rem}.micro{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1.4rem}.chip{border:1px solid var(--line);border-radius:999px;padding:.35rem .65rem;color:#b8ccda;background:rgba(255,255,255,.03);font-size:.78rem}
    .core-stage{display:grid;place-items:center;min-height:420px;transform-style:preserve-3d}.core{position:relative;width:clamp(220px,27vw,350px);aspect-ratio:1;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle at 34% 28%,rgba(255,255,255,.96),rgba(97,231,255,.52) 5%,rgba(50,83,230,.42) 18%,rgba(13,23,52,.92) 57%,#030812 72%);box-shadow:0 0 80px rgba(58,157,255,.28),inset -28px -32px 70px #01040a,inset 16px 16px 45px rgba(255,255,255,.12);animation:float 7s ease-in-out infinite}.core:before,.core:after{content:"";position:absolute;inset:-18%;border:1px solid rgba(97,231,255,.35);border-radius:50%;transform:rotateX(68deg) rotateZ(9deg);box-shadow:0 0 28px rgba(97,231,255,.12)}.core:after{inset:-34%;border-color:rgba(166,132,255,.2);transform:rotateX(72deg) rotateZ(-28deg)}.core-copy{text-align:center;z-index:2}.core-name{font-size:clamp(2rem,4vw,4rem);font-weight:820;letter-spacing:.12em;text-shadow:0 0 30px rgba(255,255,255,.5)}.core-state{display:block;margin-top:.3rem;color:#b9d6e8;font-size:.76rem;letter-spacing:.16em;text-transform:uppercase}
    .signal-list{transform:rotateY(-5deg);transform-origin:left}.signal{margin:.7rem 0;padding:.9rem 1rem;border-left:1px solid var(--cyan);background:linear-gradient(90deg,rgba(97,231,255,.08),transparent);color:#bdd0dd}.signal strong{display:block;color:var(--ink);font-size:.92rem}.signal span{color:var(--muted);font-size:.8rem}
    section.content{scroll-margin-top:85px;margin-top:2rem}.section-head{display:flex;align-items:end;justify-content:space-between;gap:1rem;margin-bottom:1rem}.section-head h2{margin:0;font-size:clamp(1.55rem,3vw,2.6rem);letter-spacing:-.035em}.section-head p{margin:0;color:var(--muted);max-width:52ch}.kicker{color:var(--cyan);font-size:.7rem;font-weight:800;letter-spacing:.15em;text-transform:uppercase}
    .constellation{position:relative;min-height:480px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;align-items:center;padding:3rem clamp(1rem,4vw,4rem);border:1px solid var(--line);border-radius:26px;background:radial-gradient(circle at 50% 48%,rgba(97,231,255,.13),transparent 22%),linear-gradient(145deg,rgba(9,25,43,.9),rgba(3,9,18,.78));box-shadow:var(--depth);overflow:hidden;perspective:1100px}.constellation:before,.constellation:after{content:"";position:absolute;left:50%;top:48%;width:min(68vw,850px);height:270px;transform:translate(-50%,-50%) rotateX(69deg);border:1px solid rgba(97,231,255,.16);border-radius:50%;pointer-events:none}.constellation:after{width:min(46vw,570px);height:180px;border-color:rgba(166,132,255,.2);transform:translate(-50%,-50%) rotateX(69deg) rotateZ(8deg)}
    .circle-card{position:relative;z-index:2;min-height:180px;padding:1.15rem;border:1px solid rgba(97,231,255,.19);border-radius:18px;background:rgba(4,14,25,.88);backdrop-filter:blur(12px);box-shadow:0 22px 55px rgba(0,0,0,.35);transition:border-color .2s ease,transform .2s ease}.circle-card:hover{transform:translateY(-4px);border-color:rgba(97,231,255,.4)}.circle-card:nth-child(1){transform:rotateY(8deg)}.circle-card:nth-child(3){transform:rotateY(-8deg)}.circle-card h3{margin:.7rem 0 .35rem;font-size:1.15rem}.circle-card p{margin:.25rem 0;color:var(--muted)}.orbit-state{display:inline-flex;align-items:center;gap:.45rem;color:var(--cyan);font-size:.68rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.orbit-state:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 12px currentColor}.circle-card.good .orbit-state{color:var(--green)}.circle-card.attention .orbit-state{color:var(--amber)}.circle-card.unavailable .orbit-state{color:var(--red)}
    .circle-core{position:relative;z-index:3;justify-self:center;display:grid;place-items:center;width:190px;aspect-ratio:1;border-radius:50%;text-align:center;padding:1.2rem;background:radial-gradient(circle at 35% 28%,#fff 0 2%,rgba(97,231,255,.85) 4%,rgba(55,81,225,.48) 22%,rgba(5,15,29,.96) 68%);border:1px solid rgba(97,231,255,.36);box-shadow:0 0 60px rgba(97,231,255,.28),inset 0 0 45px rgba(255,255,255,.08)}.circle-core strong{display:block;font-size:1.25rem}.circle-core span{display:block;margin-top:.35rem;color:#b5cede;font-size:.78rem}.memory-door{position:relative;z-index:3;grid-column:1/-1;justify-self:center;width:min(520px,100%);padding:.85rem 1rem;border:1px solid var(--line);border-radius:12px;text-align:center;color:#7591a5;background:rgba(255,255,255,.025);font-size:.8rem}.memory-door strong{color:#9cb2c2}
    .proof-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}.card{position:relative;min-height:245px;padding:1.2rem;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(14,31,51,.88),rgba(6,15,27,.78));box-shadow:var(--depth);overflow:hidden;transform:translateZ(0);transition:transform .25s ease,border-color .25s ease}.card:hover{transform:translateY(-5px);border-color:rgba(97,231,255,.36)}.card:before{content:"";position:absolute;width:130px;height:130px;right:-55px;top:-55px;border-radius:50%;background:var(--tone,var(--cyan));opacity:.08;filter:blur(2px)}.state-label{display:inline-flex;align-items:center;gap:.4rem;padding:.28rem .55rem;border-radius:999px;background:rgba(255,255,255,.045);font-size:.69rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--tone,var(--cyan))}.state-label:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 10px currentColor}.card h3{margin:1rem 0 .4rem;font-size:1.2rem}.proof{font-weight:700;color:#d9ebf6}.meaning,.next,.evidence{color:var(--muted);font-size:.86rem}.next{margin-top:.9rem;padding-top:.8rem;border-top:1px solid var(--line)}.next strong{color:#b9cddd}.evidence{font-size:.72rem;overflow-wrap:anywhere}.evidence strong{color:#7894a8}.card.live{--tone:var(--green)}.card.tested{--tone:var(--cyan)}.card.selected{--tone:var(--violet)}.card.open{--tone:var(--amber)}
    .dep-table{border:1px solid var(--line);border-radius:18px;overflow:auto;background:var(--glass);box-shadow:var(--depth)}.dep-table table{width:100%;border-collapse:collapse;min-width:930px}.dep-table th,.dep-table td{padding:1rem 1.1rem;border-top:1px solid var(--line);text-align:left;vertical-align:middle}.dep-table thead th{border-top:0;color:#6f8aa0;font-size:.68rem;letter-spacing:.12em;text-transform:uppercase}.dep-table tbody tr:hover{background:rgba(97,231,255,.035)}.dep-name{font-weight:750}.dep-version,.dep-role,.dep-decision,.dep-evidence{color:var(--muted);font-size:.86rem}.dep-evidence{font-size:.72rem;overflow-wrap:anywhere}.dep-state{display:inline-block;margin-top:.28rem;font-size:.67rem;text-transform:uppercase;letter-spacing:.1em;color:var(--cyan)}
    .dock{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.8rem}.module{padding:1rem;border:1px solid var(--line);border-radius:14px;background:rgba(8,20,34,.62)}.module strong{display:block}.module span{color:var(--muted);font-size:.8rem}.module.connected{border-color:rgba(118,242,176,.28)}.module.pending{opacity:.76}
    footer{margin:4rem 0 1rem;padding-top:1.4rem;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:1rem;color:#678196;font-size:.78rem}
    .error{color:var(--red)!important}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
    @keyframes float{0%,100%{transform:translateY(0) rotate(-1deg)}50%{transform:translateY(-13px) rotate(1deg)}}
    @media(max-width:950px){.shell{grid-template-columns:1fr}nav{position:static;height:auto;display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line);padding:.6rem}.nav-label{display:none}.nav-link{min-width:max-content}.hero{grid-template-columns:1fr;min-height:auto;padding:3rem 0}.intro,.signal-list{transform:none}.core-stage{grid-row:1}.constellation{grid-template-columns:1fr;min-height:0}.circle-card:nth-child(1),.circle-card:nth-child(3){transform:none}.circle-core{grid-row:1}.memory-door{grid-column:auto}}
    @media(max-width:560px){.topbar{align-items:flex-start}.hero{padding-top:1rem}.core{width:230px}.section-head{display:block}.section-head p{margin-top:.5rem}footer{display:block}}
    @media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.core{animation:none}.card{transition:none}.card:hover{transform:none}}
  </style>
</head>
<body>
  <a class="skip" href="#main">Ga naar de inhoud</a>
  <header class="topbar">
    <div class="brand"><span class="brand-mark" aria-hidden="true"></span><span>EVA / LIVING COCKPIT</span></div>
    <div id="runtime-pill" class="runtime-pill" aria-live="polite"><span class="runtime-dot" aria-hidden="true"></span><span id="runtime-text">Kernstatus ophalen…</span></div>
  </header>
  <div class="shell">
    <nav aria-label="Cockpitonderdelen">
      <p class="nav-label">Eva</p>
      <a class="nav-link" href="#main"><span class="nav-icon"></span>Leven</a>
      <a class="nav-link" href="#kring"><span class="nav-icon"></span>Mijn kring</a>
      <a class="nav-link" href="#bewijs"><span class="nav-icon"></span>Echte voortgang</a>
      <p class="nav-label">Werk</p>
      <a class="nav-link" href="#werkbord"><span class="nav-icon"></span>Werkbord</a>
      <a class="nav-link" href="#dependencies"><span class="nav-icon"></span>Skills & bouwstenen</a>
      <p class="nav-label">Beheer</p>
      <a class="nav-link" href="#modules"><span class="nav-icon"></span>Alle modules</a>
    </nav>
    <main id="main">
      <section class="hero" aria-labelledby="eva-title">
        <div class="intro">
          <p class="eyebrow">Eén assistent · één waarheid</p>
          <h1 id="eva-title">Het leven <span>van Eva</span></h1>
          <p>Niet alleen een serverstatus. Hier zie je wie Eva is, wat echt werkt, waar zij mee bezig is en welke handen uit de open-sourcewereld zij gebruikt.</p>
          <div class="micro"><span class="chip">alleen-lezen</span><span class="chip">geen secrets</span><span class="chip" id="progress-date">voortgang ophalen…</span></div>
        </div>
        <div class="core-stage" aria-label="Eva kern">
          <div class="core"><div class="core-copy"><span id="identity-name" class="core-name">EVA</span><span id="identity-style" class="core-state">identiteit ophalen…</span></div></div>
        </div>
        <div class="signal-list" aria-label="Kernsignalen">
          <div class="signal"><strong>Wie ben ik?</strong><span id="identity-detail">Niet beschikbaar</span></div>
          <div class="signal"><strong>Kan ik werken?</strong><span id="runtime-detail">Niet beschikbaar</span></div>
          <div class="signal"><strong>Waar werk ik aan?</strong><span>Mijn kring aangesloten · subagents en werkbord zijn het volgende project</span></div>
          <div class="signal"><strong>Wat blijft van mij?</strong><span>Identiteit, privacy, mandaat, ledger en eindantwoord</span></div>
        </div>
      </section>

      <section id="kring" class="content" aria-labelledby="kring-title">
        <div class="section-head"><div><span class="kicker">Privacyveilige live status</span><h2 id="kring-title">Mijn kring</h2></div><p>Geen namen of herinneringen op straat. Deze constellatie leest alleen veilige toestanden uit Eva's eigen database en de grove Hindsight-health.</p></div>
        <div class="constellation" aria-live="polite">
          <article id="people-card" class="circle-card unavailable"><span id="people-state" class="orbit-state">Ophalen</span><h3>Mijn mensen</h3><p id="people-detail">Koppelstatus niet beschikbaar</p><p>Persoons- en projectafscherming is eerder getest.</p></article>
          <div class="circle-core"><div><strong>EVA</strong><span id="memory-story">Mijn kring ophalen…</span></div></div>
          <article id="sharing-card" class="circle-card unavailable"><span id="sharing-state" class="orbit-state">Ophalen</span><h3>Wat we delen</h3><p id="sharing-detail">Toestemming niet beschikbaar</p><p>Groepsdelen kan altijd weer worden ingetrokken.</p></article>
          <article id="memory-card" class="circle-card unavailable"><span id="memory-state" class="orbit-state">Ophalen</span><h3>Geheugenstroom</h3><p id="memory-detail">Verwerking niet beschikbaar</p><p id="hindsight-detail">Hindsight-status ophalen…</p></article>
          <div class="memory-door"><strong>Herinneringen beheren</strong> · Hindsight Control Plane is als proefroute gekozen, maar nog niet aan Eva gekoppeld.</div>
        </div>
      </section>

      <section id="bewijs" class="content" aria-labelledby="bewijs-title">
        <div class="section-head"><div><span class="kicker">Geen wenslijst</span><h2 id="bewijs-title">De bewijsladder</h2></div><p>Geen verzonnen totaalpercentage. Live betekent hier: exact beproefd op een eerdere B-versie; herhaling op de bevroren eindimage blijft open.</p></div>
        <div id="proof-grid" class="proof-grid" aria-live="polite"><article class="card open"><span class="state-label">Laden</span><h3>Voortgang ophalen…</h3></article></div>
      </section>

      <section id="werkbord" class="content" aria-labelledby="work-title">
        <div class="section-head"><div><span class="kicker">Eerst echt afmaken</span><h2 id="work-title">Van project naar product</h2></div><p>Groen is afgebouwd en getest. Het eerste donkere blok is het enige actieve vervolgproject; de rest wacht bewust.</p></div>
        <div class="dock">
          <article class="module connected"><strong>1. Upgradeveiligheid</strong><span>Portable-v1 hersteld en onafhankelijk gereviewd</span></article>
          <article class="module connected"><strong>2. Centrale cockpit</strong><span>Lokale HTML/CSS-3D + echte status en identiteit</span></article>
          <article class="module connected"><strong>3. Mijn kring</strong><span>Accountkoppeling, groepsdelen en geheugenstroom live uit PostgreSQL</span></article>
          <article class="module pending"><strong>4. Subagents & werkbord</strong><span>Het eerstvolgende complete project</span></article>
          <article class="module pending"><strong>5. Browsercomputer</strong><span>Daarna één echte afgeschermde webtaak</span></article>
          <article class="module pending"><strong>6. Voice & aanwezigheid</strong><span>Daarna dezelfde Eva snel, onderbreekbaar en in huis</span></article>
        </div>
      </section>

      <section id="dependencies" class="content" aria-labelledby="dep-title">
        <div class="section-head"><div><span class="kicker">Bestaande bouwstenen eerst</span><h2 id="dep-title">Wat we hergebruiken</h2></div><p>Een proefroute is nog geen winnaar. Alleen een gemeten dependency wordt een afgeschermde hand van Eva, nooit een tweede brein of taakdatabase.</p></div>
        <div class="dep-table" aria-live="polite"><table><thead><tr><th scope="col">Bouwsteen</th><th scope="col">Versie</th><th scope="col">Rol</th><th scope="col">Besluit</th><th scope="col">Bewijs</th></tr></thead><tbody id="dependency-table"><tr><th scope="row">Bouwstenen ophalen…</th><td>Niet beschikbaar</td><td>Niet beschikbaar</td><td>Niet beschikbaar</td><td>Niet beschikbaar</td></tr></tbody></table></div>
      </section>

      <section id="modules" class="content" aria-labelledby="module-title">
        <div class="section-head"><div><span class="kicker">Eén ingang</span><h2 id="module-title">Het hele Eva-landschap</h2></div><p>De modules komen in deze shell. Ontbrekende projecties worden eerlijk uitgeschakeld, nooit gevuld met fictieve nullen.</p></div>
        <div class="dock">
          <article class="module connected"><strong>Overzicht</strong><span>Aangesloten</span></article>
          <article class="module connected"><strong>Identiteit</strong><span>Aangesloten</span></article>
          <article class="module pending"><strong>Gesprekken</strong><span>Nog niet aangesloten</span></article>
          <article class="module connected"><strong>Personen & geheugen</strong><span>Veilige live status aangesloten; inhoudelijk geheugenbeheer volgt later</span></article>
          <article class="module pending"><strong>Werkbord</strong><span>Read-only projectie volgt</span></article>
          <article class="module pending"><strong>Workers & subagents</strong><span>Runtimewriter nog open</span></article>
          <article class="module pending"><strong>Skills & browser</strong><span>Sidecars nog niet gekoppeld</span></article>
          <article class="module pending"><strong>Voice & huis</strong><span>Live routes nog open</span></article>
          <article class="module pending"><strong>Kosten & budget</strong><span>Projectie nog open</span></article>
          <article class="module pending"><strong>Bewijs & benchmarks</strong><span>Scorebord nog open</span></article>
          <article class="module pending"><strong>Updates & herstel</strong><span>CI-signalen nog niet gekoppeld</span></article>
        </div>
      </section>
      <footer><span>Eva Cockpit · lokale operatorroute · geen tweede agent</span><span>Ontbrekende data = niet aangesloten, nooit stil “nul”</span></footer>
    </main>
  </div>
  <script>
    const labels={live:'Live beproefd · eerdere B',tested:'Gebouwd en getest',selected:'Proefroute gekozen',open:'Nog niet aangesloten'};
    function object(value){return value!==null&&typeof value==='object'&&!Array.isArray(value)}
    function bounded(value,maximum=500){return typeof value==='string'&&value.length>0&&value.length<=maximum}
    async function getJson(path){const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw new Error('unavailable');return response.json()}
    function statusPayload(data){if(!object(data)||data.contract!=='EvaDashboardStatus/v1'||data.read_only!==true||!object(data.runtime)||typeof data.runtime.ready!=='boolean'||!['ready','not_ready'].includes(data.runtime.state)||data.runtime.ready!==(data.runtime.state==='ready'))throw new Error('invalid status');return data}
    function identityPayload(data){const pack=object(data)&&data.identity;const temperament=object(pack)&&pack.temperament;const validTemperament=bounded(temperament,240)||(Array.isArray(temperament)&&temperament.length>0&&temperament.length<=12&&temperament.every(item=>bounded(item,80)));const validVersion=(Number.isInteger(pack&&pack.pack_version)&&pack.pack_version>0)||bounded(pack&&pack.pack_version,80);if(!object(data)||data.contract!=='EvaDashboardIdentity/v1'||data.read_only!==true||!object(pack)||pack.state!=='current'||!bounded(pack.display_name,80)||!bounded(pack.language,40)||!validTemperament||!validVersion||!object(pack.voice_style)||!bounded(pack.voice_style.name,80)||!Array.isArray(pack.voice_style.qualities)||pack.voice_style.qualities.length>12||pack.voice_style.qualities.some(item=>!bounded(item,80)))throw new Error('invalid identity');return data}
    function progressPayload(data){const states=['live','tested','selected','open'];if(!object(data)||data.contract!=='EvaCockpitProgress/v1'||data.read_only!==true||!bounded(data.updated_on,32)||!Array.isArray(data.capabilities)||data.capabilities.length<1||data.capabilities.length>32||!Array.isArray(data.dependencies)||data.dependencies.length<1||data.dependencies.length>32)throw new Error('invalid progress');for(const item of [...data.capabilities,...data.dependencies]){if(!object(item)||!states.includes(item.state)||(['live','tested'].includes(item.state)&&!bounded(item.evidence)))throw new Error('invalid progress item')}for(const item of data.capabilities){if(!bounded(item.title,120)||!bounded(item.proof)||!bounded(item.meaning)||!bounded(item.next)||('evidence' in item&&!bounded(item.evidence)))throw new Error('invalid capability')}for(const item of data.dependencies){if(!bounded(item.name,120)||!bounded(item.version,120)||!bounded(item.role,240)||!bounded(item.decision)||('evidence' in item&&!bounded(item.evidence)))throw new Error('invalid dependency')}return data}
    function exact(value,keys){return object(value)&&Object.keys(value).sort().join('|')===keys.slice().sort().join('|')}
    function peopleMemoryPayload(data){if(!exact(data,['contains_raw_content','contract','control_plane','hindsight','memory','people','read_only','sharing'])||data.contract!=='EvaPeopleMemoryOverview/v1'||data.read_only!==true||data.contains_raw_content!==false||!exact(data.people,['dynamic_account'])||!exact(data.sharing,['state'])||!exact(data.memory,['state'])||!exact(data.hindsight,['state'])||!exact(data.control_plane,['state'])||!['extra_account_linked','no_extra_account','unavailable'].includes(data.people.dynamic_account)||!['private_only','group_active','expires_soon','unavailable'].includes(data.sharing.state)||!['no_signals','up_to_date','processing','retrying','needs_help','unavailable'].includes(data.memory.state)||!['ready','attention','unknown'].includes(data.hindsight.state)||data.control_plane.state!=='selected_not_connected')throw new Error('invalid people memory');return data}
    function text(id,value,error=false){const node=document.getElementById(id);node.textContent=value;if(error)node.classList.add('error')}
    function renderCapability(item){const card=document.createElement('article');card.className='card '+item.state;const label=document.createElement('span');label.className='state-label';label.textContent=labels[item.state]||'Onbekend';const title=document.createElement('h3');title.textContent=item.title;const proof=document.createElement('p');proof.className='proof';proof.textContent=item.proof;const meaning=document.createElement('p');meaning.className='meaning';meaning.textContent=item.meaning;const next=document.createElement('p');next.className='next';const strong=document.createElement('strong');strong.textContent='Om dit af te maken: ';next.append(strong,document.createTextNode(item.next));card.append(label,title,proof,meaning,next);if(item.evidence){const evidence=document.createElement('p');evidence.className='evidence';const evidenceLabel=document.createElement('strong');evidenceLabel.textContent='Bewijs: ';evidence.append(evidenceLabel,document.createTextNode(item.evidence));card.append(evidence)}return card}
    function renderDependency(item){const row=document.createElement('tr');const name=document.createElement('th');name.scope='row';name.className='dep-name';name.textContent=item.name;const state=document.createElement('span');state.className='dep-state';state.textContent=labels[item.state]||item.state;name.append(document.createElement('br'),state);row.append(name);for(const [value,className] of [[item.version,'dep-version'],[item.role,'dep-role'],[item.decision,'dep-decision'],[item.evidence||'—','dep-evidence']]){const node=document.createElement('td');node.className=className;node.textContent=value;row.append(node)}return row}
    async function loadStatus(){const pill=document.getElementById('runtime-pill');try{const status=statusPayload(await getJson('/internal/v1/dashboard/status'));const ready=status.runtime.ready;pill.classList.add(ready?'ready':'not-ready');text('runtime-text',ready?'Kern gereed':'Kern heeft aandacht nodig');text('runtime-detail',ready?'Gereed voor werk':'Niet gereed · details blijven afgeschermd',!ready)}catch(_error){pill.classList.add('not-ready');text('runtime-text','Kernstatus niet beschikbaar',true);text('runtime-detail','Niet beschikbaar',true)}}
    async function loadIdentity(){try{const pack=identityPayload(await getJson('/internal/v1/dashboard/identity')).identity;const temperament=Array.isArray(pack.temperament)?pack.temperament.join(', '):pack.temperament;text('identity-name',pack.display_name.toUpperCase());text('identity-style',pack.voice_style.name+' · v'+pack.pack_version);text('identity-detail',pack.language+' · '+temperament)}catch(_error){text('identity-name','—',true);text('identity-style','identiteit niet beschikbaar',true);text('identity-detail','Niet beschikbaar',true)}}
    async function loadProgress(){const grid=document.getElementById('proof-grid');const table=document.getElementById('dependency-table');try{const progress=progressPayload(await getJson('/internal/v1/dashboard/progress'));text('progress-date','bijgewerkt '+progress.updated_on);grid.replaceChildren(...progress.capabilities.map(renderCapability));table.replaceChildren(...progress.dependencies.map(renderDependency))}catch(_error){text('progress-date','voortgang niet beschikbaar',true);grid.replaceChildren(renderCapability({state:'open',title:'Voortgang niet beschikbaar',proof:'De bewijsprojectie kon niet worden geladen.',meaning:'Er wordt geen oude of verzonnen status getoond.',next:'Herstel de lokale cockpitroute en laad opnieuw.'}));const row=document.createElement('tr');const cell=document.createElement('td');cell.colSpan=5;cell.className='error';cell.textContent='Dependencies niet beschikbaar';row.append(cell);table.replaceChildren(row)}}
    function orbit(id,state,label,detail,tone){const card=document.getElementById(id+'-card');card.className='circle-card '+tone;text(id+'-state',label);text(id+'-detail',detail)}
    async function loadPeopleMemory(){try{const data=peopleMemoryPayload(await getJson('/internal/v1/dashboard/people-memory'));const people={extra_account_linked:['Veilig gekoppeld','Een extra Telegram-account gebruikt hetzelfde afgeschermde profiel.','good'],no_extra_account:['Geen extra koppeling','Geen extra dynamisch gekoppeld Telegram-account gezien.','good'],unavailable:['Niet beschikbaar','De veilige koppelstatus kan nu niet worden vastgesteld.','unavailable']}[data.people.dynamic_account];orbit('people',data.people.dynamic_account,...people);const sharing={private_only:['Alleen privé','Groepsdelen staat niet actief.','good'],group_active:['Delen actief','Er is geldige toestemming om in een groep te delen.','good'],expires_soon:['Loopt binnenkort af','Een toestemming voor groepsdelen verloopt binnen 24 uur.','attention'],unavailable:['Niet beschikbaar','De deeltoestemming kan nu niet worden vastgesteld.','unavailable']}[data.sharing.state];orbit('sharing',data.sharing.state,...sharing);const memory={no_signals:['Nog geen stroom','Er zijn nog geen duurzame geheugenopdrachten.','good'],up_to_date:['Bijgewerkt','Alle geheugenopdrachten zijn verwerkt.','good'],processing:['Bezig','Eva verwerkt iets nieuws voor haar geheugen.','attention'],retrying:['Probeert opnieuw','Een geheugenopdracht wordt veilig opnieuw geprobeerd.','attention'],needs_help:['Hulp nodig','Een geheugenopdracht is gestopt en vraagt controle.','unavailable'],unavailable:['Niet beschikbaar','De geheugenstroom kan nu niet worden vastgesteld.','unavailable']}[data.memory.state];orbit('memory',data.memory.state,...memory);text('hindsight-detail',data.hindsight.state==='ready'?'Hindsight-langetermijngeheugen is bereikbaar.':data.hindsight.state==='attention'?'Hindsight-langetermijngeheugen vraagt aandacht.':'Hindsight-status is onbekend.',data.hindsight.state!=='ready');const attention=[data.people.dynamic_account,data.sharing.state,data.memory.state,data.hindsight.state].some(value=>['unavailable','needs_help','attention'].includes(value));text('memory-story',attention?'Mijn geheugen vraagt aandacht.':['processing','retrying'].includes(data.memory.state)?'Ik verwerk iets nieuws.':data.memory.state==='no_signals'?'Mijn geheugen wacht op een eerste signaal.':'Mijn geheugen is bijgewerkt.',attention)}catch(_error){orbit('people','unavailable','Niet beschikbaar','De veilige koppelstatus kan nu niet worden vastgesteld.','unavailable');orbit('sharing','unavailable','Niet beschikbaar','De deeltoestemming kan nu niet worden vastgesteld.','unavailable');orbit('memory','unavailable','Niet beschikbaar','De geheugenstroom kan nu niet worden vastgesteld.','unavailable');text('hindsight-detail','Hindsight-status is onbekend.',true);text('memory-story','Mijn geheugenstatus is niet beschikbaar.',true)}}
    void loadStatus();void loadIdentity();void loadProgress();void loadPeopleMemory();
  </script>
</body>
</html>
"""


__all__ = ["COCKPIT_HTML", "cockpit_progress"]
