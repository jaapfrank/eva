# Eva 2 — gelaagd geheugen en persoonsmodel v1

Bijgewerkt: 25 augustus 2026.

## Besluit

Hindsight blijft Eva's langetermijngeheugen. Honcho en Mnemosyne zijn geen
vervangers:

- de huidige beurt en maximaal twaalf recente berichten vormen Eva's directe
  context uit PostgreSQL;
- Hindsight bewaart en vindt duurzame feiten, ervaringen, correcties en
  relaties;
- Mnemosyne mag als lokale, snelle L1-projectie worden onderzocht;
- Honcho mag als achtergrond-persoons-/relatiemodel worden onderzocht;
- Mem0 krijgt nu geen V1-runtimefunctie.

Een geheugenbrowser of dashboard is een afzonderlijke presentatielaag. Eva
kiest geen extra geheugenprovider alleen voor diens UI: haar eigen dashboard
moet de canonieke PostgreSQL-bron en toegelaten Hindsight-projecties tonen en
iedere correctie, intrekking en verwijdering via Eva's bestaande contracten
laten lopen.

De laatste twee lagen zijn optioneel en afgeleid. Als zij uitvallen, oud zijn of
te langzaam worden, blijven PostgreSQL, Hindsight en Eva's gesprek gezond. Zij
mogen identiteit, consent, groepsaudience, toolbewijs of crashstate nooit
bezitten.

## Waarom niet alles hetzelfde geheugen is

| Laag | Functie | Autoriteit | Huidige stand |
|---|---|---|---|
| L0 huidige context | Huidige beurt, tijdelijke planstate en maximaal twaalf recente berichten zonder zoek- of extractiecall | PostgreSQL + in-flight beurt | Werkend |
| L1 snelle projectie | Vaak relevante of recent geselecteerde context met TTL en snelle lokale lookup | Nooit authority; volledig herbouwbaar | Mnemosyne-kandidaat, niet gebouwd |
| L2 langetermijngeheugen | Feiten, ervaringen, correcties, temporele relaties en selectieve recall buiten het recente venster | Hindsight-projectie met Eva-provenance; canonieke bron blijft PostgreSQL | Hindsight 0.9.1 live incumbent |
| P persoonsmodel | Corrigeerbare samenvatting van voorkeuren, stijl, doelen en relaties per geverifieerde persoon | Afgeleid advies; Eva valideert audience, bron en actualiteit | Honcho-kandidaat, niet gebouwd |

PostgreSQL bewaart daarnaast altijd personen, kanaalbindings, consent, events,
outbox, opdrachten, effects, receipts en crashherstel. Een memoryframework is
geen geschikte transactionele authority voor die gegevens.

## Correctie op de internetvergelijking

De aangeleverde tabel is bruikbaar als richting, maar niet als bewezen
productmatrix.

| Combinatie | Reële potentiële winst | Correctie en open bewijs |
|---|---|---|
| Mnemosyne + Hindsight | Lokale SQLite-L1 kan goedkope snelle hits leveren; een bestaand Hermes-composiet gebruikt L1-first, Hindsight-L2 en een duurzame promotie-outbox | De exacte Mnemosyne 3.15.1 heeft ook een lokale memory browser; “alleen code/CLI” is verouderd. De gevonden composietproef was gedeeltelijk: echte live SIGKILL-recovery ontbrak. Eva heeft bovendien al directe L0-context en een PostgreSQL-retain-outbox, dus extra winst moet worden gemeten |
| Mem0 + Hindsight | Mem0 heeft een self-hosted REST-server met dashboard, gebruikers/API-keys en auditlog | Mem0 is ook duurzaam geheugen en geen bewezen snelle cache. De combinatie zou voorlopig opslag/extractie dupliceren. Cloud is niet verplicht. Het dashboard is los van de geheugenkeuze oplosbaar, dus Mem0 heeft voor Eva nu geen unieke V1-runtimefunctie |
| Honcho + Hindsight | Honcho modelleert veranderende personen, agents, groepen en projecten; de statische representationroute is bedoeld voor lage-latency persoonscontext | Honcho is eveneens zelfhostbaar, maar vraagt API + deriver + PostgreSQL/pgvector + Redis en meerdere modelroutes. “Tot 3× tokens” is geen door ons gereproduceerde vaste factor. Alleen achtergrondafleiding plus goedkope representation-read wordt kandidaat; niet `peer.chat` op iedere beurt |

## Gepinde kandidaten

- Hindsight `0.9.1`, broncommit
  `e5b49eb6729512bd9b103058daa93f701da25644`, bestaand Eva-image
  `sha256:aeb0831e…`.
- Mnemosyne `3.15.1`, broncommit
  `78506708aae344635e01a24f67a7319efc36fce9`, MIT. Het Eva-artifact
  bestaat nog niet.
- Honcho `3.0.12`, broncommit
  `93dcf59c4a4225bb020b20c628799737fefae318`, AGPL-3.0. Het Eva-artifact
  bestaat nog niet en vereist een distributie-/licentiereview.

De pins zijn een reproduceerbare onderzoeksbasis, geen promotieclaim. De
machineleesbare gate staat in
`../benchmark/memory-augmentation-gate-v1.json`.

## Mnemosyne als L1

Mnemosyne mag alleen worden gevoed met dezelfde geverifieerde scope als Eva's
directe context of met een reeds geselecteerd, provenancegebonden
Hindsight-resultaat. De L1:

- heeft een korte TTL en expliciete persoon/groep/projectscope;
- bezit nooit een herinnering die niet uit PostgreSQL/Hindsight herbouwbaar is;
- wordt direct ongeldig bij correctie, consentintrekking of erase;
- mag een Hindsight-miss niet als afwezigheidsbewijs presenteren;
- valt zonder fout in Eva-waarheid terug op directe PostgreSQL-context en
  Hindsight-recall;
- komt alleen in productie bij een meetbare p95-latency-/tokenwinst zonder
  correctheidsverlies.

Eva neemt het Hermes-plugin niet als tweede orchestrator over. Alleen de kleine
L1-semantiek is kandidaat; Eva behoudt haar bestaande PostgreSQL-outbox,
idempotency, audience en receipts.

## Honcho als persoonsmodel

Honcho krijgt alleen geconsenteerde, gescopeerde berichten en expliciete
provenancereferenties. Het bouwt asynchroon een representation/peer card. Eva
gebruikt zo'n resultaat uitsluitend wanneer:

- persoon, tenant, audience en eventuele groep/projectscope exact overeenkomen;
- de representation jonger is dan de laatste relevante correctie of intrekking;
- ieder hard persoonsfeit naar toegestane broninformatie terugleidt;
- inferentie zichtbaar inferentie blijft en nooit directe gebruikerswaarheid
  wordt;
- de beurt baat heeft bij persoonscontext; simpele lampbediening en feitelijke
  readbacks starten geen Honcho-redeneercall.

De `representation`-read is de beoogde lage-latencyroute. Honcho's `chat`- of
zwaardere reasoningroute blijft buiten gewone antwoorden en mag alleen
achtergrondwerk of een expliciete persoonsvraag bedienen. Uitval verwijdert
alleen die extra context; Eva en Hindsight blijven beschikbaar.

## Harde acceptatiecases

Beide optionele lagen moeten passend bij hun rol aantonen:

- base-core en Hindsight blijven gezond bij volledige sidecaruitval;
- een nieuwe correctie wint onmiddellijk van L1-cache en oud persoonsmodel;
- twee personen/tenants met dezelfde naam lekken nul canaries;
- privé-, groeps- en projectaudience kruisen nooit;
- consentintrekking verwijdert groepscontext vóór de volgende modelcall;
- assistenttekst of Honcho-inferentie wordt geen directe gebruikersverklaring;
- dezelfde geverifieerde persoon houdt gecontroleerde cross-channelcontinuïteit;
- er ontstaat geen dubbele retain, feedbacklus of onbegrensde achtergrondqueue;
- erase, export, herbouw, upgrade en rollback houden verwijderde data weg.
- een dashboardcorrectie of -verwijdering gebruikt Eva's authoritycontract en
  kan nooit rechtstreeks een afgeleide laag tot waarheid maken.

Een privacy-, consent-, provenance-, erase- of rollbackfout is NO-GO en kan
niet door gemiddelde benchmarkwinst worden gecompenseerd.

## Metingen

We vergelijken dezelfde verzegelde Eva-corpus eerst zonder augmentatie en daarna
per laag afzonderlijk. We meten correctheidsdelta, correctielatency,
representation/L1-read p50/p95, toegevoegde p95-first-answerlatency,
toegevoegde hot-path-contexttokens p50/p95, totale input/outputtokens,
afzonderlijke achtergrondmodeltokens, modelkosten, piek-RSS, disk, oudste
queueleeftijd, dubbele/conflicterende memories, backup/restore,
upgradedowntime en operatorstappen.

Mnemosyne en Honcho worden pas samen getest wanneer ieder afzonderlijk een
positieve bijdrage bewijst. Anders zou een derde geheugenproces snelheid,
complexiteit en foutoppervlak vergroten zonder productwinst.

## Dependencies en updates

De upstream referenties zijn niet direct een Eva-release:

- Mnemosyne heeft lichte basisdependencies maar optionele embeddings/MCP/sync
  voegen native en cryptografische packages toe;
- Honcho heeft wel een `uv.lock` en non-root Dockerfile, maar de reference
  Compose bouwt uit bron en gebruikt ongepinde database-/Redis-images en
  ontwikkelcredentials; Eva moet eigen digestgebonden artifacts maken;
- Honcho gebruikt Alembic, Redis, PostgreSQL/pgvector, API en deriver. Upgrade
  moet queue, representations en erasegedrag meenemen;
- AGPL-3.0 vereist een expliciete distributie-/wijzigingsreview voordat Honcho
  onderdeel van een verspreid Eva-pakket wordt.

Voor iedere laag gelden gesloten locks, digestimages, SBOM,
vulnerabilityscan, licentie-inventaris, export vóór mutatie, A/B-canary en
terugkeer naar exact vorig artifact en datakopie. De huidige Eva-SBOM- en
installergates zijn nog open; “makkelijk te beheren” is daarom nog geen claim.

## Kleine opleverstukken

- [ ] MA00 — Rollen gecorrigeerd: Hindsight blijft L2; Mnemosyne is alleen L1-
  kandidaat, Honcho alleen persoonsmodelkandidaat en Mem0 heeft geen V1-rol.
  Document en tests zijn groen; onafhankelijke review is nog open.
- [ ] MA01 — Vaste upstreamversies, authoritygrenzen, hard cases, metrics,
  dependency-/licentie- en updaterisico's zijn machineleesbaar vastgelegd.
  De fail-closed checker is getest; onafhankelijke review is nog open.
- [ ] MA02 — Mnemosyne wordt als gesloten, lokale, read-only herbouwbare
  L1-sidecar gematerialiseerd en tegen de no-augmentationbaseline gemeten.
- [ ] MA03 — Honcho wordt als gesloten self-hosted sidecar gematerialiseerd;
  alleen background derive en lage-latency representation-read zijn actief.
- [ ] MA04 — Correctie, audience, consent, provenance, uitval, erase en
  feedbacklusgates zijn voor beide afzonderlijk groen.
- [ ] MA05 — Token-, latency-, kosten-, RSS-, disk- en queuewinst rechtvaardigt
  iedere toegelaten laag; anders wordt zij bewijsgebonden afgewezen.
- [ ] MA06 — Upgrade, backup/restore en rollback zijn op een tweede schone
  installatie bewezen en onafhankelijk gereviewd.

Tot MA02-MA06 groen zijn blijft de productroute eenvoudig: PostgreSQL recente
context plus Hindsight-langetermijngeheugen.

## Primaire bronnen

- Mnemosyne 3.15.1: <https://github.com/AxDSan/mnemosyne/tree/v3.15.1>
- Hermes tiered-memory 1.0.2: <https://github.com/jtstothard/hermes-tiered-memory/tree/v1.0.2>
- Gedeeltelijke live composietproef: <https://github.com/jtstothard/hermes-tiered-memory/blob/v1.0.2/research/live-acceptance-32.md>
- Honcho 3.0.12: <https://github.com/plastic-labs/honcho/tree/v3.0.12>
- Honcho-documentatie: <https://honcho.dev/docs/>
- Hindsight 0.9.1: <https://hindsight.vectorize.io/blog/2026/08/14/version-0-9-1>
- Mem0 self-hosted server en dashboard: <https://docs.mem0.ai/open-source/setup>
