# Eva 2 build-map voor de Living Core

Status: `draft_for_step_3_review`

Datum: 20 augustus 2026

Deze build-map specificeert wat er gebouwd moet worden en in welke volgorde. Hij bevat geen
productcode, database-migratie, testimplementatie of toestemming om Eva 1-data te importeren of
een Eva 2-service te starten.

## 1. Bindende besluiten en doel van deze stap

Deze kaart is gebonden aan:

- commit `ac948b17bf4996e918420687e52908450266711c`;
- [ADR-0001](adr-0001-eva2-living-core.md), SHA-256
  `73b53e392b8e3b65d1241bff46bb92144960a2620dbd45f3c9d3526d9f07910d`;
- [het Eva 1 naar Eva 2-transitionmanifest](eva1-to-eva2-transition-manifest-2026-08-20.md),
  SHA-256 `7ea53e81e6fb3af0304cf88119abc9bf864e1a9dbf1445bfaed6dbd18d3f8d85`;
- [het content-addressed beslisbestand](../transition/evidence/eva1-to-eva2-transition-manifest-c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c.json).

Het doel is één schone, connectorneutrale verticale implementatie. Die moet aantonen dat de
gekozen componenten samen de vereiste semantiek kunnen dragen. De afgekeurde DBOS-v1-prototypecode
en alle Eva 1-runtimecode zijn geen startpunt.

De termen in deze kaart zijn normatief:

- **canoniek** betekent dat alleen de applicatie-eigen PostgreSQL-rij de actuele Eva-toestand
  bepaalt;
- **evidence** betekent herleidbaar bewijs, maar niet automatisch toestemming of actuele
  externe waarheid;
- **projectie** betekent volledig verwijderbaar en herbouwbaar;
- **effect** betekent een handeling die buiten de lokale authority waarneembare toestand kan
  veranderen;
- **delivery** is een gespecialiseerd effect waarmee een gebruikersresultaat via een kanaal
  wordt aangeboden;
- **generation** is de monotone versie van één episode waarop plan, calls, effecten en antwoord
  zijn gebonden.

`selected_for_validation` blijft de hoogste architectuurstatus. Deze kaart maakt de stack niet
`accepted` of `production_ready`.

De geselecteerde validatiestack blijft Python 3.12.14, Pydantic 2.12.5,
`pydantic-ai-slim` 2.32.1, DBOS Python 2.30.0 en PostgreSQL 16.13. De uiteindelijke lock bevat ook
alle transitieve dependencies; een versieverschuiving heropent de betreffende bewijspoort.

## 2. Invarianten die iedere laag moet behouden

1. Alleen de hieronder als canoniek aangewezen rijen in `eva_core`, `eva_effects`, `eva_memory` en
   `eva_evidence` zijn business-authority. DBOS, modelproviders, connectors, caches,
   `eva_telemetry` en `eva_projection` zijn dat niet.
2. Een model retourneert voorstellen. Alleen de Living Core kan een plan valideren, een
   capabilitygrant uitgeven of een effect toelaten.
3. Iedere externe call heeft vooraf een duurzaam gereserveerde attempt met budget, deadline,
   generation en payloadhash. Er bestaat geen onzichtbare provider- of frameworkretry.
4. Een write of delivery krijgt vóór externe I/O een duurzaam EffectIntent en onmiddellijk vóór
   dispatch een generation- en supersessionfence.
5. Een providerreceipt bewijst hoogstens acceptatie door de provider. Alleen een passende
   readback bewijst de waargenomen externe toestand.
6. Een onduidelijke remote commit wordt `ambiguous`. Hij wordt nooit stil als `failed` of
   `accepted` opgeslagen.
7. Iedere gebruikersclaim komt uit een canonieke OutcomeView met evidenceverwijzingen. De
   natuurlijke tekst is nooit zelf het bewijs van succes.
8. Iedere persoonlijke rij heeft expliciete tenant-, installation- en principalcontext waar die
   context van toepassing is. Er bestaat geen impliciete standaardtenant en er wordt nooit op
   alleen een naam samengevoegd.
9. Connectorworkers hebben geen databasesocket, modeltoegang, identitygeheugen of algemene
   secrets. Een operation-scoped grant is noodzakelijk maar geeft geen directe DB-toegang.
10. Verwijderbare projecties en DBOS-tabellen zijn niet nodig om een portable export te lezen.
11. Geen legacy pending work, authority, receipt, secret of executable wordt door deze build
    geactiveerd.
12. Eén duurzame Attention-route verwerkt ingress, deadlines en hervattingen. Losse intervals,
    listeners en zelfstartende scripts zijn niet toegestaan.

## 3. Python packagegrenzen

De voorgestelde bronstructuur is één Python-distributie voor de Living Core en afzonderlijke
workerdistributies voor connectors:

```text
eva2/src/eva/
  contracts/            versioned Pydantic-contracten en JSON Schema-export
  domain/               pure state machines, invarianten en beslisfuncties
  application/          use-cases en poortinterfaces
  adapters/
    postgres/           SQLAlchemy Core repositories en unit-of-work
    dbos/               workflows, transactions, steps en wake-adapter
    model/              Core-client en dunne Pydantic AI-adapterentrypoint
    connectors/         supervisorclient, grants en JSON-RPC-adapterentrypoint
    credentials/        client voor de afzonderlijke credentialbroker
    objects/            versleutelde content-addressed objectopslag
    api/                FastAPI-ingress en kanaalresponse
    projection/         FTS- en readmodelbouwers
    telemetry/          lokale, begrensde observability
  bootstrap/            composition root en processtart
  operations/           eva CLI, export, restore, diagnose en migratiecoordinatie

eva2/connector_sdk/
  contracts/            minimale language-neutral JSON-RPC-wirecontracten
  conformance/          workerconformancecontract, geen providerimplementatie

eva2/connectors/
  mail/                 afzonderlijke workerdistributie
  erp/                  afzonderlijke workerdistributie
  calendar/             afzonderlijke workerdistributie
  document/             afzonderlijke workerdistributie
  channel/              afzonderlijke deliveryworkerdistributie

eva2/forge/
  pipeline/             afzonderlijke quarantained releasepipeline, geen runtimepackage
  schemas/              DocumentationSnapshot, ConnectorCandidate en PromotionDecision
  quarantine/           alleen content-addressed kandidaatoutputs, nooit actieve connectors

eva2/migrations/
  env.py                 enige Alembic-composition root voor eva_*
  versions/              één lineaire, centraal gereviewde migratieketen

eva2/schemas/
  contracts/             gegenereerde, versioned JSON Schema-bestanden
  portable/              EvaPortableArchive/v1-schema's
```

`eva` is hier uitsluitend de interne productnamespace en CLI-naam. De zichtbare naam en stem komen
altijd uit een IdentityPack en kunnen zonder bronwijziging bijvoorbeeld Eva of Mira zijn.

### 3.1 Toegestane dependencies

| Package | Mag importeren | Mag niet importeren |
| --- | --- | --- |
| `contracts` | standaardbibliotheek, Pydantic | DBOS, SQLAlchemy, Pydantic AI, FastAPI, provider-SDK's |
| `domain` | `contracts` | alle adapters, netwerk, filesystem, klok- of randomglobals |
| `application` | `contracts`, `domain`, eigen `Protocol`-poorten | concrete adapters en provider-SDK's |
| `adapters.postgres` | application-poorten, contracts, SQLAlchemy Core | DBOS-internals en provider-SDK's |
| `adapters.dbos` | application-use-cases en poorten, DBOS | domeinregels dupliceren of providerpayloads interpreteren |
| `adapters.model` | modelpoort en contracts, `pydantic-ai-slim` | repositories, connectortools, Pydantic AI memory/sessions/durable execution |
| `adapters.connectors` | connectorpoort, wirecontracten, supervisor | provider-SDK's in het coreproces, database of modeladapter |
| `adapters.credentials` | credentialpoort en opaque references | secretbytes in Core, modelcontext, JSON-RPC of evidence |
| workerdistributie | `connector_sdk`, precies één provider-SDK | `eva2/src/eva`, databasesocket, modelprovider of andere worker |
| `forge`-pipeline | publieke schema's uit `eva2/schemas`, `connector_sdk`-conformance en eigen quarantainetools | `eva2/src/eva` runtime-import, canonical database, productiecredentials, release-signing key of actieve connectorcatalogus |
| `bootstrap` | alle gekozen implementaties | businessregels |

Een statische dependencycheck wordt later een buildpoort. Een import die naar buiten door deze
grenzen heen wijst is een architectuurfailure, niet alleen een stijlprobleem.

### 3.2 Verantwoordelijkheid per kernpackage

- `contracts` bezit alle duurzame en wire-visible typen. Geen frameworktype passeert deze grens.
- `domain` bezit Presence, Attention, Episode, Capability, Experience en Learning-semantiek als
  pure functies en expliciete state machines. Learning blijft in de eerste vertical gedeactiveerd.
- `application` bezit use-cases zoals `AdmitTurn`, `ReserveModelAttempt`, `ValidatePlan`,
  `AdvanceEpisode`, `AuthorizeEffect`, `ReconcileEffect`, `PrepareDelivery` en `CloseEpisode`.
- `adapters.postgres` vertaalt uitsluitend tussen domeincontracten en centraal gemigreerde
  tabellen. Repositorymethoden starten geen eigen geneste transactie.
- `adapters.dbos` voert durable volgorde, wachten en hervatten uit. Het bevat geen canonieke
  businessbeslissing.
- `adapters.model` accepteert een begrensde purpose-scoped request en retourneert één getypeerd
  ModelDecision, DocumentDraft of ReplyCandidate. De Pydantic AI-entrypoint draait als apart
  minimaal Python-proces zonder DB- of connectorsocket. Function tools, toolsets, handoffs en
  frameworkretries staan uit.
- `adapters.connectors` verifieert worker, manifest, sandbox en grant vóór processtart en vertaalt
  geen providerresultaat naar succes zonder het vereiste bewijs. De supervisor draait buiten het
  Living Core-proces; alleen haar typed client implementeert de application-poort.
- `adapters.credentials` praat met een afzonderlijke broker die geen gesprekken, plannen of
  effectbesluiten kent. De model- en connectorgranttypen zijn niet onderling uitwisselbaar.
- `adapters.objects` bezit encryptie, contenthash, objectreceipt, readback en crypto-shred. De
  objectbytes komen niet in PostgreSQL.
- `operations` is het enige beheervlak voor install, init, status, diagnose, migrate, export,
  restore, update, rollback en uninstall.
- `forge` is een afzonderlijke, pas in 4F gebouwde releasepipeline. Zij kan publieke documentatie
  bevriezen en een connectorcandidate in quarantaine opleveren, maar importeert of start de Living
  Core niet, bezit geen productiedatabase of secrets en kan geen promotionrecord ondertekenen.

De connectorbinding bewaart alleen een opaque credentialreference; de capability activation
verwijst naar die bevestigde binding en voegt geen credentialcustody toe. Tijdens de preflight vóór
de dispatchfence wisselt de supervisor die reference via de lokale credentialbroker om voor precies
de vereiste scope. De broker geeft een kortlevende `CredentialGrant/v1` plus een eenmalige secret
descriptor uit, geen effecttoestemming. Faalt de fence, dan wordt de descriptor ingetrokken zonder
workerinvocation. Credentialbytes komen niet in JSON-RPC, argv, logs, evidence of algemene
procesenvironment. Zij worden als een read-only descriptor of containermount aan uitsluitend die
worker gegeven en na de call verwijderd. Een connectorresponse kan nooit een nieuwe
credentialreference of ruimere scope activeren.

## 4. Centrale PostgreSQL-eigendom en migraties

### 4.1 Eigendomsregel

Eén Alembic-keten bezit alle `eva_*`-schema's. Geen package voert DDL uit bij import of startup.
Geen connector maakt tabellen. DBOS beheert uitsluitend zijn eigen `dbos`-schema onder de gepinde
DBOS-versie. `eva migrate` neemt één migratielock, controleert de DBOS-schema-compatibiliteit en
orkestreert daarna DBOS-initialisatie en de ene applicatiemigratieketen. Er bestaan geen foreign
keys vanuit `eva_*` naar `dbos` en geen businessquery leest DBOS-tabellen.

De migraties volgen expand, migrate, verify, contract. Een destructieve contractstap komt nooit in
dezelfde release als de expandstap. Vóór cutover mag een afgekeurde kandidaat met haar disposable
databasekopie worden verlaten. Nadat nieuwe ingress, receipts of commitments zijn geaccepteerd,
gebruikt approllback de vorige A/B-image op de actuele expand-compatible database en wordt de oude
databasekopie nooit over nieuwe canonieke data teruggezet. De kopie en portable export blijven
disaster-recoverybewijs, geen normaal post-traffic rollbackmechanisme.

### 4.2 Relationele basisregels

- Primaire IDs zijn applicatiegegenereerde, opaque UUID's. Legacy numerieke IDs worden alleen als
  provenancewaarde bewaard en nooit als globale identiteit.
- Persoonlijke tabellen dragen `tenant_id`; installationgebonden tabellen ook `installation_id`.
  Alle unieke sleutels, provider-ID's, idempotency keys en lookups zijn minimaal tenant- en
  connectorbinding-scoped waar dat van toepassing is.
- Persoonlijke tabellen krijgen PostgreSQL Row Level Security als tweede grens. Iedere coretransactie
  zet de tenant met `SET LOCAL`; de runtimerol heeft geen `BYPASSRLS`. Composite foreign keys
  voorkomen daarnaast een verwijzing over een tenantgrens.
- Mutabele aggregates dragen een monotone `revision`. Writes gebruiken compare-and-swap met de
  verwachte revision of generation.
- Tijden zijn `timestamptz` in UTC. Een bedoelde lokale tijd bewaart daarnaast de IANA-tijdzone en
  de oorspronkelijke lokale representatie.
- Geld, aantallen en eenheden gebruiken integer of exact decimal, nooit binary float.
- Statekolommen zijn `text` met database-CHECKs die gelijklopen met versioned contractwaarden.
  JSONB is alleen toegestaan voor versioned payloads en evidence, nooit als verborgen state machine.
- Hashes zijn 32-byte SHA-256-waarden over het vastgelegde canonicalisatiecontract. Ruwe
  gebruikersbytes krijgen daarnaast een eigen bytehash.
- Canonieke foreign keys gebruiken standaard `RESTRICT`. Privacyverwijdering doorloopt een
  expliciete deletegraph. Alleen volledig herbouwbare projecties mogen cascaderen.
- Credentials, tokens, QR-data, masterkeys en gedecrypte providersecrets komen in geen enkel
  `eva_*`-schema, exportrecord, evidencepayload of logveld.
- Persoonlijke payloads en evidence hebben een numerieke `retention_until` of een versioned
  retention policy die vóór opslag een concrete deadline oplevert.
- Partities worden pas toegevoegd na een gemeten grens. Tabelaantal of rijvolume is op zichzelf
  geen reden voor extra event-, history- of schaduwtabellen.

### 4.3 Canonieke tabellen

De onderstaande tabellen zijn de volledige doelset voor de eerste verticale schemafamilie. Een
nieuwe tabel vereist een expliciete eigenaar, lifecycle, retentie, exportmapping en deletepad.

#### `eva_core`

| Tabel | Canonieke verantwoordelijkheid | Belangrijkste constraints |
| --- | --- | --- |
| `tenants` | privacy- en authoritygrens | stabiele tenant-ID, status en policyversie |
| `installations` | installatie, release en supportniveau | tenant-scoped installatie-ID, actieve release, geen secretbytes |
| `identity_pack_versions` | ondertekende identiteit en presentatie | `(installation_id, version)` uniek, bronhash, status, precies één current |
| `policy_pack_versions` | ondertekende runtime-, effect-, proof-, budget-, consent- en retentiepolicy | `(installation_id, version)` uniek, canonical hash, status en precies één current; model en connector kunnen niet schrijven |
| `principals` | bevestigde menselijke of serviceprincipal | geen name-only merge, privacyklasse en status |
| `principal_bindings` | geverifieerde kanaal- of accountbinding | provider, externe subjecthash, bevestigingsbewijs, uniek per tenant/provider |
| `relationships` | bevestigde relatie tussen principals | bron, trust, valid-time en revision |
| `conversations` | kanaalneutrale gesprekscontinuiteit | tenant, participants, current episode pointer zonder authority-cascade |
| `messages` | originele en door Eva gemaakte berichtinhoud | rol, kanaal, bytehash, provenance, contentstatus en retentie |
| `ingress_requests` | requestidentiteit en publiek resultaat | uniek `(installation_id, channel_id, request_id)`, inputhash, principal, conversation, generation |
| `episodes` | gebruikersdoel, current generation en closure | één current generation, monotone revision, getypeerde terminale uitkomst |
| `episode_generations` | planversie en supersessionfence | uniek `(episode_id, generation)`, predecessor, workflowcontractversie, status, correction message |
| `episode_events` | immutable domeintransities en commanddedupe | unieke event- en command-ID, before/after state, payloadhash |
| `episode_outcomes` | immutable OutcomeView per generation | outcome status, step- en evidencehashes; maximaal één current outcome per generation |
| `commitments` | wat nog aan de gebruiker verschuldigd is | episode, eigenaar, due/wake policy, terminale closure of expliciete blokkade |
| `consent_grants` | geauthenticeerde toestemming voor een begrensde read of effect | principal, source message/evidence, operation/resource/payloadscope, generation, expiry, revision en status |
| `model_attempts` | zichtbare modelcallreservering en evidence | purpose `plan`, `document_draft` of `reply`; uniek `(episode, generation, purpose, ordinal)`, call-ID, requesthash, model, budget, status, responsehash, usage |
| `plans` | host-issued ValidatedPlan | generation, modelproposalhash, policyhash, capability-sethash en status |
| `plan_steps` | duurzame DAG-nodes en uitkomsten | unieke logical step key per plan, dependencies, effectklasse en outcome ref |
| `attention_items` | enige duurzame wake- en hervatrij | getypeerde reden, due-at, claimlease, episode/generation en terminale disposition |
| `connector_bindings` | bevestigde installatie-, account- en providerresourcebinding | owner principal, provider/account/resource, scopes, credentialreference, verified-at en status; nooit secretbytes |
| `capability_manifests` | immutable, gesigneerde capabilitydefinitie | capability/operation/schemahash, effectklasse, proof floor, executablehash |
| `capability_activations` | installatie- en connectorbindinggebonden toelating van manifest | scope, policyversie, status, sandboxprofiel, geen credentialbytes |

#### `eva_effects`

| Tabel | Canonieke verantwoordelijkheid | Belangrijkste constraints |
| --- | --- | --- |
| `read_intents` | begrensde externe read-, reconcile-, verify- of breakerprobeopdracht | uniek `(episode_id, generation, logical_read_key)`, purpose `business_read`, `effect_reconcile`, `effect_verify` of `breaker_probe`, optionele `effect_intent_id`, planstep, capability, resource, queryhash, freshness en budget |
| `read_attempts` | iedere werkelijke read-, reconcile-, verify- of probecall | uniek `(read_intent_id, ordinal)`, dispatchstatus, deadline, connector/buildhash, eigen CapabilityGrant/CredentialGrant en geen verborgen retry |
| `read_observations` | genormaliseerde externe observatie | provider resource-ID, observed/retrieved-at, responsehash, evidence ref, trustclass |
| `effect_intents` | bevroren write- of deliverybedoeling | uniek `(episode_id, generation, logical_effect_key)`; idempotency key uniek `(tenant_id, connector_binding_id, idempotency_key)`; planstep, capability, resource, payloadhash en proof floor bevroren |
| `effect_attempts` | iedere mogelijke externe dispatch | uniek `(effect_intent_id, ordinal)`, ordinal maximaal twee in de eerste vertical, dispatchpermit, foutklasse, providerreferentie en transport evidence |
| `effect_receipts` | provideracceptatie of afwijzing | intent/attempt, provider-ID, receipt hash, accepted-at, nooit readback vervangen |
| `effect_readbacks` | onafhankelijke toestand na effect | intent, exact `read_attempt_id`, resource, observed-at, statehash, matchstatus, evidence ref |
| `deliveries` | frozen business- of resultdelivery en deliveryclosure | exact één delivery per effectintent, role `confirmation_delivery` of `outcome_delivery`, target, contenthash, reply-to en ackniveau |
| `circuit_breakers` | duurzame provider- en connectorrate-limitstate | target/scope uniek, state, retry-at, probelease en revision |

Een delivery gebruikt dezelfde `effect_attempts`, `effect_receipts` en `effect_readbacks` als ieder
ander effect. Er komt geen tweede outboxauthority naast `effect_intents`.

#### `eva_memory`

| Tabel | Canonieke verantwoordelijkheid | Belangrijkste constraints |
| --- | --- | --- |
| `source_events` | immutable brongebeurtenis | tenant, principal/subject, bron, observed-at, contenthash, trust en privacyklasse |
| `fact_assertions` | getypeerde, temporale bewering uit een bron | subject, predicate, value, valid-time, source event, trust, status |
| `current_facts` | expliciete current pointer | uniek per tenant/subject/predicate/scope, verwijst naar niet-ingetrokken assertion |
| `erasure_tombstones` | niet-herleidbaar bewijs van uitgevoerde erase | scopehash, completed-at en policyversie, geen verwijderde payload |

Mail-, web- en documentinhoud start altijd als onbetrouwbare `source_event`. Zij kan nooit zonder
policy en provenance een directe gebruikerscorrectie overstemmen of een effect autoriseren.

Geautoriseerde privacy-erasure is de enige uitzondering op append-only. Eén deletecommand
vergrendelt de scope, verwijdert current pointers, assertions, source events, messagepayloads en
evidence volgens de expliciete reachabilitygraph, laat objectkeys crypto-shredden, verwijdert alle
projecties en schrijft pas daarna een niet-herleidbare erasuretombstone. Back-upretentie moet
dezelfde numerieke deadline afdwingen. Een tombstone bevat geen oude principal-, object- of
contenthash waarmee de verwijderde persoon opnieuw kan worden herkend.

#### `eva_evidence`

| Tabel | Canonieke verantwoordelijkheid | Belangrijkste constraints |
| --- | --- | --- |
| `evidence_envelopes` | typed metadata voor request, response, receipt en readback | contractversie, payload/objecthash, producer, captured-at, privacy en retentie |
| `evidence_links` | gerichte provenanceketen | uniek `(from, to, relation)`; relaties die als acyclisch zijn gedeclareerd worden daarop gevalideerd |
| `object_refs` | metadata voor versleutelde CAS-objecten | contenthash, bytecount, MIME, owner, encryptionref, retentie, erase status |

#### Niet-canonieke schema's

| Schema en tabellen | Gebruik | Exportgedrag |
| --- | --- | --- |
| `eva_projection.message_search`, `fact_search`, `projection_checkpoints` | FTS en readmodels | niet exporteren, volledig rebuilden |
| `eva_telemetry.diagnostic_events`, `metric_rollups` | gesaneerde lokale diagnose met korte retentie | standaard niet exporteren, nooit outcome-authority |
| `dbos.*` | gepinde frameworkadministratie | nooit als EvaPortableArchive-record exporteren |

### 4.4 Migratievolgorde

| Wave | Inhoud | Exit van de wave |
| --- | --- | --- |
| `0000` | schema's, rollen, migratielock en versieledger | alleen migrator heeft DDL-rechten; runtime- en workerrollen niet |
| `0001` | tenant, installation, IdentityPack, PolicyPack, principals, bindings, relationships en RLS-basis | twee tenants kunnen geen binding, identiteit, policy of relatie zien of refereren |
| `0002` | conversations, messages en ingress | duplicate/conflictconstraints bestaan vóór API- of modelcode |
| `0003` | episodes, generations, events, outcomes, commitments, consent, modelattempts, plans, steps, attention | iedere state heeft een DB-CHECK; iedere transitionguard heeft een domeincommand en CAS-key |
| `0004` | connectorbindings, capability manifests, activations, reads, effects, deliveries, breakers | geen effect kan zonder current generation, bevestigde binding en actieve capability worden gereserveerd |
| `0005` | evidence, links en objectrefs | alle effect- en readresultaten hebben typed provenance en retentie |
| `0006` | memoryauthority en erasure | current fact, correction, retraction en erase zijn zonder projectie uitvoerbaar |
| `0007` | projecties en begrensde telemetry | drop/rebuild verandert geen canonieke uitkomst |

### 4.5 Export-, retentie- en deleteclassificatie

| Recordfamilie | Portable export | Retentie en delete-eigenaar |
| --- | --- | --- |
| tenant, installation, IdentityPack en PolicyPack | ja, zonder hostpad of keymateriaal | installatiebeheer; packs zijn versioned en kunnen worden superseded of ingetrokken |
| principals, bindings en relationships | ja, externe IDs alleen scoped | privacyservice; principal erase trekt bindingen en relationele feiten in |
| conversations en messages | ja, originele bytes plus provenance | conversation/principalpolicy; payload, search en backupkopie volgen dezelfde deadline |
| episodes, generations, events, outcomes, commitments en consent | ja, inclusief nonterminale continuationstate | Episode-eigenaar; consent kan verlopen, worden verbruikt of ingetrokken; privacyerase verwijdert persoonlijke payload maar behoudt alleen toegestane niet-herleidbare auditdata |
| modelattempts, plans en steps | ja voor continuation en verklaarbaarheid, geen providersecret | episodepolicy en model-evidencepolicy |
| connectorbindings, capability manifests en activations | ja, signatures, hashes en accountmetadata; credentialrefs worden op restore vervangen door `reauthorization_required` | installation/releasebeheer en providerreauthorisatie |
| reads, effects, receipts, readbacks, deliveries en breakers | ja voor proof en pending work | episode plus effectpolicy; providerresource wordt waar nodig afzonderlijk verwijderd |
| memory source events, assertions en current facts | ja | memory/privacyservice volgens de expliciete erasegraph |
| evidence en objectrefs | metadata en benodigde encrypted objects | evidence/objectservice; crypto-shred en backupdeadline |
| projecties, telemetry en `dbos` | nee | drop/rebuild, korte expiry of frameworkbeheer |

De precieze numerieke standaardtermijnen worden in de pre-code privacy-policyfreeze vastgelegd en
komen daarna als versioned policy-ID plus concrete `retention_until` in ieder toepasselijk record.
Een importer mag geen ontbrekende deadline met een productdefault invullen.

## 5. Versioned kerncontracten

Alle contracten hebben `schema_version`, een canonical payloadhash en alleen language-neutral
velden. De JSON-vorm gebruikt een vooraf bevroren `EvaCanonicalJSON/v1`-profiel. Ruwe providerdata
blijft een evidenceobject en wordt niet ongemerkt onderdeel van het domeinmodel.

| Contract | Minimale velden en functie | Authority |
| --- | --- | --- |
| `IdentityPack/v1` | naam, voornaamwoorden, taal, stem, waarden, stijl, grenzen, kanaalpresentatie, bronhash, handtekening | identitybron, nooit effect- of proofpolicy |
| `PolicyPack/v1` | effectklassen, consentexempties, proof floors, budgets, retry-, retention- en privacyregels, bronhash en releasesignature | hostpolicy voor exact één versie; kan nooit gebruikersconsent verruimen |
| `TurnEnvelope/v1` | installation, authentieke principal binding, channel/request/message, conversation, raw bytehash, received-at | inputbron na hostvalidatie |
| `IngressOutcome/v1` | admitted, duplicate, conflict of rejected; canonical request/episode/generation ref | host-issued ingressbesluit |
| `PresenceContext/v1` | IdentityPackversie, relatie, compacte confirmed facts, gesprek, bewijsstatus en tokenbudget | begrensde modelcontext, geen nieuwe waarheid |
| `ContextSelection/v1` | tenant/principal/conversation/doel, geselecteerde source refs, uitsluitredenen en contexthash | audit van toegelaten context, nooit effectauthority |
| `ModelDecision/v1` | clarify, request_read, propose_effect of wait; argumenten en onzekerheid | planvoorstel zonder uitvoeringsmacht; replytekst hoort uitsluitend in `ReplyCandidate/v1` |
| `ValidatedPlan/v1` | episode/generation, typed DAG, budgets, capabilities, policyhash, input/output schemahashes | lokale planningsauthority, geen externe effectauthority |
| `ConsentGrant/v1` | geauthenticeerde principal, source ref, operation/resource/payloadscope, generation, deadline en reuse policy | gebruikersauthority binnen exact die scope |
| `ConnectorBinding/v1` | installatie, owner principal, provider/account/resource IDs, bevestigde scopes, credentialreference, verificatiebewijs, verified-at en status | host-bevestigde externe route, nooit consent, effecttoestemming of secretbytes |
| `CapabilityManifest/v1` | capability, operation, schemas, effectklasse, scopes, proof floor, freshness, executable- en sandboxhash | definieert wat bewijsbaar kan worden aangeboden |
| `CapabilityGrant/v1` | één operation, resource, payloadhash, generation, deadline, nonce en policyhash | kortstondige toestemming voor precies één call |
| `CredentialGrant/v1` | binding, worker/executablehash, nauwere providerscope, secret-descriptor-ID, deadline en nonce | broker-issued secretdelivery; nooit zelfstandig call- of effectauthority |
| `ReadIntent/v1` | purpose, optionele effectintent, resource/query, freshness, generation, budget en capability | toelating voor precies één businessread, reconcile, verify of breakerprobe, geen feit |
| `ReadObservation/v1` | provider resource-ID, retrieved/observed-at, normalized value, responsehash, trust en evidence | externe observatie binnen freshness, geen effectbewijs |
| `EffectIntent/v1` | episode/generation, operation/resource, frozen payloadhash, idempotency key en proof floor | enige lokale write-intentie |
| `EffectAttempt/v1` | attempt-ID/ordinal, dispatchpermit, deadline, connectorhash en foutklasse | audit van mogelijke I/O, niet van succes |
| `EffectReceipt/v1` | provider-ID, accept/reject, receipt hash en provider time | provideracceptatie, niet automatisch actuele toestand |
| `EffectReadback/v1` | exact effectintent, readattempt, resource, observed-at, statehash en matchstatus `matches`, `not_observed`, `conflict` of `inconclusive` | bewijs van waargenomen externe toestand |
| `DeliveryIntent/v1` | role `confirmation_delivery` of `outcome_delivery`, frozen recipient, channel, contenthash, reply-to en proof floor | gespecialiseerd EffectIntent; de twee rollen zijn nooit dezelfde intent |
| `DeliveryReceipt/v1` | delivery/attempt, provider message-ID, accepted-at, ackniveau en provider-, server- of clientbewijs | gespecialiseerd EffectReceipt volgens de vooraf bevroren channel proof floor |
| `DocumentDraft/v1` | MIME, title, natuurlijke inhoud, source refs, privacy, owner en retention | modelvoorstel voor documentbytes, geen writeauthority |
| `OutcomeView/v1` | per step completed, partial, ambiguous, blocked of failed met evidence refs | enige bron voor completion- en replyclaims |
| `ReplyCandidate/v1` | natuurlijke tekst, claimrefs, unresolved refs, identityversie en stijlresultaat | machteloos modeltekstvoorstel |
| `PresenceReply/v1` | host-gevalideerde ReplyCandidate, current OutcomeView, identityversie en claimbindings | canonieke assistantmessage die als aparte `outcome_delivery` wordt aangeboden |
| `EvidenceEnvelope/v1` | producer, contract, payload/objecthash, provenance, captured-at, privacy en retention | herleidbaarheid, nooit zelfstandig toestemming |
| `EvaPortableArchive/v1` | canonical JSONL per contracttype, schema's, checksums, IdentityPacks en encrypted objects | frameworkonafhankelijke exitvorm |

De modeladapter mag alleen `ModelDecision/v1`, `DocumentDraft/v1` of
`ReplyCandidate/v1` produceren. Een model mag geen `ValidatedPlan`, `CapabilityGrant`, receipt,
readback of OutcomeView uitgeven.

Een geaccepteerde ReplyCandidate is uitsluitend input voor hostvalidatie. Alleen de resulterende
`PresenceReply/v1` wordt vóór outcome delivery als canonieke assistant `messages`-rij aan precies
één episode, generation en `episode_outcomes`-rij gebonden. Daardoor levert replay dezelfde bytes
en dezelfde claims. Een afgewezen kandidaat kan alleen als begrensde modelattempt-evidence worden
bewaard en wordt nooit conversation- of deliverycontent.

## 6. Authority en bewijsbeslissing

| Vraag | Enige beslissende bron | Wat uitdrukkelijk niet voldoende is |
| --- | --- | --- |
| Is dit dezelfde request? | scoped request-ID plus canonical inputhash in `ingress_requests` | alleen DBOS workflow-ID of gelijke tekst |
| Wie vraagt dit? | bevestigde `principal_binding` uit de authentieke ingressadapter | naam, mailinhoud of modelinferentie |
| Welk plan is current? | current `episode_generation` plus host-issued `plans`-rij | laatste modelantwoord of DBOS-status |
| Heeft de gebruiker deze handeling toegestaan? | current, niet-verlopen `consent_grant` of vooraf vastgelegde policy-exemptie voor exact dezelfde scope | IdentityPackvoorkeur, oud consent, stilte of modelinferentie |
| Naar welk extern account of resource gaat de call? | actieve, host-bevestigde `connector_binding` plus de resource uit plan/grant | providernaam, modelkeuze, credentialinhoud of connectorinferentie |
| Mag deze operation? | current gesigneerde PolicyPackversie, actieve manifestversie, current generation en CapabilityGrant | toolnaam in prompt, IdentityPackvoorkeur of workerclaim |
| Is externe I/O nog toegestaan? | atomische dispatchfence direct vóór call | eerder plan, process-local flag of cache |
| Accepteerde de provider de write? | EffectReceipt met provider-ID | HTTP 200 zonder gebonden payload/resource |
| Is de gewenste toestand werkelijk zichtbaar? | passende, frisse EffectReadback | receipt, oude readback of modeltekst |
| Is het bericht aangeboden? | DeliveryReceipt volgens channel proof floor | gegenereerde reply of lokale outboxrij |
| Is de episode klaar? | persisted `episode_outcomes`-OutcomeView plus deliveryclosure | groen planstepje, modelclaim of DBOS completion |
| Is een memoryfeit current? | `current_facts` naar een geldige assertion | FTS-hit, embedding, mailtekst of legacy memory |

## 7. Canonieke state machines

### 7.1 Ingress en requestidentiteit

De unieke linearisatie ligt op `(installation_id, channel_id, request_id)`.

| Bestaande toestand | Nieuw aanbod | Besluit vóór model of connector |
| --- | --- | --- |
| geen rij | geldige binding en input | atomair `admitted`, episode/generation en Attention-item maken |
| zelfde scoped request-ID en zelfde inputhash | duplicate of replay | bestaande canonieke uitkomst of pending-ref retourneren, geen nieuwe modelcall |
| zelfde scoped request-ID en andere inputhash | conflict | getypeerd conflict vastleggen, originele rij niet wijzigen, nul model- en connectorcalls |
| onbekende of onbevestigde principal | iedere input | rejected of noodzakelijke bevestiging, geen effectgrant |

Een crash na admission maar vóór workflowstart verliest geen opdracht. Het bestaande Attention-item
start later dezelfde deterministische workflow-ID. Twee starters mogen daardoor nog steeds maar
één modelattempt met dezelfde purpose en ordinal voor dezelfde episode/generation reserveren.

### 7.2 Episode en generation

Episode states:

```text
accepted -> planning -> executing -> reporting -> completed
                      |           |            -> partially_completed
                      |           -> waiting_user
                      |           -> waiting_external
                      -> blocked
accepted/planning/executing/reporting/waiting_user/waiting_external/blocked -> cancelled
waiting_user -- confirmed input or correction --> planning
waiting_external -- typed wake or reconciliation --> executing
blocked -- newly granted authority or explicit decision --> planning
```

- `completed` vereist dat alle verplichte steps hun proof floor halen en dat deliveryclosure
  bestaat.
- `partially_completed` benoemt exact welke uitkomsten bewezen zijn en welke niet. Het sluit alleen
  wanneer het deliverybeleid een eerlijk deelresultaat toestaat.
- `blocked` en beide waitingstates bewaren het commitment, de volgende wake en de ontbrekende
  authority. Zij worden niet als failure weggegooid.
- Hervatten maakt of bevestigt een current generation in dezelfde transactie die de episode weer
  `planning` of `executing` maakt. Een wake alleen kan geen ontbrekende toestemming verzinnen.
- Een terminale episode wordt nooit door een timer heropend. Vervolgwerk krijgt een nieuwe episode
  met expliciete lineage.

Generation states zijn `current`, `superseded`, `sealed`, `blocked` en `cancelled`. Een correctie
maakt in één PostgreSQL-transactie generation `g+1` current en `g` superseded. Plan-, model-, read-,
effect- en replyresultaten van `g` mogen daarna wel als historical evidence worden vastgelegd, maar
niet meer als current resultaat worden geprojecteerd.

### 7.3 IdentityPack, PolicyPack en Presence

IdentityPack- en PolicyPackstates zijn `staged`, `current`, `superseded` en `revoked`.

```text
staged -> current -> superseded
staged|current -> revoked
```

Activatie vervangt per packtype in één transactie de vorige `current` door de nieuwe, gesigneerde
versie. Een pack kan niet in-place wijzigen. Wanneer een current IdentityPack om
veiligheidsredenen wordt ingetrokken zonder geldige opvolger, blokkeert Eva nieuwe modelruns en
deliveries in plaats van een hardcoded identiteit te gebruiken. Ontbreekt een geldige current
PolicyPack, dan blokkeert iedere nieuwe read, effectgrant en delivery. Iedere PresenceContext,
modelattempt, assistantmessage en ReplyCandidate bewaart de exacte IdentityPackversie; ieder plan,
grant, intent en retentionbesluit bewaart de exacte PolicyPackversie en hash. PresenceContext zelf
is een immutable selectie per modelattempt en heeft daarom geen mutabele lifecycle.

### 7.4 Consent

Consent states zijn `active`, `consumed`, `revoked`, `expired` en `superseded`.

```text
active -> consumed
active -> revoked
active -> expired
active -> superseded
```

Een gebruikersuiting kan alleen door de geauthenticeerde ingressroute een ConsentGrant worden.
Het model en een connector mogen consent hoogstens voorstellen als ontbrekende authority. Een
correctie met nieuwe payload, resource of generation supersedet oud consent tenzij de oorspronkelijke
reuse policy die variant expliciet en veilig omvat. De dispatchfence valideert consent opnieuw in
dezelfde transactie als `dispatch_authorized`.

### 7.5 Connectorbinding

Connectorbindingstates zijn `pending_confirmation`, `active`, `reauthorization_required`,
`disabled` en `revoked`.

```text
pending_confirmation -> active
active -> reauthorization_required -> active
active -> disabled -> active
pending_confirmation|active|reauthorization_required|disabled -> revoked
```

Alleen hostverificatie met een principalbinding en providerbewijs mag `active` maken. Een
credentialfout maakt de binding `reauthorization_required`; een connector mag zichzelf niet
herbinden. Restore importeert accountmetadata maar zet iedere binding met een niet-overdraagbare
credentialreference op `reauthorization_required`. `revoked` is terminaal en blokkeert nieuwe
reads, effects en CredentialGrants; reeds vastgelegde evidence blijft onder haar retentiebeleid.

### 7.6 Capabilitymanifest en activation

Manifeststates zijn `staged`, `active`, `retired` en `revoked`.

```text
staged -> active -> retired
staged|active -> revoked
```

Activationstates zijn `inactive`, `active`, `suspended`, `superseded` en `revoked`.

```text
inactive -> active -> suspended -> active
inactive|active|suspended -> superseded
inactive|active|suspended -> revoked
```

Manifestrecords zijn immutable en alleen een geldige releasesignature kan `active` maken. Een
activation bindt exact manifest-, executable-, sandbox-, connectorbinding-, scope- en policyhash.
Een scope- of hashwijziging maakt een nieuwe activationrevision en supersedet de oude; zij verruimt
nooit een bestaand record. `retired`, `superseded` en `revoked` mogen geen nieuwe grants opleveren.
De dispatchfence hercontroleert de `active` binding, manifest en activation binnen dezelfde
transactie.

### 7.7 Planstep

Een plan heeft de gesloten states `validated`, `current`, `superseded`, `completed`, `blocked` en
`cancelled`: `validated -> current -> completed|blocked`, terwijl `validated|current` ook naar
`superseded|cancelled` kan. Alleen een host-issued ValidatedPlan kan `validated` worden en slechts
één plan per current generation kan `current` zijn. Een modelproposal wordt niet als planstate
opgeslagen.

```text
pending -> ready -> running -> succeeded
                         |  -> partial
                         |  -> blocked
                         |  -> failed_terminal
pending/ready/running -> superseded
pending/ready         -> cancelled
```

Een step wordt alleen `ready` wanneer alle harde dependencies een toegestane outcome hebben.
`running` geeft geen bewijs van externe dispatch of succes. Voor reads en effecten is uitsluitend
de bijbehorende attemptstate beslissend.

### 7.8 Commitment en Attention

Commitmentstates zijn `open`, `waiting`, `blocked`, `fulfilled` en `cancelled`.

```text
open -> waiting -> open
open|waiting -> blocked -> open
open|waiting|blocked -> fulfilled
open|waiting|blocked -> cancelled
```

`fulfilled` en `cancelled` zijn terminaal. Een episode kan alleen sluiten wanneer ieder commitment
terminaal is of in dezelfde closuretransactie expliciet naar een nieuwe episode is overgedragen.
Een processcrash, dead letter of technisch foutrecord annuleert nooit stil een commitment.

Attention gebruikt de volgende gesloten states:

```text
pending -> claimed -> completed
                  -> waiting
                  -> dead_letter
claimed -- lease expired --> pending
waiting -- typed wake --> pending
```

De Attention-claim zelf doet nooit externe I/O. Een verlopen claim mag daarom opnieuw worden
opgepakt. Iedere externe call heeft daarnaast zijn eigen attempt en reconciliationsemantiek.
`dead_letter` behoudt het commitment en vereist een zichtbare operator- of gebruikersbeslissing.

### 7.9 Modelattempt

```text
reserved -> dispatch_authorized -> responded
                              -> ambiguous
                              -> provider_rejected
                              -> rate_limited
reserved -> budget_exhausted
```

Call-ID, requesthash, model, token- en tijdsbudget en generation worden vóór de HTTP-call gecommit.
Een crash na providerrespons maar vóór lokale responsecommit wordt `ambiguous`. Er is geen stille
Pydantic AI- of providerretry. Aantoonbare provider-idempotency of een geattesteerde cache mag
hetzelfde attemptresultaat herstellen zonder nieuwe providercall. Anders blokkeert deze eerste
vertical de modelstap zichtbaar. De enige nieuwe modelattempt is plan ordinal twee na de exact
verzegelde provider-429 en breakertermijn; een gewone ambiguous uitkomst opent die route niet.

### 7.10 Readattempt

```text
prepared -> dispatch_authorized -> observed
                               -> ambiguous
                               -> rejected
                               -> rate_limited
```

Een read mag alleen als een nieuwe expliciete attempt worden herhaald, met deadline, budget en
freshness. Mail- of webcontent wordt als onbetrouwbare data gelabeld en kan geen nieuwe instruction,
grant of policy opleveren.

`effect.reconcile`, onafhankelijke effectreadback en een breakerprobe zijn geen verborgen helpercall.
Elk krijgt vooraf een eigen `ReadIntent/v1` en `ReadAttempt/v1`, purpose, operation-scoped
CapabilityGrant, afzonderlijke CredentialGrant, deadline en budget. Een `EffectReadback/v1`
verwijst verplicht naar de exacte ReadAttempt die de observatie deed. Deze calls tellen mee in het
connectorbudget. Een reconcile- of probecall kan nooit zelf een write autoriseren.

### 7.11 EffectIntent en effectattempt

`effect_intents.status` gebruikt uitsluitend `prepared`, `dispatch_authorized`, `waiting_breaker`,
`ambiguous`, `not_observed`, `provider_accepted`, `verified`, `accepted_unverified`,
`provider_rejected`, `manual_reconciliation`, `blocked`, `superseded` en `cancelled`.
`effect_attempts.status` gebruikt uitsluitend
`reserved`, `dispatch_authorized`, `provider_accepted`, `definitive_reject`, `ambiguous_transport`,
`rate_limited`, `policy_denied`, `hash_conflict` en `internal_failure`. De attempt bewaart wat met
één mogelijke dispatch gebeurde. De intent bewaart de samengevoegde, current uitkomst over maximaal
twee attempts en de bijbehorende readbacks.

```text
prepared -> dispatch_authorized -> provider_accepted -> verified
          |                   |                     -> accepted_unverified
          |                   -> provider_rejected
          |                   -> waiting_breaker -- one bounded resume --> dispatch_authorized
          |                   -> ambiguous -> verified
          |                                -> not_observed -- one guarded retry --> dispatch_authorized
          |                                -> manual_reconciliation
          -> blocked
          -> superseded
          -> cancelled
```

- `superseded` en `cancelled` zijn alleen toegestaan vóór `dispatch_authorized`.
- `dispatch_authorized` is het veilige onzekerheidspunt. Na een crash wordt aangenomen dat remote
  I/O kan zijn gestart, ook wanneer het proces mogelijk vóór de call stopte.
- `provider_accepted` wordt alleen `verified` na de vereiste readback.
- `accepted_unverified` is toegestaan wanneer de capability vooraf verklaart dat onafhankelijke
  readback niet bestaat. De gebruikersclaim moet dan letterlijk provideracceptatie en de ontbrekende
  verificatie onderscheiden.
- Foutklasse `ambiguous_transport` kan na een concluderend `not_observed`-reconcile_result uit een
  canonieke `effect_reconcile`-ReadAttempt één nieuwe attempt krijgen. De idempotency key blijft
  gelijk en attempt ordinal wordt maximaal twee.
  Dit pad is alleen automatisch wanneer de provider de key afdwingt, of wanneer een stabiele
  applicatiesleutel een lookup oplevert die afwezig, aanwezig-met-dezelfde-hash en conflict
  ondubbelzinnig onderscheidt. Anders blijft de intent `manual_reconciliation`.
- Een `rate_limited` attempt maakt de intent `waiting_breaker`. Alleen een geslaagde half-open
  readattempt met purpose `breaker_probe`, een nog geldige deadline en het vooraf vastgelegde
  episodebudget mogen in de eerste vertical één nieuwe effectattempt openen. Een tweede rate limit
  maakt deze intent `blocked`, zodat geen retrylus kan ontstaan.
- Een 429, 422, policyweigering, hashconflict of programmeerfout wordt nooit generiek door DBOS
  herhaald.
- Een batchwrite wordt vóór dispatch opgesplitst in één intent per zelfstandig resource-effect.
  Daardoor wordt een deelresultaat niet verborgen in één succesvol batchrecord.

Iedere mogelijke write gebruikt exact deze volgorde:

1. Als eerdere externe I/O mogelijk is geweest, reserveert en voltooit Core eerst een afzonderlijke
   reconcile-ReadAttempt. `found_same`, `not_observed`, `conflict` en `inconclusive` worden
   canoniek vastgelegd. Alleen `not_observed` kan, binnen policy, een nieuwe writeattempt openen.
2. Core reserveert het EffectAttempt. Supervisor, executable, manifest, sandbox, protocolhandshake,
   capability, credentialdescriptor en gesloten egresslease worden volledig voorbereid zonder de
   effectcall aan de worker vrij te geven.
3. De dispatchfence commit met CAS op current generation, intentrevision, capabilityactivation,
   consent en deadline. Bij failure worden worker en credentialdescriptor ingetrokken en is er nul
   effect-I/O.
4. Na een geslaagde fence geeft de supervisor direct exact één `effect.execute` met dezelfde
   attempt en grant vrij. Er staat geen sleep, queue, reconcile, brokerlookup, workerstart of andere
   planbare stap tussen fence en call.
5. Receipt of foutklasse wordt canoniek gecommit. Een onafhankelijke verifycall krijgt daarna een
   eigen ReadIntent/ReadAttempt en grant; haar observatie wordt via `read_attempt_id` aan
   EffectReadback gebonden.

Een breakerprobe volgt hetzelfde readpad vóór een eventuele nieuwe writeattempt. Zij is nooit een
effectattempt en kan de providerstate niet veranderen.

### 7.12 Delivery

Delivery volgt de effectmachine. `deliveries.status` gebruikt uitsluitend `prepared`,
`dispatch_authorized`, `provider_accepted`, `acknowledged`, `ambiguous`, `failed_terminal`,
`superseded` en `cancelled`. Het capabilitymanifest bepaalt of provideracceptatie, serverreadback
of een authentieke client-ACK de proof floor is. Een crash na deliveryacceptatie en vóór
episodeclosure hervat vanaf de bestaande receipt en verstuurt het bericht niet opnieuw.

De eerste vertical bevat twee afzonderlijke deliveries. `confirmation_delivery` verstuurt het
bevestigingsdocument naar het unieke, bevestigde ERP-adres en moet vóór OutcomeView haar eigen
receipt en eventuele readback hebben. `outcome_delivery` biedt daarna de host-gevalideerde
PresenceReply aan het authentieke ingresskanaal aan. Beide hebben een eigen EffectIntent,
CapabilityGrant, CredentialGrant, idempotency key, attempt, target, contenthash en proof floor. Een
receipt voor de ene role kan de andere nooit sluiten.

Een oude generation mag na supersession geen reply meer afleveren. Wachtende clients van die
generation krijgen een getypeerde `superseded`-uitkomst met een verwijzing naar de current
generation, nooit het oude succesantwoord.

### 7.13 Circuit-breaker

```text
closed -- 429 --> open(until)
open -- due durable wake and probe lease --> half_open
half_open -- success --> closed
half_open -- 429/transport failure --> open(new bounded until)
```

Er is precies één half-open probelease per target en scope. `Retry-After` wordt begrensd door policy.
Een open breaker maakt één Attention-wake en geen minuutlus of nieuwe workepisode per controle.

### 7.14 Memoryassertion en erasure

Assertionstates zijn `active`, `superseded` en `retracted`.

```text
active -> superseded
active -> retracted
```

Een directe geauthenticeerde correctie voegt een nieuwe assertion toe en verplaatst in dezelfde
transactie de `current_facts`-pointer nadat de vorige assertion `superseded` is. Een retraction
verwijdert de pointer wanneer geen geldige opvolger bestaat. Historische antwoorden selecteren op
valid-time en provenance, niet op de current pointer alleen. Een privacyerase is geen status:
source events, assertions, pointers en payloads worden volgens de deletegraph verwijderd en pas na
crypto-shred ontstaat de niet-herleidbare tombstone. Geen projectie mag een assertion activeren,
superseden of intrekken.

### 7.15 Gesloten fouttaxonomie

Iedere model- of connectoruitkomst wordt vóór een workflowbeslissing een van deze klassen:

| Foutklasse | Betekenis | Toegestane vervolgstap |
| --- | --- | --- |
| `ambiguous_transport` | externe acceptatie kan hebben plaatsgevonden | readback of reconcile, nooit blind resend |
| `rate_limited` | geldige 429 of equivalente providerlimiet | breaker openen en één durable wake plannen |
| `definitive_reject` | provider wijst payload of resource definitief af | terminale stepuitkomst en eerlijk deelresultaat |
| `policy_denied` | hostpolicy of grant weigert de call | nul extern verkeer, eventueel noodzakelijke menselijke keuze |
| `hash_conflict` | request, resource of payload wijkt af van de bevroren hash | fail-closed, geen retry |
| `transient_read_failure` | read heeft aantoonbaar geen write-effect | nieuwe expliciete readattempt binnen budget en deadline |
| `internal_failure` | contract-, programmeer- of sandboxfout | terminale blokkade en diagnose, geen frameworkretry |

Een worker- of SDK-exception die niet veilig naar precies één klasse kan worden vertaald wordt
`internal_failure`, behalve wanneer mogelijke remote acceptatie niet kan worden uitgesloten. Dan
wordt hij fail-safe `ambiguous_transport`.

## 8. Minimale DBOS-integratie

### 8.1 Toegestane subset

DBOS wordt alleen gebruikt voor:

- één workflow per `episode_id:generation`;
- transactionstappen voor atomische lokale state transitions;
- external steps met expliciet uitgeschakelde automatische retries;
- durable sleep of wait voor een getypeerde Attention-wake;
- hervatten na procesuitval.

DBOS queues, Conductor, businessdata, frameworkmemory, modeltools en een tweede scheduler zijn geen
onderdeel van de eerste vertical.

### 8.2 Eén lokaal commitpunt

Een transactionstap ontvangt een immutable commandcontract. De DBOS datasource opent de
PostgreSQL-transactie. Alle SQLAlchemy Core-repositories in die stap gebruiken exact de door DBOS
geleverde connection of session. Zij mogen geen eigen enginecommit, autocommit of geneste
transactie starten. De adapter zet op diezelfde connection eerst `SET LOCAL` voor de geverifieerde
tenantcontext; een ontbrekende of afwijkende tenantcontext faalt vóór iedere query.

De applicatiemutatie, het immutable `episode_events`-record en het DBOS-transactieresultaat moeten
daardoor in één PostgreSQL-commit vallen. Een crash vóór commit laat geen van drie achter. Een
crash na commit laat alle drie achter en DBOS retourneert bij replay hetzelfde transactieresultaat.

Dit is nog niet bewezen door de afgekeurde v1. De eerste implementatieproef moet vóór alle overige
workflowcode aantonen dat DBOS Python 2.30 deze datasourcegrens werkelijk draagt. Wanneer hiervoor
een tweede applicatiecommit, sidecar of directe DBOS-tabelquery nodig blijkt, is dit een
engine-intrinsieke NO-GO en wordt ADR-0001 heropend.

### 8.3 Workflowvorm

De workflow orkestreert uitsluitend reeds getypeerde use-cases:

1. lees canonieke episode/generation en reserveer de volgende command of attempt in een
   transactionstap;
2. voer hoogstens één model- of connectorcall uit in een external step;
3. classificeer ieder ontbrekend lokaal resultaat na mogelijke I/O als ambiguous;
4. commit receipt, readback of foutklasse in een nieuwe transactionstap;
5. bereken de volgende ready steps uit de canonieke DAG;
6. wacht duurzaam of bouw OutcomeView en de twee rolgebonden deliveries;
7. sluit pas na alle door policy vereiste confirmation- en outcome-deliveryreceipts.

DBOS workflow-ID is een herstelpointer, geen requestidentiteit. De canonieke tabellen bevatten
voldoende state om pending work te exporteren, te lezen en na expliciete reconciliation onder een
andere executor te hervatten. Geen portable record bevat een noodzakelijk DBOS-intern ID.

### 8.4 Retrybeleid

- DBOS external steps hebben één frameworkattempt.
- Een eventuele tweede effectattempt wordt door de Living Core als nieuw canoniek attemptobject
  gemaakt. Dat mag alleen na een concluderend `not_observed`-reconcile_result uit een canonieke
  `effect_reconcile`-ReadAttempt bij ambiguous transport, of
  na een geslaagde half-open breakerprobe bij 429. De idempotency key blijft gelijk en de eerste
  vertical staat nooit een derde attempt voor dezelfde intent toe.
- Transactionstappen mogen alleen begrensd opnieuw worden uitgevoerd voor een zuiver lokale
  serialization- of deadlockfout. Zij bevatten geen externe I/O en gebruiken hetzelfde command-ID.
- Model-, provider- en connector-SDK-retries staan uit.
- Iedere timeout, 429, retrybeslissing en budgetafschrijving wordt canoniek zichtbaar.

## 9. Crash, replay en supersession

### 9.1 Linearisatiepunten

| Beslissing | Linearisatiepunt |
| --- | --- |
| duplicate of conflict | commit van unieke `ingress_requests`-rij en inputhashvergelijking |
| current plan | commit die exact één episode generation current maakt |
| effect mag mogelijk starten | commit van `dispatch_authorized` na generationfence |
| provider accepteerde | commit van gebonden EffectReceipt |
| gewenste toestand bewezen | commit van passende EffectReadback |
| antwoord mag current zijn | OutcomeView voor dezelfde current generation |
| episode gesloten | commit van terminale episode plus vereiste deliveryclosure |

### 9.2 Verplichte crashmatrix

| Crashpunt | Verplichte replayuitkomst |
| --- | --- |
| vóór admissioncommit | request kan opnieuw worden aangeboden; geen episode of modelcall bestaat |
| na admissioncommit, vóór workflowstart | Attention start exact dezelfde generation; geen verloren commitment |
| na modelattemptreservering, vóór modelcall | attempt blijft reserved en kan veilig dispatch krijgen |
| na modelrespons, vóór responsecommit | attempt wordt ambiguous; geen verborgen modelretry |
| na plancommit | hetzelfde plan wordt gelezen; model wordt niet opnieuw aangeroepen |
| na EffectIntent, vóór dispatchpermit | intent kan current worden gefenced of superseded; nul externe I/O bewezen |
| na dispatchpermit, vóór bekende calluitkomst | intent wordt ambiguous en doet eerst een canonieke reconcile-ReadAttempt op de idempotency key; `found_same` krijgt daarna waar vereist een aparte verify-readback |
| na remote commit, vóór receiptcommit | reconcile op dezelfde idempotency key bepaalt eerst of het effect bestaat; daarna volgt de passende verify-readback; geen blind resend |
| na receiptcommit, vóór readback | receipt wordt hergebruikt en alleen readback volgt |
| na readbackrespons, vóór lokale commit | nieuwe readbackattempt, nooit een nieuwe write |
| na confirmation-deliveryacceptatie, vóór receiptcommit | reconcile-before-resend volgens de business-channelproof; geen tweede document of delivery |
| na confirmation-deliveryreceipt, vóór OutcomeView | bestaande receipt voedt OutcomeView zonder tweede business delivery |
| na outcome-deliveryacceptatie, vóór receiptcommit | reconcile-before-resend volgens de authentieke ingresschannelproof; businesshandelingen blijven onaangeraakt |
| na outcome-deliveryreceipt, vóór episodeclosure | bestaande receipt sluit de episode zonder tweede result delivery |
| na closurecommit, vóór bytegelijke replayresponse | replay retourneert dezelfde canonieke uitkomst en vraagt geen tweede outcome delivery |

### 9.3 Correctie en supersessionrace

Een geldige correctie bevat dezelfde bevestigde principal en een ondubbelzinnige episodebinding.
Zij maakt atomair een nieuwe generation. De dispatchfence van ieder oud effect gebruikt een CAS op
`episode_id`, `generation`, current status en intentrevision.

Workerstart, capability- en credentialpreflight, rate-limitwacht en iedere andere planbare delay
liggen vóór die dispatchfence. Tussen de gecommitteerde fence en de ene connectorcall staat geen
sleep, queue, brokerlookup of nieuwe frameworkstap. De sealed supersessionproef injecteert haar
delay daarom vóór de fence. Zodra de correctietransactie is gecommit, moet iedere later gestarte
fence-CAS voor de oude generation falen.

- Commit de correctie vóór de dispatchfence, dan faalt de oude CAS en doet de oude generation nul
  nieuwe effects.
- Commit de dispatchfence eerst, dan kan I/O al zijn gestart. De correctie markeert dat effect niet
  als ongedaan gemaakt, maar wacht op reconciliation en plant zo nodig een expliciete compensatie.
- Reads of modelcalls van de oude generation mogen afronden als historical evidence, maar mogen
  geen current plan, reply of effect voeden.
- Een fresh-process replay leest altijd de current generation uit PostgreSQL en vertrouwt niet op
  process-local cancellation.

## 10. Eerste connectorneutrale cross-domain vertical

### 10.1 Gebruikersuitkomst

De vertical gebruikt exact deze normale opdracht:

> Lees de nieuwste mail van Noor BV, zoek Noor op in het ERP, controleer wanneer we woensdag
> allebei kunnen, plan een afspraak van een half uur, maak een korte bevestiging, stuur die naar
> het bevestigde e-mailadres uit het ERP en koppel terug wat er werkelijk is gelukt.

Daarmee bewijst hij deze semantiek:

1. lees de bedoelde e-mail;
2. zoek de genoemde klant in ERP op;
3. controleer agenda-informatie en plan de afspraak;
4. maak het gevraagde document met alleen bewezen gegevens;
5. verstuur de bevestiging naar het unieke, bevestigde ERP-adres en bewijs die afzonderlijke
   business delivery;
6. koppel daarna via het authentieke ingresskanaal in normale taal terug wat werkelijk gelukt,
   gedeeltelijk gelukt of nog onzeker is.

Geen providernaam, Home Assistant-entiteit, account-ID of hardcoded persoon komt in de Living Core.
De sealed scenario-input bindt vooraf principal, mailboxscope, selectiecriterium, ERP-zoekbeleid,
agenda-policy, documentdoel, de business-deliverytarget, de result-deliverytarget, toegestane
effecten en verboden effecten.

### 10.2 Capabilities en bewijs

| Capability | Klasse | Minimale uitkomst en proof floor |
| --- | --- | --- |
| `mail.message.search` | read | provider message/thread IDs, queryhash, retrieved-at, bounded resultset |
| `mail.message.read` | read | exact message-ID, contenthash, headerscope, retrieved-at, untrusted-contentlabel |
| `erp.customer.lookup` | read | provider customer-ID, lookup provenance, match cardinality, responsehash en freshness |
| `calendar.availability.read` | read | account/resource, window, timezone, retrieved-at en responsehash |
| `calendar.event.create` | effect | frozen attendee/time/timezone/payload, stable idempotency, provider event-ID, receipt en exact readback |
| `document.create` | effect | frozen contenthash, owner, MIME, retention, object/provider-ID, write receipt en byte/versionreadback |
| `message.deliver` | confirmation-delivery-effect | frozen bevestigd ERP-target, document/contenthash, provider message-ID, receipt en channel proof floor |
| `channel.result.deliver` | outcome-delivery-effect | exacte authenticated-HTTP requestRef, frozen PresenceReply- en wirehash, delivery-ID en P-256 ES256 client-ACK over de vooraf gebonden challenge |

Een ERP-match met nul of meer dan één geldige klant wordt niet gegokt. De episode vraagt alleen de
minimaal noodzakelijke verduidelijking. Mail- en ERP-tekst kan geen toolinstructie, accountwijziging
of nieuwe scope afdwingen.

### 10.3 Uitvoerings-DAG

```text
V0 AdmitTurn
  -> V1 Reserve and run bounded planner
  -> V2 ValidatePlan and capability set
  -> V3 Search and read mail
  -> V4 ERP customer lookup
  -> V5 Calendar availability read
  -> V6 Freeze calendar EffectIntent and generation fence
  -> V7 Calendar connector call, receipt and readback
  -> V8 Produce typed document draft from accepted evidence
  -> V9 Freeze document EffectIntent and generation fence
  -> V10 Document write, receipt and exact readback
  -> V11 Freeze confirmation DeliveryIntent for confirmed ERP target
  -> V12 Confirmation delivery, receipt and required readback
  -> V13 Build OutcomeView
  -> V14 Produce ReplyCandidate and host-validate PresenceReply
  -> V15 Freeze outcome DeliveryIntent for authenticated ingress channel
  -> V16 Outcome delivery and receipt
  -> V17 Episode closure
```

Harde dependencies:

| Node | Moet beschikbaar zijn | Mag niet gebeuren bij ontbrekende dependency |
| --- | --- | --- |
| V4 | één geselecteerde mail met evidence | klant raden uit vrije modeltekst |
| V5 | geldige klantcontext en afspraakcriteria | breed agenda- of accountbereik openen |
| V6 | current generation, actieve manifesten, exact slot en policy | calendar write uitvoeren |
| V8 | mail-, ERP- en verified calendaruitkomst | onbewezen datum, klant of providerclaim in document zetten |
| V9 | documentdraft met source refs, contenthash, owner en retentie | documentbytes schrijven |
| V11 | verified document, unieke bevestigde ERP-recipient en current generation | businessbevestiging aan een onbewezen target versturen |
| V13 | alle business-stepoutcomes, inclusief confirmation delivery, met evidence | globale successtatus uit workflow completion afleiden |
| V15 | current generation en host-gevalideerde PresenceReply | oud, technisch of te positief resultaat versturen |
| V16 | authentieke result-channel proof floor | result delivery als geleverd behandelen |
| V17 | alle door policy vereiste business- en outcome-deliveryclosures | episode stil sluiten |

### 10.4 Deelresultaat en herstel

- Mail gelezen maar ERP-match ambigu: geen write, één menselijke verduidelijking, commitment blijft
  `waiting_user`.
- ERP gevonden maar geen passend agendaslot: geen afspraak en geen document dat een afspraak claimt;
  het bewezen deel mag eerlijk worden teruggekoppeld.
- Agenda-event verified maar documentwrite faalt: afspraak blijft als bewezen effect bestaan,
  documentstep wordt blocked of retryable volgens beleid en de reply noemt precies dit deelresultaat.
- Agenda-effect ambiguous: eerst readback. Document en delivery wachten, omdat zij van de werkelijke
  afspraak-ID en tijd afhangen.
- Document verified maar confirmation delivery ambiguous: geen tweede document en geen blind
  resend; eerst een eigen reconcile-ReadAttempt. Outcome delivery mag de onzekerheid melden, maar
  de episode sluit niet vóór het deliverybeleid is afgehandeld.
- Confirmation delivery definitief afgewezen: agenda en document blijven bewezen deelresultaten;
  outcome delivery meldt precies dat de bevestiging niet is verstuurd.
- Outcome delivery ambiguous of afgewezen: alle businessreceipts blijven behouden, het commitment
  blijft zichtbaar open en herstel reconcilieert de result delivery zonder de businesshandelingen
  opnieuw uit te voeren.
- Correctie vóór V6: oude generation doet nul writes. Correctie na V6: bestaand effect wordt eerst
  gereconcilieerd en nooit stil als ongedaan beschouwd.

### 10.5 Menselijke reply zonder bewijsverlies

Presence maakt een compacte context uit de actieve IdentityPack, bevestigde relatie, de originele
opdracht en OutcomeView. Pydantic AI produceert vrije natuurlijke tekst plus expliciete claimrefs en
unresolved refs. De host weigert de kandidaat wanneer:

- een succesclaim geen passende verified of vooraf toegestane accepted-unverified outcome heeft;
- een deelresultaat als volledige voltooiing wordt geformuleerd;
- een oude generation, ongeverifieerd memoryfeit of onbetrouwbare mailinstructie als authority dient;
- naam, stijl, taal of kanaalpresentatie niet bij de actieve IdentityPack hoort;
- intern jargon verschijnt zonder dat de gebruiker daarom vroeg;
- de Eva-packtekst een em- of en-dash bevat.

De stijlpoort schrijft geen canned antwoord. In deze eerste vertical krijgt een geweigerde
ReplyCandidate geen tweede modelcall. Er volgt direct een korte, eerlijke hostfallback uit de
canonieke OutcomeView, zonder nieuwe succesclaim. Een latere expliciete contractversie mag pas na
gemeten noodzaak een afzonderlijk, canoniek gereserveerd rewritebudget invoeren.

## 11. Build dependency-DAG

```text
B0 Step 3 design freeze
  -> B1 exact dependency, policy, budget and platform lock plus package skeleton
  -> B2 versioned contracts and generated schemas
  -> B3 minimal migration, domain and repository slice for one atomic command
  -> B4 immediate minimal DBOS atomic-commit proof

B4      -> B5 full central migration chain, state machines and repositories
B2      -> B6 connector wirecontract and sandbox profile
B2      -> B7 portable export contract and generic reader contract
B2 + B5 -> B8 Presence and powerless model adapter
B5 + B6 -> B9 evidence/object store and connector supervisor
B6      -> B10 five connector capability manifests and reference workers

B5 + B8 + B9 + B10 -> B11 cross-domain vertical
B7 + B11           -> B12 clean install, export, restore, update and rollback path
B11 + B12          -> B13 deterministic, crash-process, isolation and portability gates
B13                -> B14 real provider model adapter and quarantined Connector Forge gates
B14                -> B15 separately approved disposable real connector canary
B15                -> B16 ADR acceptance decision
B16                -> B17 separately authorized Eva 1 importer work
```

### 11.1 Exit per buildnode

| Node | Concrete exit, niet alleen geproduceerde code |
| --- | --- |
| B1 | Python 3.12.14, alle directe/transitieve packages, numerieke budgetten, OS/architectuur/engine en OCI-bases exact gelockt; licenties en Linux amd64/arm64 wheels/images gecontroleerd |
| B2 | ieder contract round-tript via eigen JSON Schema en bevat geen frameworktype |
| B3 | uitsluitend de minimaal benodigde tabellen en repositorycommand creëren vanaf nul, met tenantconstraints en zonder runtime-DDL |
| B4 | crash vóór/na commit bewijst één atomisch applicatie- plus DBOS-transactieresultaat; anders engine-intrinsieke NO-GO vóór verdere schema-uitbouw |
| B5 | volledige centrale head, iedere transitionmatrix, CAS-guard, terminale toestand, verboden overgang en immutable event aantoonbaar |
| B6 | ongeautoriseerde operation, extra secret, DB-socket en niet-allowlisted egress zijn vóór workerstart onmogelijk |
| B7 | canonical records en objectmanifest zijn leesbaar zonder DBOS of Eva-runtime |
| B8 | Eva- en Mira-pack geven herkenbaar verschillende natuurlijke taal zonder bronwijziging; model kan niets uitvoeren |
| B9 | evidencehash, objectreceipt, readback, retention en crypto-shred hebben één eigenaar en geen secretlek |
| B10 | alle acht businessoperations plus de drie generieke reconcile-, readback- en probeoperations gebruiken hetzelfde corecontract; providerdetails blijven in afzonderlijke workers |
| B11 | exact één outcome per request/generation, eerlijke partials en geen action/reply-mismatch |
| B12 | tweede schone installatie reconstrueert identity, conversations, episodes, intents, receipts, memory en pending work zonder Git-worktree |
| B13 | volledige deterministic, process, secret, egress, export, second-restore en A/B-poorten groen, nog zonder live connectorwrite |
| B14 | echte modelattempts zichtbaar; documentatie blijft onbetrouwbare data; Forge kan geen write activeren |
| B15 | afzonderlijk goedgekeurde disposable opdracht met echte allowlisted providerreceipts en readbacks groen |
| B16 | afzonderlijk besluit zet hoogstens `accepted`; `production_ready` vereist daarna releaseacceptatie |
| B17 | alleen starten met ondertekende row-level selectie en alle importgates uit Step 2 |

## 12. Exitcriteria vóór de eerste productcode

Productcode mag pas beginnen wanneer alle onderstaande ontwerpcriteria op dezelfde commit zijn
afgevinkt:

1. Deze build-map heeft een onafhankelijke P0/P1-review zonder open P0 of P1.
2. ADR-0001 en het transitionmanifest zijn nog exact hashgebonden en geen latere edit heeft stack,
   authority of migratieauthorization stil gewijzigd.
3. Iedere packagegrens heeft één eigenaar en de toegestane importpijlen zijn volledig.
4. Iedere canonieke tabel hierboven heeft primary key, tenant/installationscope, lifecycle,
   retentie, exportmapping en deletepad toegewezen.
5. Iedere state machine heeft een gesloten vocabulary, legal-transitionmatrix, CAS-guard en
   terminaliteitsregel. Er bestaat geen `unknown` die automatisch mag doorlopen.
6. Iedere model-, read-, effect- en deliverycall in de vertical heeft vooraf een fouttaxonomie,
   timeout, budget, idempotencyregel, proof floor en reconciliationpad.
7. De crashmatrix en beide DAG's zijn verzegeld vóór de implementatierun. Een rode gate mag niet
   tijdens dezelfde run worden herschreven om bestaand gedrag groen te verklaren.
8. De exacte dependencylock, hashes, licenties, OCI-bases en gedeclareerde platformmatrix zijn
   gereviewd. Er is één Python Living Core en geen tweede vertrouwde Node-, Rust- of C++-kern.
9. Het threat model bevestigt tenantisolatie, prompt-injectiongrens, secretcustody,
   operation-scoped grants, worker sandbox, egressallowlist en privacy-erasure.
10. EvaPortableArchive/v1 heeft per canonieke tabel een frameworkonafhankelijke recordmapping en
    maakt expliciet welke projecties, telemetry en DBOS-state niet meegaan.
11. De sealed cross-domain case bevat exacte input, allowed en forbidden effects, before/after
    provideroracles, verwachte partials, correctiontiming en natuurlijk replycriterium.
12. `dataMigrationAuthorized`, `serviceStartAuthorized` en `eva1CodeImportAuthorized` blijven
    `false`.

Een ontbrekend antwoord blokkeert de start. Het wordt niet als implementatiedetail doorgeschoven.

## 13. Wat in deze eerste build uitdrukkelijk niet wordt gebouwd

- geen Eva 1-importer, dataconversie, schemaoverneming of heractivatie van episode 82 of 83;
- geen Home Assistant-, robot-, camera-, face-, wakeword- of voicevertical;
- geen algemene Connector Forge, automatische SDK-installatie of zelfpromotie naar writes;
- geen accountaanmaak die betaling, voorwaarden, CAPTCHA, MFA of menselijke identiteitsverklaring
  probeert te omzeilen;
- geen Learning-runtime, zelfedit, autonome doelen, background exploration of losse schedulerloops;
- geen vector-, embedding-, Hindsight-, Graphiti-, Mem0- of Letta-authority;
- geen GUI, avatar of overname van `eva.glb` en de oude webapp;
- geen shelltool, algemene browsercomputer, packageinstaller of legacy workspacecode in de core;
- geen native Windows- of macOS-belofte. De eerste supportgrens blijft de gedeclareerde
  containerized route;
- geen multi-region, active-active, Kubernetes- of high-availabilitylaag voordat één node de
  sealed correctnesspoort haalt;
- geen generieke eventbus, microservicevloot, tweede business-eventstore of tabel per script;
- geen claim dat Eva al beter is dan OpenClaw, Hermes of Agent Zero;
- geen productiestart of gebruikersdatamigratie op basis van alleen groene unit- of fixturetests.

De connectorcontracten zijn generiek genoeg om deze mogelijkheden later toe te voegen. Dat is geen
reden om ze vóór bewijs van de eerste vertical alvast te implementeren.

## 14. Eigen P0/P1-review van deze kaart

### P0-controle

- **Gesplitste authority:** voorkomen. Alleen de aangewezen canonieke `eva_*`-rijen zijn
  business-authority; DBOS en projecties zijn dat niet, terwijl receipts en evidence uitsluitend
  de hierboven toegewezen bewijsauthority hebben.
- **Dubbele of verloren effecten:** iedere write heeft intent, generationfence, stable
  idempotency, attempt, ambiguous state en reconcile-before-resend.
- **Stale supersession:** de correctioncommit en dispatchpermit zijn expliciete concurrerende
  linearisatiepunten. Oud werk kan na verlies van current generation geen effect of reply doen.
- **Secrets en tenantlek:** connectorworkers krijgen geen DB/model/general secrets; alle
  persoonlijke lookups zijn tenant-scoped, composite foreign keys en RLS vormen twee DB-grenzen,
  en name-only merge is verboden.
- **Legacy-activatie:** import- en serviceauthorisaties blijven false; legacy work, credentials en
  executables komen niet in deze build.
- **Exit lock-in:** portable canonical records hebben geen DBOS-FK en blijven leesbaar wanneer
  DBOS en projecties ontbreken.

### P1-controle

- **Te veel tabellen of scripts:** een tabel bestaat alleen bij een eigen lifecycle, constraint,
  retentie of exportsemantiek. Er is één workflowlaag, één migrator en één Attention-route.
- **Verborgen retries:** model- en external steps hebben één frameworkattempt. Iedere volgende
  attempt is canoniek, begrensd en zichtbaar.
- **Provider- of Home Assistant-koppeling:** corecontracten gebruiken capability, resource,
  provider-ID en evidence; providerpayloads blijven in workers.
- **Action/reply-mismatch:** OutcomeView is de enige completionbron en iedere natuurlijke claim
  verwijst naar dezelfde current generation en evidence.
- **JSONB als vuilnisbak:** primaire state, transitions en relaties zijn relationeel en
  constrained; JSONB is alleen versioned payload of evidence.
- **Onbewezen DBOS-aanname:** de atomische datasourcegrens is de eerste zelfstandige NO-GO-proef,
  niet een stil geaccepteerde eigenschap.
- **Portabiliteit achteraf:** exportmapping, generic reader en clean restore zijn dependencies van
  de vertical en geen releasepolish.

Eigen verdict: geen bekende P0 of P1 in het ontwerp. Dit is geen vervanging voor de onafhankelijke
Step 3-review en geen toestemming om code te schrijven voordat de pre-codecriteria zijn gesloten.
