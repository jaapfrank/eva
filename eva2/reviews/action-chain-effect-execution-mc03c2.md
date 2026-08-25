# Verificatiedossier — action-chain effectuitvoering MC03c2

Bijgewerkt: 25 augustus 2026.

## Actuele uitkomst

`TESTED`, nog geen `REVIEWED` of `LIVE_PROVEN`.

Eva kan nu uitsluitend de eerste duurzaam geclaimde effectcall via de bestaande
sealed subprocessgrens uitvoeren en de provideruitkomst atomair bewaren. Een
provideracceptatie levert een `EffectReceipt/v1`, maar geen `EffectReadback/v1`
en geen geslaagde planstap. Replay, procesherstart en een call-start waarvan de
uitkomst ontbreekt starten nooit blind een tweede proces.

De echte subprocessproef gebruikte een lokale gepinde testworker die een
protocolfout produceerde. De succesproef gebruikte een synthetisch maar volledig
schema-, request- en manifestgebonden providerantwoord. Er is geen Home
Assistant-provider aangeroepen, niets op B gedeployd en productie A is niet
gewijzigd.

## Waarom dit onderdeel bestaat

MC03c1b bewijst alleen dat precies één caller uitvoerbare proces- en RPC-bytes
krijgt nadat `ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED` gecommit is. MC03c2
bezit de onomkeerbare grens daarna: het proces draait buiten de
databasetransactie, terwijl antwoord, bewijs en herstelprojectie exact aan
dezelfde intent, attempt, release, manifest en idempotency key moeten blijven.

Voor een effect is onbekend resultaat gevaarlijker dan een readfout. Een crash
na mogelijk extern effect mag daarom niet tot een tweede call leiden. Eva houdt
zo'n toestand gesloten voor menselijke hulp of latere providerreconciliatie.

## Bestandseigenaarschap

- `src/eva/operations/action_chain_effect_results.py` projecteert een
  providerresultaat puur naar terminale intent/attempt, evidence en eventueel
  providerreceipt; het claimt nooit fysieke doeltoestand.
- `src/eva/adapters/postgres/action_chain_effect_results.py` bezit de ene
  atomaire resultaattransactie en exacte replay zonder callmateriaal.
- `src/eva/adapters/postgres/action_chain_effect_result_validation.py`
  reconstrueert resultaat, receipt, evidence, callvenster en terminale tijdlijn
  bij iedere ledgerreload.
- `src/eva/adapters/postgres/action_chain_effect_state_validation.py`
  reconstrueert de oorspronkelijke geautoriseerde staat uit zowel actieve als
  terminale rijen en controleert hun revision- en evidencecardinaliteit.
- `src/eva/operations/action_chain_effect_executor.py` is de enige eigenaar van
  `run_connector_process`; hij begrenst timeout op 30 seconden en op de
  resterende attemptdeadline.
- `src/eva/adapters/connectors/process.py` blijft de gedeelde read/effect-
  launchergrens; MC03c2 voegt geen tweede processandbox toe.
- `tests/postgres/test_action_chain_effect_dispatcher.py` bezit callclaim,
  projectie, resultaattransactie, reload, corruptie en executorproeven.

## Checkpoints

- `cc3094c` — pure providerresultaatprojectie zonder database-I/O.
- `2e4e84d` — atomaire resultaatsopslag en exacte terminale replay.
- `67035d1` — hash- en historygebonden terminale resultaatsvalidatie.
- `33a2424` — veilige ledgerreload, rollback en evidencecorruptiedetectie.
- `89de787` — algemene eenmalige effectsubprocesexecutor.

Iedere codecommit voegde netto minder dan 600 productregels toe. De vijf
hoofdproductbestanden tellen respectievelijk 349, 594, 556, 119 en 155 regels.
Het bestaande effectvalidatiebestand kromp van 721 naar 698 regels; het
960-regelige ledgerbestand groeide netto niet. Er is geen dependency toegevoegd.

## Uitgevoerde controles

- 10 gerichte dispatcher/result/reload/executorgevallen op een verse,
  tijdelijke `postgres:16.13`-database met verplichte
  `eva2_action_chain_disposable_`-prefix.
- Synthetische provideracceptatie: exact één processcall, één accepted receipt,
  nul readbacks, planstap blijft `running`; replay en nieuwe engine doen geen
  tweede call.
- Werkelijk gestart gepind Python-subprocess: exacte
  `ConnectorProtocolFailure/v1` duurzaam opgeslagen en op replay niet herhaald.
- Achtergelaten `PROVIDER_CALL_STARTED` zonder resultaat: executor geeft geen
  callmateriaal uit en start niets.
- Geforceerde receipt-insertfout: evidence, receipt en terminale state rollen
  volledig terug; dezelfde eerste claim kan daarna exact worden opgeslagen.
- Opzettelijk gewijzigde evidencehash onder expliciete tenantscope: een nieuwe
  ledgerreload faalt gesloten.
- 37 brede action-chainchecks en afzonderlijk 24 tijdlijnchecks groen op verse
  PostgreSQL 16.13-databases.
- Ruff, compileall en `git diff --check` groen.
- Runtime-contractkaart groen voor 76 bestanden. De volledige architectuurcheck
  stopt alleen op reeds bestaande `GITIGNORE_CONTRACT_DRIFT` (verwacht exact 44
  uitsluitingen en 2 negaties); deze slice claimt geen groene
  repository-inventarisgate.

## Bewijsgrenzen en optimalisatie

- Provideracceptatie bewijst alleen dat de provider het effect aannam. Pas een
  verse fysieke readback mag `STATE_VERIFIED` en effectsucces opleveren.
- De lokale testworker bewijst proces- en protocolgedrag, niet dat Home Assistant
  of een apparaat werkelijk veranderde.
- Er is nog geen onafhankelijke review van deze checkpoints.
- Providerlatency, p50/p95, RSS, tokengebruik en kosten zijn `unmeasured`; er is
  dus geen optimalisatieclaim.
- Er is geen B-start, Telegramnetwerkproef, Home Assistant-call of A/B-adoptie.

## Open vervolg

1. Onafhankelijke review van MC03c0–MC03c2 met specifieke aandacht voor het
   crashvenster tussen externe acceptatie en resultaatcommit.
2. Eén algemene `effect.readback`/reconciliatieroute via dezelfde sealed
   providergrens, zonder providerspecifiek script per opdracht.
3. Alleen exacte doeltoestand voltooit effectstap, `outcome_ref`, continuation
   en afhankelijke stappen atomair.
4. Echte Telegram-B → Home Assistant-proef, plus crash na effect vóór readback,
   op één bevroren kandidaatimage.
