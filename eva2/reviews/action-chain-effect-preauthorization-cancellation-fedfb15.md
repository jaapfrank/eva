# Onafhankelijke review — annuleren vóór een effect wordt gestart

Datum: 26 augustus 2026

Broncheckpoint: `fedfb15cfa73d96820b8868fa75966b076edeb60`

Oordeel: **GO voor een lokaal geteste annulering vóór autorisatie; NO-GO voor
stoppen van al gestarte acties of voor product/live gebruik**

## Wat onafhankelijk is gecontroleerd

- Zeven gerichte scenario's zijn groen op een tijdelijke PostgreSQL 16
  database, waaronder eigenaarcontrole, gelijktijdige annulering versus
  autorisatie, databasefout en herstart.
- Als annulering wint, ontstaat geen effectpoging, receipt, readback of
  providerproces.
- Als autorisatie al heeft gewonnen, geeft Eva eerlijk `already_dispatched`.
  Zij doet dan niet alsof een mogelijk al gestart extern effect nog stopbaar is.
- De annulering bewaart eigenaar, binding en verificatie als onveranderlijk
  historisch bewijs. Intrekken van die binding ná annulering maakt het oude
  bewijs niet ongeldig.
- De ledger weigert een vervalste eigenaarbinding in het opgeslagen
  annuleringsevent.

## Wat dit precies oplevert

De geverifieerde eigenaar kan de **laatste voorbereide actie** weghalen zolang
Eva die actie nog niet voor uitvoering heeft vrijgegeven. Annulering en
vrijgeven kunnen gelijktijdig worden aangevraagd, maar er kan maar één winnen.
Dat voorkomt dat een geannuleerde actie later alsnog een providerproces start.

## Harde bewijsgrens

Dit is een duurzame PostgreSQL-bouwsteen, geen bedienbare stopknop. Er is nog
geen Telegram- of cockpitknop, geen B-deploy en geen live providerproef.
Zodra autorisatie of een provider-call is begonnen, is deze stap bewust niet
meer als annulering beschikbaar. Stoppen van een lopende browseractie, externe
terugdraaiing en reconciliatie blijven afzonderlijk open.
