# Eva 1 naar Eva 2 transition manifest

Status: `selected_not_migrated`

Datum: 20 augustus 2026

Beslisbestand: [content-addressed manifest](../transition/evidence/eva1-to-eva2-transition-manifest-c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c.json)

Dit document sluit stap 2 van de besluitvolgorde. De Eva 2-stack staat in
[ADR-0001](adr-0001-eva2-living-core.md). Hier staat nu exact wat uit Eva 1
waarde houdt en wat niet meegaat. Er is niets gemigreerd, verwijderd, gestart
of opnieuw geactiveerd.

## Besluit in gewone taal

Eva 2 wordt niet gemaakt door de oude database, code of scripts over te zetten.
De oude runtime blijft een read-only herstel- en bewijsbron.

Wat wel waarde houdt:

- de echte gespreksinhoud;
- geselecteerde personen, relaties, herinneringen en twee niet-uitvoerbare
  commitmentreviews;
- de waarden achter Eva's identiteit en menselijke manier van praten;
- bewezen contractbetekenis rond opdracht, episode, effect, receipt, readback,
  correctie, herstel en aflevering;
- echte incidenten als acceptatieproef.

Wat niet waarde houdt als actieve toestand:

- 95.398 algemene timelineregels en 22.099 cognitieve events als zogenaamd
  leven of geheugen;
- oude mood, emotion, innerlife, autonomy, timers, cursors en retrytoestand;
- oude authority- en receiptregels als bewijs dat iets nu nog waar is;
- hardcoded namen, Home Assistant als kern, oude connectors en toolcode;
- secrets, biometrie, packagebomen, vendorcheckouts en zelfreparatiecode.

Continuiteit komt voortaan uit een configureerbaar IdentityPack, bevestigde
relaties, brongebonden ervaringen en expliciete commitments. Niet uit zoveel
mogelijk oude machinerierijen.

## Bevroren bronnen

| Bron | Binding |
| --- | --- |
| Eva 2 candidate | commit `7b56f754d0bb303a6174eb10ee5c785eacd053ae`, tree `70d64d999ffadd6545b10487edfc12c8a83edc7b` |
| Eva 1 source tree | `6e6427d4664034324a52aea83117cbf5523ea8fe` |
| Recovery checkpoint | commit `147e8496e04a7d24caad923a5a88bdd71a28c58b`, tree `cfdc6b96d499bd31cf514485b6de91cecbe2efcd` |
| Recoverymanifest | SHA-256 `2fa3ff0b2f4bb58c92b225f60cfc63ff7998c24f8614cc87c1419c8a146c8bd4` |
| Logische SQLitekopie | SHA-256 `951e31e32105bba5576653094bd2d473c85e7e23f73842d961c1eae933a2cd1f`, 146.538.496 bytes |
| Sensitive archive | SHA-256 `074e665ba0408bf66f11183216fd82781d5b211b9b89d720756322a32d2072a7`, 474.153.118 bytes |

De database is alleen geopend met `mode=ro&immutable=1` en
`query_only=ON`. De SHA-256 was voor en na de audit gelijk. `quick_check` was
`ok` en er waren nul foreign-keyovertredingen. Van de sensitive archive zijn
alleen headers gelezen. Hij is niet uitgepakt. De Eva-service bleef inactive en
disabled.

## Alle 140.099 databaserijen hebben een bestemming

| Dispositie | Rijen | Aandeel | Gevolg |
| --- | ---: | ---: | --- |
| Behouden | 2.948 | 2,10% | Bytegetrouwe gespreksinhoud, opnieuw omhuld met legacy-provenance |
| Converteren | 1.499 | 1,07% | Alleen als getypeerde kandidaat, meestal pas actief na bevestiging |
| Alleen archiveren | 69.689 | 49,74% | Afgeschermd incident- en rollbackbewijs; een latere archiefverpakking moet versleuteld zijn; nooit hot authority of standaard modelcontext |
| Opnieuw opbouwen | 10.775 | 7,69% | Oude projectie of runtime-state niet importeren |
| Verwijderen of uitsluiten | 55.188 | 39,39% | Nooit naar Eva 2; fysieke bronverwijdering pas volgens privacy- en rollbackbeleid |
| **Totaal** | **140.099** | **100%** | Alle 72 tabellen zijn in het JSON-manifest opgenomen |

Deze telling bevat 6.555 FTS- en schaduwindexrijen. Die zijn afgeleid en worden
niet als primaire kennis geteld.

## Wat behouden en geconverteerd wordt

### Gesprekken

Alle 2.948 rijen uit `messages` houden hun originele tekstbytes, tijd, kanaal en
rol. Ze worden een legacy transcript, geen feiten- of effectauthority. Eva 2
voegt installation, principal, provenance en contenthash toe. Er wordt nooit op
alleen een naam samengevoegd. Geen legacy transcript komt in normale
modelcontext totdat tenant, principal en test- of synthetic-status expliciet
zijn bevestigd.

De 573 extra Telegramrijen blijven transportarchief. Daarvan overlappen 445
tekstrijen op inhoud en rol met `messages`. De overige 28 tekstrijen en alle
100 foto-enveloppen zijn alleen archief-reviewkandidaten, geen canonical
conversation import. De importer mag daarom niet op tekst alleen dedupliceren
en mag geen bericht tweemaal als gesprek importeren.

### Personen en relaties

De 19 `people`-rijen worden reviewkandidaten. Slechts twee hebben een expliciet
geconfigureerde huishoudrol. De twee Telegram-ID's worden pas connectorbinding
na nieuwe eigenaarsbevestiging. De zes relatiecorrecties blijven aparte,
laagvertrouwde bronevents omdat een oorspronkelijk berichtbewijs ontbreekt.

### Geheugen

De 1.212 niet-gearchiveerde memories kunnen alleen als
`legacy_unverified` kandidaat worden geconverteerd. Het oude schema mist tenant,
bron-ID, bronhash, trust, valid-time en supersession. Geen enkele rij wordt door
de import automatisch een waar of handelbaar feit.

De 1.975 reeds gearchiveerde memories blijven alleen archief. Alle FTS-data
wordt na een eventuele selectie opnieuw uit Eva 2-bronnen opgebouwd.

### Commitments

Er zijn vijf actieve goal-kandidaten en precies twee volledige ketens met een
actief project, blocked episode en blocked work item. Hun episode-ID's zijn 82
en 83. Zij komen uitsluitend binnen als niet-uitvoerbare
`LegacyCommitmentReview/v1` met status `blocked_requires_reconciliation`.
Import doet nul modelcalls, connectorcalls, deliveries of effecten en maakt
geen runnable episode.

Voor iedere kandidaat is eerst een verzegelde
`LegacyEffectReconciliation/v1` nodig die alle gerelateerde executions,
deliveries, artifacts, het oude effectmanifest en actuele externe readbacks
bindt. Alleen bewezen `zero_effects` of volledig gereconcilieerde toestand kan,
na expliciete toestemming, leiden tot een aparte nieuwe Eva 2-episode.
Ambigue of onleesbare toestand blijft geblokkeerd. Alleen opnieuw toestemming
geven is nooit effectauthority. Geen oude stap wordt hervat.

Alle 36 reminders zijn al done of cancelled. Zij worden niet opnieuw actief.

### Media

Van de 615 JPEG-BLOBs zijn 107 aan een bericht gekoppeld. Alleen deze 107 zijn
conversatiemediakandidaten, samen 18.180.259 bytes. Conversie vereist per object
eigenaar, doel, afgebeelde personen, toestemming en retentie. De bytes gaan naar
versleutelde content-addressed objectopslag, nooit als BLOB naar PostgreSQL.
Deze eisen zijn een pre-copy gate: geen blob verlaat het recoverycheckpoint
voordat zij allemaal zijn verzegeld.

De overige 508 contextfoto's, samen 30.873.923 bytes, zijn uitgesloten. De 29
ArcFace-embeddings gaan nooit mee. Een latere gezichtscapability begint alleen
met nieuwe, expliciete enrolment en eigen verwijderbewijs.

### Identiteit en voorkeuren

De dertien geselecteerde KV-sleutels hebben drie verschillende eigenaren. Zes
zijn uitsluitend IdentityPack-kandidaten: uiterlijk, karakter, communicatie,
drives, verjaardag en humor. Twee zijn installatie- of connectorconfiguratie:
cameraregio's en locality. Vijf zijn relatie- of routinekandidaten: ochtend,
avond, week, gedeelde rituelen en social-carethreads. Iedere waarde wordt
afzonderlijk beoordeeld en ondertekend. Geen van deze waarden mag bewijsregels,
succescriteria, schedules of effectauthority definiëren. De overige 89
KV-rijen zijn archief, rebuild of delete.

De karakterwaarden uit `src/mind/persona.js` worden als betekenis herzien, niet
als promptcode gekopieerd. De beoogde kern blijft warm, praktisch, nieuwsgierig,
een tikje eigenwijs, software-eerlijk, bondig en menselijk. Eva controleert
uitkomsten voor ze succes claimt, stelt alleen noodzakelijke vragen, gebruikt
geen intern jargon en geen em- of en-dashes. `Eva`, `Jaap`, `gezin` en
Home Assistant worden geen functionele constanten.

## Wat alleen bewijs blijft

De volgende gegevens blijven voorlopig uitsluitend in het afgeschermde
recoverycheckpoint. De huidige sensitive tar is onversleuteld (bestandmode
`0600` in directorymode `0700`); pas een latere archiefverpakking moet
versleuteld en niet-actief zijn:

- terminale doelen, projecten, episodes, work en deliveryclaims;
- 731 oude tool-executions en acht interpretation handoffs;
- alle 281 HTTP-proof- en authorityrijen, inclusief 37 ingressrequests en 63
  TurnUnderstanding-authorities;
- 426 HTTP lease-, state- en attemptevents;
- cognitive episodes/events, explorations, error book, workcells, doorbell,
  capability- en toolinstallatiegeschiedenis;
- geselecteerde incidentdelen van `timeline`;
- AgentMail-accountmetadata zonder credentials;
- oude source, schema's, generated runtimekaarten, vendorcode, packages en
  historische quarantaine.

Deze records bewijzen hoogstens wat Eva 1 dacht of deed. Zij geven Eva 2 geen
toestemming en bewijzen geen huidige externe toestand.

## Wat opnieuw wordt opgebouwd

- PostgreSQL full-text en alle andere memoryprojecties;
- conversation activity uit de geselecteerde canonical messages;
- attention, scheduler, cursors, caches, metrics en circuitstate;
- capabilitycatalogus uit gesigneerde manifests;
- connectors als geïsoleerde workers;
- optionele face- en wakewordassets alleen na nieuwe toestemming en bewijs;
- install, backup, export, restore, update, rollback en uninstall via de nieuwe
  `eva`-CLI en gesigneerde OCI-images.

## Wat nooit wordt overgenomen

- de Eva 1-JavaScript-, Python-, C- of shellruntime als executable onderdeel;
- het SQLite-schema, oude IDs als globale identiteit of verspreide DDL;
- `.env`, `.eva_mind_key`, `auth.json`, vaultwaarden, tokens, wachtwoorden,
  SSH-keys, Voice PE-secrets of provisioning-QR's;
- hardcoded namen, provideraccounts, absolute paden of devicebindings;
- ongevraagde biometrie, facevectors, encoder- en wakewordmodellen;
- mood, emotions, innerlife, telemetry of autonomyruis als Eva's leven;
- pending effects, deliveries of authority die na import vanzelf kan hervatten;
- receipts als bewijs dat een oude externe toestand nog steeds bestaat;
- node_modules, virtual environments, toolinstallaties, Git-hooks,
  vendorcheckouts en quarantained instructies;
- de mislukte strict snapshot of het lege validatorrapport.

Oude credentialbytes worden niet geconverteerd. Eva 2 krijgt een nieuwe
installation key en nieuw uitgegeven, operation-scoped connectorcredentials.
De Eva 2-converter krijgt nooit `.eva_mind_key`, `kv[vault]`, ontsleutelde
credentials of provisioning-QR-data. De oude mind-key blijft uitsluitend bij
de afgesloten Eva 1-rollbackcustodian tot de rollbacktermijn wordt beëindigd.
Provider- en devicecredentials worden ingetrokken nadat de nieuwe binding met
readback is bewezen. `delete-exclude` wint altijd van archive, rebuild,
convert en iedere glob, ook binnen backups.

Er is nu geen workspace-document voor import geselecteerd. Zulke bestanden
blijven alleen reviewarchief totdat een nieuw ondertekend, content-addressed
manifest per bestand eigenaar, rechten, doel, MIME en retentie bindt. De oude
UI, HTML, CSS en `eva.glb` blijven eveneens alleen designreferentie. Het model
vereist eerst rechtencontrole en expliciete selectie.

De oude wakewordpatch, quarantainemodellen en volledige Voice PE-map zijn geen
rebuildbron. Een nieuw wakeword mag uitsluitend worden opgebouwd uit nieuw
verkregen, gelicenseerde en expliciet consented assets. Secrets, QR-data,
biometrische samples, oude modellen en trainingmedia blijven uitgesloten.

## Capabilities die als betekenis terugkomen

De [capabilitydispositie](../transition/evidence/eva1-capability-disposition-80e429e336826399bc9fac512013ae255dd4ccd4207d19c0905ace23ea581eb7.json)
classificeert alle 49 legacy capabilities en alle 93 operation-ID's afzonderlijk.
Iedere operation heeft een target owner, bouwfase en bewijsbeleid. Dat bewaart
alleen contractbetekenis; geen oude handler wordt executable.

| Familie | Wat terugkomt | Wat achterblijft |
| --- | --- | --- |
| Ingress en planning | request-ID, inputhash, principal, conversation, generation, conflict, supersession, getypeerde ModelDecision en ValidatedPlan | Regex-orchestrator, WeakMap/WeakSet, sidecars en oude SQLiteauthority |
| Episode en effect | monotone generation, intent, stable idempotency, typed ambiguity, receipt, onafhankelijke readback en deliveryclosure | Oude tables, timers, PID/boot-identiteit en process-local capabilities |
| Capability en Forge | versioned manifest, input schema, effectklasse, scope, proof floor, freshness, staged/tested/quarantined/active | Handlers, npm-state, in-process secrets en modeltoegang tot runtime-updates |
| Connectors | provider resource-ID, partial result, reconcile-before-resend, receipt/readback en onderscheiden transports | Nodeclients, gedeelde procesenvironment, caches als proof en HA als speciale kern |
| Workspace | een geïsoleerde writer, generation, pad/rootgrens, contenthash en readback | Oude C/JS-broker, absolute workspacepaden en packagebomen |
| Learning en autonomie | een durable Attention-loop, budget, stopregel, canary en rollback | losse intervals, listeners, retryloops, self-edit en gegenereerde doelen als identiteit |
| Tests | gebruikersinvoer, allowed/forbidden effects, before/after, receipts, crashpunten en natuurlijk resultaat | fixtures die private oude rows zaaien en groene unittests als live bewijs |

Geen JavaScriptmodule wordt geïmporteerd door de Python Living Core. Alleen
versioned contractbetekenis en language-neutral behaviororacles worden opnieuw
geimplementeerd.

## Importpoort

Dit manifest geeft nog geen migratietoestemming. Iedere echte importer moet
eerst aan alle volgende voorwaarden voldoen:

1. De verticale Eva 2-poort uit ADR-0001 is volledig groen.
2. Iedere rij krijgt de bron-DB-hash, tabel, legacy-primary-key en canonical
   rowhash.
3. De converter draait tweemaal bytegelijk en doet geen model-, connector- of
   externe call.
4. Ieder item heeft tenant/principal, provenance, privacyklasse, numerieke
   retentie en delete-SLA.
5. Secrets, absolute paden, executables, testidentiteiten en ongemapte relaties
   worden fail-closed geweigerd.
6. Import naar een lege installatie is idempotent en activeert geen oud werk of
   effect.
7. `eva export` herstelt alles naar een tweede schone installatie; een generieke
   reader kan identity, conversations, commitments, memorybronnen en evidence
   lezen terwijl DBOS en Eva 1 uitstaan.
8. Batchrollback en crypto-shred bewijzen verwijdering, inclusief projecties,
   objecten en back-upretentie.
9. De uiteindelijke row-level selectie en converteroutput worden met de release
   signing key ondertekend.

Het JSON-beslisbestand is nu content-addressed en alle 72 tabellen reconciliëren
naar exact 140.099 rijen. Er is nog geen releasehandtekening, omdat er bewust nog
geen row-level export of importer bestaat. `dataMigrationAuthorized`,
`serviceStartAuthorized` en `eva1CodeImportAuthorized` staan daarom alle drie op
`false`.

## Gevolg voor de bouwvolgorde

Stap 1 is afgerond: de doelstack is gekozen. Stap 2 is met dit manifest
afgerond: Eva 1 is nu een expliciet begrensde bron, geen impliciete basis.

Stap 3 kan nu beginnen met een schone buildkaart voor precies wat Eva 2 nodig
heeft: de minimale Living Core-contracten, de eerste cross-domain vertical,
centrale migraties, connector- en credentialgrens, IdentityPack, installer,
portable export, geselecteerde importers, A/B-route en de volledige bewijsset.
Tot die kaart en de verticale poort groen zijn, blijft Eva 1 uit en wordt niets
geïmporteerd.
