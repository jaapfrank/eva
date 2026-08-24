# Onafhankelijke live review — schema-installerprofiel

Broncommit: `03ec03a7659d` (schone `git archive`).

## Uitgevoerd

- Een unieke testimage, Compose-project, PostgreSQL-volume en netwerk zijn
  aangemaakt.
- Alleen `schema-installer` is uitgevoerd, met een nieuwe wegwerp-PostgreSQL,
  test-DNS en testcredentials in tijdelijke private bestanden.
- De container heeft de volledige route van een lege database doorlopen en
  daarna de Alembic-driftcontrole uitgevoerd.

## Receipt

```json
{"contract":"EvaSchemaInstallationReceipt/v1","drift_check":"no_new_upgrade_operations_detected","final_revision":"4e_0001_operational_timeline","source_revision":null,"status":"passed"}
```

De route bevatte de 4B-basis, de 4D-upgrades en de 4E-operationele tijdlijn.
De twee 4E-tabellen zijn daarmee ook deel van de metadata voor de
driftcontrole.

## Grens

Na afloop zijn uitsluitend de eigen testcontainer, het testvolume, netwerk,
image en tijdelijke directory verwijderd en afwezig gecontroleerd. De
bestaande B is niet gelezen, gestart of gewijzigd: er is geen B-context,
B-naam, globale containerlijst of B-volume gebruikt.
