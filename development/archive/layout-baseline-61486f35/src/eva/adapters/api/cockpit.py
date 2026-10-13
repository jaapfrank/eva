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
                "proof": "React-admin leest status, identiteit en het duurzame Kanbanbord uit Eva's eigenaar-API",
                "meaning": "De handgeschreven cockpitpresentatie is uit de kandidaat verwijderd; Eva's server blijft de enige bron voor identiteit, rechten en werkstatus.",
                "next": "De verpakte assets in een scan-schone image bouwen en uitsluitend via A/B live zetten.",
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
                "version": "hoofdbron 2.37.0; live/worker 2.32.1; OpenAI SDK 3.6.0",
                "role": "Algemene agentkern en afzonderlijke duurzame hulpagent",
                "state": "tested",
                "decision": "De actuele bronkandidaat gebruikt de minimale 2.37.0-kern. De laatste offline gebouwde, scan- en crashbeproefde kandidaat gebruikt 2.36.0; live en de afzonderlijke worker draaien nog 2.32.1.",
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
                "name": "React-admin",
                "version": "5.15.1",
                "role": "Eigenaar-dashboard en read-only Kanban",
                "state": "selected",
                "decision": "De verpakte kandidaat vervangt de handgeschreven presentatie; dnd-kit volgt pas wanneer echte bordmutaties bestaan.",
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


__all__ = ["cockpit_progress"]
