# ADR-0001: Eva 2 Living Core

Status: `selected_for_validation`, gekozen doelarchitectuur voor Eva 2, niet productiegeaccepteerd

Datum: 20 augustus 2026

Vervangt: impliciete voortzetting van de Eva 1-runtime als doelarchitectuur

## Besluit in één zin

Eva 2 wordt een nieuwe, connectorneutrale Python 3.12-applicatie met Pydantic-contracten,
Pydantic AI als dunne model- en planningslaag, DBOS als duurzame workflowlaag, een door Eva zelf
beheerd PostgreSQL-datamodel als enige authority en een herbouwbare SQL/full-text-geheugenprojectie.

Dit is een architectuurkeuze. Het is geen claim dat de huidige bakeoffcode productierijp is en
geen toestemming om Eva 1-data te migreren of een service te starten.

## Waarom we nu wel kiezen

De vorige werkwijze liet de bestaande runtime en losse testproblemen de architectuur bepalen. Dat
is gestopt. De keuze hieronder is gebaseerd op één vooraf vastgelegde cross-domain episode,
werkelijke crash- en concurrentieproeven, de feitelijke Eva 1-incidenten en expliciete
exitcriteria.

De eerste DBOS-prototype-implementatie is volledig afgekeurd. Zij faalde niet alleen op
disconnect-na-commit, maar ook op inputconflict, supersession, circuit-breaker, canonieke export,
capability-isolatie en zichtbare identiteit. DBOS als component is niet afgekeurd. Voor het
ambigue commitvenster kwamen twee aantoonbare adapterfouten naar voren: de broker had geen
getypeerde ambigue transportfout en de DBOS-step stond op geen retry. Een geïsoleerde proef met
precies één selectieve herstelpoging deed eerst readback en leverde één extern effect en een
gereconcilieerde receipt. Dat bewijst uitsluitend dat DBOS dit ene venster kan dragen. Het maakt
een schone tweede implementatie technisch verdedigbaar en maakt reactief repareren van het eerste
prototype niet verdedigbaar.

De doelstack staat vanaf dit ADR vast voor één schone verticale implementatie. Alleen een
engine-intrinsieke mislukking van een hieronder genoemde architectuurinvariant heropent de
componentkeuze. Een gewone implementatie- of fixturefout doet dat niet. `accepted` en
`production_ready` blijven uitdrukkelijk onwaar totdat de volledige poort groen is.

## Gekozen stack

| Laag | Keuze | Verantwoordelijkheid | Is uitdrukkelijk geen eigenaar van |
| --- | --- | --- | --- |
| Primaire runtime | Python 3.12 | Living Core, API, workflows, policies en connector-SDK | Een tweede vertrouwde Node-, Rust- of C++-kern |
| Domeincontracten | Pydantic 2.12.5 en versioned JSON Schema | Strikte input, output, events, evidence en portable export | Durable state of connectorauthority |
| Model- en planningslaag | `pydantic-ai-slim` 2.32.1 | Getypeerde planvoorstellen, dialoogbesluiten en natuurlijke replykandidaten | Tooluitvoering, effects, identiteit, geheugen of retries |
| Duurzame uitvoering | DBOS Python 2.30.0, self-hosted zonder Conductor | Doel: hervatten, timers, workflowstappen en één atomair commitpunt voor applicatietransactie plus transactieresultaat; dit moet de vertical nog bewijzen | Canonieke Eva-data of de waarheid over externe effecten |
| Canonieke opslag | PostgreSQL 16.13 | Identiteit, gesprekken, episodes, commitments, effects, receipts, readbacks, geheugen en migraties | Ruwe media of uitsluitend frameworkinterne state |
| Database-interface | SQLAlchemy 2.x Core via een DBOS datasource; Alembic 1.x | Getypeerde SQL, centrale migraties en exact één schema-eigenaar | Een actieve-record-ORM die domeinstate verstopt |
| Eerste geheugenprojectie | PostgreSQL full-text en gewone SQL-projecties | Recall, temporaliteit, provenance en fallback | Canonieke memoryauthority |
| Ingress API | ASGI/FastAPI, achter één lokale Eva API | Web, kanaaladapters, health en beheer | Connectorlogica of workflowauthority |
| Connectorgrens | Versioned JSON-RPC over stdio met geïsoleerde workers | Cross-language reads, effectvoorstellen, receipts en readbacks | Directe DB-, model-, identity- of memorytoegang |
| Evidence en bestanden | PostgreSQL-metadata plus versleutelde content-addressed objectopslag | Documenten, media, payloadhashes en bewijsbundels | Onbegrensde BLOB-opslag in de hot database |
| Observability | Lokale OpenTelemetry en content-addressed evidence, standaard geen externe export | Correlatie van request tot externe readback | Een tweede business-eventstore |
| Distributie | Gesigneerde OCI-images plus een kleine `eva`-beheer-CLI | Install, init, update, A/B, export, restore en uninstall zonder Git-worktree | Een ontwikkelomgeving op het productiesysteem |

Exacte patchversies worden in één lockfile van de eerste verticale build vastgezet. Een upgrade
wijzigt het ADR niet, maar vereist dezelfde upgrade-, wachtende-episode- en rollbackproef.

## Gebruiken we een agentframework?

Ja, maar niet als monolithische kern. Pydantic AI is uitsluitend de dunne modeladapter en
typed planner. DBOS is uitsluitend de durable executor. Eva's identiteit, geheugen, aandacht,
commitments, connectorauthority en effectbewijs blijven in kleine Eva-domeinmodules en in het
canonieke PostgreSQL-schema.

De modelrun krijgt geen databasehandle, connectorcredentials of uitvoerbare connectortools. Zij
krijgt een begrensde context en retourneert één getypeerd `ModelDecision`, bijvoorbeeld:

- vraag een noodzakelijke verduidelijking;
- vraag een toegestane read aan;
- stel een effect voor;
- sluit met een natuurlijke reply;
- wacht op een duurzaam moment of extern event.

De Living Core valideert dat voorstel tegen capability, beleid, actuele generatie en bewijs. Pas
daarna kan de effectgateway een connector aanroepen. Hierdoor is Pydantic AI vervangbaar zonder
identiteit, memory, episodes of receipts te migreren.

Function tools, toolsets, MCP, hosted tools, Pydantic AI-sessions, memory en Pydantic AI durable
execution staan in deze adapter uit. Providercredentials blijven in de aparte modeladapter en
komen nooit in modelcontext of connectorworkers. Alleen eigen Pydantic-domeinmodellen mogen de
adaptergrens passeren; geen `pydantic_ai`-type wordt duurzaam opgeslagen of geëxporteerd.

Pydantic AI is gekozen boven de OpenAI Agents SDK omdat Eva providerneutraal moet blijven, de
geteste Pydantic-contracten al exact dezelfde modelgrens gebruiken en we geen tweede ingebouwde
tool- of handoffauthority nodig hebben. Een volledig zelfgeschreven loop is afgewezen omdat dat
opnieuw eigen provideradapters, structured-outputreparatie en testglue zou bouwen. De echte
modelkwaliteit is nog niet bewezen; die wordt een acceptatiepoort van de gekozen adapter, niet
een reden om de rest van de architectuur open te laten.

Dit ADR vervangt daarmee bewust het deel van het eerste bakeoffcontract dat nog een gewogen
loopvergelijking vóór de architectuurkeuze eiste. Er wordt geen kwaliteitsvoorsprong voor
Pydantic AI geclaimd. Vóór `accepted` draait dezelfde live modelproef via Pydantic AI en via de
minimale exitadapter; de OpenAI Agents SDK hoeft niet alsnog authority te worden om
vervangbaarheid te bewijzen.

Telemetry en tracing zijn opt-in en lokaal. De slim-installatie krijgt alleen de werkelijk
gebruikte providerextra. Provider-SDK-retries staan uit; timeout, tokenbudget, 429 en circuitstate
worden door Eva begrensd en zijn zichtbaar in de canonieke evidence. Ook Pydantic AI output- en
validation-retries staan op nul. Een nieuwe modelpoging is altijd een afzonderlijke, duurzame
Living Core-plannerstap met eigen budget en evidence.

## De Living Core

De Living Core is geen verzameling van honderden scripts. Hij heeft zes samenwerkende
verantwoordelijkheden met één durable attention loop:

1. **Presence** bouwt vanuit identity pack, relatie, gesprek en actuele situatie een compacte
   menselijke context. De bewijslaag levert feiten; Presence bepaalt hoe Eva daar normaal over
   praat.
2. **Attention** kiest uit nieuwe gebruikersinput, deadlines, wachtende commitments en externe
   events. Er is één duurzame scheduler met getypeerde wake-redenen, geen verzameling losse
   intervalscripts.
3. **Episode** bewaakt doel, generatie, correcties, toestemming, deelresultaat en closure. Een
   nieuwere geldige correctie kan oud werk duurzaam superseden vóór een effect.
4. **Capability** beschrijft wat beschikbaar, toegestaan en bewijsbaar is. Home Assistant,
   e-mail, ERP, agenda en documenten zijn gelijkwaardige connectors.
5. **Experience** bewaart herleidbare gebeurtenissen en facts, inclusief correctie, vertrouwen,
   geldigheid en verwijdering. Projecties mogen worden weggegooid en opnieuw opgebouwd.
6. **Learning** maakt alleen een kandidaatverbetering met benchmark, canary en rollback. Zij mag
   niet stil productiecode, policies of credentials veranderen.

Een gesprek wordt niet uit scripts samengesteld. Pydantic AI schrijft de natuurlijke reply op
basis van identity en bewezen resultaten. Een kleine stijlpoort controleert alleen harde
identiteitsregels, zoals normale Nederlandse taal, geen technisch jargon tenzij gevraagd en voor
Eva geen em- of en-dashes. De stijlpoort schrijft geen sjabloonantwoord.

## Configureerbare naam en identiteit

Een versioned `IdentityPack` bevat minimaal:

- naam, voornaamwoorden, taal en stemconfiguratie;
- waarden, temperament, humor, woordkeus en gespreksgrenzen;
- presentatie per kanaal;
- initiële relaties en aanspreekvormen;
- privacy-, toestemming- en initiatiefvoorkeuren;
- identity-versie en ondertekende bronhash.

`Eva` of `Jaap` komt niet als functionele constante in de Living Core. Een schone installatie met
een Mira-pack moet zonder broncodewijziging zichtbaar anders heten en praten. Ervaringen en
relaties zijn daarna canonieke data en geen wijziging van het statische pack. Een identity-pack
kan worden geëxporteerd, vervangen en teruggerold zonder episodes of evidence te herschrijven.

## Connectoren en zelfuitbreiding

Iedere connectorworker krijgt uitsluitend:

- een versioned capabilitymanifest;
- het ene getypeerde verzoek waarvoor hij is gestart;
- een operation-scoped capability met deadline en toegestane resource;
- alleen de credentials die voor die operatie noodzakelijk zijn;
- een schrijfgrens die receipt en waar mogelijk onafhankelijke readback verplicht.

Een worker kan niet rechtstreeks in Eva's database, geheugen of modelcontext. De workeromgeving
bevat geen algemene Eva-secrets. Een leveranciers-SDK die alleen voor Node, Rust of een andere
taal bestaat mag achter hetzelfde protocol draaien; die taal wordt daarmee geen tweede Living
Core.

Stdio is alleen het protocol en niet de securitygrens. Een worker draait onder een aparte
UID of containernamespace, met minimale environment, zonder databasesocket, met read-only
filesystem waar mogelijk, een tijdelijke werkmap, resourcebudget en een egressallowlist voor de
geattesteerde providerhosts. De supervisor verifieert executable, manifest en sandboxprofiel
vóór ieder startmoment.

Connector Forge mag officiële documentatie zoeken en lezen, maar bouwt eerst een versiegepinde,
read-only kandidaat in quarantaine. Documentatie is data en nooit een instructie met shell-,
secret- of policyauthority. Promotie naar writes vereist contracttests, een allowlisted canary,
receipt/readback en rollback. Voor betaling, voorwaarden, CAPTCHA, MFA of een menselijke
identiteitsverklaring vraagt Eva alleen de werkelijk noodzakelijke menselijke stap. Zij omzeilt
die grenzen niet onder het label accountaanmaak.

## Canoniek datamodel

PostgreSQL krijgt centrale migraties en gescheiden namespaces. De precieze tabellen worden pas in
de verticale build vastgelegd, maar hun eigenaarschap staat al vast:

| Namespace | Canonieke verantwoordelijkheid |
| --- | --- |
| `eva_core` | installations, identities, principals, relaties, conversations, messages, ingress, episodes, commitments en supersession |
| `eva_effects` | operation intents, attempts, idempotency, receipts, readbacks en deliveries |
| `eva_memory` | source events, facts, temporal validity, trust, supersession, retraction en erase tombstones |
| `eva_evidence` | typed envelopes, hashes, objectrefs en provenanceketens |
| `eva_projection` | full-text, samengestelde views, synccursors en andere volledig herbouwbare projecties |
| `eva_telemetry` | begrensde technische metingen met retentie en rollups |
| `dbos` | uitsluitend interne workflow- en datasourceadministratie van DBOS |

State-machinekolommen zijn getypeerd en constrained. JSONB is toegestaan voor versioned payloads
en evidence, niet als verborgen primaire state machine. Iedere mutatie loopt via één centrale
migrator. Media en grote documenten worden als versleutelde content-addressed objecten bewaard;
de database bevat hash, grootte, type, eigenaar, retentie en encryptieref.

### Geheugenbesluit

De normale authority is een append-only bron-event plus een expliciet current fact. Ieder fact
bewaart tenant, subject, predicate, value, bron, `observed_at`, valid-time, trust, supersession of
retraction en bronhash. Geautoriseerde privacyverwijdering is de bewuste uitzondering op
append-only: zij verwijdert of crypto-shredt het bronmateriaal, facts, projecties en objecten en
legt alleen een niet-herleidbare erasuretombstone vast. Het export- en back-upretentiebeleid moet
dezelfde verwijderdeadline afdwingen. Onbetrouwbare mail- of webinhoud kan nooit automatisch een
directe gebruikerscorrectie overstemmen.

De sealed SQLite-controleproef bleef op 10.000 en 100.000 events correct voor temporal now/then,
tenantisolatie, provenance, onbetrouwbare input, crashreadback, erase en rebuild. De gemeten p95
was 0,125 ms op beide schalen. Dit kiest het memorymodel, niet de nog onbewezen
PostgreSQL-performance. Daarom begint Eva 2 met SQL/full-text en moet dezelfde proef op het
uiteindelijke PostgreSQL-schema worden herhaald. Embeddings, pgvector, Hindsight of Graphiti mogen
later alleen als herbouwbare recallprojectie worden toegevoegd. Geen van deze systemen wordt
identity-, intent-, effect- of memoryauthority.

## Duurzaamheid en externe effecten

Een DBOS-workflow-ID alleen bewijst geen identieke gebruikersopdracht. Vóór workflowstart bewaart
`eva_core` atomair request-ID, canonieke inputhash, principal, conversation en generation.
Dezelfde request-ID met andere bytes is een getypeerd conflict en mag nooit een oude uitkomst
teruggeven.

Voor ieder effect gelden achtereenvolgens:

1. een duurzaam `EffectIntent` met episode, generation, capability, resource, payloadhash en
   stabiele idempotency key;
2. een laatste generation- en supersessioncheck vóór nieuwe externe I/O;
3. de connectorcall;
4. een connectorreceipt en onafhankelijke readback wanneer de provider die biedt;
5. een atomair lokaal resultaat dat alleen bewezen toestand als accepted projecteert;
6. een natuurlijke reply en deliveryreceipt voordat de episode sluit.

Een disconnect na remote commit is `ambiguous`, niet failed en niet accepted. De enige
automatische herstelpoging doet eerst readback met dezelfde idempotency key. DBOS mag voor deze
exacte foutklasse maximaal één nieuwe stepattempt doen. 429, 422, hashconflict,
programmeurfouten en policyweigering krijgen geen generieke frameworkretry.

Provider- en connector-429 openen een canonieke circuit-breaker met `Retry-After` of begrensde
backoff. Eén durable probe mag de breaker half-open zetten. Er ontstaat geen minuutlus en geen
nieuwe work-episode per mislukte probe.

Een modelprovidercall is eveneens een kostbare externe attempt. Voor de call worden call-ID,
requesthash, model, budget en episodegeneration duurzaam vastgelegd. Na respons worden
responsehash, usage en providerreferentie als evidence gecommit. Een crash na providerrespons en
vóór lokale commit mag nooit tot een verborgen frameworkretry leiden. Eva hervat alleen vanuit
een provider-idempotency/cachebewijs of maakt een nieuwe, zichtbare plannerattempt onder hetzelfde
budget. De procesproef telt de echte HTTP-attempts.

## Export, installatie en beheer

De eerste distributie bestaat niet uit een Git-repository. Een gesigneerde release bevat:

- gepinde Linux amd64- en arm64-images;
- een `eva`-CLI voor init, status, diagnose, backup, export, restore, update, rollback en uninstall;
- een gepinde PostgreSQL-image en migratiebundle;
- een identity-packtemplate en connectorcatalogus;
- een lokale, privacyvriendelijke standaardconfiguratie.

Tier 1 is Linux. macOS en Windows krijgen in de eerste release een expliciet containerized
supportniveau via Docker of Podman. Dat wordt niet als native installatie verkocht. Native
packaging kan later worden toegevoegd wanneer dezelfde clean-install- en restoreproef daar groen
is.

`eva export` maakt een frameworkonafhankelijk `EvaPortableArchive/v1`: canonieke JSONL per
contracttype, schema's, checksums, identity packs en versleutelde objecten. Een eenvoudige reader
moet identiteit, gesprekken, episodes, intents, receipts, pending work en memories kunnen
reconstrueren terwijl DBOS uitstaat of verwijderd is. Een dump van alleen DBOS-tabellen telt
niet.

Updates zijn immutable A/B-releases. Vóór omschakeling maakt Eva export plus databasebackup,
draait migratie op een kopie en voert de sealed kandidaatpoort uit. De oude image en databasekopie
blijven terugschakelbaar totdat de live canary is geaccepteerd.

## Gemeten alternatieven en afwijzing

| Alternatief | Besluit | Feitelijke reden |
| --- | --- | --- |
| Bestaande Eva 1 Node-runtime | Niet voortbouwen als kern | 76 runtimebestanden, één cluster van 37 modules, verspreide DDL en bewezen actie/reply-, retry- en deploymentincidenten |
| Node/TypeScript als nieuwe kern | Niet gekozen | Geen gemeten runtimevoordeel; zou naast de gekozen Python-workflow- en modelstack een tweede trusted runtime en contractgrens maken |
| Rust of C++ als kern | Niet gekozen | Geen gemeten CPU- of latencybottleneck; hogere connector-, SDK-, packaging- en ontwikkelkosten. Rust blijft toegestaan voor een later geprofiled workerprobleem |
| Restate | Afgevallen | Supersession faalde echt; providerretry, secretisolatie en frameworkonafhankelijke export bleven onbewezen; extra stateful server, BSL- en Windows-exitrisico |
| Custom SQL-workflow | Afgevallen als executor | Concurrent duplicate ingress deed twee modelcalls en 21 connectorcalls; zelf repareren herbouwt locking, timers, replay en versioning. SQL blijft de datalaag |
| Temporal | Niet gekozen, alleen noodfallback | Sterk maar operationeel zwaarder; pas een nieuwe bakeoff wanneer DBOS-core op een architectuurinvariant faalt |
| LangGraph | Geen effectauthority | Nodes kunnen rond checkpoints opnieuw uitvoeren; bruikbaar hoogstens als vervangbare planningsimplementatie |
| OpenAI Agents SDK | Niet gekozen voor de kernloop | OpenAI-first en extra tool/handoff/tracinggedrag zonder voordeel aan Eva's afgescheiden effectgrens |
| Volledig eigen model-loop | Niet gekozen | Onnodige provider-, structured-output- en retryglue terwijl de planner juist dun en vervangbaar moet zijn |
| Hindsight | Niet gekozen als eerste projectie | Echte 10k-infrastructuurproef was circa 6,17 s p95 en 1,1 GiB RAM; semantische kwaliteit zonder extractiemodel en 100k bleven onbewezen |
| Graphiti/Mem0/Letta | Niet gekozen | Geen sealed Eva-bewijs; Graphiti's lokale Kuzu-pad is deprecated, Letta is een concurrerende runtime, memory mag geen authority worden |

De eerste DBOS-kandidaat zelf is eveneens afgewezen. Alleen de DBOS-component en de aantoonbaar
veilige subset van haar semantics worden in een nieuwe verticale implementatie gebruikt.

## Niet-onderhandelbare poort vóór enige Eva 1-migratie

De eerste verticale build wordt vooraf in één contract bevroren en daarna niet tijdens de run
gerepareerd. Zij moet op een schone installatie aantonen:

1. **Canonieke authority:** alle Eva-data staat in `eva_*`; DBOS-internals kunnen worden
   verwijderd zonder verlies van de portable data.
2. **Atomisch lokaal commitpunt:** een `eva_*`-mutatie en het bijbehorende DBOS
   datasource-transactieresultaat vormen aantoonbaar één PostgreSQL-commit. Crashes vlak vóór en
   vlak na commit laten nooit een halve dual write achter. Als DBOS dit niet kan dragen, is dat
   een engine-intrinsieke NO-GO.
3. **Duplicate en conflict:** twee gelijktijdige gelijke requests geven één modelrun en één
   uitkomst; dezelfde request-ID met andere inhoud geeft conflict vóór model of connector.
4. **Supersession:** een nieuwere correctie die vóór het eerste effect arriveert maakt het oude
   plan duurzaam niet-current; oud werk doet nul effects.
5. **Crashmatrix:** ieder vastgelegd crashpunt, inclusief disconnect na remote commit en crash na
   deliveryacceptatie, geeft nul dubbele effecten en nul verloren commitments.
6. **Circuit-breaker:** provider- en connector-429 geven één duurzame breaker en geen retrylus.
7. **Effectbewijs:** mail-, ERP-, agenda-, document- en deliveryclaims hebben externe IDs,
   payloadhashes, receipts en waar mogelijk onafhankelijke readbacks.
8. **Connectorisolatie:** worker heeft geen database, model, identitygeheugen of niet-toegestane
   secrets; een ongeautoriseerde operation wordt vóór extern verkeer geweigerd.
9. **Identity:** dezelfde build met Eva- en Mira-pack verandert naam en herkenbare stem zonder
   broncodewijziging.
10. **Memory:** correctie, historisch antwoord, tenantisolatie, trust, bronverwijdering en volledige
   projectierebuild blijven groen in het gekozen PostgreSQL-schema.
11. **Exit:** export met DBOS uit, restore op een tweede schone installatie en readback met een
    generieke reader reconstrueren identity, episodes, intents, receipts en pending work.
12. **Installatie en beheer:** create-from-zero, update met wachtende episode, rollback en
    uninstall werken zonder Git-worktree of voorgevulde database op Linux amd64/arm64 en de
    gedeclareerde containerized macOS- en Windows-paden.
13. **Menselijk resultaat:** de Noor cross-domainopdracht eindigt met één normale Nederlandse
    reply die exact succes, deelresultaat en onzekerheid onderscheidt. Geen intern jargon en geen
    em- of en-dashes.
14. **Echte canary:** pas na de deterministische en procespoorten draait één disposable live
    opdracht met echte allowlisted connectors, connector-eigen before/after en onafhankelijke
    readbacks.

Een failure wordt eerst geclassificeerd als kandidaatbeperking, implementatiefout, fixturefout of
contractfout. Alleen een kandidaatbeperking op een architectuurinvariant heropent dit ADR. Er komt
geen derde ronde waarin een mislukte proef gaandeweg tot groen wordt gepatcht.

## Gevolg voor Eva 1

Vanaf dit ADR is Eva 1 alleen:

- een read-only bron voor gebruikersdata die later expliciet wordt geselecteerd;
- incident- en gedragsbewijs;
- rollbackcheckpoint zolang Eva 2 niet live is.

De 37 oude HTTP-records, 63 authorityrecords, 72 tabellen en bestaande modules bepalen geen Eva 2
schema. De volgende fase classificeert gegevens en capabilities als behouden, converteren,
archiveren, herbouwen of verwijderen. Er wordt nog niets geïmporteerd.

## Waar Eva 2 op moet uitblinken

De differentiatie is niet meer tools of meer achtergrondloops. Eva 2 wordt beoordeeld op de
combinatie die de drie concurrenten in exact dezelfde proeven moeten bewijzen:

- een herkenbare, hernoembare identiteit en menselijke presence;
- opdrachten werkelijk afmaken over meerdere domeinen;
- weinig vragen stellen, maar nooit ontbrekende toestemming verzinnen;
- iedere claim koppelen aan extern bewijs;
- veilig hervatten zonder duplicate of ghost work;
- gecontroleerd nieuwe connectors leren uit documentatie;
- zichzelf verbeteren via evidence, canary en rollback in plaats van ongecontroleerde zelfedit.

Er is nog geen claim dat Eva beter is dan OpenClaw, Hermes of Agent Zero. Die claim mag pas na
dezelfde semantische live cases, dezelfde modellen en dezelfde externe oracles worden gedaan.

## Beslisbewijs

- [Content-addressed beslismanifest](../experiments/eva2-architecture-bakeoff/evidence/architecture-bakeoff-decision-442b6e0dc1434da9dcd31f88d8395be0332d0d46b15d74f7ed5850ce262dbf1c.json)
- [Architectuurbakeoffcontract](eva2-architecture-bakeoff-v1.md)
- [Herstel- en herbouwplan met feitelijke incidentoracles](eva-rebuild-plan-2026-08-20.md)
- [Memory-eindmanifest](../experiments/eva2-architecture-bakeoff/memory/evidence/memory-run-2652a3e97aafccc7c083dc4af7cf766932b7acb0b53215e3dff9c793512ae092.json)
- [SQL-memorybewijs](../experiments/eva2-architecture-bakeoff/memory/evidence/sql-memory-probe-b3388b9c29c2d3ce5cee19a757d5e06a1839da925567ab891d2733aef0d34ae6.json)
- [DBOS datasource-transacties](https://docs.dbos.dev/python/tutorials/transaction-tutorial)
- [DBOS workflowsemantiek](https://docs.dbos.dev/python/tutorials/workflow-tutorial)
- [Pydantic AI modelproviders](https://pydantic.dev/docs/ai/models/overview/)
- [Pydantic AI getypeerde output](https://pydantic.dev/docs/ai/core-concepts/output/)
