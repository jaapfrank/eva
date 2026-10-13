# CP-001 echte EVA-ingress + PostgreSQL-journal — 12 september 2026

Gebouwd en offline getest; onafhankelijke review open. Geen product/modelcall,
productdatabase, serverrestart, privégegevens, Bottie-executie of winstclaim.

Code: `776643492e3c99c2d1e7e2f7b6a02d47db0d40b3` (parent `34626d5d`).
Alleen de nieuwe benchmarktest `tests/benchmark/test_cp001_native_postgres.py`
en benchmark-README zijn gewijzigd. Deze bewijsnotitie verandert de code niet.
Eigen worktree `/home/administrator/project/.eva2-artifacts/cp001-runner-20260912`.

## Werkelijk uitgevoerd

De CP-001 `native.exchange`-adapter roept via FastAPI TestClient de echte
`create_app`-ingang aan. `PostgresHindsightMemoryRuntime.respond`,
`LivingConversation` en `PostgresConversationJournal` verwerken, genereren,
committeren en replayen het antwoord. Alleen model en semantische memory zijn
stubs; de HTTP-reply en journalpayload worden niet door de test geconstrueerd.
De bestaande runtime-testconstructie omzeilt uitsluitend de volledige bootstrap;
backgroundwerk staat uit en cold-path-submit is een noop. Geen claim van echte
semantische opslag of geheugenkwaliteit.

Een aparte SQLAlchemy-engine leest de gecommitteerde rij rechtstreeks uit
PostgreSQL in een read-only transactie met statement-timeout. De query bindt
exact tenant, installatie, conversation-thread/message en REPLY_COMMITTED.
De adapter controleert vervolgens de vijf payloadbindingsvelden en teksthash.

Vier echte databaseproeven slagen:

1. HTTP-antwoord en journalusage komen overeen; exacte replay heeft één
   REPLY_COMMITTED-record en veroorzaakt geen tweede modelstubcall.
2. Hergebruik van hetzelfde bericht-ID met andere conversation geeft HTTP 409;
   de originele DB-rij blijft aan de oorspronkelijke sessie gebonden.
3. Het model ontvangt daadwerkelijk PG-historie bij de tweede beurt in dezelfde
   sessie, maar geen oude sessiehistorie bij de derde beurt in een nieuwe sessie.
4. Een verkeerde werkelijk opgeslagen journalrij met dezelfde eigenaar en
   antwoordtekst wordt geweigerd: readback_failed, geen verified_end of usage.

De modelstub levert bewust 23 input-/7 outputtokens en één call. De echte
journal bewaart/replayt die waarden. Dit is usage-contractbewijs, geen gemeten
providerverbruik. Productkwaliteit, volledig achtergrondverbruik, kosten en
Bottie-admission blijven unknown.

## Isolatie en duurzaam bewijs

Hergebruikt: bestaande `people_memory_engine`-fixture, inclusief lege database,
versiecontrole, migraties en downgrade. De lokale PG-image was exact
`sha256:5d143123fdf80462d1778cd4f24b9f7ca13c87174bca19141fb194c5a1ebca59`
(PostgreSQL 16.13). Nieuw gemaakte container had `network=none`, geen gepubliceerde
poorten, tmpfs-data, één private Unix-socket, 1 CPU en 512 MiB. Geen nieuwe
image/dependency gedownload of production-DB benaderd.

36 tests groen (4 nieuwe PG-proeven + 32 bestaande CP001/EV001), 4.94s;
ruff op het nieuwe testpad groen. De behouden voice-testomgeving is hergebruikt:
`/home/administrator/project/.eva2-artifacts/native-voice-offline-env-w8rt00c4/venv/bin/python`.
Python 3.12.3, FastAPI 0.141.1, SQLAlchemy 2.0.52, psycopg 3.3.4,
pydantic 2.12.5, pytest 8.4.2, alembic 1.19.1, pydantic-ai-slim 2.37.0.
Dit is een contracttestomgeving, geen bewijs van een volledig release-image;
het project specificeert Python 3.12.14 voor dat releaseprofiel.

Alles staat duurzaam onder
`/home/administrator/project/.eva2-artifacts/cp001-pg-contract-20260912/`:

- `receipt.json`: exact sourcecommit/tree en negen relevante bronhashes,
  PG-image, isolatie, versies en hashes van junit/testlog/vier readback-artifacts.
- `evidence/*.json`: werkelijk ontvangen lokale HTTP-antwoorden en afzonderlijk
  gelezen databasepayloads, inclusief de replay en misgerichte readerproef.
- `tests-final.txt`, `junit.xml`: 36 passed, geen skip.
- `verify.py`: afzonderlijke controle van persisted payloadhash, messagebinding,
  usage, replay-identiteit, bronseal en lege DB na fixture-downgrade.
- `cleanup.json`: uitsluitend de nieuw gemaakte container is verwijderd;
  geen matchende container of aangemaakte named volume achtergelaten.

De verificatie zag na teardown nul EVA-schema's. Daarna is alleen deze
container met tmpfs-data verwijderd. Bewijs en testomgeving zijn behouden.

## Reviewgrens

Graag dezelfde onafhankelijke reviewer op de echte ingress/runtime/journalketen,
SQL-readerbinding, replay, nieuwgesprekhistorie, verkeerd-rijbewijs en expliciete
model/memory-stubs. Dit heft niet de overige paired liveadmissionblockers op:
gelijke serverbudgetten, volledige usage, schone productprofielen, serverstop en
Bottie's echte route blijven afzonderlijke voorwaarden. Geen brede benchmark-GO.
