# Eva 2 bouwkaart

Status: `step3_design_frozen_not_implemented`

Datum: 20 augustus 2026

Dit is de korte hoofdkaart. De technische details staan in de drie onderliggende kaarten voor
[core en contracten](eva2-build-map-core-contracts.md),
[platform en beheer](eva2-build-map-platform.md) en
[presence en bewijs](eva2-build-map-presence-evidence.md).

De eerste productepisode is vooraf vastgelegd in het
[content-addressed verticalcontract](../design/evidence/eva2-first-vertical-contract-2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1.json).

Deze kaart geeft toestemming om uitsluitend in de nieuwe map `eva2/` te bouwen. Zij geeft geen
toestemming om Eva 1-code te importeren, Eva 1-data te migreren, productiecredentials te gebruiken,
een live connectorwrite te doen of een productservice te starten.

## De drie besluiten in de juiste volgorde

### 1. Wat we gebruiken

- Python 3.12.14 als enige Living Core-runtime voor deze buildfreeze.
- Pydantic 2 voor eigen, versioned contracten.
- Pydantic AI als vervangbare modeladapter zonder tools, sessions, memory of retries.
- DBOS 2.30 alleen voor durable uitvoering, timers en het te bewijzen lokale transactiepunt.
- PostgreSQL 16.13 als enige business-authority.
- SQL en PostgreSQL full-text als eerste, volledig herbouwbare recallprojectie.
- JSON-RPC over stdio naar geïsoleerde connectorworkers.
- Versleutelde content-addressed objectopslag voor documenten en media.
- Eén lokale API, één beheer-CLI en gesigneerde OCI-releases.

Een extern agent- of memoryframework wordt dus niet Eva's kern. Het model stelt voor. De Living
Core beslist. Een connector voert alleen een begrensde operatie uit. PostgreSQL bewaart wat waar,
actueel, toegestaan en bewezen is.

### 2. Wat uit Eva 1 nodig blijft

Commit `ac948b1` heeft alle 72 tabellen, 140.099 rijen, 49 capabilities, 93 operations en de
gevoelige assetklassen geclassificeerd. Alleen geselecteerde gebruikersinhoud en bewezen
contractbetekenis kunnen later als kandidaat terugkomen. Oude runtimecode, secrets, pending werk,
authority, telemetry, modellen en projecties worden niet actief overgenomen.

De twee oude blocked episodes 82 en 83 worden nooit runnable geïmporteerd. Zij blijven
commitmentreviews totdat actuele externe readbacks en een verzegelde effectreconciliatie bewijzen
dat een aparte nieuwe opdracht veilig kan worden gemaakt.

### 3. Wat we nu nieuw moeten bouwen

```text
eva2/
  src/eva/             Living Core en adapters
  connector_sdk/       taalneutrale workercontracten
  connectors/          mail, ERP, agenda, document en delivery als losse workers
  forge/               aparte quarantained releasepipeline, geen Living Core-package
  migrations/          de enige Alembic-keten
  schemas/             JSON Schema en portable exportcontracten
  tests/               contract-, process-, crash-, security- en behaviororacles
  release/             lock, OCI, SBOM, signatures en beheer-CLI
```

Eva 1 en Eva 2 delen geen imports, schema-eigenaar, secrets of processtate. Een leveranciers-SDK
mag later in Python, Node of Rust achter het workerprotocol draaien. Daarmee wordt die taal geen
tweede Living Core.

## Eén levende kern, geen scriptsysteem

De kern heeft zes samenwerkende verantwoordelijkheden:

| Verantwoordelijkheid | Betekenis |
| --- | --- |
| Presence | Vormt vanuit identiteit, relatie, gesprek en bewezen resultaten een menselijke context en stem |
| Attention | Kiest uit nieuwe input, deadlines, wachtende commitments en externe events via één durable route |
| Episode | Bewaakt doel, generatie, correctie, toestemming, deelresultaat en closure |
| Capability | Beschrijft wat een connector mag en welk bewijs nodig is |
| Experience | Bewaart herleidbare brongebeurtenissen, facts, correcties en verwijdering |
| Learning | Maakt alleen een meetbare kandidaat met canary en rollback, nooit een stille self-edit |

Er komen geen losse achtergrondtimers per functie. Alle wakes zijn getypeerde durable
Attention-items. Er komt geen tweede eventstore voor een gesimuleerd innerlijk. Eva's presence
ontstaat uit herkenbare identiteit, relaties, continuiteit, initiatief en daadwerkelijk afgeronde
commitments.

## Database en geheugen

PostgreSQL krijgt één centrale migrator en zes duidelijke namespaces:

| Namespace | Eigenaar |
| --- | --- |
| `eva_core` | identity, principals, relaties, gesprekken, ingress, episodes, commitments en aandacht |
| `eva_effects` | reads, intents, attempts, breakers, receipts, readbacks en deliveries |
| `eva_memory` | source events, temporale facts, correcties, retractions en erase |
| `eva_evidence` | typed bewijs, provenance en objectreferenties |
| `eva_projection` | wegwerpbare full-text- en readprojecties |
| `eva_telemetry` | begrensde technische metingen met retentie en rollups |

`dbos` blijft een afzonderlijk frameworkschema en is nooit Eva-data. Er komt geen DDL bij import of
startup en geen tabel per script. Een tabel bestaat alleen wanneer zij een eigen lifecycle,
constraint, retentie, exportmapping of deletepad nodig heeft. JSONB mag versioned payloads dragen,
maar geen verborgen state machine.

Geheugen begint bewust eenvoudig. Canonieke source events en current facts staan in SQL. Full-text
rangschikt alleen al toegestane fact-ID's en kan volledig worden herbouwd. Externe mail, web- en
documentinhoud blijft onbetrouwbare data en kan nooit identiteit, toestemming, policy, memory of
een effect autoriseren.

## Model, connector en effectgrens

Het model krijgt een compacte PresenceContext en kan alleen een getypeerd voorstel teruggeven.
Het krijgt geen database, connectortools, credentials, memoryframework of runtimebeheer. Iedere
modelpoging wordt vooraf duurzaam gereserveerd en er zijn geen verborgen SDK- of frameworkretries.

Een externe write volgt altijd dezelfde keten:

```text
ValidatedPlan
  -> EffectIntent
  -> indien nodig een afzonderlijke reconcile-ReadAttempt
  -> volledig voorbereide worker, capability, credential en gesloten egresslease
  -> actuele generation- en supersessionfence
  -> direct exact één connectorcall
  -> providerreceipt
  -> afzonderlijke readback-ReadAttempt waar vereist
  -> confirmation delivery naar het bevestigde ERP-adres
  -> OutcomeView
  -> host-gevalideerde natuurlijke PresenceReply
  -> outcome delivery naar het authentieke ingresskanaal
  -> twee rolgebonden deliveryreceipts
  -> episodeclosure
```

Een disconnect na remote commit is `ambiguous`. Eva doet dan eerst reconcile en readback met
dezelfde idempotency key. Zij neemt niet aan dat het effect mislukte en stuurt niet blind opnieuw.
Een 429 opent één durable breaker met hoogstens één half-open probe. Een definitieve reject krijgt
geen retry.

## Eerste verticale gebruikersopdracht

> Lees de nieuwste mail van Noor BV, zoek Noor op in het ERP, controleer wanneer we woensdag
> allebei kunnen, plan een afspraak van een half uur, maak een korte bevestiging, stuur die naar
> het bevestigde e-mailadres uit het ERP en koppel terug wat er werkelijk is gelukt.

Deze ene opdracht dwingt de hele architectuur door mail, ERP, agenda, document en delivery. Home
Assistant speelt hierin geen rol en krijgt geen speciale kernpositie.

De happy path staat vooraf vast:

- nul onnodige vragen;
- exact één afspraak van 15:00 tot 15:30;
- exact één bevestigingsdocument;
- exact één confirmation delivery naar het unieke, bevestigde ERP-adres;
- exact één outcome delivery met de terugkoppeling naar het authentieke ingresskanaal;
- exact vier rolgebonden effectbewijzen: readback voor agenda, document en confirmation delivery,
  plus een authentieke channelproof voor outcome delivery;
- één gewone Nederlandse terugkoppeling die alleen bewezen succes noemt;
- geen intern jargon en voor Eva geen en dash of em dash;
- geen ghost work, duplicate effect of verborgen retry.

Dezelfde contractrun bevat ook conflict, supersession, poisoned mail, ambigue commit, provider- en
connector-429, ERP-ambiguiteit, document- en beide deliveryrejects, vijftien crashpunten,
identityswap,
export/restore en update met wachtend werk.

## Configureerbare identiteit en normale menselijke taal

`IdentityPack/v1` bevat naam, voornaamwoorden, taal, stem, waarden, temperament, humor,
woordkeus en gespreksgrenzen. Het bevat geen credentials, effectpolicy of succesbewijs. Relaties,
ervaringen en herinneringen zijn aparte canonieke data.

Dezelfde release draait eerst met een Eva-pack en daarna met een Mira-pack. Naam en stem moeten
herkenbaar verschillen zonder bronwijziging, terwijl feiten, effecten en bewijs gelijk blijven.
Presence begint met het gebruikersresultaat, stelt alleen een noodzakelijke gebundelde vraag en
noemt technische termen alleen wanneer de gebruiker daar expliciet om vraagt.

Eva kan documentatie zoeken en een connectorkandidaat voorbereiden. Documentatie blijft
onbetrouwbare data. Connector Forge bouwt en test in quarantaine, doet eerst een read-only canary
en activeert writes pas na signature, expliciete approval, receipt/readback en geoefende rollback.
Voor betaling, voorwaarden, CAPTCHA, MFA of een menselijke identiteitsverklaring vraagt Eva de ene
noodzakelijke menselijke stap. Zij doet niet alsof deze externe grenzen niet bestaan.

## Installatie, beheer en exit

De eerste supportmatrix is bewust smaller dan de uiteindelijke portabilitydoelstelling:

- Tier 1 releasegate: Ubuntu Server 24.04.4 LTS op amd64 en arm64 met exact Docker Engine
  29.7.2;
- Podman, macOS en Windows zijn nog geen supportclaim. Zij krijgen pas een claim na een afzonderlijk
  4E-receipt op een vooraf verzegelde host-, architectuur- en engineversie;
- de eerste macOS-doelgate is macOS 15 op arm64, containerized;
- de eerste Windows-doelgate is Windows 11 24H2 op x86_64, containerized en niet native.

Een release vereist geen Git-worktree, Node, system-Python, ontwikkeltoolchain, bestaande database
of hardcoded hostuser. De `eva`-CLI beheert init, start, stop, status, diagnose, backup, export,
restore, update, rollback en uninstall. Images en dependencies zijn immutable en gesigneerd.

`eva export` maakt `EvaPortableArchive/v1` uit alleen canonical `eva_*`-data, schema's,
checksums, identity packs en versleutelde objecten. Een generieke reader moet identity,
conversations, episodes, intents, receipts, pending work en memory kunnen lezen wanneer DBOS en Eva
uitstaan. Restore gebruikt nieuwe secrets en zet connectorbindings op `reauthorization_required`.

Updates gebruiken A/B. Na cutover wordt een oude databasekopie nooit over nieuwe receipts heen
teruggezet. De vorige appversie blijft tijdens het rollbackvenster compatibel met het uitgebreide
schema. Destructieve contractmigraties volgen pas na het rollbackvenster.

## Bouwvolgorde en stopmomenten

| Tranche | Uitkomst | Vereist voor doorgang |
| --- | --- | --- |
| 4A | Nieuwe `eva2/` package-, contract-, budget-, platform- en release-skeleton met exacte lock | `CONTRACT-01`, `PLATFORM-DESIGN-01` en `SUPPLY-DESIGN-01`; nog geen proces- of installerclaim |
| 4B | Alleen de minimale migration-, domain- en repositoryslice voor de atomiciteitsproef | create-from-zero van de minimale slice, tenantisolatie en geen runtime-DDL |
| 4C | Onmiddellijke minimale DBOS-transactieproef | appmutatie plus DBOS-resultaat is aantoonbaar één commit bij crash voor en na commit; bij NO-GO wordt ADR-0001 heropend vóór schema-uitbouw |
| 4D | Volledige centrale schemas, identity/policy packs, generic reader en deterministische Noor-vertical met fixtureworkers | happy, partial, conflict, supersession, breaker, exportmapping en crashmatrix groen |
| 4E | Proces-, secret-, egress-, supply-chain-, export-, tweede-restore-, A/B- en rollbackbewijs | volledige `PLATFORM-01` en `SUPPLY-01`; nul secretlek, duplicate, verloren commitment of frameworklock-in |
| 4F | Echte Pydantic AI-adapter en beperkte Connector Forge | echte modelpogingen zichtbaar, docs blijven data, writes vereisen promotionauthority |
| 4G | Disposable echte connectorcanary | echte before/after, provider-IDs, receipts, readbacks en menselijke acceptatie |
| 5 | Alleen ondertekende Eva 1-importers | alle Step 2-gates plus row-level selectie; geen secrets, executable code of runnable oud werk |

De eerste rode harde gate stopt de tranche. Wijzigingen tijdens dezelfde run mogen het contract of
de oracle niet herschrijven. Eerst wordt vastgesteld of het om een kandidaatbeperking,
implementatiefout, fixturefout of contractfout gaat. Alleen daarna kan een nieuwe, zichtbaar
afzonderlijke run worden toegestaan.

De atomische DBOS-datasourceproef is de eerste technische stop. Als DBOS 2.30 hiervoor een tweede
applicatiecommit, sidecar of businessquery op frameworktabellen nodig heeft, is dat een
engine-intrinsieke NO-GO en wordt het ADR heropend voordat de vertical wordt gebouwd.

## Bewijs boven tests

Tests zijn noodzakelijk, maar zij accepteren Eva niet. Iedere run levert een content-addressed
evidencebundle met request, identity, context, modelattempts, effecten, receipts, readbacks,
crashpunten, before/after, natuurlijke reply, kosten en rollback.

Bewijsklassen blijven gescheiden:

- `sim`: contract en fixture;
- `core_live`: echte processen met gecontroleerde providers;
- `native_live`: echte disposable connectoraccounts en providerreadbacks;
- `resident_long`: langdurig werkelijk gebruik door de eigenaar.

Pas na alle procespoorten draait een vooraf allowlisted, omkeerbare echte canary. Pas daarna worden
dezelfde semantische cases in een controlled lane en een native lane eerlijk vergeleken met
OpenClaw, Hermes en Agent Zero. De controlled lane gebruikt hetzelfde model, budget, hardware,
fixtures, effecten en oracles. De native lane rapporteert de aanbevolen configuratie, extra setup,
operatorwerk en kosten. Er komt geen winnerclaim uit mocks, marketing of één totaalscore.

## Wat nu bewust niet wordt gebouwd

- geen Eva 1-importer of schemaovername;
- geen Home Assistant-, camera-, face-, robot-, wakeword- of voicevertical;
- geen algemene browsercomputer, shelltool of packageinstaller in de kern;
- geen memoryframework, vectors of embeddingauthority;
- geen Learning-runtime, zelfedit of automatisch geactiveerde connector;
- geen GUI- of avatarovername;
- geen productieautonomie of live accounts;
- geen native Windows- of macOS-claim;
- geen vergelijkingsoverwinning voordat echte gelijke evidence bestaat.

## Huidige autorisatie

| Handeling | Status |
| --- | --- |
| Nieuw bouwen onder `eva2/` volgens de verzegelde tranches | toegestaan na onafhankelijke Step 3-review |
| Eva 1-code importeren | niet toegestaan |
| Eva 1-data migreren | niet toegestaan |
| Productiecredentials toevoegen | niet toegestaan |
| Live connectorwrite uitvoeren | niet toegestaan zonder aparte disposable-canaryapproval |
| Eva-service starten of deployen | niet toegestaan |
| Productiepromotie | niet toegestaan |

De eerstvolgende concrete handeling is dus niet nog een architectuuronderzoek en ook niet nog een
Eva 1-reparatie. Het is tranche 4A: de schone `eva2/`-skeleton, exacte dependencylock, eigen
contracten en releasebasis, gevolgd door de DBOS-atomiciteitsproef vóór businessgedrag.
