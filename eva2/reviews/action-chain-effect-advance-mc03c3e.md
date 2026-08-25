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
- `5a1556e` — werkelijke driestappenketen, vervolgclaim en rollbackbewijs.
- `0ca0c4b` — geïntegreerd herstel over iedere procesgrens zonder dubbele call
  of vrijgave.

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
- Gerichte PostgreSQL 16.13-slice: 9 tests groen.
- De volledige reeks van twaalf action-chainmodules: 75/75 groen op een verse,
  2 GB begrensde PostgreSQL 16.13-database. Dit vervangt de eerdere onvolledige
  run die op een testschijf van 512 MB vastliep.
- Alle tijdelijke containers zijn exact op naam verwijderd; A, B en hun
  volumes zijn niet benaderd.

De negen MC03c3e-cases omvatten:

- een exacte match in een driestappenketen, replay na een nieuwe engine en een
  werkelijk claimbare vervolgstap;
- `not_observed`, `conflict` en `inconclusive` blijven gesloten;
- een providerclaim `matches` met de verkeerde werkelijke waarde blijft dicht;
- twee gelijktijdige callers produceren één advance;
- een geforceerde fout bij `STATE_VERIFIED` rolt effect, step én nog gesloten
  vervolgstap terug;
- een gewijzigd outcome-ref maakt ledgerreload rood.
- een geïntegreerde procesgrensproef gebruikt na effect, readbackvoorbereiding,
  readbackresultaat en afronding telkens een verse engine; zij telt exact één
  effectattempt/call-start, één readbackattempt/call-start, één fysieke
  readback, één `STATE_VERIFIED` en één dependencyvrijgave. Replay voegt niets
  toe.

## Eerlijke bewijsgrens

- De provider in deze slice is een sealed testprovider, niet het echte Home
  Assistant-apparaat in B.
- De langere keten is nog een sealed testketen. Dezelfde vervolgclaim via een
  werkelijk Home Assistant-apparaat blijft onderdeel van MX04.
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
