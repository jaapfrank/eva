# Incident: testimport migreerde de production-ledger

Datum: 2026-08-13 (Europe/Amsterdam)

Status: **engineeringvoorbereiding deels GO: DB-guardprimitive GO,
direct-ELF-runnercomponent GO, quiescente offline-snapshotvoorbereiding GO en
offline recovery-candidatevoorbereiding GO. De werkelijke runnerinstallatie en
de productierelease blijven NO-GO zolang geen gecontroleerde installatie en
releasekwalificatie zijn uitgevoerd. Live service-stop, live snapshot/swap,
restart en deploy wachten nog steeds op expliciete toestemming en zijn niet
uitgevoerd.**

## Samenvatting

Een of meer ongescopeerde Node-testruns importeerden `src/db.js` zonder een
expliciete `EVA_DATA_DIR`. Daardoor werd `data/eva.db` geopend en werd
`turn_tool_executions` additief gemigreerd van de production14-vorm naar de
nieuwe zeventienkolomsvorm met `execution_seq`, `execution_contract` en
`execution_verification`, twee ledgerindexen en tien triggers.

Tijdens de oorspronkelijke audit was PID 954119 de geobserveerde service en had
die de oude modulecode in geheugen. Onder de toen geobserveerde combinatie laat
de oude INSERT `execution_seq` weg, waarna de nieuwe `BEFORE INSERT`-trigger de
defaultwaarde 0 verwerpt. Ledgerclaims via die runtime/schema-combinatie falen
daarom gesloten; andere writes konden tijdens de audit nog doorgaan.

Omdat toestemming voor een nieuwe live inspectie en service-stop ontbreekt,
doet dit document geen uitspraak over het huidige PID, de huidige servicestatus
of de actuele activiteit van het hoofdbestand, WAL of SHM.

## Vaststaand bewijs

- De laatste bekende onaangetaste backup is
  `data/backups/eva-2026-08-12-20-30-50.db`.
- Die backup bevat 344 ledgerrows in production14-vorm.
- Een read-only vergelijking na het incident vond alle 344 oorspronkelijke
  rows in de live DB terug met **alle veertien oorspronkelijke velden
  bytegelijk**.
- De SHA-256 over die gedeelde oude velden is aan beide kanten exact:
  `dc8428433bb11f73a76ab0217cf05ca44a689377b7925c14893eb2264f9fae0c`.
- Er waren zeven extra productionrows; hun `started_ts` lag vóór het
  incidentvenster. Ze zijn dus niet door de testrun gefabriceerd.
- Van de 351 rows zijn 71 historische done-rows expliciet
  `legacy_unverifiable`; de overige rows zijn als bestaande v1-bewijzen
  geclassificeerd. Er is geen oude bewijsinhoud herschreven.
- De live DB had na het incident 17 ledgerkolommen, twee ledgerindexen en tien
  nieuwe ledgertriggers.
- Tijdens de oorspronkelijke audit was PID 954119 de enige geobserveerde houder
  van DB/WAL/SHM; het hoofdbestand en de WAL veranderden toen. Dit is historisch
  auditbewijs, geen actuele proces- of WAL-observatie. De waarnemingen in deze
  engineeringrun liepen op 2026-08-13 ten minste tot 10:28 CEST; een afzonderlijk
  duurzaam proces-evidence-id is niet beschikbaar.

## Waarschijnlijke oorzaak

Minstens één gecombineerde Workcompletion-run importeerde transitief `src/db.js`
zonder `EVA_DATA_DIR`. De vroegste teruggevonden risicovolle opdracht was rond
07:20–07:35 CEST:

```text
node --test test/execution-ledger-main-schema.test.js \
  test/artifact-versions.test.js test/artifact-collections.test.js \
  test/material-lifecycle.test.js test/work-completion-contract.test.js
```

Er waren eerder ook gecombineerde 127/127- en 197-pass-runs met dezelfde
dependencyfamilie. Door contextcompactie is niet meer bewijsbaar welke run als
eerste migreerde. Een latere afzonderlijke Workcompletion-run rond 08:00 opende
de default DB, maar faalde door de inmiddels aangescherpte triggerattestatie vóór
verdere migratiemutatie.

## Insluiting

- Tijdens de oorspronkelijke insluiting zijn de toen bekende testprocessen
  beëindigd. Dit is geen actuele procesinventarisatie.
- Verdere brede tests en default-DB-imports blijven verboden.
- Live herstel, service-stop, restart en deploy zijn niet uitgevoerd; daarvoor
  ontbreekt nog steeds expliciete toestemming.
- De DB-guardprimitive die vóór ieder filesystem- of SQLite-effect een
  expliciete, descriptorgebonden `/tmp`-root afdwingt, heeft een onafhankelijk
  GO-besluit. Dit GO geldt voor de primitive en impliceert niet zelfstandig
  release-GO.
- De direct-ELF-runnercomponent heeft een onafhankelijk component-GO voor
  normale/accidentele tests. De actuele rootGate-keten is statisch en gericht
  gekwalificeerd, maar geen runnerartifact is root-owned geïnstalleerd of
  gedeployed. Op de actuele bevroren basis waren de globale typecheck-, lint-
  en complexiteitsgates respectievelijk `543/543`, `0/0` en `15/15`.
  `543/543` is een ongewijzigde telgrens voor bestaande TypeScript-schuld en
  betekent uitdrukkelijk niet dat TypeScript nul diagnoses rapporteert; de gate
  bindt evenmin de identiteit van die bestaande diagnoses.
- De quiescente snapshothelper heeft een onafhankelijk GO-besluit voor
  uitsluitend offline snapshotvoorbereiding na een bewezen servicestop. De
  helper is niet op de live evidence-trio uitgevoerd en geeft geen toestemming
  voor stop, restore, swap, restart of deploy.
- De offline voorbereiding van een recoverycandidate op self-contained kopieën
  heeft een onafhankelijk GO-besluit. Dit was geen live restore, swap,
  servicestop of restart.

## Readiness- en candidatebewijs

### DB-guardprimitive

- beslissing: `GO`
- scope: `primitive; geen releasegoedkeuring`
- config SHA-256:
  `23b46c2c87001b6b3b8bf36b0dd0790458c0eaaaf621991bffce6d8c47573386`
- db SHA-256:
  `e4562a53c5f7a39898eb7f2d857c69390ece74c18c0750071d1893d7d6d98300`
- gerichte evidence: guardmatrix `20/20`; onafhankelijke statische ruling `GO`
- beslistijd: 2026-08-13 (Europe/Amsterdam); exact duurzaam tijdstip ontbreekt

### Direct-ELF-runner

- componentbeslissing: `GO` voor normale/accidentele directe ELF-uitvoering
- releasebeslissing: `NO-GO` totdat root-owned installatie en rootGate-integratie
  werkelijk zijn uitgevoerd en alle globale gates groen zijn
- statische integratiebeslissing: `GO`; geen installatie/deploygoedkeuring
- ELF SHA-256:
  `a281de6047697c4996d4a51f0c379c74f593bebd3ba7ad93374393b7bb3414b1`
- bootstrapbron SHA-256:
  `76849ac3d0c24997e8e2fe79cd173a09755c48fd6f4a113524447c2414062576`
- runner SHA-256:
  `664f0b5ec7b7a5d6a3e14dbb4aab55c6e6fae05f1507777e5b52c516e427ee07`
- releasecontract SHA-256:
  `6c95d6f28cf9f6ed40023fe03aaa9f920bb162858db7a10dd28cefe613b06cf9`
- gerichte evidence: mandatory selfgate en runnerproeven groen; runtime-
  self-repair `44/44`; reproduceerbare A/B-builds bytegelijk; onafhankelijke
  componentruling `GO` binnen de normale/accidentele grens
- maintainer SHA-256:
  `2a9103960deab57f5907700d6bc6fe92a448c47976fd043d7b2e25ec8d099732`
- installer SHA-256:
  `ac55741d7da104b206a4c5973418a6d38b696b7303752923293cea804cab8cb5`
- runtimecontroller SHA-256:
  `f3136a83fb3e2a7cb84b67b66510740684416ff3640be33130d22ca7b75d7e5f`
- statische integratie-evidence: bootstrapgate, normale geneste Node onder de
  begrensde 6-GiB-adresruimte en rootGate-contract groen
- resterende releaseblokkades: het geattesteerde ELF is nog niet werkelijk
  root-owned geïnstalleerd; er is geen install/deploy uitgevoerd; de garantie
  dekt geen vijandige test die haar eigen environment wist of een gelijktijdige
  same-UID-aanvaller
- beslistijd: 2026-08-13 (Europe/Amsterdam); exact duurzaam tijdstip ontbreekt

### Quiescente snapshotvoorbereiding

- prepbeslissing: `GO`
- scope: uitsluitend na bewezen quiescentie een nieuwe zelfstandige offline
  snapshot voorbereiden uit gepinde main-, WAL- en SHM-evidence; geen restore,
  swap, restart, deploy of live-DB-open
- snapshothelper SHA-256:
  `41ce879953c9de58e85dde10446f8822ab86bb585b0d97580ef095bd6ffb16d9`
- snapshottest SHA-256:
  `966cfe10072b1d35ee3c3f7080dcb437e2e194c3774e0d6e3c705481b2738ab9`
- gerichte evidence: lokaal `21/21`; geïsoleerde mandatory selfgate en suite
  `21/21`; publisher A/B bytegelijk; onafhankelijke statische ruling zonder
  P0/P1
- de evidence-trio wordt niet door SQLite geopend; alleen main en WAL worden
  naar private scratch gekopieerd, terwijl SHM uitsluitend als evidence wordt
  geattesteerd. De zelfstandige output wordt na checkpoint en
  `journal_mode=DELETE` vanuit een vooraf gefsyncte `O_TMPFILE` no-clobber
  gepubliceerd en opnieuw aan inode en hash gebonden
- contractgrens: trusted pre-Node/operator-startup en geen vijandige of
  gelijktijdige same-UID-writer
- er is nog geen actuele productie-evidence verworven of geconverteerd
- beslistijd: 2026-08-13 (Europe/Amsterdam); exact duurzaam tijdstip ontbreekt

### Offline recoverycandidate

- prepbeslissing: `GO`
- scope: uitsluitend een nieuwe offline production14-kandidaat maken uit een
  self-contained private bronkopie; geen restore, swap, restart of deploy
- recoverytool SHA-256:
  `2950ba139540d5f4e32021df62949284eb14041e5de0455aba0e56feb0fccfbc`
- recoverytest SHA-256:
  `099a4cbd4e6251a4c74004af283a06d85e27f0c26f9cf100f388c5053f995376`
- gerichte evidence: volledig programmatische private-`/tmp`-matrix `31/31`;
  onafhankelijke finale statische ruling `GO`
- de tool verifieert volledige `integrity_check`, `foreign_key_check`, exact
  production14-DDL/objectset, alle veertien ledgervelden, niet-ledgerrows en
  intrinsieke rowids, pragmas, filemode, sidecarafwezigheid en checksum
- er is nog geen candidate uit een nieuwe post-incident snapshot gemaakt;
  candidatepad, candidate-SHA en snapshotmanifest ontbreken daarom bewust
- beslistijd: 2026-08-13 (Europe/Amsterdam); exact duurzaam tijdstip ontbreekt

## Voorgestelde operationele volgorde (niet geautoriseerd; nog niet uitvoeren)

Deze stappen vereisen expliciete toestemming en een tweede persoon/reviewer die
de concrete commando's en hashes controleert.

1. Na expliciete toestemming: leg vlak vóór de stop de daadwerkelijk
   geobserveerde service-identiteit, `MainPID` en observatietijd vast; stop
   `eva.service` en bewijs vervolgens dat die concrete instantie is beëindigd.
   PID 954119 is uitsluitend historisch bewijs en mag niet als huidig PID worden
   verondersteld.
2. Bewijs na de stop, met vastgelegde observatietijd en evidence-id, dat geen
   proces `eva.db`, `eva.db-wal` of `eva.db-shm` meer open heeft. Totdat deze
   geautoriseerde controle is uitgevoerd, is de actuele holder- en WAL-status
   onbekend.
3. Maak vanuit de gestopte, bewezen quiescente evidence-trio met de hierboven
   bevroren snapshothelper een consistente, nieuwe **post-incident snapshot**
   die WAL-inhoud behoudt. Kopieer nooit alleen het hoofdbestand terwijl een WAL
   nog actief is en open de evidence-trio nooit rechtstreeks met SQLite.
4. Houd hoofdbestand en sidecars onaangeroerd als forensische bron; noteer
   grootte, mode en SHA-256 van de self-contained snapshot.
5. Kopieer die snapshot naar een private `/tmp`-directory en laat daar pas de
   onafhankelijk goedgekeurde recoverytool een nieuw production14-bestand
   maken. Geen in-place wijziging.
6. Controleer `quick_check`, `foreign_key_check`, exact production14-DDL en
   objectset, alle veertien ledgervelden, alle niet-ledgerrows/rowids,
   schemaobjecten, pragmas, filemode en checksum.
7. Maak vóór swap nog een afzonderlijke kopie van het originele incidentbestand
   plus sidecars. Verwijder of overschrijf die nooit.
8. Installeer de gevalideerde candidate atomair met eigenaar/mode van de
   production-DB; er mogen geen stale WAL/SHM-sidecars naast de candidate staan.
9. Start de oude runtime alleen wanneer exact bewezen is dat zij weer de
   production14-layout ziet. Voer eerst een read-only healthcheck uit, daarna
   één begrensde echte canary en controleer conversation/ledgercontinuïteit.
10. Bij enige afwijking: stop opnieuw en herstel de onaangeroerde
    post-incident snapshot; niet improviseren op de live DB.

Een toekomstige v2-deploy is een aparte wijziging. De incidentrollback is geen
toestemming om de huidige v2-worktree te deployen.

Geen offline candidate-GO, component-GO of eerder auditbewijs vervangt de nog
ontbrekende toestemming voor live inspectie, stop, snapshot, swap, restart,
canary of deploy.
