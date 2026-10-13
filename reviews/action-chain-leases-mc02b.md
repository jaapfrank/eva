# Onafhankelijke controle — meerstapswerklease MC02b

Datum: 25 augustus 2026.

## Afgebakende claim

- Eén due `continuation` wordt met PostgreSQL `FOR UPDATE SKIP LOCKED` aan
  precies één worker gegeven.
- Het leasebewijs bindt attention-ID, revision, worker en harde expiry. Release
  controleert die vier velden opnieuw met CAS; een oude, verkeerde of verlopen
  worker kan niets meer wijzigen.
- Na expiry kan een nieuwe worker dezelfde nog niet uitgevoerde ready stap via
  een nieuwe revision claimen. De foutteller staat uitsluitend op het bestaande
  child-checkpoint. Een andere foutfingerprint begint bij één; de derde gelijke
  fout zet de stap op `needs_help` en de continuation op `waiting`.
- Attention- en checkpointwijzigingen worden atomair met een append-only
  tijdlijnevent geschreven en bij herladen tegen dat laatste event gevalideerd.

## Echte databaseproeven

- Primaire proef op een verse, gemigreerde wegwerp-PostgreSQL `16.13`:
  twee gelijktijdige claimers leverden exact één token; wrong-scope,
  wrong-worker, future `not_before`, stale release, expiry, engine-dispose,
  reclaim en retry 1/2/3 zijn uitgevoerd.
- Onafhankelijke agentproef op een tweede `postgres:16.13`-tmpfs-container en
  twee nieuwe databases: action-chain-gate `1 passed`, operational timeline
  `24 passed` en application action-chain `27 passed`.
- Daarnaast zijn lokaal de 51 planning- en contracttests groen. Ruff, compile
  en `git diff --check` zijn groen.
- Voor en na de leaseproef bleven plan- en step-records gelijk en waren er nul
  `read_intents` en nul `effect_intents`.
- Beide tijdelijke testomgevingen zijn verwijderd. Live B en productie A zijn
  niet gelezen, gestart, vervangen of gewijzigd.

De architectuurreview en de onafhankelijke live-databasereviewer geven GO voor
de expliciete lease-only scope.

## Niet geclaimd

- Geen runner, dependency-advance, toolcall, providercall, Home Assistant,
  Telegram of zichtbaar antwoord.
- Geen bewijs voor crashherstel midden in een extern effect; dat volgt pas
  nadat MC03 dezelfde bestaande prepare/execute/readback-keten hergebruikt.
