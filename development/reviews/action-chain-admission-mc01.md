# Onafhankelijke review — meerstapsplanning MC01

Datum: 25 augustus 2026.

## Scope

- `src/eva/contracts/planning.py`
- `src/eva/application/action_chain.py`
- `tests/application/test_action_chain.py`
- de bijbehorende architectuur- en matrixregels

MC01 hergebruikt `ModelDecision/v1`, `PlanModelInput/v1` en
`ValidatedPlan/v1`. Er is geen tweede plancontract, runner of databasepad
toegevoegd.

## Teamcontrole

- De onafhankelijke architectuurreview gaf GO voor een pure host-validator en
  onveranderlijke capabilitycatalogus rond de bestaande contracten.
- De onafhankelijke codeverifier gaf eerst NO-GO op onvolledige URI-/command-
  filtering en ontbrekende volledige catalogusbinding.
- Na regressies voor algemene URI-schemes, command- en executablevarianten en
  content-addressed binding van gekozen én ongekozen catalogusentries gaf
  dezelfde verifier GO.

## Zelfstandig herhaalde checks

- Onafhankelijke verifier: 27 gerichte tests groen, Ruff groen en
  `git diff --check` schoon.
- Implementatiecheck: 51 tests groen over MC01 en de contract-/schemagates,
  Ruff groen, volledige `src/eva` compile groen en diff-check schoon.

## Bewijsgrens

Dit bewijst planvalidatie, begrenzing en deterministische identiteit. Het
bewijst nog geen PostgreSQL-opslag, uitvoering, providerreadback, Telegram-
gedrag, Home Assistant-effect of crashherstel. Die volgen als MC02, MC03 en
MX04/MX05.
