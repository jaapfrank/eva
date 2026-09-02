# RR-001 — onafhankelijke review hermetische kandidaatbron

Besluit: **GO**

Beoordeelde keten:

- taak: `RR-001` — Hermetische kandidaatbron herstellen
- basis: `ca6de2b0c0fe7bde1fdf740979ca3959c3e998ae`
- implementatie: `daa9022d2ef9f8aefa7e22c1f90d7f6a0c463c6c`
- testbewijs: `78ad15c93869b663f2f55eaf1dbb15a828abbb77`
- release-inputauthority: `2383f9bfa9cb3b470fbd4908de7f8f33b98a287d64277c457d22286bd0c3dec0`
- reviewer: `/root/rr001_commit_review`
- datum: `2026-09-02`

## Bevindingen

De implementatiecommit is het directe kind van de basiscommit. De diff is exact
gelijk aan de dertien vastgelegde `changePaths`: de elf RR-001-bronpaden plus
uitsluitend de twee canoniek gegenereerde repository-inventoryuitgangen onder
`docs/`. De testbewijscommit is het directe kind van de implementatiecommit en
voegt uitsluitend
`eva2/evidence/product/RR-001-hermetic-candidate-source-2026-09-02.json` toe.
Het receipt bindt RR-001, de volledige implementatiecommit, de authorityhash,
beide inventoryhashes en de Noor-fixturehash.

De release-inputauthority bevat exact 316 release-inputs, 147 uitvoerbare
testmodules en 20 supportinputs. Alle drie padsets zijn gesorteerd, intern uniek
en onderling gescheiden; alle 483 genoemde blobs bestaan op de
implementatiecommit en iedere SHA-256-pin klopt. De Noor-fixture
`design/fixtures/noor-client-ack-key-v1.json` staat exact eenmaal en uitsluitend
als supportinput opgenomen. Haar SHA-256 is
`330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93`, haar
publieke JWK-digest klopt en de fixture is expliciet als publieke testfixture
zonder productieautorisatie gemarkeerd. De drie consumerende tests lezen haar
via hun eigen Eva2-root en niet meer uit een bovenliggende repository.

De kandidaatlokale componentcatalogustest blijft
`tests/design/test_component_catalog.py` en staat in `requiredTests`. De nieuwe
repositorybrede integratietest
`tests/design/test_component_catalog_repository.py` is daarvan afgesplitst en
staat in geen enkele authorityset. Daarmee blijft de volledige repository-CI
verplicht zonder de vlakke kandidaat aan `docs/`, `design/` of een huidige
checkout boven Eva2 te koppelen.

De controles zijn opnieuw uitgevoerd op uitsluitend bytes uit exact
`daa9022d2ef9f8aefa7e22c1f90d7f6a0c463c6c`, met bytecode- en pytestcache
uitgeschakeld:

```text
Volledige repositorykopie, alle zeven RR-001-testmodules: 119 passed
Vlakke Eva2-only kopie zonder bovenliggende repositorybestanden: 118 passed
tests/operations/test_managed_b_release_inputs.py afzonderlijk: 48 passed
python3 tools/check_component_catalog.py: ok=true, violations=[]
ruff check op alle gewijzigde Pythonbron- en testpaden: All checks passed!
```

De vlakke kopie had een willekeurige rootnaam. Daardoor is ook de nieuwe
layout-neutrale bronrootselectie daadwerkelijk geraakt; de kandidaatlokale
tests konden niet terugvallen op een sibling `eva2/` of bovenliggende
repositorydocumenten.

De implementatiediff bevat geen evidence- of reviewbestand. De historische
RB-003- tot en met RB-006-receipts zijn byte-ongewijzigd, staan niet in de
nieuwe authority en worden in het nieuwe testreceipt expliciet niet als bewijs
voor deze broncommit gebruikt. Het bewijs is dus een verse bron- en testrun,
geen hergebruik van historisch build-, reproduceerbaarheids- of scanbewijs.

## Nonclaims

- RR-001 bouwt, scant, signeert, publiceert, deployt of promoveert geen nieuw
  image en bewijst geen productie- of live-routegereedheid.
- De authority bindt testinputs; zij vervangt niet het afzonderlijke fysieke
  uitvoerbewijs van latere releasekaarten.
- Historische 4A-tools die bewust repository-rootgebonden blijven, worden niet
  als onderdeel van de vlakke kandidaat geclaimd.
- Bestaande ongetrackte living-core-evidence valt buiten deze review en is niet
  goedgekeurd of in RR-001 opgenomen.

## Conclusie

RR-001 voldoet op de volledige implementatiecommit aan de hermetische
kandidaatgrens, de exacte fixture- en authoritybinding, de scheiding tussen
kandidaat- en repository-CI en de eis om geen historisch bouw- of scanbewijs
als nieuw bewijs te hergebruiken. Er resteert geen reviewblokkade.
