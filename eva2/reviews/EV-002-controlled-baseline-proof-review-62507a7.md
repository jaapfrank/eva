# EV-002 — onafhankelijke proofreview van de gecontroleerde nulmeting

Besluit: **GO**

- Taak: `EV-002`
- Implementatiecommit: `62507a7d4caa034bb3d30f8ed23efd61ab0eaba0`
- Evidencecommit: `177c7bac7701a53cb98ca89c464dc67731292233`
- Basiscommit: `b3c119849d277abb744030de41af54b4209c423d`

De commitketen is direct. De implementatie raakt exact de zes toegestane
`changePaths`; de evidencecommit voegt uitsluitend het vereiste openbare
EV-002-resultaat toe. Plan en resultaat zijn met dezelfde SHA-256 aan elkaar
gebonden. De vier deelnemers, broncommits, taak, model, reasoningniveau,
nul-toolsgrens, nul retries en call-, token- en tijdbudgetten zijn vooraf gelijk
vastgelegd.

De poging deed in totaal **twee modelcalls**: één via Eva2 en één via Hermes.
Beide antwoorden zijn niet leeg, hun hashes kloppen en ze voldoen volgens de
drie vooraf vastgelegde eenvoudige criteria. Hermes bewaart daarnaast een
native usagebestand met één call, 807 inputtokens, 139 outputtokens en 946
tokens totaal.

Eva's bewijs is aantoonbaar zwakker: haar antwoord en gemeten gebruik waren
zichtbaar voor de operator, maar het oorspronkelijke disposable-stackreceipt
kon door verkeerde directoryrechten niet worden opgeslagen. Het resultaat
noemt dit eerlijk `completed_receipt_degraded`, claimt geen wandkloktijd of
providerfactuur en reconstrueert het ontbrekende receipt niet. Daardoor telt
Eva's antwoord voor deze kleine nulmeting, maar niet als even sterk native
gebruiksbewijs als Hermes.

OpenClaw en Agent Zero vielen beide **vóór de modelcall** uit. OpenClaw kon de
geladen Codex-harness niet registreren; bij Agent Zero kon de vooraf vereiste
nul-tools-/nul-retrygrens niet op verzoekniveau worden afgedwongen en hield de
harness de prompt daarom tegen. Beide resultaten bewaren nul calls, nul tokens,
nul kosten en geen antwoord. Dit zijn technische admission failures, geen
inhoudelijke nederlagen.

Herhaalde controles:

- `pytest -q tests/benchmark/test_inspect_ai_ev002.py`: 20 passed.
- `ruff check benchmark/inspect_ai_v1/ev002_baseline.py experiments/managed-b-clean-graph-v1/probe.py tests/benchmark/test_inspect_ai_ev002.py`: passed.
- `python3 tools/check_component_catalog.py`: ok, nul overtredingen.
- De twee repository-inventorytests: passed vanaf de repositoryroot.
- `git diff --check` voor basis→implementatie en implementatie→evidence: passed.
- De resultaatvalidator accepteert het openbare bewijs; antwoordhashes,
  plandigest en totalen van twee calls, 3.101 inputtokens en 249 outputtokens
  zijn onafhankelijk herberekend.

Toegestane claim: dit is één begrensde C04-ontwikkelnulmeting met twee geldige
antwoorden en twee concrete technische uitvallen. Niet toegestaan en ook niet
geclaimd: een vierweg-inhoudsvergelijking, productranglijst, officiële
benchmarkscore, algemene winnaar, concurrentiewinst of cross-domainkwaliteit.
Er is geen betaalde modeljury gebruikt en er is geen zelfbedachte totaalscore.
