# Eva Runtimekaart

Dit document is het architectuurcontract voor Eva. Het beschrijft niet wat de
code *bedoelt* te doen, maar welke component op ieder moment eigenaar is van een
bericht, handeling, resultaat en verzending.

## Oude live route (31 juli 2026)

```mermaid
flowchart TD
    TG[Telegram] --> TA[Telegram archief en reply/foto-context]
    WEB[Web chat] --> AG
    VOICE[Home Assistant voice] --> AG
    TA --> AG[agent.routeMessage]

    AG --> FR{Vroege speciale route?}
    FR --> FACE[Face antwoordroute]
    FR --> HOME[Snelle huisroute]
    FR --> PROP[Voorstelroute]
    FR --> EVID[Bewijs-vraagroute]
    FR --> MAIN[agent.handle]

    MAIN --> INTENT[coreturn.classifyIntent]
    MAIN --> THREAD1[Commitment thread resolver]
    MAIN --> THREAD2[Mission thread resolver]
    MAIN --> THREAD3[Follow-up resolver]
    MAIN --> PLAN1[Turnplan / LLM planner]
    PLAN1 --> COMMIT[Commitment ledger]
    PLAN1 --> TOOLS[Toolselectie en mandaat]
    TOOLS --> LLM[LLM + toolloop]
    LLM --> VERIFY1[Coreturn bewijscontrole]

    VERIFY1 --> DIRECT[Direct antwoord]
    VERIFY1 --> OPDR[Opdrachtenboek]
    OPDR --> PLAN2[Executive plant opnieuw]
    PLAN2 --> MISSION[Mission ledger]
    MISSION --> HEART[Heartbeat worker]
    HEART --> COG[Cognition + eigen coreturn]
    COG --> VERIFY2[Mission verifier]
    VERIFY2 --> SPEECH[Speech renderer]

    DIRECT --> REWRITE[Coreturn antwoordherschrijving]
    SPEECH --> REWRITE
    REWRITE --> ATT[Attention director]
    ATT --> ARB[Output arbiter]
    ARB --> DEDUPE[Telegram transport-dedupe]
    DEDUPE --> FAMILY[Gezin]

    COMMIT -.status.-> STATUS[Dashboard werkstatus]
    OPDR -.status.-> STATUS
    MISSION -.status.-> STATUS
    THREAD3 -.status.-> STATUS
```

Probleem: intentie wordt tot negen keer beslist, werk heeft vier statusbronnen,
en tekst kan drie keer inhoudelijk veranderen. Een verzendstatus kan bovendien
teruglekken naar de werkstatus. Daardoor kan Eva intern tegelijk klaar, open en
gemeld zijn.

## Actieve minimale route

```mermaid
flowchart TD
    TG[Telegram-adapter] --> ARCHIVE[Sla bericht en exacte reply/foto op]
    WEB[Web-adapter] --> TURN
    VOICE[HA voice-adapter] --> TURN
    ARCHIVE --> TURN[agent.handleMessageDetailed]

    TURN --> QUEUE[Een seriële rij per gesprek]
    QUEUE --> QUICK{Eenduidige enkele huisactie?}
    QUICK -->|ja| TOOLS
    QUICK -->|nee| CONTEXT[Bouw één context: gesprek + geheugen + gezin + actuele HA]
    CONTEXT --> LOOP[runAgent: één model/tool-lus]
    LOOP --> TOOLS[tools.executeTool]
    TOOLS --> VERIFY[Tool geeft een ActionResult met bewijs terug]
    VERIFY --> LOOP
    VERIFY -->|actie bewezen| ANSWER
    LOOP --> ANSWER[Een eindantwoord in Eva's ene persona]
    ANSWER --> PREPARE{Telegram?}
    PREPARE -->|ja| OUTBOX[Antwoord duurzaam aan bronbericht koppelen]
    PREPARE -->|nee| TRANSPORT[Exact één antwoordtransport]
    OUTBOX --> TRANSPORT
    TRANSPORT --> FAMILY[Gezin]
    TRANSPORT --> DELIVERED[Pas nu als gezegd opslaan]

    HAEVENT[Betekenisvolle HA-signalen] --> INBOX[Een begrensde signaal-inbox]
    INBOX --> CLOCK[Autonomietimer]
    CLOCK --> SAFE[Dezelfde loop, veilige toolset]
    SAFE --> LOOP
    SAFE -.alleen bij gecontroleerd resultaat.-> TRANSPORT

    HA[Home Assistant-events] --> CAMERA[Camera-adapter]
    CAMERA --> FACE[Lokale gezichtsherkenning]
    FACE --> FILTER{Binnen meldgebied en deurbel/bekend/leerbaar na rusttijd?}
    FILTER -->|nee| INTERNAL[Alleen interne waarneming]
    FILTER -->|ja| PHOTO[Een gekoppelde Telegram-foto]
    PHOTO --> FAMILY

    REM[Herinneringstimer] --> DUE[Lees vervallen reminder]
    DUE --> TRANSPORT

    WORK[Een duurzaam werkboek] <--> LOOP
```

### Beurt als sequentie

```mermaid
sequenceDiagram
    participant M as Huisgenoot
    participant A as Adapter
    participant E as Eva-agentloop
    participant T as Gereedschap
    participant X as Externe bron

    M->>A: bericht / opdracht
    A->>A: archiveer exact bericht en reply
    A->>E: een InboundEnvelope
    E->>E: gesprek, geheugen en HA-context
    loop Tot eindantwoord of echte blokkade
        E->>T: toolcall
        T->>X: lees of verander
        X-->>T: resultaat
        T-->>E: feitelijke uitkomst
    end
    E-->>A: één eindantwoord
    A-->>M: één publicatie
```

## Actieve bronkoppeling

| Stap | Bestand | Verantwoordelijkheid |
| --- | --- | --- |
| Processtart | `src/index.js` | Start uitsluitend adapters en de ene kern |
| Gespreksbeurt | `src/mind/agent.js` | Rij, context, model/tool-lus en één antwoord |
| Persoonlijkheid | `src/mind/persona.js` | Eén stabiele Eva-stem |
| Gereedschap | `src/mind/tools.js` | HA, onderzoek, bestanden, geheugen en verzending |
| Telegram | `src/integrations/telegram.js` | Archief, reply/fotobewijs en transport |
| Huiscontext | `src/integrations/homeassistant.js` | Toestand, acties en zintuig-events |
| Camera | `src/mind/perception.js` | Event, lokale herkenning en gekoppelde foto |
| Autonomie | `src/mind/autonomy.js` | Timing en begrensde HA-signaal-inbox; betekenis blijft in `agent.js` |
| Duurzaam werk | `src/mind/work.js` | Alleen doel, volgende stap, bewijs, status en eigenaar |
| Dashboard | `src/web/server.js` | Projectie van agentstatus, geen eigen werkmodel |

De runtime bestaat nu uit 31 JavaScriptmodules en 11.431 regels, inclusief de
statische dashboardclient en de lokale inference-worker. De 78 niet-bereikbare
oude regisseurs zijn fysiek verwijderd. De afzonderlijke Python-breinservice
voor ArcFace en de eigen encoder blijft bestaan. De toestand van voor deze
opschoning staat in
`data/backups/eva-code-before-minimal-core-20260731.tar.gz`.

## Eigenaarschap

| Vraag | Enige eigenaar |
| --- | --- |
| Wat bedoelt dit bericht? | De ene agentloop |
| Welke gespreksdraad hoort erbij? | Exacte reply-context, anders recente sessie |
| Welke actie wordt uitgevoerd? | Het gekozen gereedschap |
| Is de actie gelukt? | De teruggegeven gereedschapsuitkomst |
| Hoe klinkt Eva? | De ene Eva-persona in de agentprompt |
| Is iets naar het gezin gestuurd? | Het Telegram/web-transport |
| Mag autonomie iets in huis veranderen? | In `full` wel voor normale omkeerbare apparaten, met actuele controle en readback; nooit voor sloten, alarmen of geheimen |

## Harde invarianten

1. Een inkomend bericht levert maximaal een zichtbaar antwoord op.
2. Een actieclaim mag alleen steunen op een tooluitkomst uit dezelfde beurt.
3. Een reply-id gaat altijd voor tekstuele of fuzzy draadherkenning.
4. Home Assistant bepaalt de thuislocatie. Eva kiest geen plaats uit modelkennis.
5. Tussenstanden worden niet spontaan verzonden.
6. In `full` kiest Eva zelfstandig doelen en mag ze onderzoeken, bouwen, programma's
   in haar werkruimte uitvoeren, communiceren en normale omkeerbare huisapparaten
   bedienen. Geheimen, sloten/alarmen en wijzigingen buiten haar werkruimte blijven
   technisch onbereikbaar.
7. Een voorstel is geen bijdrage totdat de bruikbaarheid is onderzocht.
8. Geen transportstatus mag een inhoudelijke werkstatus veranderen.
9. Een programma uit Eva's werkruimte krijgt geen procesgeheimen of tokens mee.
10. Een Telegram-reply wordt voor verzending duurzaam aan het ene bronbericht
    gekoppeld en na een herstart hervat in plaats van opnieuw bedacht.

## OpenClaw-vergelijking

Voor deze vergelijking is OpenClaw op 31 juli 2026 ondiep gekloond op commit
`d2b869eda722`. We nemen geen code over; we gebruiken alleen dezelfde nuttige
grenzen:

| OpenClaw-bron | Wat daar leidend is | Eva-equivalent |
| --- | --- | --- |
| `packages/agent-core/src/agent.ts` | Een stateful `Agent` bezit transcript, tools en wachtrijen | `src/mind/agent.js` |
| `packages/agent-core/src/agent-loop.ts` | Assistent -> toolcalls -> toolresultaten -> volgende beurt, in één loop | `runAgent` in `src/brain/llm.js` |
| `src/agents/embedded-agent-runner/lanes.ts` | Een seriële lane per sessie | `queueFor(conversationId)` |
| `src/agents/embedded-agent-runner/run-orchestrator.ts` | Eén session-lane rond een run | `handleMessageDetailed` |

OpenClaw is zelf een groot algemeen platform; minder regels kopiëren is dus
niet het leerpunt. Het leerpunt is eigenaarschap: adapters beslissen niet wat
een bericht betekent, tools schrijven niet het eindantwoord en een sessie heeft
maar één actieve agent-run.

Officiële referenties:

- <https://docs.openclaw.ai/agent-runtime-architecture>
- <https://docs.openclaw.ai/openclaw-agent-runtime>
