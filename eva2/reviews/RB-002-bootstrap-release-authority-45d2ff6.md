# RB-002 — onafhankelijke review bootstrap-releaseauthority

Besluit: **GO**

Beoordeelde keten:

- basis: `5f0df55da7eb84c1c0575943bd21e2e26aa891a6`
- implementatie: `45d2ff6d61793809a514c01b3a3c000ce1632da6`
- evidence: `dff58c3fbe6d08addb83da86e4adb8d6df490000`
- RB-001-implementatiebinding: `d54dd0af97514acf6189ef7a1c0f66dafbb86086`

De implementatiecommit heeft de opgegeven basis als directe parent en wijzigt
uitsluitend de drie `changePaths` van RB-002. De evidencecommit heeft de
implementatie als directe parent en voegt uitsluitend het opgegeven
evidencebestand toe.

## Bevindingen

- Alle drie RB-001-bootstrapbronnen en alle drie bijbehorende aanvalstests
  staan exact eenmaal in de release-inputauthority met hun SHA-256.
- De zes gepinde bytes op de RB-002-implementatiecommit zijn bytegelijk aan
  dezelfde zes bestanden op RB-001-implementatiecommit `d54dd0af`.
- De release-inputreceipt bindt zowel die zes paden en bytes als de expliciete
  RB-001-implementatiecommit.
- Een ontbrekend bootstrapbron- of testpad wordt voor elk van de zes paden
  afzonderlijk geweigerd.
- De bestaande authorityvalidatie weigert ontbrekende en extra authoritypins
  en gewijzigde bron- of testbytes. Daardoor kan een pad niet stil worden
  verwijderd, toegevoegd of gewijzigd.
- De opnieuw gegenereerde receipt bindt broncommit `45d2ff6d`, authorityhash
  `20323cc1…`, 316 inputs, 147 tests en 19 testsupportinputs. Dit komt overeen
  met het publieke bewijs.
- De nonclaims zijn juist begrensd: deze statische verzegeling bewijst geen
  imagebuild, scan, release, deployment of live-routewijziging. RB-003 blijft
  de afzonderlijke fysieke builder-A-stap.

## Herhaalde controles

```text
TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_managed_b_release_inputs.py
48 passed in 2.10s

ruff check tools/managed_b_candidate/release_inputs.py tests/operations/test_managed_b_release_inputs.py
All checks passed!
```

Ook `git diff --check` is schoon voor zowel basis naar implementatie als
implementatie naar evidence. Er zijn geen live routes, containers, services of
andere runtimeonderdelen geopend of gewijzigd.

## Conclusie

RB-002 voldoet aan alle `doneWhen`-voorwaarden. De na RB-001 bewezen
bootstrapcode en aanvalstests zijn volledig, bytevast en commitgebonden in de
release-inputauthority opgenomen. Er is geen resterende reviewblokkade.
