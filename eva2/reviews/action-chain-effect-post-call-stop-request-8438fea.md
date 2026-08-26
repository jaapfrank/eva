# Onafhankelijke review — stopverzoek ná een gestarte effectcall

Datum: 26 augustus 2026

Broncheckpoint: `8438fea`

Oordeel: **GO voor een lokaal geteste, duurzame stopopdracht ná call-start;
NO-GO voor proceskill, rollback of product/live gebruik**

## Wat onafhankelijk is gecontroleerd

- De eigenaar kan pas na de duurzame `PROVIDER_CALL_STARTED`-grens één apart
  stopverzoek opslaan. De actie, poging en provideruitkomst veranderen daardoor
  niet.
- Stopverzoek en binnenkomend providerresultaat gebruiken dezelfde lockvolgorde.
  Zij krijgen dus één duurzame volgorde; er komt geen tweede providercall vrij.
- Een bestaande stopopdracht blijft na een latere intrekking van de binding
  leesbaar voor exact dezelfde historische eigenaar. Een nieuw verzoek vereist
  daarentegen nog steeds een actuele, geverifieerde binding.
- De opgeslagen stopcontext zit vast aan de oorspronkelijke call-start, het
  plan, de stap, de historische eigenaar/binding én de volledige
  checkpointinhoud. Zestien afzonderlijke contextvervalsingen en één vervalste
  checkpointkop werden met opnieuw berekende hash alsnog gesloten geweigerd.
- `ruff`, `py_compile` en `git diff --check` zijn groen. De gerichte suite
  draaide op een tijdelijke PostgreSQL 16-database: **8/8 groen**.

## Wat dit precies oplevert

Na een al gestarte browser- of huiscall kan Eva eerlijk bewaren: “stop gevraagd;
het resultaat is nog niet bekend”. Zij start die call niet opnieuw. Het echte
providerresultaat en de onafhankelijke readback blijven de waarheid bepalen.

## Harde bewijsgrens

Dit beëindigt geen lopend proces en draait geen externe klik of huisactie terug.
Er is nog geen Telegram- of cockpitbediening, geen B-deploy, geen echte
browserstop en geen live providerbewijs. De passende gebruikersmelding blijft:

> Je stopopdracht is bewaard. De actie kan al onderweg zijn. Ik start niets
> nieuws en controleer wat er werkelijk is gebeurd.
