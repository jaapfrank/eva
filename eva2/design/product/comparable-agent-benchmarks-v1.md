# Eva 2 — extern vergelijkbare agentbenchmarks

Bijgewerkt: 27 augustus 2026.

## Eerlijke conclusie

AgentBench, GAIA en OSWorld zijn samen een goede openbare basis, maar zij meten
niet hetzelfde en leveren dus geen eerlijke zelfbedachte totaalscore op.

- **AgentBench** meet agentgedrag in meerdere afgeschermde omgevingen, waaronder
  OS, database, knowledge graph, ALFWorld en WebShop. De actuele repository is
  de function-callingvariant; de exacte commit en alle taakimages moeten bij de
  run worden vastgezet.
- **GAIA** meet algemeen assistentwerk: webonderzoek, redeneren, bestanden,
  multimodaliteit en toolgebruik met een kort, automatisch te beoordelen
  eindantwoord. Het zegt weinig over echte mutaties, crashherstel of GUI-besturing.
- **OSWorld 2.0** meet lange computerworkflows in echte desktop- en webapps. De
  actuele aanbevolen release is `v2026.08.08`; code, gated taken, assets,
  websites en providerimages moeten uit precies diezelfde release komen.

Deze drie worden Eva's **publiek vergelijkbare scorelaag**. LAB-36, MX01-MX06,
presence, voice, privacy, Home Assistant-readback en de zevendaagse residentrun
blijven daarnaast nodig, maar hun uitslagen mogen nooit als officiële
AgentBench-, GAIA- of OSWorldscore worden gepresenteerd.

## Twee scoreborden, nooit vermengen

| Scorebord | Vraag | Verplichte gelijkheid |
|---|---|---|
| **Officieel/native** | Hoe scoort iedere agent met zijn openbaar beschreven standaardopstelling? | Exact dezelfde benchmarkrelease, split en officiële scorer; model, tools en budget mogen verschillen maar staan zichtbaar bij de score |
| **Gecontroleerd/framework** | Welk agentframework presteert beter wanneer de belangrijkste externe variabelen gelijk zijn? | Zelfde modelrevision, provider, reasoning/temperatuur, contextgrens, tool- en stapbudget, VM/browserimage, hardwareklasse, netwerkbeleid en benchmarkrelease |

Eva wordt in de gecontroleerde matrix minimaal naast OpenClaw, Hermes, Agent
Zero en OpenBot gezet. Andere open-source agents worden toegevoegd wanneer hun
bron, licentie en benchmarkadapter reproduceerbaar zijn. Een native score mag
niet als frameworksnelheid of frameworkkwaliteit worden uitgelegd wanneer een
ander model, budget of computerimage is gebruikt.

## Publicatiecontract per run

Iedere score krijgt één heropenbaar manifest met:

- benchmarknaam, upstream Git-commit/tag, dataset-/assetrevision en scorerhash;
- split, volledige taaklijst en expliciete lijst van skips, retries en uitval;
- agentcommit, image digest, adapterhash, systeem-/toolcatalogushash en config;
- model-ID en waar beschikbaar exacte revision, provider en reasoninginstelling;
- context-, token-, stap-, tijd-, retry- en kostenbudget;
- OS/VM/browser/providerimage, hardwareklasse, netwerk- en datumgrens;
- seed en alle ruwe trajecten, toolcalls, screenshots/video's en eindantwoorden;
- ongewijzigde officiële hoofdscore en uitsplitsing per level/domein;
- totale tokens, kosten, wall time, fouten en aantal menselijke ingrepen als
  afzonderlijke secundaire metrics;
- cryptografische hashes, runnercommando en onafhankelijke replay/review.

Een run met ontbrekende taken krijgt geen geëxtrapoleerde volledige score. Bij
stochastisch gedrag publiceren we alle individuele runs en daarnaast gemiddelde
en spreiding; alleen het beste resultaat tonen is ongeldig. Verborgen
testantwoorden worden niet opgeslagen in Eva's runtime of ontwikkelcontext.

## Claimregels

- “Eva scoort hoger dan agent X op GAIA” mag alleen bij dezelfde GAIA-release,
  split en scorer, met duidelijk label `native` of `controlled`.
- OSWorld 1/Verified, OSWorld 2.0 en verschillende 2.0-releases krijgen aparte
  tabellen; scores worden nooit tussen versies omgerekend.
- AgentBench original en AgentBench FC krijgen eveneens aparte scoreborden.
- Er komt geen zelfgewogen gemiddelde van AgentBench, GAIA, OSWorld en LAB-36.
- “Eva is beter” vereist vooraf vastgelegde productcriteria én de relevante
  scorevector; één gewonnen benchmark is geen algemene productclaim.
- Een officiële leaderboardinzending of upstream-verificatie heeft voorkeur.
  Is dat niet mogelijk, dan moeten een onafhankelijke reviewer en alle openbare
  artifacts de lokale score kunnen reproduceren.

## Kleine benchmarkgates

- [x] CB00 — AgentBench, GAIA en OSWorld op primaire bronnen afgebakend;
  benchmarkrollen, actuele OSWorldrelease en nonclaims zijn vastgelegd. Dit is
  documentatiebewijs, geen uitgevoerde score.
- [x] CB01 — Eén `ComparableAgentRun/v1`-schema en fail-closed validator voor
  alle hierboven verplichte provenance-, configuratie- en resultaatvelden.
  De validator weigert onder meer een ontbrekende upstream-/scorerhash,
  mutabele image, ontbrekend answer/log/tooltrace-artifact, padtraversal en een
  score op een nog `planned` run. Schema en validator staan in
  `../benchmark/comparable-agent-run-v1.schema.json` en
  `../benchmark/validate_comparable_agent_run.py`. Dit registreert alleen
  controleerbare resultaten; CB02-CB07 en iedere echte score blijven open.
- [ ] CB02 — Dunne Eva-adapters voor de officiële runnerinterfaces; geen
  taaknamen, antwoorden of evaluatieregels in Eva's prompts of productcode.
  De GAIA-developmentadapter is hiervoor als eerste deel gereed:
  `../benchmark/run_gaia_development.py` leest uitsluitend een door de eigenaar
  geautoriseerde lokale metadata-JSONL of actuele Parquet-export en geeft de
  agent alleen `task_id`, `Question` en optioneel bestaand lokaal bestand. De
  Parquet-route vraagt Arrow expliciet om alleen die drie kolommen; `Final
  answer` wordt dus niet geladen. De geïsoleerde benchmark-lock
  `../benchmark/requirements-gaia-benchmark-linux-amd64-py312.lock` installeert
  offline met hashes `datasets 5.0.1` en `pyarrow 25.0.1` plus hun transitive
  wheels. Hij schrijft het officiële antwoordpaar `task_id`/`model_answer`,
  maar leest geen `Final answer`, weigert een antwoord dat bestaand Eva-geheugen
  noemt, roept geen scorer aan en kan dus geen score
  of leaderboardclaim maken. AgentBench- en OSWorld-adapters, de echte
  modelroute en een volledige CB02-closure blijven open. De AgentBench-kant
  heeft nu wel een even kleine kandidaatadapter:
  `../benchmark/eva_agentbench_client.py` past rechtstreeks in de upstream
  `AgentClient`-sleuf op commit
  `d1e4a10db08c87075c78972e48ecc182be03e2d5`. Elke door AgentBench gevraagde
  beurt wordt als een nieuwe, hashgebonden Eva-context gestuurd met uitsluitend
  de complete door AgentBench aangeleverde gesprekshistorie. Daardoor kan geen
  gewone Eva-herinnering tussen samples terechtkomen. De bijbehorende
  `../benchmark/eva-agentbench-client.yaml` bevat alleen de class- en
  timeoutconfiguratie, geen model- of secretinstellingen. Acht lokale
  contractproeven zijn groen; er is nog geen AgentBench-taakserver, image,
  score of developmentrun gestart.
- [ ] CB03 — Gepinde development-/smokeruns op publieke of devtaken voor alle
  drie suites, zonder leaderboardclaim.
- [ ] CB04 — GAIA met officiële datasetrevision en scorer; publieke validatie
  volledig reproduceerbaar en een testscore alleen via de officiële route.
- [ ] CB05 — AgentBench FC op één exacte upstreamcommit en volledige gekozen
  taakset, met ongewijzigde officiële per-omgeving- en totaalscores.
- [ ] CB06 — OSWorld 2.0 `v2026.08.08` op exact bijpassende gated taken,
  assets, websites en providerimages; trajecten en officiële succesmetrics
  publiceren en waar mogelijk upstream laten verifiëren.
- [ ] CB07 — Dezelfde gecontroleerde matrix voor Eva, OpenClaw, Hermes, Agent
  Zero en OpenBot; volledige ruwe scores, kosten en latency onafhankelijk
  gecontroleerd. Pas daarna volgt een frameworkclaim.

## Primaire bronnen

- AgentBench: <https://github.com/THUDM/AgentBench>
- AgentBench-paper: <https://arxiv.org/abs/2308.03688>
- GAIA-paper en scoringsopzet: <https://arxiv.org/abs/2311.12983>
- GAIA-organisatie, dataset en leaderboard:
  <https://huggingface.co/gaia-benchmark>
- OSWorld 2.0: <https://github.com/xlang-ai/OSWorld-V2>
- Actuele gepinde OSWorldrelease:
  <https://github.com/xlang-ai/OSWorld-V2/tree/v2026.08.08>
