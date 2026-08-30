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
        "updated_on": "2026-08-27",
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
                "meaning": "Hindsight is het langetermijngeheugen; PostgreSQL bewaakt identiteit en toestemming. Een eventuele Eva1-overname begint nu met een read-only voorvertoning van aantallen en hashes, niet met kopiëren.",
                "next": "Echte gezins- en groepstest plus het geheugenbeheerscherm koppelen. Kies pas daarna bewust of een beperkte Eva1-proef gewenst is.",
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
                "proof": "Status, identiteit, Mijn kring en het duurzame Kanbanbord zijn lokaal aangesloten",
                "meaning": "Eén pagina leest Eva's veilige status en duurzaam opgeslagen proeftaken; er staan geen voorbeeldkaarten in.",
                "next": "De cockpit achter eigenaar-authenticatie op de beheerde kandidaat starten.",
                "evidence": _evidence("capability/cockpit"),
            },
            {
                "id": "child-engine",
                "title": "Losse subagentmotor",
                "state": "tested",
                "proof": "Pydantic/Harness-kandidaten samen 45 van 45 tests en onafhankelijk groen",
                "meaning": "De herbruikbare modelmotor is apart getest; de volgende kaart laat zien hoe Eva hem duurzaam gebruikt.",
                "next": "Harness pas inzetten wanneer Eva korte interactieve fork/join nodig heeft.",
                "evidence": _evidence("capability/child-engine"),
            },
            {
                "id": "runtime-subagents",
                "title": "Subagents vanuit Eva-chat",
                "state": "tested",
                "proof": "Starten, volgen en actief stoppen werkt; de aparte workerimage van 86,6 MB is onafhankelijk groen.",
                "meaning": "Eva kan één begrensde onderzoeker aansturen. Na stop of crash verzint zij geen resultaat en start zij dezelfde modeloproep niet blind opnieuw.",
                "next": "Eén door de eigenaar goedgekeurde echte providerproef via Telegram en de beheerde kandidaat uitvoeren.",
                "evidence": _evidence("capability/runtime-subagents"),
            },
            {
                "id": "browser",
                "title": "Gecontroleerde browserhand",
                "state": "tested",
                "proof": "Lokale read + klik én 1 openbare accountloze pagina via allowlist; 37 + 3 controles groen",
                "meaning": "De openbare leesproef had geen vrije internetbrowser: directe egress was geblokkeerd en een andere website kreeg 403. De lokale klik had afzonderlijke servercontrole en geen blinde tweede poging. Een verse scan vond 8 kritieke + 69 hoge bevindingen in de upstreamimage; twee onderhoudskandidaten verbeterden dat tot maximaal 7 + 47, maar ook die beste kandidaat is afgekeurd voor release. Een derde, onderhouden kandidaat (`agent-browser`) slaagde in dezelfde gesloten klikproef maar bleef 8 kritieke + 55 hoge bevindingen houden en is eveneens niet toegelaten.",
                "next": "Wacht op een aantoonbaar schonere onderhouden browserbasis en herhaal scan én dezelfde proeven; pas daarna een tweede openbare testtaak met eigenaargrant en live stop.",
                "evidence": _evidence("capability/browser"),
            },
            {
                "id": "telegram-real-test",
                "title": "Telegram met twee testaccounts",
                "state": "selected",
                "proof": "Testbot, afgescheiden testdatabase en privacyveilige receipt zijn klaar; nog nul echte testberichten.",
                "meaning": "Eva verwerkt niets totdat twee door de eigenaar gekozen testaccounts de nieuwe testbot benaderen. Dit is geen productie- of gezinsgroep.",
                "next": "Laat beide accounts privé /start sturen, maak een lege testgroep en voer daarna de groeps-, intrek- en herstartproef uit.",
            },
            {
                "id": "voice",
                "title": "Onderbreekbare voice",
                "state": "selected",
                "proof": "Lokale spraakketen stuurde 38 audioblokken door vóór einde invoer; 28 gerichte voice-/gesprekstests bewaken dat zowel speaker als antwoordgenerator stoppen. Deze WSL-host heeft geen bruikbare audiohardware.",
                "meaning": "Dezelfde Eva moet luisteren, streamen en direct kunnen stoppen. De softwarestop is gebouwd, maar dit is nog geen echte microfoon-, speaker- of onderbreekproef.",
                "next": "Dezelfde keten op een host met microfoon en speaker draaien, onderbreken testen en de echte spreektijd meten.",
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
                "proof": "De lokale wizard hervat identiteit en basiskeuzes en kan model, Telegram, Home Assistant en AgentMail instellen, toegang bewaren en controleren",
                "meaning": "Eva controleert eerst het platform. Een koppeling wordt pas groen na een aparte server-side controle; configuratie, secret en providerantwoord komen niet terug in de browser.",
                "next": "Echte provideraccounts, secretrotatie, loskoppelen, fresh install, update en terugrol via dezelfde wizard bewijzen.",
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
                "proof": "GAIA's ontwikkelrunner én een aparte AgentBench FC-CPU-omgeving zijn gecontroleerd; samen 21 checks groen, zonder score.",
                "meaning": "Elke proeftaak krijgt een eigen privégesprek. De officiële antwoorden en scorers blijven buiten Eva; LAB-36 blijft apart.",
                "next": "De AgentBench-taakservers veilig isoleren en daarna dataset, scorer en een echte kandidaatmodelrun vastpinnen; pas dan dezelfde vijf-agentproef uitvoeren.",
            },
        ],
        "dependencies": [
            {
                "name": "Hindsight",
                "version": "0.9.1",
                "role": "Langetermijngeheugen",
                "state": "live",
                "decision": "Behouden; de Control Plane is hoogstens een eigenaar-only, read-only overzicht en nooit Eva's geheugenwaarheid.",
                "evidence": _evidence("dependency/hindsight"),
            },
            {
                "name": "Pydantic AI",
                "version": "2.32.1 + OpenAI SDK 3.6.0",
                "role": "Model- en realtimebasis",
                "state": "tested",
                "decision": "De duurzame subagentworker gebruikt deze gepinde communityroute; 2.34.0 blijft de apart geteste voicekandidaat.",
                "evidence": _evidence("dependency/pydantic-ai"),
            },
            {
                "name": "Pydantic AI Harness",
                "version": "0.25.0 kandidaat",
                "role": "Geïsoleerde interactieve subagents",
                "state": "tested",
                "decision": "Voor korte interactieve fork/join; de duurzame worker gebruikt rechtstreeks Pydantic AI zodat usage en getypeerde output behouden blijven.",
                "evidence": _evidence("dependency/pydantic-ai-harness"),
            },
            {
                "name": "Microsoft Playwright MCP",
                "version": "0.0.79",
                "role": "Browsercomputer",
                "state": "tested",
                "decision": "Functioneel getest, maar een verse scan blokkeert de huidige MCR-digest met 8 CRITICAL + 69 HIGH. Twee onderhoudskandidaten haalden Node-bevindingen weg; de beste (standaard Debian-updates plus npm-verkleining) bleef 7 + 47 en is afgewezen. Een beter onderhouden basis of provider moet opnieuw scannen en proeven.",
                "evidence": _evidence("dependency/microsoft-playwright-mcp"),
            },
            {
                "name": "agent-browser",
                "version": "0.35.1 kandidaat",
                "role": "Onderhouden native browserprovider",
                "state": "tested",
                "decision": "De gesloten read/één-klik/readbackproef, domeinallowlist en gatewayloze netwerkcel zijn groen. De standaard MCP-core bevat echter JavaScript-evaluatie en de verse scan vond 8 CRITICAL + 55 HIGH; daarom afgewezen, niet geïnstalleerd in Eva.",
                "evidence": _evidence("dependency/agent-browser"),
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
                "version": "0.9.1 kandidaat",
                "role": "Algemene Hindsight-beheerinterface",
                "state": "open",
                "decision": "Open kandidaat voor een toekomstige eigenaar-weergave; directe UI-wijzigingen worden niet Eva's geheugenwaarheid.",
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
                "version": "Renovate-config; gerichte Trivy-scan vond blockers",
                "role": "Updatevoorstellen en beveiligingsscan",
                "state": "selected",
                "decision": "Renovate doet alleen kleine, handmatig goed te keuren voorstellen met gepinde image-digests. De eerste gerichte scan blokkeert beide browserimages; een volledige release-scan blijft open.",
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
    .delivery-stage{position:relative;padding:clamp(1.2rem,3vw,2.2rem);border:1px solid rgba(118,242,176,.25);border-radius:26px;background:radial-gradient(circle at 84% 10%,rgba(118,242,176,.12),transparent 34%),linear-gradient(145deg,rgba(10,30,45,.96),rgba(4,12,23,.9));box-shadow:var(--depth);overflow:hidden;perspective:1200px}.delivery-stage:before{content:"";position:absolute;inset:auto -12% -78% 36%;height:360px;border:1px solid rgba(97,231,255,.18);border-radius:50%;transform:rotateX(68deg);box-shadow:0 0 70px rgba(97,231,255,.08);pointer-events:none}.delivery-grid{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;margin-top:1.2rem}.delivery-metric{min-height:132px;padding:1rem;border:1px solid var(--line);border-radius:16px;background:rgba(3,12,22,.72);transform:translateZ(18px);box-shadow:0 18px 36px rgba(0,0,0,.26)}.delivery-metric strong{display:block;color:var(--green);font-size:clamp(1.45rem,3vw,2.35rem);letter-spacing:-.04em}.delivery-metric span{display:block;color:#d8e8f2;font-weight:700}.delivery-metric small{display:block;margin-top:.35rem;color:var(--muted)}.journey{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1.7rem;margin-top:1rem}.journey-step{position:relative;padding:1rem;border:1px solid rgba(97,231,255,.2);border-radius:15px;background:rgba(7,22,36,.82)}.journey-step:not(:last-child):after{content:"→";position:absolute;right:-1.28rem;top:50%;transform:translateY(-50%);color:var(--cyan);font-size:1.25rem}.journey-step b{display:block;color:var(--cyan)}.journey-step span{color:var(--muted);font-size:.82rem}.proof-boundary{position:relative;margin:.9rem 0 0;color:#7995a8;font-size:.78rem}.proof-boundary strong{color:var(--amber)}
    .constellation{position:relative;min-height:480px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;align-items:center;padding:3rem clamp(1rem,4vw,4rem);border:1px solid var(--line);border-radius:26px;background:radial-gradient(circle at 50% 48%,rgba(97,231,255,.13),transparent 22%),linear-gradient(145deg,rgba(9,25,43,.9),rgba(3,9,18,.78));box-shadow:var(--depth);overflow:hidden;perspective:1100px}.constellation:before,.constellation:after{content:"";position:absolute;left:50%;top:48%;width:min(68vw,850px);height:270px;transform:translate(-50%,-50%) rotateX(69deg);border:1px solid rgba(97,231,255,.16);border-radius:50%;pointer-events:none}.constellation:after{width:min(46vw,570px);height:180px;border-color:rgba(166,132,255,.2);transform:translate(-50%,-50%) rotateX(69deg) rotateZ(8deg)}
    .circle-card{position:relative;z-index:2;min-height:180px;padding:1.15rem;border:1px solid rgba(97,231,255,.19);border-radius:18px;background:rgba(4,14,25,.88);backdrop-filter:blur(12px);box-shadow:0 22px 55px rgba(0,0,0,.35);transition:border-color .2s ease,transform .2s ease}.circle-card:hover{transform:translateY(-4px);border-color:rgba(97,231,255,.4)}.circle-card:nth-child(1){transform:rotateY(8deg)}.circle-card:nth-child(3){transform:rotateY(-8deg)}.circle-card:nth-child(4){grid-column:2}.circle-card h3{margin:.7rem 0 .35rem;font-size:1.15rem}.circle-card p{margin:.25rem 0;color:var(--muted)}.orbit-state{display:inline-flex;align-items:center;gap:.45rem;color:var(--cyan);font-size:.68rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.orbit-state:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 12px currentColor}.circle-card.good .orbit-state{color:var(--green)}.circle-card.attention .orbit-state{color:var(--amber)}.circle-card.unavailable .orbit-state{color:var(--red)}
    .circle-core{position:relative;z-index:3;justify-self:center;display:grid;place-items:center;width:190px;aspect-ratio:1;border-radius:50%;text-align:center;padding:1.2rem;background:radial-gradient(circle at 35% 28%,#fff 0 2%,rgba(97,231,255,.85) 4%,rgba(55,81,225,.48) 22%,rgba(5,15,29,.96) 68%);border:1px solid rgba(97,231,255,.36);box-shadow:0 0 60px rgba(97,231,255,.28),inset 0 0 45px rgba(255,255,255,.08)}.circle-core strong{display:block;font-size:1.25rem}.circle-core span{display:block;margin-top:.35rem;color:#b5cede;font-size:.78rem}.memory-door{position:relative;z-index:3;grid-column:1/-1;justify-self:center;width:min(520px,100%);padding:.85rem 1rem;border:1px solid var(--line);border-radius:12px;text-align:center;color:#7591a5;background:rgba(255,255,255,.025);font-size:.8rem}.memory-door strong{color:#9cb2c2}
    .proof-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}.card{position:relative;min-height:245px;padding:1.2rem;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(14,31,51,.88),rgba(6,15,27,.78));box-shadow:var(--depth);overflow:hidden;transform:translateZ(0);transition:transform .25s ease,border-color .25s ease}.card:hover{transform:translateY(-5px);border-color:rgba(97,231,255,.36)}.card:before{content:"";position:absolute;width:130px;height:130px;right:-55px;top:-55px;border-radius:50%;background:var(--tone,var(--cyan));opacity:.08;filter:blur(2px)}.state-label{display:inline-flex;align-items:center;gap:.4rem;padding:.28rem .55rem;border-radius:999px;background:rgba(255,255,255,.045);font-size:.69rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--tone,var(--cyan))}.state-label:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 10px currentColor}.card h3{margin:1rem 0 .4rem;font-size:1.2rem}.proof{font-weight:700;color:#d9ebf6}.meaning,.next,.evidence{color:var(--muted);font-size:.86rem}.next{margin-top:.9rem;padding-top:.8rem;border-top:1px solid var(--line)}.next strong{color:#b9cddd}.evidence{font-size:.72rem;overflow-wrap:anywhere}.evidence strong{color:#7894a8}.card.live{--tone:var(--green)}.card.tested{--tone:var(--cyan)}.card.selected{--tone:var(--violet)}.card.open{--tone:var(--amber)}
    .dep-table{border:1px solid var(--line);border-radius:18px;overflow:auto;background:var(--glass);box-shadow:var(--depth)}.dep-table table{width:100%;border-collapse:collapse;min-width:930px}.dep-table th,.dep-table td{padding:1rem 1.1rem;border-top:1px solid var(--line);text-align:left;vertical-align:middle}.dep-table thead th{border-top:0;color:#6f8aa0;font-size:.68rem;letter-spacing:.12em;text-transform:uppercase}.dep-table tbody tr:hover{background:rgba(97,231,255,.035)}.dep-name{font-weight:750}.dep-version,.dep-role,.dep-decision,.dep-evidence{color:var(--muted);font-size:.86rem}.dep-evidence{font-size:.72rem;overflow-wrap:anywhere}.dep-state{display:inline-block;margin-top:.28rem;font-size:.67rem;text-transform:uppercase;letter-spacing:.1em;color:var(--cyan)}
    .dock{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.8rem}.module{padding:1rem;border:1px solid var(--line);border-radius:14px;background:rgba(8,20,34,.62)}.module strong{display:block}.module span{color:var(--muted);font-size:.8rem}.module.connected{border-color:rgba(118,242,176,.28)}.module.pending{opacity:.76}
    .board-shell{position:relative;border:1px solid rgba(97,231,255,.18);border-radius:24px;padding:1rem;background:linear-gradient(145deg,rgba(9,25,43,.94),rgba(3,9,18,.84));box-shadow:var(--depth);overflow:hidden}.board-shell:before{content:"";position:absolute;inset:-40% 20% 45%;background:radial-gradient(circle,rgba(97,231,255,.12),transparent 58%);pointer-events:none}.board-status{position:relative;display:flex;justify-content:space-between;gap:1rem;align-items:center;margin:.2rem .2rem 1rem;color:var(--muted);font-size:.82rem}.readonly{border:1px solid var(--line);border-radius:999px;padding:.32rem .62rem;color:var(--cyan);font-size:.68rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase}.kanban{position:relative;display:grid;grid-template-columns:repeat(5,minmax(210px,1fr));gap:.75rem;overflow-x:auto;padding-bottom:.35rem}.lane{min-height:280px;padding:.8rem;border:1px solid var(--line);border-radius:16px;background:rgba(3,11,20,.66)}.lane h3{display:flex;justify-content:space-between;gap:.5rem;margin:.1rem 0 .8rem;font-size:.78rem;letter-spacing:.1em;text-transform:uppercase;color:#8ba7ba}.lane-count{display:grid;place-items:center;min-width:22px;height:22px;border:1px solid var(--line);border-radius:50%;color:var(--ink);font-size:.68rem}.work-cards{display:grid;gap:.65rem}.work-card{padding:.85rem;border:1px solid var(--line);border-radius:13px;background:linear-gradient(145deg,rgba(16,34,55,.96),rgba(6,16,29,.95));box-shadow:0 12px 28px rgba(0,0,0,.3);transform:translateZ(12px)}.work-card.result-failed,.work-card.result-ambiguous{border-color:rgba(255,124,143,.38)}.work-card.result-awaiting_review{border-color:rgba(166,132,255,.35)}.work-card.result-approved{border-color:rgba(118,242,176,.3)}.work-card.result-stopped{border-color:rgba(255,200,106,.35)}.work-card h4{margin:.35rem 0;font-size:.94rem}.work-card p{margin:.2rem 0;color:var(--muted);font-size:.76rem}.worker-line{display:flex;justify-content:space-between;gap:.5rem;color:var(--cyan);font-size:.67rem}.work-meta{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.65rem}.work-meta span{border:1px solid var(--line);border-radius:999px;padding:.22rem .42rem;color:#9eb5c5;font-size:.63rem}.lane-empty{padding:.8rem;border:1px dashed var(--line);border-radius:12px;color:#607d91;text-align:center;font-size:.72rem}.roadmap{margin-top:1rem}
    footer{margin:4rem 0 1rem;padding-top:1.4rem;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:1rem;color:#678196;font-size:.78rem}
    .error{color:var(--red)!important}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
    @keyframes float{0%,100%{transform:translateY(0) rotate(-1deg)}50%{transform:translateY(-13px) rotate(1deg)}}
    @media(max-width:950px){.shell{grid-template-columns:1fr}nav{position:static;height:auto;display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line);padding:.6rem}.nav-label{display:none}.nav-link{min-width:max-content}.hero{grid-template-columns:1fr;min-height:auto;padding:3rem 0}.intro,.signal-list{transform:none}.core-stage{grid-row:1}.delivery-grid,.journey{grid-template-columns:repeat(2,minmax(0,1fr))}.journey-step:after{display:none}.constellation{grid-template-columns:1fr;min-height:0}.circle-card:nth-child(1),.circle-card:nth-child(3){transform:none}.circle-card:nth-child(4){grid-column:auto}.circle-core{grid-row:1}.memory-door{grid-column:auto}}
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
      <a class="nav-link" href="#vandaag"><span class="nav-icon"></span>Vandaag opgeleverd</a>
      <a class="nav-link" href="#kring"><span class="nav-icon"></span>Mijn kring</a>
      <a class="nav-link" href="#bewijs"><span class="nav-icon"></span>Echte voortgang</a>
      <p class="nav-label">Werk</p>
      <a class="nav-link" href="/internal/v1/dashboard/conversation"><span class="nav-icon"></span>Gesprek</a>
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
          <div class="micro"><span class="chip">alleen-lezen</span><span class="chip">lokale route · authenticatie nog open</span><span class="chip" id="progress-date">voortgang ophalen…</span></div>
        </div>
        <div class="core-stage" aria-label="Eva kern">
          <div class="core"><div class="core-copy"><span id="identity-name" class="core-name">EVA</span><span id="identity-style" class="core-state">identiteit ophalen…</span></div></div>
        </div>
        <div class="signal-list" aria-label="Kernsignalen">
          <div class="signal"><strong>Wie ben ik?</strong><span id="identity-detail">Niet beschikbaar</span></div>
          <div class="signal"><strong>Kan ik werken?</strong><span id="runtime-detail">Niet beschikbaar</span></div>
          <div class="signal"><strong>Waar werk ik aan?</strong><span id="work-signal">Duurzame subagenttaken ophalen…</span></div>
          <div class="signal"><strong>Wat blijft van mij?</strong><span>Identiteit, privacy, mandaat, ledger en eindantwoord</span></div>
        </div>
      </section>

      <section id="vandaag" class="content" aria-labelledby="vandaag-title">
        <div class="delivery-stage">
          <div class="section-head"><div><span class="kicker">Verticale slice gesloten</span><h2 id="vandaag-title">Vandaag kreeg Eva haar eerste echte onderzoeker</h2></div><p>Niet nog een ontwerpdocument: één kleine subagentroute is van Eva-opdracht tot stop, opslag, workerimage en onafhankelijke controle afgemaakt.</p></div>
          <div class="delivery-grid" aria-label="Bewijs van de subagentkandidaat">
            <article class="delivery-metric"><strong>40 + 28</strong><span>gerichte controles groen</span><small>Inclusief echte PostgreSQL-, proces- en stopraces.</small></article>
            <article class="delivery-metric"><strong>Maximaal 1×</strong><span>dezelfde modeloproep</span><small>Twee workers of een herstart veroorzaken geen tweede blinde oproep.</small></article>
            <article class="delivery-metric"><strong>86,6 MB</strong><span>aparte workerimage</span><small>Niet-root, read-only en zonder browser-, web- of migratieplatform.</small></article>
            <article class="delivery-metric"><strong>GO</strong><span>onafhankelijke herreview</span><small>De gevonden stopraces zijn eerst hersteld en daarna opnieuw beproefd.</small></article>
          </div>
          <div class="journey" aria-label="Hoe Eva een subagent gebruikt">
            <article class="journey-step"><b>1 · Jij vraagt</b><span>Eva's commandroute ontvangt start, status of stop.</span></article>
            <article class="journey-step"><b>2 · Eva begrenst</b><span>Taak, eigenaar, model, calls, tokens, tijd en kosten liggen vast.</span></article>
            <article class="journey-step"><b>3 · Onderzoeker werkt</b><span>Een kleine losse worker gebruikt de officiële SDK precies binnen die grenzen.</span></article>
            <article class="journey-step"><b>4 · Eva blijft eerlijk</b><span>Resultaat, actieve stop of verloren contact verschijnt als afzonderlijke toestand.</span></article>
          </div>
          <p class="proof-boundary"><strong>Nog niet live:</strong> de door de eigenaar goedgekeurde eerste echte providerproef, echt Telegram en de beheerde B-kandidaat blijven de volgende poort.</p>
        </div>
        <div class="delivery-stage">
          <div class="section-head"><div><span class="kicker">Derde verticale slice gesloten</span><h2 id="vandaag-browser-title">Eva klikte één keer en controleerde zelf het resultaat</h2></div><p>Geen zelfgebouwde browser: de officiële Microsoft Playwright MCP-sidecar voerde één exact toegestane lokale actie uit via Eva's bestaande duurzame logboek.</p></div>
          <div class="delivery-grid" aria-label="Bewijs van de afgeschermde browserkandidaat">
            <article class="delivery-metric"><strong>37 + 3</strong><span>controles onafhankelijk groen</span><small>37 connectorgevallen en drie echte PostgreSQL-/Chromiumscenario's uit een schone Git-kopie.</small></article>
            <article class="delivery-metric"><strong>Precies 1×</strong><span>de vaste knop gewijzigd</span><small>De normale route gaf één POST en één servertoestandsovergang.</small></article>
            <article class="delivery-metric"><strong>2 foutvensters</strong><span>geen tweede klik</span><small>Voor en na mogelijke uitvoering bleef de hervatte keten gesloten.</small></article>
            <article class="delivery-metric"><strong>GO</strong><span>voor lokale technische kandidaat</span><small>Onafhankelijk groen; productie, B en algemene browserautonomie zijn NO-GO.</small></article>
          </div>
          <div class="journey" aria-label="Hoe Eva één browseractie veilig uitvoerde">
            <article class="journey-step"><b>1 · Eén vaste opdracht</b><span>Alleen de vastgelegde lokale pagina, knop en verwachte toestand zijn toegestaan.</span></article>
            <article class="journey-step"><b>2 · Eerst in het logboek</b><span>Eva schrijft vóór de klik duurzaam op wat zij gaat doen.</span></article>
            <article class="journey-step"><b>3 · Chromium klikt</b><span>De gepinde communitycontainer voert precies de toegestane actie uit.</span></article>
            <article class="journey-step"><b>4 · Eva controleert apart</b><span>De servertoestand, niet de stoere klikmelding, bepaalt of de taak echt klaar is.</span></article>
          </div>
          <p class="proof-boundary"><strong>Nog niet live:</strong> publieke websites, formulieren, downloads, accounts, meekijken/overname, automatische oplossing van een onduidelijke actie en de Telegram/B-route blijven bewust open.</p>
        </div>
      </section>

      <section id="kring" class="content" aria-labelledby="kring-title">
        <div class="section-head"><div><span class="kicker">Actuele privacyveilige status</span><h2 id="kring-title">Mijn kring</h2></div><p>Geen namen of herinneringen op straat. Deze constellatie leest alleen veilige toestanden uit Eva's eigen database en de grove Hindsight-health.</p></div>
        <div class="constellation" aria-live="polite">
          <article id="people-card" class="circle-card unavailable"><span id="people-state" class="orbit-state">Ophalen</span><h3>Mijn mensen</h3><p id="people-detail">Koppelstatus niet beschikbaar</p><p>Persoons- en projectafscherming is eerder getest.</p></article>
          <div class="circle-core"><div><strong>EVA</strong><span id="memory-story">Mijn kring ophalen…</span></div></div>
          <article id="sharing-card" class="circle-card unavailable"><span id="sharing-state" class="orbit-state">Ophalen</span><h3>Wat we delen</h3><p id="sharing-detail">Toestemming niet beschikbaar</p><p>Groepsdelen kan altijd weer worden ingetrokken.</p></article>
          <article id="memory-card" class="circle-card unavailable"><span id="memory-state" class="orbit-state">Ophalen</span><h3>Geheugenstroom</h3><p id="memory-detail">Verwerking niet beschikbaar</p><p id="hindsight-detail">Hindsight-status ophalen…</p></article>
          <div class="memory-door"><strong>Herinneringen beheren</strong> · Hindsight Control Plane is kandidaat voor een eigenaar-weergave. Hij krijgt pas toegang achter Eva’s privacygrens; directe UI-wijzigingen worden niet stil als waarheid aangenomen.</div>
        </div>
      </section>

      <section id="bewijs" class="content" aria-labelledby="bewijs-title">
        <div class="section-head"><div><span class="kicker">Geen wenslijst</span><h2 id="bewijs-title">De bewijsladder</h2></div><p>Geen verzonnen totaalpercentage. Live betekent hier: exact beproefd op een eerdere B-versie; herhaling op de bevroren eindimage blijft open.</p></div>
        <div id="proof-grid" class="proof-grid" aria-live="polite"><article class="card open"><span class="state-label">Laden</span><h3>Voortgang ophalen…</h3></article></div>
      </section>

      <section id="werkbord" class="content" aria-labelledby="work-title">
        <div class="section-head"><div><span class="kicker">Taken uit Eva's eigen opslag · geen hardgecodeerde voorbeeldkaarten</span><h2 id="work-title">Eva's werkplaats</h2></div><p>Iedere kaart komt uit Eva's opgeslagen werk. Je ziet alleen een veilige titel, korte uitleg, workerstatus, poging, tijd en gemeten kosten. De volledige opdracht, interne nummers en ruwe resultaten blijven verborgen. Deze lokale route heeft nog geen eigenaar-authenticatie.</p></div>
        <div class="board-shell" aria-live="polite">
          <div class="board-status"><span id="workboard-message">Werkbord ophalen…</span><span class="readonly">alleen-lezen</span></div>
          <div class="kanban">
            <section class="lane"><h3>Te doen <span id="count-todo" class="lane-count">0</span></h3><div id="cards-todo" class="work-cards"><p class="lane-empty">Ophalen…</p></div></section>
            <section class="lane"><h3>Bezig <span id="count-doing" class="lane-count">0</span></h3><div id="cards-doing" class="work-cards"><p class="lane-empty">Ophalen…</p></div></section>
            <section class="lane"><h3>Aandacht <span id="count-attention" class="lane-count">0</span></h3><div id="cards-attention" class="work-cards"><p class="lane-empty">Ophalen…</p></div></section>
            <section class="lane"><h3>Controle <span id="count-review" class="lane-count">0</span></h3><div id="cards-review" class="work-cards"><p class="lane-empty">Ophalen…</p></div></section>
            <section class="lane"><h3>Klaar <span id="count-done" class="lane-count">0</span></h3><div id="cards-done" class="work-cards"><p class="lane-empty">Ophalen…</p></div></section>
          </div>
        </div>
        <div class="dock roadmap" aria-label="Bewuste projectvolgorde">
          <article class="module connected"><strong>Af · Subagentkandidaat</strong><span>Chatcommando's, actieve stop, duurzame status, workerimage en onafhankelijke GO</span></article>
          <article class="module connected"><strong>Af · Eerste browser-read</strong><span>Gepinde Playwright-sidecar, echte Chromium en één exact toegestane lokale pagina</span></article>
          <article class="module connected"><strong>Af · Eerste lokale browseractie</strong><span>Eén vaste klik via Eva's logboek, aparte servercontrole en geen blinde tweede klik na twee foutvensters</span></article>
          <article class="module pending"><strong>Volgende · Publieke testroute</strong><span>Eén afgeschermde publieke site met grant, live stop en controle; accounts en downloads volgen later</span></article>
          <article class="module pending"><strong>Daarna · Voice & aanwezigheid</strong><span>Dezelfde Eva snel, onderbreekbaar en weer voelbaar in huis</span></article>
        </div>
      </section>

      <section id="dependencies" class="content" aria-labelledby="dep-title">
        <div class="section-head"><div><span class="kicker">Bestaande bouwstenen eerst</span><h2 id="dep-title">Wat we hergebruiken</h2></div><p>Versie, rol, besluit en bewijs staan hier bij elkaar. Alleen een gemeten dependency wordt een afgeschermde hand van Eva, nooit een tweede brein of taakdatabase.</p></div>
        <div class="dep-table" aria-live="polite"><table><thead><tr><th scope="col">Bouwsteen</th><th scope="col">Versie</th><th scope="col">Rol</th><th scope="col">Besluit</th><th scope="col">Bewijs</th></tr></thead><tbody id="dependency-table"><tr><th scope="row">Bouwstenen ophalen…</th><td>Niet beschikbaar</td><td>Niet beschikbaar</td><td>Niet beschikbaar</td><td>Niet beschikbaar</td></tr></tbody></table></div>
      </section>

      <section id="modules" class="content" aria-labelledby="module-title">
        <div class="section-head"><div><span class="kicker">Eén ingang</span><h2 id="module-title">Het hele Eva-landschap</h2></div><p>De modules komen in deze shell. Ontbrekende projecties worden eerlijk uitgeschakeld, nooit gevuld met fictieve nullen.</p></div>
        <div class="dock">
          <article class="module connected"><strong>Overzicht</strong><span>Aangesloten</span></article>
          <article class="module connected"><strong>Identiteit</strong><span>Aangesloten</span></article>
          <article class="module connected"><strong>Gesprekken</strong><span>Lokale eigenaarspagina gebruikt Eva's gebonden privégesprek; Telegram en voice blijven apart</span></article>
          <article class="module connected"><strong>Personen & geheugen</strong><span>Veilige actuele status aangesloten; inhoudelijk geheugenbeheer volgt later</span></article>
          <article class="module connected"><strong>Werkbord</strong><span>Duurzame read-only projectie aangesloten</span></article>
          <article class="module connected"><strong>Workers & subagents</strong><span>Chatstart, status, actieve stop en kleine workerimage getest; externe provider- en Telegramcanary volgen</span></article>
          <article class="module connected"><strong>Skills & browser</strong><span>Eén lokale Playwright-read en één vaste actie via Eva's logboek getest; publieke sites, accounts, downloads en live route zijn open</span></article>
          <article class="module pending"><strong>Voice & huis</strong><span>Lokale audiostroom getest; echte microfoon-, speaker- en huisproef nog open</span></article>
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
    function peopleMemoryPayload(data){if(!exact(data,['contains_raw_content','contract','control_plane','hindsight','memory','people','read_only','sharing'])||data.contract!=='EvaPeopleMemoryOverview/v1'||data.read_only!==true||data.contains_raw_content!==false||!exact(data.people,['dynamic_account'])||!exact(data.sharing,['state'])||!exact(data.memory,['state'])||!exact(data.hindsight,['state'])||!exact(data.control_plane,['state'])||!['extra_account_linked','no_extra_account','unavailable'].includes(data.people.dynamic_account)||!['private_only','group_active','expires_soon','unavailable'].includes(data.sharing.state)||!['no_signals','up_to_date','processing','retrying','needs_help','unavailable'].includes(data.memory.state)||!['ready','attention','unknown'].includes(data.hindsight.state)||data.control_plane.state!=='owner_viewer_candidate')throw new Error('invalid people memory');return data}
    function workboardPayload(data){const columns=['todo','doing','attention','review','done'];const health=['waiting','active','contact_lost','finished','stopped'];const results=['not_received','awaiting_review','approved','failed','ambiguous','stopped'];const allowed={not_received:['todo','doing'],awaiting_review:['review'],approved:['done'],failed:['attention'],ambiguous:['attention'],stopped:['done']};const root=['cards','contract','message','read_only','schema_version','status','truncated'];const cardKeys=['attempt','changed','column','contract','deadline','duration_seconds','maximum_attempts','overdue','reason','result_status','schema_version','summary','title','usage','worker_health','worker_name','worker_role'];const usageKeys=['contract','cost_usd_micros','input_tokens','measurement','output_tokens','schema_version','total_tokens'];if(!exact(data,root)||data.contract!=='EvaWorkBoard/v1'||data.schema_version!==1||data.read_only!==true||data.status!=='ready'||typeof data.truncated!=='boolean'||!bounded(data.message,240)||!Array.isArray(data.cards)||data.cards.length>64)throw new Error('invalid workboard');for(const card of data.cards){if(!exact(card,cardKeys)||card.contract!=='EvaWorkBoardCard/v1'||card.schema_version!==1||!bounded(card.title,120)||!bounded(card.summary,240)||!columns.includes(card.column)||!bounded(card.worker_name,32)||!bounded(card.worker_role,80)||!health.includes(card.worker_health)||!Number.isInteger(card.attempt)||!Number.isInteger(card.maximum_attempts)||card.attempt<0||card.attempt>card.maximum_attempts||typeof card.overdue!=='boolean'||!Number.isInteger(card.duration_seconds)||card.duration_seconds<0||!bounded(card.deadline,40)||Number.isNaN(Date.parse(card.deadline))||!results.includes(card.result_status)||!allowed[card.result_status].includes(card.column)||!bounded(card.reason,240)||!bounded(card.changed,32)||!exact(card.usage,usageKeys)||card.usage.contract!=='EvaWorkBoardUsage/v1'||card.usage.schema_version!==1||!['measured','partial','unmeasured'].includes(card.usage.measurement))throw new Error('invalid workboard card')}return data}
    function text(id,value,error=false){const node=document.getElementById(id);node.textContent=value;if(error)node.classList.add('error')}
    function renderCapability(item){const card=document.createElement('article');card.className='card '+item.state;const label=document.createElement('span');label.className='state-label';label.textContent=labels[item.state]||'Onbekend';const title=document.createElement('h3');title.textContent=item.title;const proof=document.createElement('p');proof.className='proof';proof.textContent=item.proof;const meaning=document.createElement('p');meaning.className='meaning';meaning.textContent=item.meaning;const next=document.createElement('p');next.className='next';const strong=document.createElement('strong');strong.textContent='Om dit af te maken: ';next.append(strong,document.createTextNode(item.next));card.append(label,title,proof,meaning,next);if(item.evidence){const evidence=document.createElement('p');evidence.className='evidence';const evidenceLabel=document.createElement('strong');evidenceLabel.textContent='Bewijs: ';evidence.append(evidenceLabel,document.createTextNode(item.evidence));card.append(evidence)}return card}
    function renderDependency(item){const row=document.createElement('tr');const name=document.createElement('th');name.scope='row';name.className='dep-name';name.textContent=item.name;const state=document.createElement('span');state.className='dep-state';state.textContent=labels[item.state]||item.state;name.append(document.createElement('br'),state);row.append(name);for(const [value,className] of [[item.version,'dep-version'],[item.role,'dep-role'],[item.decision,'dep-decision'],[item.evidence||'—','dep-evidence']]){const node=document.createElement('td');node.className=className;node.textContent=value;row.append(node)}return row}
    function renderWorkCard(card){const healthLabels={waiting:'wacht',active:'bezig',contact_lost:'contact kwijt',finished:'klaar met uitvoeren',stopped:'gestopt'};const node=document.createElement('article');node.className='work-card result-'+card.result_status;const worker=document.createElement('div');worker.className='worker-line';worker.append(document.createTextNode(card.worker_name+' · '+card.worker_role),document.createTextNode(healthLabels[card.worker_health]));const title=document.createElement('h4');title.textContent=card.title;const summary=document.createElement('p');summary.textContent=card.summary;const reason=document.createElement('p');reason.textContent=card.reason;const meta=document.createElement('div');meta.className='work-meta';const usage=card.usage.measurement==='unmeasured'?'verbruik onbekend':(card.usage.total_tokens===null?'tokens deels gemeten':card.usage.total_tokens+' tokens');const cost=card.usage.cost_usd_micros===null?'kosten onbekend':'$'+(card.usage.cost_usd_micros/1000000).toFixed(4);for(const value of ['poging '+card.attempt+'/'+card.maximum_attempts,Math.round(card.duration_seconds/60)+' min',usage,cost,card.overdue?'te laat':'binnen tijd']){const chip=document.createElement('span');chip.textContent=value;meta.append(chip)}node.append(worker,title,summary,reason,meta);return node}
    async function loadStatus(){const pill=document.getElementById('runtime-pill');try{const status=statusPayload(await getJson('/internal/v1/dashboard/status'));const ready=status.runtime.ready;pill.classList.add(ready?'ready':'not-ready');text('runtime-text',ready?'Kern gereed':'Kern heeft aandacht nodig');text('runtime-detail',ready?'Gereed voor werk':'Niet gereed · details blijven afgeschermd',!ready)}catch(_error){pill.classList.add('not-ready');text('runtime-text','Kernstatus niet beschikbaar',true);text('runtime-detail','Niet beschikbaar',true)}}
    async function loadIdentity(){try{const pack=identityPayload(await getJson('/internal/v1/dashboard/identity')).identity;const temperament=Array.isArray(pack.temperament)?pack.temperament.join(', '):pack.temperament;text('identity-name',pack.display_name.toUpperCase());text('identity-style',pack.voice_style.name+' · v'+pack.pack_version);text('identity-detail',pack.language+' · '+temperament)}catch(_error){text('identity-name','—',true);text('identity-style','identiteit niet beschikbaar',true);text('identity-detail','Niet beschikbaar',true)}}
    async function loadProgress(){const grid=document.getElementById('proof-grid');const table=document.getElementById('dependency-table');try{const progress=progressPayload(await getJson('/internal/v1/dashboard/progress'));text('progress-date','bijgewerkt '+progress.updated_on);grid.replaceChildren(...progress.capabilities.map(renderCapability));table.replaceChildren(...progress.dependencies.map(renderDependency))}catch(_error){text('progress-date','voortgang niet beschikbaar',true);grid.replaceChildren(renderCapability({state:'open',title:'Voortgang niet beschikbaar',proof:'De bewijsprojectie kon niet worden geladen.',meaning:'Er wordt geen oude of verzonnen status getoond.',next:'Herstel de lokale cockpitroute en laad opnieuw.'}));const row=document.createElement('tr');const cell=document.createElement('td');cell.colSpan=5;cell.className='error';cell.textContent='Dependencies niet beschikbaar';row.append(cell);table.replaceChildren(row)}}
    function orbit(id,state,label,detail,tone){const card=document.getElementById(id+'-card');card.className='circle-card '+tone;text(id+'-state',label);text(id+'-detail',detail)}
    async function loadPeopleMemory(){try{const data=peopleMemoryPayload(await getJson('/internal/v1/dashboard/people-memory'));const people={extra_account_linked:['Veilig gekoppeld','Een extra Telegram-account gebruikt hetzelfde afgeschermde profiel.','good'],no_extra_account:['Geen extra koppeling','Geen extra dynamisch gekoppeld Telegram-account gezien.','good'],unavailable:['Niet beschikbaar','De veilige koppelstatus kan nu niet worden vastgesteld.','unavailable']}[data.people.dynamic_account];orbit('people',data.people.dynamic_account,...people);const sharing={private_only:['Alleen privé','Groepsdelen staat niet actief.','good'],group_active:['Delen actief','Er is geldige toestemming om in een groep te delen.','good'],expires_soon:['Loopt binnenkort af','Een toestemming voor groepsdelen verloopt binnen 24 uur.','attention'],unavailable:['Niet beschikbaar','De deeltoestemming kan nu niet worden vastgesteld.','unavailable']}[data.sharing.state];orbit('sharing',data.sharing.state,...sharing);const memory={no_signals:['Nog geen stroom','Er zijn nog geen duurzame geheugenopdrachten.','good'],up_to_date:['Bijgewerkt','Alle geheugenopdrachten zijn verwerkt.','good'],processing:['Bezig','Eva verwerkt iets nieuws voor haar geheugen.','attention'],retrying:['Probeert opnieuw','Een geheugenopdracht wordt veilig opnieuw geprobeerd.','attention'],needs_help:['Hulp nodig','Een geheugenopdracht is gestopt en vraagt controle.','unavailable'],unavailable:['Niet beschikbaar','De geheugenstroom kan nu niet worden vastgesteld.','unavailable']}[data.memory.state];orbit('memory',data.memory.state,...memory);text('hindsight-detail',data.hindsight.state==='ready'?'Hindsight-langetermijngeheugen is bereikbaar.':data.hindsight.state==='attention'?'Hindsight-langetermijngeheugen vraagt aandacht.':'Hindsight-status is onbekend.',data.hindsight.state!=='ready');const attention=[data.people.dynamic_account,data.sharing.state,data.memory.state,data.hindsight.state].some(value=>['unavailable','needs_help','attention'].includes(value));text('memory-story',attention?'Mijn geheugen vraagt aandacht.':['processing','retrying'].includes(data.memory.state)?'Ik verwerk iets nieuws.':data.memory.state==='no_signals'?'Mijn geheugen wacht op een eerste signaal.':'Mijn geheugen is bijgewerkt.',attention)}catch(_error){orbit('people','unavailable','Niet beschikbaar','De veilige koppelstatus kan nu niet worden vastgesteld.','unavailable');orbit('sharing','unavailable','Niet beschikbaar','De deeltoestemming kan nu niet worden vastgesteld.','unavailable');orbit('memory','unavailable','Niet beschikbaar','De geheugenstroom kan nu niet worden vastgesteld.','unavailable');text('hindsight-detail','Hindsight-status is onbekend.',true);text('memory-story','Mijn geheugenstatus is niet beschikbaar.',true)}}
    async function loadWorkboard(){const columns=['todo','doing','attention','review','done'];try{const data=workboardPayload(await getJson('/internal/v1/dashboard/workboard'));for(const column of columns){const cards=data.cards.filter(card=>card.column===column);text('count-'+column,String(cards.length));const lane=document.getElementById('cards-'+column);if(cards.length){lane.replaceChildren(...cards.map(renderWorkCard))}else{const empty=document.createElement('p');empty.className='lane-empty';empty.textContent='Geen taken';lane.replaceChildren(empty)}}text('workboard-message',data.message+(data.truncated?' · toon de laatste 64':''));text('work-signal',data.cards.length?data.cards.length+' duurzame subagenttaak'+(data.cards.length===1?'':'en')+' zichtbaar':'Geen duurzame subagenttaak actief.')}catch(_error){for(const column of columns){text('count-'+column,'—');const lane=document.getElementById('cards-'+column);const empty=document.createElement('p');empty.className='lane-empty error';empty.textContent='Niet beschikbaar';lane.replaceChildren(empty)}text('workboard-message','Werkbord niet beschikbaar · er worden geen voorbeeldkaarten getoond',true);text('work-signal','Werkbord niet beschikbaar',true)}}
    void loadStatus();void loadIdentity();void loadProgress();void loadPeopleMemory();void loadWorkboard();
  </script>
</body>
</html>
"""


__all__ = ["COCKPIT_HTML", "cockpit_progress"]
