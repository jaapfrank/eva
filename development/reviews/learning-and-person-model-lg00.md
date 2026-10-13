# Onafhankelijke review — leren en persoonsmodel LG00

Datum: 25 augustus 2026.

## Scope

- `design/product/learning-and-person-model-v1.md`
- `CONCURRENTIE_EN_HERGEBRUIK.md`
- `PROJECT_STATUS.md`
- `EVA2_MOGELIJKHEDEN_EN_GATEN.md`
- de persoonsmodelrij in `design/architecture/living-core-v1.md`

De reviewer wijzigde geen bestanden en benaderde geen B-service of secrets.

## Eerste oordeel

`NO-GO`. De eerste versie suggereerde bij M01 een echt Telegramnetwerk, liet
relatie-leren onvoldoende los van identiteit/toegang, specificeerde vergeten
niet over alle geheugenlagen, liet onbekende usage mogelijk door een promotie
heen en kon met benoemde per-persoonusage gedragsmetadata lekken.

## Correcties

- M01 is begrensd tot de definitieve connector tegen een lokale Telegram Bot
  API-contractprovider; dit is geen Telegramnetwerkbewijs.
- Leren kan geen principals, bindings, groepslidmaatschap, audience, consent,
  eigenaarschap of permissions wijzigen. Relatie-afleidingen blijven private,
  onbevestigde kandidaten en geven nooit toegang.
- Erasure omvat Hindsight, actieve PostgreSQL-projecties, samenvattingen,
  caches en procedurele kandidaten. Alleen een non-content tombstone blokkeert
  replay en herintroductie; restore mag gewiste inhoud niet reactiveren.
- Onbekende token-, kosten-, latency- of opslagusage weigert automatische
  promotie en maakt LG07 rood.
- Usageweergave blijft in de eigen scope; alleen expliciet geautoriseerde,
  niet-herleidbare installatieaggregaten zijn toegestaan.

## Eindoordeel

`GO`. LG00 onderscheidt bestaand Eva-bewijs van de open LG01-LG07-gates,
begrensd Hermes correct als gedocumenteerd maar niet door ons gelijk live
bewezen, en sluit de vijf genoemde risico's fail-closed. Dit oordeel bewijst
geen geïmplementeerde zelfleercyclus; LG01-LG07 blijven open.
