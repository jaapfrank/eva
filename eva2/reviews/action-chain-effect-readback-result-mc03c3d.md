# MC03c3d — opgeslagen waarneming als canoniek readbackbewijs

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested` op `76404a4`.
- Onafhankelijke review: **open**. Dit document is review-input en keurt zijn
  eigen implementatie niet goed.
- Live bewijs: niet uitgevoerd. Productie A, kandidaat B, netwerk en Home
  Assistant zijn niet benaderd of gewijzigd.
- Optimalisatie: codeomvang is gemeten; latency, geheugen en kosten zijn
  `unmeasured`.

## Wat deze stap doet

MC03c3d neemt alleen een al opgeslagen, getypeerde readbackwaarneming. Zonder
een nieuwe providercall bindt zij die aan het exacte effect, geaccepteerde
receipt, controleverzoek, controlepoging, providerresource, idempotency key en
bewijs. Daarna schrijft zij precies één onveranderlijk `EffectReadback/v1`.

De werkelijk waargenomen toestand wordt genormaliseerd en canoniek gehasht.
Ook `not_observed`, `conflict` en `inconclusive` blijven daardoor eerlijk
zichtbaar; alleen het bestaan van een readbackrij betekent nog niet dat het
gevraagde effect gelukt is.

## Bestanden en omvang

| Bestand | Rol |
|---|---|
| `src/eva/application/action_chain_effect_readback_results.py` | Pure, providerloze projectie van bewaarde bytes naar het canonieke contract |
| `src/eva/adapters/postgres/action_chain_effect_readback_results.py` | Enige writer; lockt de poging, herlaadt de keten en schrijft maximaal één bewijsrij |
| `src/eva/adapters/postgres/action_chain_effect_readback_validation.py` | Reconstrueert en vergelijkt het contract bij iedere ledgerreload |
| `src/eva/adapters/postgres/action_chain_effect_result_validation.py` en `action_chain_effect_state_validation.py` | Laten alleen de ene geldige bewijsrij naast een geaccepteerd effect toe |
| `tests/postgres/test_action_chain_effect_readback_results.py` | Correctheid, statussen, replay, concurrency, rollback, afwezig bewijs en corruptie |

- Netto productgroei: 515 regels; testgroei: 322 regels.
- Nieuwe productbestanden: 122 en 280 regels; geen bestand overschrijdt de
  800-regelgrens.
- Geen schema, migratie of dependency toegevoegd.

## Uitgevoerde controles

- Acht gerichte cases op een verse tijdelijke `postgres:16.13`-database met de
  verplichte `eva2_action_chain_disposable_`-prefix; na de importgrenscorrectie
  opnieuw groen.
- De bredere keten had daarvoor 67 action-chaincases groen; 24 tijdlijntests
  zijn afzonderlijk groen op een database met `eva2_living_disposable_`-prefix.
- `matches`, `not_observed`, `conflict` en `inconclusive` bewaren hun exacte
  status en de hash van de werkelijke toestand of `null`.
- Twee gelijktijdige writers leveren één `recorded` en één
  `already_recorded`; er blijft precies één bewijsrij staan.
- Replay via een nieuwe engine retourneert hetzelfde contract en schrijft geen
  nieuw tijdlijnevent.
- Een geforceerde insertfout rolt volledig terug. Een gewijzigde statehash maakt
  de hele ledgerreload corruption-sensitive rood.
- Een wel geautoriseerde maar nog niet waargenomen controle kan geen fysiek
  bewijs maken.
- Ruff, compileall en `git diff --check` zijn groen; tijdelijke containers en
  databases zijn verwijderd.

De repositorybrede architectuurchecker blijft rood op twintig reeds aanwezige
overtredingen buiten deze slice. Na verplaatsing van de pure projector naar de
applicationlaag voegt MC03c3d zelf geen nieuwe overtreding toe. Daarom wordt
geen groene repositorybrede architectuurclaim gedaan.

## Bewijsgrenzen

- De succeswaarneming is synthetisch; er was geen live Home Assistant-call.
- `EffectReadback/v1` bewijst hier dat providerbytes duurzaam en exact aan de
  juiste actie zijn gebonden. Het vergelijkt nog niet zelf de gewenste toestand
  met de waargenomen toestand.
- Er wordt geen `STATE_VERIFIED` geschreven. Effect, stap, continuation en
  dependencies blijven gesloten en `running`.
- Er is geen onafhankelijke review, B-deploy, Telegramproef of A/B-adoptie.

## Open vervolg

1. Projecteer de gewenste doeltoestand uit het effectcontract en vergelijk die
   typevast met de waargenomen toestand.
2. Alleen een verse exacte match mag in één transactie `STATE_VERIFIED`, effect,
   stap, continuation en vrijgegeven dependencies voltooien.
3. Mismatch, onduidelijkheid of ontbrekende waarneming blijft gesloten en krijgt
   een expliciete vervolg- of hulpstatus zonder het effect blind te herhalen.
4. Laat de volledige MC03c-keten onafhankelijk reviewen en voer daarna de echte
   Telegram-B → Home Assistant-proef met crash tussen effect en readback uit.
