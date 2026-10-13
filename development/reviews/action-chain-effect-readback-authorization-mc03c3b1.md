# MC03c3b1 — autorisatie van één effectcontrole

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested` op `2cb2c27`.
- Onafhankelijke review: **open**. Dit document is review-input, geen eigen
  goedkeuring.
- Live bewijs: niet uitgevoerd. Productie A, kandidaat B en Home Assistant zijn
  niet benaderd of gewijzigd.
- Optimalisatie: alleen codeomvang is exact gemeten. Latency, geheugen en kosten
  zijn `unmeasured`.

## Waarom deze stap bestaat

Een geaccepteerde Home Assistant-opdracht bewijst nog niet dat de lamp, schakelaar
of andere doeltoestand werkelijk is veranderd. MC03c3a maakte daarvoor een
afzonderlijk alleen-lezen controleverzoek. MC03c3b1 bindt dat verzoek aan exact
één actuele, gepinde providerpoging voordat later provider-I/O mag plaatsvinden.

De autorisatietransactie geeft bewust geen procesprogramma, startcommando,
initialisatiepakket of RPC-verzoek terug. Een volgende aparte stap moet vlak
vóór I/O nog één call-startclaim vastleggen.

## Gebonden waarheid

Vóór commit worden opnieuw gecontroleerd:

- het onveranderlijke `effect_verify`-verzoek en zijn voorbereidend event;
- de nog `running` effectstap en het geaccepteerde effect met attempt en receipt;
- de afzonderlijke actuele `verification_read`-activation, providerbinding,
  policy, manifest, scope en deadline;
- de gepinde providerrelease, executable en sandbox;
- de exacte requesthash, grants, retention en eigen readbacktijdlijn.

Daarna worden precies één `ReadAttempt/v1` in `dispatch_authorized` en één
`ACTION_CHAIN_READ_DISPATCH_AUTHORIZED`-event atomair opgeslagen. Replay levert
dezelfde poging terug. De gewone business-read en effect-readback gebruiken
dezelfde gesloten bouwstenen, maar het gereserveerde `effect.readback`-protocol
blijft expliciet van `resource.read` onderscheiden.

## Bestanden en omvang

| Bestand | Rol |
|---|---|
| `src/eva/operations/action_chain_effect_readback_authorizer.py` | Enige writer van de readback-autorisatie; retourneert uitsluitend disposition en attempt |
| `src/eva/adapters/postgres/action_chain_effect_readback_validation.py` | Valideert voorbereiding, optionele attempt en volledige eigen tijdlijngeschiedenis bij iedere reload |
| `src/eva/adapters/postgres/action_chain_read_validation.py` | Hergebruikt de gesloten attempt-/grant-/envelopevalidatie en bindt gereserveerde leesprotocollen aan hun exacte RPC-methode |
| `src/eva/operations/action_chain_read_authorizer.py` | Levert gedeelde pure grant-, attempt- en eventbouwstenen voor gewone reads en effectcontrole |
| `tests/postgres/test_action_chain_effect_readbacks.py` | Succes, herstart, concurrency, deadline, intrekking, rollback en corruptie op echte PostgreSQL 16.13 |

- Netto productgroei van checkpoint `2cb2c27`: 493 regels.
- Nieuwe authorizer: 364 regels; alle betrokken productbestanden blijven onder
  800 regels.
- Geen schema, migratie of dependency toegevoegd.

## Uitgevoerde controles

- 13 gerichte effectreadbackcases op een verse tijdelijke
  `postgres:16.13`-database met verplichte
  `eva2_action_chain_disposable_`-prefix.
- 47 brede action-chainchecks op een tweede verse PostgreSQL 16.13-database.
- 24 operationele tijdlijnchecks op een derde verse PostgreSQL 16.13-database
  met verplichte `eva2_living_disposable_`-prefix.
- Nieuwe engine/replay bewaart exact één attempt en één autorisatie-event.
- Twee gelijktijdige autorisatoren leveren één `authorized` en één
  `already_authorized`, zonder tweede poging.
- Verlopen deadline en ingetrokken activation schrijven nul pogingen en nul
  autorisatie-events.
- Geforceerde timeline-insertfout rolt attempt en event samen terug.
- Opzettelijk gewijzigde granthash faalt bij ledgerreload gesloten.
- Ruff, compileall en `git diff --check` zijn groen.
- Alle tijdelijke containers en databases zijn verwijderd.

De repositorybrede architectuurchecker is op de huidige brede werkboom niet
groen: hij rapporteert 19 import-boundary-overtredingen in omliggende bestaande
en gelijktijdig gewijzigde componenten. De nieuwe authorizer staat niet in die
overtredingenlijst. Dit checkpoint claimt daarom geen groene repositorybrede
architectuurgate.

## Bewijsgrenzen

- Er is geen readback-call-start gecommit en geen callable providerpakket
  vrijgegeven.
- Er is geen subprocess, netwerk, Home Assistant of ander provider-I/O gestart.
- Er is nul `EffectReadback/v1` en nul `STATE_VERIFIED` geschreven.
- Effectstap, continuation en afhankelijke stappen blijven gesloten.
- De provideracceptatie en controleprovider in tests zijn synthetisch; er is
  geen echte fysieke doeltoestand bewezen.
- Er is geen onafhankelijke review, B-deploy, Telegramproef of A/B-adoptie.

## Open vervolg

1. MC03c3b2 commit precies één call-start en geeft alleen aan de eerste caller
   het sealed proces- en RPC-pakket.
2. Een aparte executor voert alleen die claim uit en bewaart succes, mismatch,
   afwijzing, rate-limit, protocolfout of ambiguïteit zonder tweede proces.
3. `EffectReadback/v1` bindt de verse waarneming aan effect, attempt en receipt.
4. Alleen een exacte doelmatch schrijft `STATE_VERIFIED` en voltooit effect,
   stap, continuation en vrijgekomen dependencies atomair.
5. Onafhankelijke review en daarna een echte Telegram-B → Home Assistant-proef
   met crash tussen effectacceptatie en readback.
