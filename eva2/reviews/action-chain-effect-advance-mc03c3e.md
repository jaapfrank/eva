# Verificatiedossier MC03c3e — effect alleen afronden na exacte readback

Bijgewerkt: 25 augustus 2026.

## Uitkomst

Status: `implemented` en `tested`; onafhankelijke review staat open.

Eva kan een action-chain-effect nu technisch pas succesvol maken wanneer een
verse fysieke-readbackrepresentatie twee controles doorstaat:

1. de readback zelf is volledig aan effect, request, providerresource,
   idempotency key, evidence en opgeslagen providerstaat gebonden;
2. een verzegelde, declaratieve hostprojectie selecteert de bedoelde waarde en
   dezelfde waarde uit de waargenomen toestand. De canonieke JSON-hashes moeten
   exact gelijk zijn, zodat bijvoorbeeld `true` niet als `1` kan slagen.

Alleen dan veranderen effectintent, planstap, childtijdlijn, continuation en
eventueel uitvoerbare dependencies in één PostgreSQL-transactie. Een replay of
tweede gelijktijdige afronding doet dit niet opnieuw.

## Checkpoints

- `484e805` — effecttools dragen een verzegelde verificatieprojectie.
- `19b4115` — pure onafhankelijke gewenste-versus-waargenomen vergelijking.
- `0acdd33` — duurzame bewijsreconstructie en verified-statevalidatie.
- `f43b73f` — herstartvalidatie van effect- en dependencyvoortgang.
- `a99563b` — atomische writer, `STATE_VERIFIED`, step en continuation.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `src/eva/application/effect_verification.py` | Declaratieve velden en canonieke projectie; geen providercode |
| `src/eva/application/action_chain_effect_advances.py` | Pure tijd-, readback- en waardecontrole |
| `src/eva/adapters/postgres/action_chain_effect_advances.py` | Enige atomische MC03c3e-writer |
| `src/eva/adapters/postgres/action_chain_effect_advance_validation.py` | Bouwt het fysieke bewijs bij iedere reload opnieuw op |
| `src/eva/adapters/postgres/action_chain_effect_progress_validation.py` | Valideert `STATE_VERIFIED`, rootevent en dependencyvrijgave |
| `tests/postgres/test_action_chain_effect_advances.py` | Match-, mismatch-, replay-, concurrency-, rollback- en corruptieproeven |

## Uitgevoerde controles

- Ruff, Pythoncompile en `git diff --check`: groen.
- Applicationlaag: 45 tests groen.
- Gerichte PostgreSQL 16.13-slice: 8 tests groen.
- De brede action-chainrun bereikte 67 groene tests. Daarna raakte de bewust op
  512 MB begrensde tijdelijke database vol; dit veroorzaakte zes setupfouten en
  één memfd-resourcefout, geen assertion tegen MC03c3e.
- Op een verse PostgreSQL 16.13-database zijn die ene executorcase en alle acht
  MC03c3e-cases opnieuw uitgevoerd: 9/9 groen.
- Beide tijdelijke containers zijn exact op naam verwijderd; A, B en hun
  volumes zijn niet benaderd.

De acht MC03c3e-cases omvatten:

- een exacte match en replay na een nieuwe engine;
- `not_observed`, `conflict` en `inconclusive` blijven gesloten;
- een providerclaim `matches` met de verkeerde werkelijke waarde blijft dicht;
- twee gelijktijdige callers produceren één advance;
- een geforceerde fout bij `STATE_VERIFIED` rolt effect- en stepupdates terug;
- een gewijzigd outcome-ref maakt ledgerreload rood.

## Eerlijke bewijsgrens

- De provider in deze slice is een sealed testprovider, niet het echte Home
  Assistant-apparaat in B.
- Het huidige tweestappenplan heeft na het effect geen derde teststap; de
  dependencyvrijgave gebruikt en valideert dezelfde generieke topologische
  regel als de bestaande readvrijgave, maar een echte langere HA-keten blijft
  onderdeel van MX04.
- Geen latency-, geheugen- of kostenoptimalisatieclaim; deze metrics zijn
  `unmeasured`.
- Zonder onafhankelijke review is dit niet `reviewed`. Zonder Telegram-B,
  fysieke Home Assistant-readback en crashinjectie is het niet `live_proven`.

## Eerstvolgend

1. Onafhankelijke review van MC03c3e en de eerdere open effectslices.
2. MX04: dezelfde algemene keten via echt Telegram-B en Home Assistant, met
   fysieke readback en een afhankelijke vervolgstap.
3. MX05: crash tussen effectacceptatie, readback en afronding; geen tweede
   provideractie en na herstel uitsluitend bewijsgebonden voortgang.
