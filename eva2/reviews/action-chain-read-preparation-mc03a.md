# Onafhankelijke review — MC03a readvoorbereiding

Datum: 25 augustus 2026.

## Afbakening

- Eén geldige, geleasede `read`-stap omzetten in precies één duurzaam
  `read_intent`.
- Geen provider aanroepen, geen readpoging of waarneming maken en geen effect
  voorbereiden.
- Live A en B niet benaderen of wijzigen.

## Bewezen

- Het claimtoken bindt attention, revision, worker, expiry, plan en stap.
- Plan, stap, volledige actuele catalogus, argumenten en scope worden opnieuw
  gevalideerd.
- Activation, connectorbinding en policy blijven met PostgreSQL `FOR SHARE`
  tot de prepare-commit gebonden; het capabilitymanifest is onveranderlijk en
  via de gelockte activation gebonden.
- Eén transactie maakt een deterministisch `read_intent`, zet de read-stap op
  `running`, zet de continuation op `waiting` en schrijft hetzelfde prepare-
  event naar child- en parenttijdlijn.
- Een geforceerde fout halverwege laat nul resten achter. Twee gelijktijdige
  schrijvers leveren één `prepared` en één bytegelijke `already_prepared`.
- Een echte tweede verbinding kon de activation tijdens prepare niet
  intrekken: de update blokkeerde en liep gecontroleerd tegen haar timeout.
- Na een nieuwe database-engine wordt exact dezelfde prepare herkend; een
  afwijkend of stale token en gewijzigde authority worden geweigerd.

## Uitgevoerde controles

- `tests/postgres/test_action_chain_ledger.py`: 2 groen op een tijdelijke
  PostgreSQL 16.13, inclusief rollback, concurrency, herstart en TOCTOU-race.
- Applicatie- en contractset: 57 groen.
- `tests/postgres/test_operational_timeline.py`: 24 groen.
- Ruff, compile en `git diff --check`: groen.
- De onafhankelijk live herhalende reviewer gebruikte een eigen PostgreSQL
  16.13-`tmpfs`-container en verwijderde die daarna.

## Reviewbesluit

- Onafhankelijke implementatiereview: **GO**.
- Onafhankelijke architectuur- en TOCTOU-herreview: **GO**.

## Niet geclaimd

- Geen providerdispatch of providerreceipt.
- Geen Home Assistant-read, effect of fysieke readback.
- Geen dependency-advance of volledige ketenrunner.
- Geen Telegram-, B-, eindimage- of product-livebewijs.
