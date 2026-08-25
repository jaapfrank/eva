# Verificatiedossier — action-chain readvrijgave MC03b3

Bijgewerkt: 25 augustus 2026.

Status: `implemented`, `tested`; nog niet onafhankelijk `reviewed` en niet
`live_proven`.

Checkpoints:

- `bccca57` — herstartvalidatie en dynamische steprevisies;
- `617ba91` — atomische readvrijgever en gerichte PostgreSQL-proeven.

## Waarom dit onderdeel bestaat

Een providerread die succesvol is opgeslagen, mag niet vanzelf een volgende
actie autoriseren. MC03b3 vormt de bewijsgrens: pas een volledig herlaadbare
`observed` attempt met haar exacte `ReadObservation/v1` voltooit de readstep.
De uitkomstbinding, alle daardoor uitvoerbare dependencies, continuation en het
append-only event veranderen in één transactie. `rejected`, `ambiguous` en
`rate_limited` leveren uitdrukkelijk geen vervolgautoriteit.

## Bestandseigenaarschap

- `src/eva/adapters/postgres/action_chain_read_advances.py` bezit de enige
  atomische CAS-transactie van readbewijs naar step- en continuationvoortgang.
  Het bestand voert geen provider-I/O uit en maakt geen effectintent.
- `src/eva/adapters/postgres/action_chain_read_advance_validation.py` bezit de
  herstartcontrole van succeeded reads, observatiebindingen, released step-ID's
  en historische `ACTION_CHAIN_READ_DEPENDENCIES_RELEASED`-events.
- `src/eva/adapters/postgres/action_chain_read_validation.py` blijft eigenaar
  van intent, attempt, providerresultaat en observatie vóór én na stepvoltooiing.
- `src/eva/adapters/postgres/action_chain_ledger.py` assembleert de volledige
  opgeslagen keten en weigert ongereceipte of ten onrechte vrijgegeven stappen.
- `src/eva/adapters/postgres/action_chain_leases.py` schrijft de bestaande
  attention-CAS en neemt nu optioneel de gesloten advancecontext in hetzelfde
  parent-event op.
- `src/eva/adapters/postgres/action_chain_reads.py` gebruikt revisie 2→3 voor
  een eerder vrijgegeven afhankelijke read; de eerste rootread blijft 1→2.
- `tests/postgres/test_action_chain_read_advances.py` bezit succes-, reject-,
  ambiguïteit-, rate-limit-, concurrency-, rollback-, replay-, herstart- en
  afhankelijke-readproeven.

## Uitgevoerde verificatie

- Acht gerichte MC03b3-proeven op een echte tijdelijke PostgreSQL 16.13:
  succes/replay/herstart, twee concurrente advancers, een vrijgegeven tweede
  read, drie niet-successtates, geforceerde transactierollback en detectie van
  ongereceipte dependencyprogressie.
- 44 action-chain-tests groen: de acht MC03b3-cases plus dispatcher,
  authorizer, ledger en pure planningsregressies.
- 24 operational-timeline-tests groen op een afzonderlijke disposable database.
- Ruff, `compileall` en `git diff --check` groen.
- De runtime-contractkaart bleef groen voor 76 gecontroleerde bestanden. De
  repository-inventaris stopte op reeds aanwezige `GITIGNORE_CONTRACT_DRIFT`
  (verwacht 44 uitsluitingen en 2 negaties); deze slice claimt daarom geen
  groene repositorybrede inventarisgate.

## Omvang en optimalisatie

De twee nieuwe productiecomponenten tellen 365 en 286 regels; beide blijven
onder de 800-regelgrens. De implementatie is over twee checkpoints gesplitst,
zodat geen normaal codecheckpoint meer dan 600 netto productregels toevoegt.
Er is geen nieuwe directe dependency en geen aparte runner per taak ontstaan.

Latency, CPU, geheugen en kosten zijn voor deze PostgreSQL-slice `unmeasured`.
De tests bewijzen juistheid en begrensde dubbele uitvoering, geen p50/p95-winst.
Er geldt dus geen optimalisatieclaim.

## Open vervolg

1. Een onafhankelijke reviewer controleert beide checkpoints en dit dossier.
2. MC03c maakt alleen voor een vrijgegeven effectstep een bestaand effectintent
   en hergebruikt `TOOL_PREPARED` → providercall → fysieke readback.
3. Telegram-B plus echte Home Assistant leveren later live provider- en
   crashherstelbewijs; deze lokale slice verandert A of B niet.
