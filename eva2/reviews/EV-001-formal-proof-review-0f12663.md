# EV-001 — formele onafhankelijke proofreview

Besluit: **GO**

- Task: `EV-001`
- Implementatiecommit: `0f126633a429b03ca83786203d08289d67368521`
- Evidencecommit: `effff17c33e6212d643e208e67cf4d6b53bc520c`
- Boardbasis: `8569871d9713662c5b3a3ababf6ce58b56c13b6b`

De commitketen is direct en de implementatie raakt exact de vijf toegestane
`changePaths`. De evidencecommit trekt het eerdere, onvoldoende bewijs eerlijk
in en voegt alleen het nieuwe EV-001-bewijs toe.

De Inspect AI-release `0.3.263` is exact gelockt voor CPython 3.12 op Linux
x86_64. De lockhash, implementatieboom en hashes van de daadwerkelijk gebruikte
probe- en lockbytes sluiten op commit `0f126633a429b03ca83786203d08289d67368521`.
Een dirty wijziging van probe of lock wordt vóór uitvoering geweigerd; de eerdere
dirty-checkoutaanval slaagt daardoor niet meer.

Het bewaarde private receipt en beide eval-logs onder
`/home/administrator/project/.eva2-artifacts/ev001-inspect-bridge-20260904`
sluiten bytegelijk op het openbare bewijs. Zowel de Python-agentbrug als de
sandbox/CLI-agentbrug liep precies eenmaal met `mockllm/model`. De sandbox is
vastgezet op de digestgebonden image, `network_mode=none` en
`pull_policy=never`; er waren nul betaalde modelcalls. De originele log bleef
bytegelijk tijdens de afzonderlijke rescore en beide samples scoorden correct.

Het private receipt bevat volledige, hashgebonden lijsten van containers,
netwerken en volumes vóór en na de proef. De hashes zijn onafhankelijk
herberekend, alle drie delta's zijn leeg en de actuele read-only telling komt
overeen met de na-inventaris. Daarmee bleef geen door EV-001 toegevoegd
Docker-object achter.

Herhaalde controles:

- `pytest -q tests/benchmark/test_inspect_ai_ev001.py`: 13 passed.
- `ruff check benchmark/inspect_ai_v1/bridge_probe.py tests/benchmark/test_inspect_ai_ev001.py`: passed.
- `git diff --check` voor basis→implementatie en implementatie→evidence: passed.

Deze review bevestigt alleen EV-001: de twee Inspect-bruggen en de
log/rescorecyclus. Zij bewijst geen betaalde modelvergelijking,
concurrentiewinst, productfunctie of live uitrol.
