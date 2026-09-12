# CP-001 lokale streamobservatie in bestaande CLI

Code `b6427b5fd231e879b1378f640c3d4e6e72b6dd8f`.
Alleen benchmarkdecoder, bestaande benchmarktest en README; geen runtimewijziging.
Gebruikt de apart gereviewde/geïntegreerde lokale streamobservatie uit de directe
MODEL_RECORDED-events. De providerbron is toegevoegd aan de vereiste bronhashbinding;
het manifest heeft nu vier producthashes, oudere driehash-exports moeten opnieuw
met de passende bron worden gemaakt. Geen wijziging van event- of databaseschema.

## Rapportbetekenis

De bestaande accounting/journal CLI rapporteert observed_local_settlement uitsluitend
voor de geëxporteerde directe admissions:

| Situatie | Lokale classificatie |
| --- | --- |
| Alle admissions terminal met native local_stream_closed=True | settled |
| Een admission zonder terminal | unfinished |
| Geen admissions of ontbrekende native sluitobservatie | unknown |

local_stream_closed_requests telt de geldige sluitobservaties. Ongeldige waarden
zoals een string "true" of False worden geweigerd als malformed native export.
Afwezige legacyvelden worden niet tot sluitbewijs gepromoveerd. Een settled-export
bewijst geen authenticiteit/volledigheid, remoteproviderstilstand of helperdekking.
remote_provider_quiescent en server_quiescent blijven null. De bestaande gelijke-
constraint-, runtime-limit- en totaleusagevoorwaarden blijven intact; live_enabled
blijft false en de CLI geeft exitcode 2.

## Echte events in één geïntegreerde PG/CLI-proef

Nieuwe test_actual_stream_events_drive_cli_local_settlement gebruikt de echte
providercontext, SDK, Pydantic-parser, httpx2-stream en PG-journal met een offline
SSE-transport. Zij zet geen fixtureboolean in een terminalrecord. Een afzonderlijke
SQL-verbinding exporteert de daadwerkelijk geschreven scoped events; zes aparte
CLI-processen lezen die exports.

De eerste echte lokale poging sluit correct en geeft settled. Een tweede poging
faalt tijdens echte transportclose (oorspronkelijke exceptionidentiteit behouden),
waardoor haar terminal geen sluitobservatie krijgt en de gecombineerde export
unknown wordt. Een derde admission zonder beweerde providerdispatch geeft unfinished;
stop en stopreplay laten die status onveranderd. Er zijn precies twee netwerkfixture-
requests, geen livecalls. De negatieve stringclaim wordt afzonderlijk rejected.

86 gerichte tests op immutable codecheckpoint, inclusief bestaande journal-admission-
PG-proef en accounting/native/runner/EV-001-set; Ruff/diffcheck slagen. Geen herhaling
van de volledige releasepoort of claim van nieuwe livevoorwaarden.

Artifact `/home/administrator/project/.eva2-artifacts/cp001-settlement-cli-20260913`
bevat settlement-lifecycle.json met zes echte PG-exports/CLI-rapporten, eerdere
budget/stop-lifecycle-uitvoer, JUnit/tests, verifier en receipt. Negen bronbestanden
gebonden aan checkpoint. Verifier vergelijkt de sluitcounts met de native terminal-
velden en bevestigt alle unknown/livefalsegrenzen. Alle CLI-runs exit2.

PG16.13 network none, geen poorten, tmpfs data/private Unixsocket; nul eva-schema's
na fixture, container verwijderd en afwezigheid gecontroleerd. Geen productdata,
nieuwe quota, helperbeleid, framework of runtimecode aangepast.

Uitvoeren via bestaande `cp001_accounting.py --exports /private/export.json`.
Exact JSON-formaat in README; echte exportquery staat in de test. Onafhankelijke
review vóór integratie, werkbord blijft bij centrale beheerder. Geen livevrijgave.
