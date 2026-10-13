# GOV-003 — onafhankelijke review van canonieke schrijfboom

Besluit: **GO**

- Taak: `GOV-003` — Eva2-werk tot één zichtbare schrijfboom begrenzen
- Basecommit: `458631cb022efb44adfb0b2cf4abac3c07eacc41`
- Implementatiecommit: `dc4757338165effa0aff97f9d0d6e0b5c6070c35`
- Testbewijscommit: `dce1c11805bf9429ceaf5e72fce4a32b0d110a0f`
- Reviewer: `/root/workboard_review`
- Datum: `2026-09-02`

## Bevindingen

De implementatiecommit is direct gebaseerd op de basecommit en wijzigt exact de
vijf vooraf vastgelegde kaartpaden: `TEAM_WERKWIJZE.md`, `WERKBORD.md`, de
machineleesbare workboardbron, de workboardchecker en zijn gerichte testbestand.

De testbewijscommit is het directe kind van de implementatiecommit en voegt
uitsluitend
`eva2/evidence/product/GOV-003-canonical-workspace-containment-tests-2026-09-02.json`
toe. Dat receipt bindt taak, basecommit en volledige implementatiecommit en
registreert de uitgevoerde positieve en negatieve controles.

De checker beperkt open kaarten tot paden onder de canonieke Eva2-authoringroot.
Een bekend in-scope pad mag gewijzigde bytes hebben zonder door een oude
inventoryhash te worden geblokkeerd; een ontbrekend pad, ontbrekende
inventory-entry, verkeerde inventory-eigenaar, verkeerde componenteigenaar en
een pad buiten Eva2 blijven fail-closed. De volledige artifactregel met
eigenaar, doel, retentiepad en opruimmoment is onderdeel van het exacte
workboardcontract en kan niet worden verwijderd of verzwakt. Het leesbare bord
is exact uit de machineleesbare bron gerenderd.

De controles zijn onafhankelijk opnieuw uitgevoerd zonder pytest- of Ruff-cache:

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q -p no:cacheprovider tests/operations/test_current_workboard.py` — **45 passed**
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/check_current_workboard.py` — **valid**, `GOV-003` actief en `CP-000` hierna
- `ruff check --no-cache tools/check_current_workboard.py tests/operations/test_current_workboard.py` — **All checks passed!**
- `git diff --check` — **passed**
- `WERKBORD.md` en `--render-human` — beide SHA-256 `567fe0c8c255fbd5b9eee8ae21f888a9d833c85b5f7da5d6892561da4faa93a7`

## Nonclaims

- Deze review verwijdert of verplaatst geen historische worktrees, artifacts of
  bestaande untracked evidence; zij beoordeelt uitsluitend de authoringpolicy en
  workboardbegrenzing van GOV-003.
- Deze review voert geen Docker-, runtime-, database-, secret- of live
  providercontrole uit.
- De bestaande untracked living-core-evidence wordt niet door deze review
  geclassificeerd, goedgekeurd of onderdeel van GOV-003 gemaakt.
