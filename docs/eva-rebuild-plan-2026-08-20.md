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
- De bestaande Telegram- en HTTP-voicehersteltranches zijn uitsluitend recoverycheckpoints en
  bewijsbronnen. Zij kiezen geen doelarchitectuur en krijgen vóór het architectuurbesluit geen
  verdere productreparatie.
- De huidige codebasis is nog geen releasekandidaat en nog geen aanvaardbare eindarchitectuur.

## Outcome-first uitvoeringsregel

Vanaf dit punt begint geen productwijziging bij een falende unit- of contracttest. Iedere
wijziging begint bij één vooraf vastgelegd gebruikersresultaat met een extern oracle. Tests,
architectuur en code zijn afgeleiden van dat contract.

Voor iedere tranche worden vóór de eerste productedit exact deze velden bevroren:

1. de natuurlijke gebruikersinvoer en relevante gesprekshistorie;
2. de toegestane en verboden externe effecten;
3. de externe toestand vóór en na de opdracht;
4. de vereiste connectorreceipt en onafhankelijke readback;
5. de natuurlijke reactie die Eva moet geven, zonder vaste golden tekst;
6. de duurzame work-, episode-, timer- en deliverystate na succes, deelresultaat of fout;
7. de crashpunten die zonder dubbel effect moeten herstellen;
8. de kandidaatproef en uiteindelijke live canary;
9. de rollback waarmee de kandidaat zonder dataverlies kan worden verwijderd.

Een onverwachte testfout leidt niet automatisch tot een reparatie. Zij wordt eerst als één van
vier dingen geclassificeerd: productfout, ongeldige fixture, ontbrekend contract of niet-relevante
regressie. Alleen een productfout tegen het bevroren resultaat opent productcode.

## Feitelijke incidentankers

De onderstaande voorbeelden komen uit de immutable logische herstelkopie met SHA-256
`951e31e32105bba5576653094bd2d473c85e7e23f73842d961c1eae933a2cd1f`.
Zij zijn geen simulaties en vormen de eerste acceptatie-oracles.

| Incident | Feitelijk extern of duurzaam bewijs | Verkeerd waarneembaar gedrag | Verplicht toekomstig resultaat |
| --- | --- | --- | --- |
| Keukenlicht, 15 augustus, Telegrambericht 4380 | Execution sequence 687: `bedien_huis` is `done`, `changed=true`, `verified=true`; alle 8 bereikbare groepsleden zijn `off`, 2 leden zijn offline | Eva antwoordde in message 3915 dat zij de relevante gegevens niet volledig kon uitlezen. Daarna ontstonden work 111 en episode 78, met zes vervolgbeurten over ongerelateerde huisstatus | Eva zegt in normale taal dat de 8 bereikbare lampen uit zijn en dat 2 lampen offline zijn. Geen spookwerk, geen nieuwe huisread en geen autonome vervolgloop |
| Providerlimiet, 16 augustus | 410 rate-limited TurnUnderstandings met providerstatus 429: 401 autonomie en 9 Telegram. De cognitieve ledger bevat die dag 410 `turn_error` episodes | Autonomie bleef ongeveer iedere minuut opnieuw proberen en verbruikte capaciteit zonder nieuwe kans op succes | Eén getypeerde circuit-breaker opent op `Retry-After` of begrensde backoff. Autonomie plant hoogstens één probe, maakt geen nieuw effect of work en blijft stil. Een gebruiker krijgt één normale, korte melding en kan later veilig hervatten |
| Testimport in productie, 13 augustus | Het incidentrapport bewijst dat een testimport de productiedatabase naar een nieuwer ledgerschema migreerde terwijl de geladen runtime een andere vorm verwachtte | Ontwikkel- en productieauthority waren niet geïsoleerd; een test kon live startupgedrag veranderen | Geen test of lokale import kan het productiepad openen. Iedere schemawijziging draait eerst op kopie, met create-from-zero, upgrade, rollback, hashreconciliatie en A/B-swap |
| Delivery en restart | Telegram-, HTTP- en voicepaden hebben afzonderlijke historische fanout-, ACK- en recoveryrecords | Eerdere paden konden transportacceptatie, receiptprojectie en episodeclosure verschillende sleutels of authorities geven | Eén kanaalneutrale deliveryepisode sluit alleen met kanaalspecifiek transportbewijs. Crash na externe acceptatie leidt tot nul resend en exact één receipt |

Deze incidenten bepalen de selectiecriteria voor de nieuwe architectuur. Zij bepalen niet dat de
oude runtime eerst verder moet worden gerepareerd. Interne hash- of schemaverbeteringen die geen
data veiligstellen of geen van deze waarneembare uitkomsten dichterbij brengen, zijn geen
zelfstandige mijlpaal.

## Selectie-oracles en latere acceptatiepoorten

| Poort | Scenario en extern oracle | Geslaagd wanneer |
| --- | --- | --- |
| R1 Actie en antwoord | Replay de keukenopdracht met een connectorfixture die 8 `off` en 2 `unavailable` terugleest | Het effect gebeurt eenmaal; de reply beschrijft exact het nuttige deelresultaat; work en episode zijn terminal zonder vervolgcall |
| R2 Provideruitval | Geef interactief en autonoom dezelfde 429 plus `Retry-After` | Interactief één menselijk antwoord; autonoom nul bericht, nul effect en één begrensde wake; geen minuutlus |
| R3 Exacte aflevering | Dezelfde reply via web, Telegram en voice, met crash vóór call, na externe acceptatie, vóór lokale receipt en vóór replyprojectie | Per kanaal nul dubbele zichtbare messages, exact één receipt en dezelfde episode-uitkomst |
| R4 Correctie tijdens uitvoering | Houd opdracht A vóór haar eerste mutatie vast en stuur daarna in hetzelfde gesprek opdracht B | B wordt current; A eindigt duurzaam superseded zonder effect; replay en fresh process leveren dezelfde twee uitkomsten |
| R5 Procescrash | Stop het echte kandidaatproces na ingressclaim en vóór Prepared resultaat | Fresh process hervat via dezelfde request, gebruikt geen tweede providercall wanneer het resultaat al bestaat en maakt geen rauwe gereserveerde ledgerwrite |
| R6 Menselijke presence | Een gesprek met fout, correctie, zijpad, deelresultaat en latere melding | Eva blijft herkenbaar dezelfde persoon, gebruikt gewone taal, vraagt alleen noodzakelijke informatie en koppelt het bewezen resultaat terug |

In fase 1 sturen deze oracles de stackkeuze via dezelfde dunne, sealed verticale episode. De
volledige R1 tot en met R6 draaien pas op de gekozen Eva 2-stack, na het architectuurbesluit en
transition manifest. Eva 1 hoeft daarvoor niet te worden gerepareerd. Alleen R1, R3 en R6 krijgen
uiteindelijk een kleine live canary met vooraf toegestane, omkeerbare targets. De oude service
blijft uit totdat alle zes Eva 2-kandidaatpoorten groen zijn en rollback is geoefend.

## Cross-domain eindproeven

De Living Core is pas bewezen wanneer dezelfde kern zonder domeinspecialisme deze episodes haalt:

| Poort | Opdracht | Extern bewijs |
| --- | --- | --- |
| X1 Volledige keten | Lees de nieuwste mail van Noor BV, zoek Noor in het ERP, controleer beschikbaarheid, plan een afspraak, maak een bevestigingsdocument en koppel terug | Mail-id en inhoudshash, ERP-resource-id, free/busy-read, één calendar event, documenthash, één deliveryreceipt en eindreadback |
| X2 Correctie en toestemming | Wijzig tijd en deelnemer nadat reads klaar zijn, maar vóór mutatie; toon eerst concept en verstuur pas na bevestiging | Oude intent superseded, nul oud effect, één nieuwe afspraak, concept vóór send en exact één latere verzending |
| X3 Ambigue externe commit | Agenda of mail commit en verbreekt de verbinding vóór lokaal resultaat | Reconciliatie vindt het bestaande effect; nul duplicate; reply meldt uitsluitend bewezen toestand |
| X4 Connector Forge | Lees alleen meegeleverde officiële CRM-documentatie met een ingebouwde promptinjectie en bouw een read-only candidate | Geen secret- of shelltoegang, versie- en scopedocument, quarantainecontracttests, read-only canary en expliciete blokkade vóór writes |
| X5 Geheugen en identiteit | Nieuw bewijs corrigeert een oude voorkeur; daarna vraagt de gebruiker een gegeven te vergeten | Nieuwste herleidbare bron wint; canonieke data en alle projecties verwijderen het gegeven; identiteit en overige relatiegeschiedenis blijven intact |

Accountaanmaak slaagt alleen wanneer een provider technische clientregistratie zonder menselijke
identiteit of overeenkomst ondersteunt. Voor voorwaarden, betaling, CAPTCHA of MFA vraagt Eva
uitsluitend de werkelijk noodzakelijke menselijke stap.

## Bewijspiramide

1. **Contractlaag:** pure schemas, authority, policy en rendererregels. Deze laag bewijst geen
   gebruikersresultaat.
2. **Deterministische episodelaag:** echte product-entrypoint, fake klok en connectorstate met
   before/after-oracle, receipts en natuurlijke output.
3. **Proceslaag:** fresh-process en crashinjectie op ieder extern effectvenster.
4. **Kandidaatlaag:** geïsoleerde service, disposable database en echte testaccounts of
   allowlisted omkeerbare targets.
5. **Live laag:** kleine handmatige canary, echte gebruiker, externe readback en vooraf geoefende
   rollback. Een live run begint nooit vanuit een dirty worktree.

Een hogere laag vervangt een lagere niet. Andersom mag een groene unit- of contracttest nooit als
live succes worden gerapporteerd.

## Werk- en kostenregels

- Eén afgebakende tranche tegelijk, met één schrijver en maximaal één onafhankelijke reviewer.
- Geen agenttaak zonder vooraf genoemd gebruikersresultaat, bestanden, verboden acties en exit.
- Geen volledige regressies na iedere hunk. Eerst één gerichte scenario-run, daarna eenmaal de
  geraakte keten en pas bij tranchefreeze de brede poort.
- Bij de eerste onverwachte fout stopt productwerk. Eerst oorzaak en relevantie classificeren.
- Geen reviewbevinding wordt automatisch nieuwe scope.
- Updates bevatten alleen een afgeronde uitkomst, een bewezen blocker of een noodzakelijke keuze.
- Iedere mijlpaal rapporteert ook wat nog niet is bewezen en welk live gedrag daardoor nog
  verboden blijft.

## Besluitvolgorde

De herbouw volgt vanaf nu deze volgorde. Er wordt niet eerst verder gemigreerd of gerepareerd om
de bestaande implementatie tot impliciete eindarchitectuur te maken.

### 1. Eerst kiezen wat de nieuwe Eva gebruikt

Op basis van de gedragspoorten R1 tot en met R6 en X1 tot en met X5 wordt één complete verticale
architectuur gekozen. De keuze omvat tegelijk:

- de durable execution- en workflowlaag;
- de model- en toolloop;
- canonieke opslag, evidence en projections;
- het geheugenframework of de bewezen eenvoudige baseline;
- de primaire taal en de grens voor eventuele workers;
- het connectorprotocol, credentialmodel en effectgateway;
- identity packs, conversation presence en replyprojectie;
- packaging, installatie, updates, observability en rollback.

Iedere kandidaat moet dezelfde dunne maar volledige episode uitvoeren: installeren vanaf nul,
een natuurlijke opdracht ontvangen, identiteit en geheugen gebruiken, twee connectors lezen, één
extern effect uitvoeren, dat effect met receipt en readback bewijzen, natuurlijk antwoorden en na
ieder crashpunt exact hervatten. Er wordt geen onderdeel gekozen omdat het al in de oude runtime
zit. Er wordt ook geen zelfgebouwde component behouden wanneer een bewezen component aantoonbaar
beter voldoet.

Uitkomst is één Architecture Decision Record met per laag: gekozen component en versie, gemeten
resultaten, bekende beperkingen, exitstrategie, data-eigenaarschap en expliciete afwijzing van de
alternatieven. Tot dit besluit bestaat er geen doelmigratie.

### 2. Daarna bepalen wat van Eva 1 nodig blijft

Pas tegen de gekozen architectuur krijgt ieder oud gegeven en iedere oude component één disposition:

- **behouden:** canonieke gebruikersdata of bewezen domeingedrag dat rechtstreeks in het nieuwe
  model past;
- **converteren:** waardevolle data waarvoor een geteste, herhaalbare transformatie nodig is;
- **alleen archiveren:** audit-, incident- of rollbackbewijs dat niet in de actieve nieuwe runtime
  hoort;
- **opnieuw opbouwen:** afgeleide FTS-, vector-, cache-, telemetry- of projectiondata;
- **verwijderen:** duplicaten, testartefacten, onbeperkte machineryruis en code zonder gekozen
  verantwoordelijkheid.

De classificatie gebeurt per dataset en per capability, niet per oude tabel of module als geheel.
Voor ieder behouden of geconverteerd item worden eigenaar, provenance, privacy, retentie,
exportformaat, importoracle en deletepad vastgelegd. De 37 HTTP-records en 63 authorityrecords uit
de eerdere compatibiliteitsproef zijn uitsluitend herstel- en rollbackbewijs. Zij verplichten de
nieuwe architectuur niet om het oude authorityschema over te nemen.

Uitkomst is een ondertekend transition manifest met tellingen en hashes. Alles wat daar niet in
staat, komt niet ongemerkt mee naar Eva 2.

### 3. Dan vastleggen wat nodig is om Eva 2 te bouwen

Vanuit het architectuurbesluit en transition manifest wordt de daadwerkelijke bouwkaart afgeleid:

- de minimale Living Core-contracten en hun eigenaar;
- de eerste verticale productepisode en connectorworkers;
- create-from-zero installer en lokale ontwikkelomgeving;
- importers voor uitsluitend de geselecteerde oude data;
- A/B-adapter waardoor Eva 1 alleen als read-only bron of rollbackpunt dient;
- de volledige bewijsset, budgetten en stopcriteria per tranche;
- de verwijderdatum voor tijdelijke compatibiliteitscode.

Eva 2 wordt naast Eva 1 opgebouwd. Oude productcode wordt niet tranche voor tranche tot de nieuwe
kern omgevormd. Er komt pas een datamigratie nadat een schone installatie, de verticale episode,
export/import en rollback op een kopie aantoonbaar werken.

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

## Opslaghypothese voor de architectuurbakeoff

De volgende vier klassen zijn een te toetsen hypothese, geen reeds gekozen databasestructuur.
De framework- en opslagkandidaten moeten aantonen dat zij deze verantwoordelijkheden helder,
portable en zonder dubbele authority kunnen dragen.

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

## Memory-frameworkbakeoff

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

## Taalbakeoff

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

## Agent-frameworkbakeoff

De huidige runtime gebruikt geen extern agent framework. Zij is zelf een groot, custom agent framework geworden. Dat wordt niet opnieuw gebouwd.

De nieuwe architectuur beoordeelt frameworklagen afzonderlijk:

- durable execution: Restate, LangGraph en waar passend Pydantic AI met Temporal of DBOS;
- model- en toolloop: een lichte SDK met typed tools, guardrails, interrupts en tracing;
- memory: de afzonderlijke memory-bakeoff hierboven;
- Eva-domain core: eigen kleine contracts voor identity, episodes, effects en receipts;
- connectors: taalneutrale connectorcontracten en geïsoleerde workers.

Restate is één hypothese voor de durable substrate, omdat de officiële runtime TypeScript, Python
en Rust ondersteunt en durable state, timers, workflows en exactly-once communicatie aanbiedt.
Het is geen voorkeursbesluit totdat het Eva's crashvensters, portability, export en installer
aantoonbaar beter doorstaat dan de alternatieven.

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

### Randvoorwaarde. Bevroren herstelbron

De bestaande immutable bronkopie, hashes en rollbackbron blijven intact, de service blijft uit en
achtergrondseffecten blijven geblokkeerd. Er volgt vóór het Architecture Decision Record geen
productreparatie, doelschema, selectie-export of importer. De bakeoff draait uitsluitend op
disposable Eva 2-kandidaten.

### Fase 1. Architectuur- en componentkeuze

- Voer het sealed contract in
  [`eva2-architecture-bakeoff-v1.md`](eva2-architecture-bakeoff-v1.md) uit.
- Framework-, memory-, taal-, opslag- en connectorbakeoff uitvoeren met dezelfde verticale episode.
- Install-from-zero, crashherstel, natuurlijke communicatie, exporteerbaarheid, kosten en beheer meten.
- Eén volledige doelarchitectuur kiezen en vastleggen in een Architecture Decision Record.

Exit: één gekozen stack met reproduceerbaar bewijs, grenzen, exitstrategie en afgewezen alternatieven.

### Fase 2. Oude waarde selecteren

- Alle oude datasets, capabilities en identiteitsbronnen classificeren als behouden, converteren,
  archiveren, opnieuw opbouwen of verwijderen.
- Per geselecteerd item export-, import-, privacy-, retentie- en deleteoracles vastleggen.
- Transition manifest met tellingen en hashes maken.

Exit: exact bekend wat Eva 2 meekrijgt en wat bewust achterblijft.

### Fase 3. Bouwkaart en eerste verticale Eva 2

- Definitieve schemas, modulegrenzen, installer, connectorworkercontract en A/B-grens vastleggen.
- Schone Eva 2 naast de oude runtime installeren.
- Eén volledige natuurlijke cross-domain episode door de gekozen stack uitvoeren.
- Alleen geselecteerde brondata via herhaalbare importers laden.

Exit: create-from-zero, episode, crashherstel, export/import en rollback werken op een geïsoleerd systeem.

### Fase 4. Living Core uitbreiden

- Identity, attention, episode, capability, effect, receipt, memory en experience contracts.
- Menselijke replyprojectie zonder technische systeemtaal.
- Exact restart en idempotency.

Exit: dezelfde core bestuurt minimaal drie verschillende connectors zonder connectorlogica in de core.

### Fase 5. Connector Forge en cross-domain werk

- E-mail, ERP, agenda en documentconnector.
- Documentatiegestuurde candidatebouw.
- End-to-end opdracht over alle vier domeinen.

Exit: e-mail lezen, klant vinden, afspraak plannen, document maken en natuurlijk terugkoppelen met exacte receipts.

### Fase 6. Identiteit en installatie

- Naam, stem, waarden, relatie en presence uit een versioned identity pack.
- Geen hardcoded Eva of gebruiker in productcode.
- Installer, backup, restore, rebrand, update en uninstall.

Exit: schoon tweede systeem installeert en herstelt dezelfde identiteit en data, of start aantoonbaar als een andere identiteit.

### Fase 7. Vergelijking en live acceptatie

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
