# Eva 2

Dit is de nieuwe, connectorneutrale Eva-runtime. Deze map deelt geen uitvoerbare code,
schema-eigenaarschap, secrets of processtate met Eva 1.

Tranche 4A blijft de immutable contract-, platform- en supply-chainbasis. De
huidige bronboom bevat daarnaast de minimale tranches 4B en 4C voor
PostgreSQL-atomiciteit:

- strikte, versiegebonden contracten en gegenereerde JSON Schema's;
- vaste Python-packagegrenzen;
- numerieke budgetten en een exact platformdesign;
- een hash-locked dependency- en supply-chaindesign;
- drie lineaire Alembic-revisions voor uitsluitend `eva_core.tenants`,
  `installations`, `episodes` en `episode_events`;
- een pure `accepted -> planning`-transition en een connection-injected
  SQLAlchemy Core-repository zonder eigen transactiebeheer;
- een hash-gebonden DBOS 2.30.0+eva.1 sync-datasource-overlay, een
  pre-migrated/no-DDL opener en één gesloten workflow-composition;
- echte PostgreSQL 16.13-proeven voor één fysieke head/event/result-commit,
  begrensde `40001`/`40P01`-pogingen, COMMIT-ambiguïteit, route-uitval en
  fresh-process recovery.

Tranche 4C bewijst uitsluitend deze synchrone datasource- en
database-atomiciteitsslice. De async DBOS-datasource is niet ondersteund. De map
bevat nog geen werkende agent, algemene installer, connector, modelintegratie,
gebruikersgedrag of productieservice; die 4D- en latere claims volgen niet uit
dit bewijs.

Veiligheid blijft een dunne grens rond echte risico's, geen tweede bestuurssysteem. Lezen,
redeneren, plannen en uitleggen krijgen geen onnodige toestemmingslagen. Alleen externe of
moeilijk omkeerbare handelingen, zoals schrijven naar accounts, publiceren, betalen, verwijderen
en credentialgebruik, passeren de bijbehorende begrensde effectroute.

## Lokale 4A-controle

De drie controles draaien vanuit de repositoryroot met een externe Pythonomgeving die exact
overeenkomt met `eva2/release/gate-toolchain.lock.json`:

```bash
<sealed-gate-python> eva2/tools/run_gate.py --gate CONTRACT-01
<sealed-gate-python> eva2/tools/run_gate.py --gate PLATFORM-DESIGN-01
<sealed-gate-python> eva2/tools/run_gate.py --gate SUPPLY-DESIGN-01
<sealed-gate-python> eva2/tools/finalize_4a_evidence.py
```

De runner installeert niets en gebruikt geen Eva 1-data, database, service of externe connector.
Een ontbrekende of afwijkende toolchain levert `not_proven` op in plaats van een groen resultaat.

## Tranche 4B PostgreSQL-gate

De lokale pure, contract-, architectuur- en statische tests starten geen database,
container, service of netwerkprovisioning. De PostgreSQL-integratietests vereisen
expliciet `EVA2_TEST_POSTGRES_DSN`. De URL moet wijzen naar een vooraf gemaakte,
lege, disposable PostgreSQL 16.13-database waarvan de naam begint met
`eva2_4b_disposable_`; de testrol moet de vier NOLOGIN-groepsrollen mogen maken.

```bash
PYTHONPATH=eva2/src python3 -m pytest -q -s \
  eva2/tests/domain \
  eva2/tests/postgres/test_4b_static_boundaries.py

EVA2_TEST_POSTGRES_DSN='postgresql+psycopg://.../eva2_4b_disposable_run1' \
PYTHONPATH=eva2/src python3 -m pytest -q -s eva2/tests/postgres
```

Een ontbrekende DSN, verkeerde serverversie, niet-lege database of niet-disposable
databasenaam faalt de integratiegate. Er is geen skip-naar-groen. De gate oefent
fresh create, stapsgewijze upgrade, een uitsluitend pre-traffic downgrade,
re-upgrade, RLS, privileges, repositorycommit/rollback en frameworkonafhankelijk
lezen. Een operationele rollback na verkeer en de DBOS-crashmatrix horen niet bij
4B.

Het bevroren 4B-bewijs staat in
[`postgresql-core-gate.json`](evidence/4b/postgresql-core-gate.json):
38 PostgreSQL-proeven en 139 gecombineerde pinned-environmentproeven zijn groen.
Het receipt claimt uitdrukkelijk nog geen DBOS-, model-, connector- of
gebruikersgedrag.

## Tranche 4C PostgreSQL/DBOS-gate

De 4C-gate draait in de exact bevroren Python 3.12.14-omgeving met de 4A/4B
requirements-locks en vervangt daarin uitsluitend de stock DBOS-wheel door de
hash-gebonden `2.30.0+eva.1` sync-datasource-overlay. De 41 proeven gebruiken
een lege disposable PostgreSQL 16.13-database en bewijzen alleen de hierboven
afgebakende database-atomiciteit, begrensde retry en crashrecovery.

Het bevroren resultaat staat in
[`postgresql-durability-gate.json`](evidence/4c/postgresql-durability-gate.json).
Het claimt geen async datasource, connector-, model-, effect-,
gebruikers- of productiegedrag.
