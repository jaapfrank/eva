# CP-000 — onafhankelijke review van evaluatiemotorvoorselectie

Besluit: **GO**

- Taak: `CP-000` — Open-source evaluatiemotor voorselecteren
- Basecommit: `ae43849c4a71fc0755247b3185543528ccf21a68`
- Implementatiecommit: `f0eb745581ae63da22dbc6060d3477507fe39e8a`
- Testbewijscommit: `327033bb2ecd2ae5de644f6796cd605051a9dd82`
- Reviewer: `/root/workboard_review`
- Datum: `2026-09-02`

## Bevindingen

De implementatiecommit is het directe kind van de basecommit en wijzigt exact
de zes vooraf vastgelegde changePaths: het leesbare bord, het
benchmarkdocument, het dashboarddocument, de machineleesbare workboardbron, de
workboardtests en de checker. De testbewijscommit is het directe kind van de
implementatiecommit en voegt uitsluitend
`eva2/evidence/product/CP-000-open-source-eval-engine-selection-2026-09-02.json`
toe. Dat receipt bindt taak, basecommit en volledige implementatiecommit.

De funderingsvolgorde van `GOV-001` tot en met `EV-003` is zowel als vaste
codevolgorde als via een digest van identiteit en volledige onveranderlijke
kaartscope gebonden. Daardoor falen productinserties vóór `RB-010` en tussen
`RB-010` en `EV-003`, ook wanneer iemand tegelijk alleen het bord probeert aan
te passen. Herbestemmen van een funderingsmijlpaal of de changePaths,
evidence-eisen en doneWhen van een funderingsuitvoerkaart faalt eveneens.

Nieuwe uitvoerkaarten mogen geen van de drie historische
`ComparableAgentRun/v1`-validatorpaden in changePaths of testcommando's noemen.
Het benchmarkdocument noemt het formaat uitsluitend als afgekeurde historie,
wijst verwijdering toe aan `OS-001` en laat `EV-001` tot en met `EV-003` het
oude formaat niet lezen. Het dashboard toont later alleen projecties uit
ongewijzigde, hashgebonden Inspect-logs en introduceert geen tweede scorer of
benchmarkformaat.

De actuele officiële upstreamrefs zijn onafhankelijk gecontroleerd. Inspect AI
tag `0.3.261` wijst naar
`f9186b4e2f34ca81f192ae2c08535c24b7e8f356` en `main` naar
`e2a8e854d95f70c0969930fbc0a6455bebad99b6`; promptfoo tag `0.122.2` wijst
naar `89052308bce06f53645b1f189ada5ac9d1897347` en `main` naar
`48a71cd0163b01ba8efb2954eb0165dd810a6c6e`; OpenAI Evals `main` wijst naar
`8eac7a7de5215c907fbddc30efdaf316913eccdd` en de GitHub-releasepagina bevat
geen releases. De officiële Inspect-documentatie bevestigt de afzonderlijke
Python- en sandbox/CLI-agentbruggen en de eval-logroute. De officiële
promptfoo-documentatie bevestigt tracecorrelatie en trajectory-assertions, maar
ook de extra exporter/instrumentatie voor de Python Agents-route. Daarmee is
Inspect als voorlopige eerste bridgekandidaat uitvoerbaar en eerlijk
onderbouwd, zonder definitieve adoptie te claimen.

De closurechecks zijn zonder pytest-, bytecode- of Ruff-cache herhaald:

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q -p no:cacheprovider tests/operations/test_current_workboard.py` — **51 passed**
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/check_current_workboard.py` — **valid**, `CP-000` actief en `RB-007` hierna
- `ruff check --no-cache tools/check_current_workboard.py tests/operations/test_current_workboard.py` — **All checks passed!**
- `git diff --check` — **passed**
- `WERKBORD.md` en `--render-human` — beide SHA-256 `a67292a5d74a8cf6ef42deb9e326e6ae67a4277022fc05b14fb8340b24e12d8e`

## Nonclaims

- Inspect AI is nog niet definitief gekozen, geïnstalleerd of aan Eva's
  productie-image toegevoegd; `EV-001` moet beide echte agentbruggen en de
  log-/her-scorecyclus eerst bewijzen.
- CP-000 levert geen vijfwegmeting, benchmarkscore, concurrentiewinst of claim
  dat Eva beter is.
- De historische `ComparableAgentRun/v1`-bestanden zijn niet verwijderd en een
  bestaande release-regressie mag de oude validator nog uitvoeren; zij zijn
  uitsluitend uitgesloten als bron voor nieuw uitvoerbewijs.
- Deze review installeert of start geen evaluatiemotor, agent, container,
  runtime, database, provider of benchmarktaak en bewijst geen upstream
  supply-chainprovenance.
- Bestaande untracked living-core-evidence wordt niet geclassificeerd,
  goedgekeurd of onderdeel van CP-000 gemaakt.
