# Onafhankelijke controle — meerstapsadmission MC02a

Datum: 25 augustus 2026.

## Afgebakende claim

- Eén reeds host-gevalideerde `ModelDecision/v1` en `ValidatedPlan/v1` worden
  atomair toegelaten.
- De bestaande `eva_core.plans`, `plan_steps`, `attention_items`, append-only
  operational events en checkpoints worden hergebruikt; er is geen tweede
  planschema of queue gemaakt.
- De exacte modelbeslissing inclusief argumenten en de volledige gesaneerde
  catalogus staan in het eerste append-only admissionevent. De strikte plan- en
  stap-JSONB blijven ongewijzigd.
- Plan-, stap-, thread-, operation- en attention-ID's zijn deterministisch.
- `load()` controleert scope, modelattempt, alle hashes, catalogus/policy,
  deterministische ID's, beginstates, retentie, revisies en de volledige
  checkpointprojectie fail-closed.

## Echte databaseproef

- Vers gemigreerde wegwerp-PostgreSQL `16.13`.
- Een tijdelijke databasetrigger forceerde de fout pas bij het schrijven van de
  continuation. Daarna bestonden nul plannen, stappen, action-chain-events,
  checkpoints of continuation-items.
- Twee echte writerthreads boden gelijktijdig dezelfde keten aan: één
  `inserted`, één `already_present` en geen duplicaten.
- Een nieuwe SQLAlchemy-engine las na connection-dispose dezelfde bytes terug;
  replay bleef idempotent.
- Een andere RLS-scope kon niet toelaten of laden.
- Gewijzigde argumenten, policybinding en corrupte plan-, stap-, attention- en
  checkpointprojecties werden geweigerd.
- Er ontstonden nul `read_intents` en nul `effect_intents`.

Gerichte lokale controle: 27 applicationtests, 24 bestaande
operational-timelinetests en één echte MC02a-PostgreSQL-gate groen; Ruff,
compile en diff-check groen.

Een tweede agent herhaalde de MC02a-gate op een afzonderlijke nieuwe
wegwerpdatabase, plus de 27 planningtests en Ruff/diff-check: GO. Een andere
agent controleerde contracten, atomische transacties, RLS, herladen en de
nonclaims: GO voor de expliciete admission-only scope.

## Niet geclaimd

- Geen attention-claim, lease-expiry, release, retry of stapvoortgang; dat is
  MC02b.
- Geen algemene runner, modelcall, providercall, Home Assistant-readback,
  Telegram-B-proef of crash midden in een effect; dat volgt in MC03 en
  MX04/MX05.
- Live B en productie A zijn niet gelezen, gestart, vervangen of gewijzigd.
