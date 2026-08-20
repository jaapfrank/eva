# Eva herstel- en herbouwplan

Status: actief plan, 20 augustus 2026

Dit document is het technische kompas voor het herstel en de herbouw van Eva. Het is geen bewijs dat een fase al voltooid is. Alleen de genoemde acceptatie-evidence mag een fase sluiten.

## Einddoel

Eva wordt een installeerbare, hernoembare en menselijke digitale persoon die:

- opdrachten over meerdere systemen begrijpt en afmaakt;
- e-mail, ERP, agenda, documenten, communicatie en andere connectors als gelijkwaardige vermogens gebruikt;
- haar identiteit, relaties, ervaringen en correcties duurzaam maar controleerbaar onthoudt;
- externe effecten alleen claimt met een echte receipt of readback;
- na fouten en herstarts exact verdergaat zonder dubbel werk;
- nieuwe connectors uit documentatie kan voorbereiden, testen en veilig aanbieden;
- zichzelf alleen via meetbare kandidaat, canary en rollback verbetert;
- eerlijk wordt vergeleken met OpenClaw, Hermes en Agent Zero.

Home Assistant is één connector. Het is niet de kern en niet het hoofdproduct.

## Huidige bewezen toestand

- De oude productieboom is als recovery-checkpoint bewaard.
- De productieservice blijft gestopt en disabled totdat de candidate-poort groen is.
- De recovery-runtime blokkeert autonome en externe achtergrondseffecten standaard.
- Telegram direct delivery is gerepareerd naar één duurzame fanout- en receipt-sleutel en heeft een gerichte 54-uit-54 testpoort.
- HTTP voice heeft een bewezen authority-gat en wordt afzonderlijk gerepareerd.
- De huidige codebasis is nog geen releasekandidaat en nog geen aanvaardbare eindarchitectuur.

## Databasefeiten uit de immutable herstelkopie

Bron: `Bottie-recovery/2026-08-20T1536CEST/db-logical/eva-logical-2026-08-20T1542CEST.db`.

- SQLite `quick_check` is groen en er zijn nul aangetroffen foreign-keyovertredingen.
- 72 applicatietabellen bevatten samen 140.099 getelde rijen.
- 6.555 daarvan zijn afgeleide FTS- en schaduwindexrijen.
- `timeline` bevat 95.398 rijen.
- `cognitive_events` bevat 22.099 rijen.
- Deze twee tabellen vormen samen 83,87 procent van alle getelde rijen.
- Slechts 4,35 procent van `timeline` bestaat uit chat.
- Herhaalde machinery-, house- en visionobservaties domineren de eventstroom.
- Het bestand is ongeveer 139,8 MiB. `timeline`, `nameable_photos` en `cognitive_events` gebruiken samen 88,63 procent.
- `nameable_photos` bewaart ongeveer 49 MiB ruwe fotoblobs in de hot database.
- 61,97 procent van de memories is archived maar blijft in dezelfde actieve tabel en FTS-projectie.
- 77,66 procent van Telegram-content komt ook in de generieke messages-tabel voor.
- `user_version` is 0 en er is geen centrale SQLite-migratieversie.
- DDL is verspreid over ongeveer 25 runtimebestanden. Dat is een groter onderhoudsrisico dan het aantal rijen.

Conclusie: 140.099 rijen is voor SQLite klein. Het probleem is onbeperkte telemetry, brede payloads, dubbele projecties, verspreid schema-eigenaarschap en ontbrekende retentie.

## Doelopslag

De nieuwe opslag heeft vier expliciete klassen.

### 1. Canonieke core state

Kleine, getypeerde tabellen voor:

- installations en logical root bindings;
- identities, principals en relaties;
- conversations, messages en turns;
- work items, episodes en commitments;
- connector accounts en bindings;
- operations, effects en deliveries;
- memories met provenance;
- schema migrations.

Hot state gebruikt geen absolute hostpaden en geen JSON als primaire state machine.

### 2. Immutable evidence

Een gedeelde evidence-envelop bewaart namespace, type, aggregate, sequence, idempotency key, schema version, timestamps, payloadhash en predecessorhash. Protocolspecifieke tabellen blijven alleen bestaan wanneer hun invarianten echt verschillen.

Bewijs van een effect komt nooit uit semantisch geheugen. E-mail verzonden, afspraak gemaakt, document geschreven of betaling uitgevoerd vereist een connectorreceipt en waar mogelijk readback.

### 3. Telemetry en projections

Ruwe telemetry krijgt:

- change-only ingest;
- coalescing van identieke observaties;
- hourly en daily rollups;
- expliciete retentie;
- rebuildable projections;
- gecontroleerde `ANALYZE`, queryplanmeting en compactie.

### 4. Content store

Foto's, documenten en andere grote bytes gaan naar versleutelde content-addressed objectopslag. De database bewaart hash, type, eigenaar, provenance, retentie en toegangsbeleid. FTS en vectorindexen zijn rebuildable en tellen niet als primaire kennis.

## Memory-frameworkbesluit

Er wordt geen eigen geheugenframework gebouwd voordat bestaande oplossingen eerlijk zijn getest.

Kandidaten:

- Hindsight: gestructureerde feiten, ervaringen, samenvattingen en veranderende overtuigingen; lokaal/self-hosted; taalneutrale API en meerdere SDK's.
- Mem0: brede self-hosted memorylaag, Node-SDK en reproduceerbaar evaluatieframework.
- Zep/Graphiti: temporele knowledge graph met provenance en veranderende relaties.
- Letta: stateful agentruntime met memory blocks en archival memory.
- LangGraph: checkpoints, fault-tolerant graphstate en long-term stores.
- Baseline: eenvoudige canonieke SQL plus full-text en vector retrieval zonder autonome memoryextractie.

Geen kandidaat krijgt vertrouwen op basis van vendorcijfers. Iedere kandidaat draait dezelfde Nederlandse Eva-cases:

- gewijzigde voorkeur en latere correctie;
- relatiefeit met bron en geldigheidsperiode;
- onderscheid tussen gehoord, afgeleid, geloofd en bewezen;
- cross-session en cross-domain recall;
- conflict tussen oud en nieuw bewijs;
- vergeten, exporteren en verwijderen;
- crash en restart;
- injectie en poisoned memory;
- latency, kosten, opslaggroei en herbouwbaarheid.

Het memory-framework is een afgeleide recall- en leerlaag. De canonieke core blijft eigenaar van identiteit, opdrachten, effects en receipts.

Primaire bronnen voor de bakeoff:

- Hindsight: <https://github.com/vectorize-io/hindsight>
- Mem0: <https://github.com/mem0ai/mem0>
- Zep/Graphiti: <https://github.com/getzep/graphiti>
- Letta: <https://docs.letta.com/>
- LangGraph JS memory: <https://docs.langchain.com/oss/javascript/langgraph/add-memory>

## Taalbesluit

De bestaande recovery-runtime blijft tijdelijk Node omdat een taalconversie niet met incidentherstel wordt gecombineerd. De nieuwe Living Core heeft nog geen gekozen taal.

Dezelfde verticale flow wordt gebouwd in:

- Rust;
- strikt TypeScript;
- Python met strikte schemas.

De flow bevat minimaal: inbound turn, identity lookup, memory recall, episode, connector-read, connector-effect, receipt, natuurlijke reply en restart.

Meetpunten:

- installatie vanaf nul;
- single-binary of runtimecomplexiteit;
- type- en contractsafety;
- crashherstel en deterministic replay;
- p95 latency en idle memory;
- connector- en memoryintegratie;
- packaging voor Linux, macOS en Windows;
- testbaarheid en hoeveelheid eigen gluecode;
- upgrade, rollback en datamigratie.

Rust is een serieuze kandidaat voor de kleine control plane. Python en TypeScript blijven sterke connector- en memoryworkers. C++ wordt alleen voor gemeten native hotspots getest, zoals audio, vision of lokale inference.

## Agent-frameworkbesluit

De huidige runtime gebruikt geen extern agent framework. Zij is zelf een groot, custom agent framework geworden. Dat wordt niet opnieuw gebouwd.

De nieuwe architectuur beoordeelt frameworklagen afzonderlijk:

- durable execution: Restate, LangGraph en waar passend Pydantic AI met Temporal of DBOS;
- model- en toolloop: een lichte SDK met typed tools, guardrails, interrupts en tracing;
- memory: de afzonderlijke memory-bakeoff hierboven;
- Eva-domain core: eigen kleine contracts voor identity, episodes, effects en receipts;
- connectors: taalneutrale connectorcontracten en geïsoleerde workers.

Voorlopige sterke kandidaat is Restate als durable substrate, omdat de officiële runtime TypeScript, Python en Rust ondersteunt en durable state, timers, workflows en exactly-once communicatie aanbiedt. Dit moet nog worden bewezen op Eva's crashvensters en installer.

LangGraph blijft kandidaat voor graph-orchestration en checkpointing. De officiële documentatie vermeldt dat nodes na een checkpoint opnieuw uitvoeren, inclusief mogelijke LLM- en API-calls. Een connectorcall blijft daarom altijd achter Eva's eigen idempotency- en receiptgrens.

OpenAI Agents SDK en Pydantic AI zijn kandidaten voor de lichte model- en toolloop, niet automatisch voor database-, memory- of effectauthority.

Primaire bronnen:

- Restate: <https://docs.restate.dev/>
- LangGraph persistence: <https://docs.langchain.com/oss/javascript/langgraph/persistence>
- Pydantic AI durable execution: <https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/>
- OpenAI Agents SDK TypeScript: <https://openai.github.io/openai-agents-js/>

De framework-bakeoff bevat minimaal:

- crash voor en na iedere externe call;
- deterministic replay zonder tweede model- of connectorcall;
- durable timer en menselijke interrupt;
- exact-once signal en delivery;
- state-export en frameworkvervanging;
- lokale installatie en resourcegebruik;
- frameworkuitval zonder verlies van canonieke Eva-data.

## Connectorneutrale kern

Iedere connector implementeert dezelfde begrippen:

- account en authentication status;
- capability catalog;
- resource identity en schema;
- read operation;
- planned effect;
- effect authorization;
- idempotency key;
- execution receipt;
- readback of reconciliation;
- retry classification;
- rollback of compensating action;
- documentation and version provenance.

Een ERP-klant, e-mailbericht, agenda-afspraak en Home Assistant-entity zijn daardoor resources van verschillende connectors, niet speciale gevallen in de kern.

## Veilige Connector Forge

Eva mag connectoren voorbereiden vanuit documentatie, maar nooit stil productiecredentials of brede netwerktoegang verwerven.

Volgorde:

1. Officiële documentatie en OpenAPI, GraphQL, MCP of SDK-metadata verzamelen.
2. Versie, licentie, scopes, rate limits en authenticatiemethode vastleggen.
3. Een connectorcandidate en contracttests genereren in quarantaine.
4. Alleen mock- en testaccountverkeer toestaan.
5. Read-only canary uitvoeren.
6. Effectscopes afzonderlijk laten autoriseren.
7. Receipts, retry, rollback en credentialrotatie bewijzen.
8. Candidate ondertekend promoveren of volledig verwijderen.

Accountaanmaak zonder gebruiker kan alleen wanneer de aanbieder dit rechtmatig en technisch toestaat en geen menselijke identiteit, betaling, captcha, overeenkomst of MFA vereist. Eva mag nooit een mens simuleren of voorwaarden stil accepteren.

## Uitvoeringsfasen

### Fase 0. Recovery

- Service uit en data bevroren.
- Recovery-mode fail-closed.
- Web, Telegram en voice delivery exact herstellen.
- Volledige regressies classificeren.

Exit: schone recoverycandidate, gerichte deliverypoorten groen, geen externe achtergrondseffecten.

### Fase 1. Data en schema

- Tabel- en querycatalogus met één eigenaar per writer.
- Centrale versioned migrator.
- Geen runtime-DDL tijdens normale startup.
- Mediaextractie, telemetryrollups en retentie op een kopie.

Exit: create-from-zero, upgrade, rollback en restore zijn herhaalbaar en verliesvrij.

### Fase 2. Technologie-bakeoff

- Memory-frameworkmatrix uitvoeren.
- Rust, TypeScript en Python verticale flow uitvoeren.
- Winnaar kiezen op vooraf bevroren meetcriteria.

Exit: reproduceerbare resultaten en expliciet architectuurbesluit.

### Fase 3. Living Core

- Identity, attention, episode, capability, effect, receipt, memory en experience contracts.
- Menselijke replyprojectie zonder technische systeemtaal.
- Exact restart en idempotency.

Exit: dezelfde core bestuurt minimaal drie verschillende connectors zonder connectorlogica in de core.

### Fase 4. Connector Forge en cross-domain werk

- E-mail, ERP, agenda en documentconnector.
- Documentatiegestuurde candidatebouw.
- End-to-end opdracht over alle vier domeinen.

Exit: e-mail lezen, klant vinden, afspraak plannen, document maken en natuurlijk terugkoppelen met exacte receipts.

### Fase 5. Identiteit en installatie

- Naam, stem, waarden, relatie en presence uit een versioned identity pack.
- Geen hardcoded Eva of gebruiker in productcode.
- Installer, backup, restore, rebrand, update en uninstall.

Exit: schoon tweede systeem installeert en herstelt dezelfde identiteit en data, of start aantoonbaar als een andere identiteit.

### Fase 6. Vergelijking en live acceptatie

- Dezelfde sealed semantische cases voor Eva, OpenClaw, Hermes en Agent Zero.
- Gelijke modellen, budgets, fixtures, effects en judges.
- A/B candidate en rollback.
- Echte interacties en connectorreadbacks.

Exit: geen marketinggetal, maar per case bewijs van completion, herstel, menselijke communicatie, kosten en fouten.

## Harde SLO's voor de nieuwe opslag

Op afgesproken referentiehardware en een dataset van minimaal één miljoen evidence-records:

- gewone startup voert geen DDL en geen volledige scan uit;
- schema-check p95 onder 250 ms;
- hot reads p95 onder 20 ms;
- lokale writes p95 onder 50 ms en p99 onder 150 ms;
- projection lag p95 onder 1 seconde;
- dubbele externe effecten en deliveries exact nul;
- orphan references exact nul;
- absolute hostpaden in portable coretabellen exact nul;
- restore van 2 GiB binnen 5 minuten;
- migratiereconciliatie 100 procent op IDs, aantallen en hashes;
- DDL buiten de centrale migratiemap exact nul.

## Verboden snelkoppelingen

- Geen live schemawijziging zonder kopie, rehearsal en rollback.
- Geen veiligheidsvalidator versoepelen om een test groen te maken.
- Geen modeltekst als effectreceipt behandelen.
- Geen memory-recall als actuele waarheid behandelen.
- Geen frameworkscore als bewijs voor Eva gebruiken.
- Geen taalkeuze op voorkeur of trend.
- Geen connector per ongeluk tot productkern maken.
- Geen service-start vanuit een dirty worktree.
