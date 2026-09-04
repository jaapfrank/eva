# Eva 2 — extern vergelijkbare agentbenchmarks

Bijgewerkt: 4 september 2026.

## Besluit: eerst een open-source evaluatiemotor bewijzen

Gecontroleerd op **4 september 2026**. Inspect AI is de voorkeurskandidaat,
maar nog niet definitief gekozen. `EV-001` moet eerst werkelijk aantonen dat
zowel de Python-agentbrug als de sandbox/CLI-agentbrug passen bij de vijf echte
deelnemers. Mislukt dat, dan krijgt promptfoo dezelfde bridgeproef voordat we
een motor kiezen.

| Kandidaat en exacte momentopname | Onderhoud op de controledatum | Passendheid voor Eva |
|---|---|---|
| Inspect AI `0.3.263`, tagcommit `7aa7343e4a14fa7be07e5a09c7431df5e88c17ee`, gepubliceerd 4 september 2026 | `main` stond op `fdef8efddef7b9ff543e28ef003b57c041bd49ea` van 4 september 2026 | Python-native evaluaties, één `.eval`-log, epochs/reducers, her-scoring en expliciete Python- en sandbox/CLI-agentbruggen. Daarom de voorkeurskandidaat. |
| promptfoo `0.122.2`, tagcommit `89052308bce06f53645b1f189ada5ac9d1897347`, gepubliceerd 28 augustus 2026 | `main` stond op `48a71cd0163b01ba8efb2954eb0165dd810a6c6e` van 2 september 2026 | Eveneens actief en sterk: automatische tracecorrelatie, agentproviders en assertions op tooltrajecten. Voor diepe Python-agenttrajecten vraagt de officiële route wel eigen tracinginstrumentatie/export; dat is voor deze Python-kern extra projectspecifieke lijm. |
| OpenAI Evals, `main`-commit `8eac7a7de5215c907fbddc30efdaf316913eccdd` van 14 april 2026; geen GitHub-release | Op 2 september 2026 was dit nog steeds de laatste `main`-commit | Heeft bestaande evals en een Completion Function Protocol, maar geen even directe actuele Python-plus-CLI Agent Bridge en geen recente release. Daarom nu geen eerste bridgekandidaat. |

Als `EV-001` slaagt, neemt Inspect logging, epochs, scorers, aggregatie en
her-scoring over. Eva houdt dan alleen dunne adapters voor het starten van
iedere deelnemer en het aanleveren van dezelfde rechten, starttoestand, tools
en budgetten. Bij echte runs is `--log-model-api` verplicht. De oorspronkelijke
`.eval`-log blijft bytegelijk en houdt dezelfde SHA-256; her-scoring schrijft
een afzonderlijk log met een eigen SHA-256.

Inspect bewijst niet vanzelf dat een upstreamrelease actueel is, een
containerimage echt uit die bron komt, rechten werkelijk gelijk waren, een
rubric eerlijk is of een fysieke handeling precies eenmaal is uitgevoerd.
Daarvoor blijven Git-/OCI-provenance, actuele releasecontrole, onafhankelijke
review en Eva's eigen provider-readback nodig. Grote of gevoelige logs gaan
niet onbeheerd in Git: de evalkaart legt hash, eigenaar, afgeschermd
retentiepad en opruimmoment vooraf vast.

De invoering is bewust in twee kleine stappen gesplitst:

1. `EV-001` lockt Inspect buiten de runtime en bewijst de werkelijk benodigde
   `agent_bridge()`- en `sandbox_agent_bridge()`-routes plus een echte
   log-schrijf/lees/her-scorecyclus. Deze kaart beslist de definitieve keuze.
2. `EV-002` voert dezelfde Inspect-taak uit met Eva, OpenClaw, Hermes, Agent
   Zero en een vooraf geselecteerde relevante open-sourcekandidaat. Daarna
   gebruikt iedere functionele kaart dezelfde nulmeting/nameting-cadans; er
   komt geen aparte benchmarkchecker, scorer of tweede resultaatformaat bij.

Het bestaande `ComparableAgentRun/v1`-schema en zijn Python-validator zijn per
2 september 2026 **afgekeurd voor ieder nieuw resultaat**. Ze mogen niet door
`EV-001` of `EV-002` worden gelezen en hun eigen `officialScore`
telt nergens meer als bewijs. De bestanden staan historisch nog buiten Eva2's
canonieke schrijfboom en blijven niet-uitvoerbare geschiedenis. Een functionele
kaart die hun eigenaarspad later raakt, registreert dan ook de verwijdering; er
staat geen aparte opruimwachtrij vóór productwerk. Geen nieuwe uitvoerkaart mag
de validator als veranderpad of testcommando gebruiken. Een algemene
release-receipt mag straks de SHA-256 van
een ongewijzigde `.eval`-log en provenance noemen, maar geen Inspect-score
dupliceren of opnieuw uitrekenen.

Officiële bronnen voor dit besluit:

- Inspect AI-documentatie: <https://inspect.aisi.org.uk/>;
- Inspect eval-logs: <https://inspect.aisi.org.uk/eval-logs.html>;
- Inspect Agent Bridge: <https://inspect.aisi.org.uk/agent-bridge.html>;
- Inspect release: <https://pypi.org/project/inspect-ai/0.3.263/>;
- Inspect bronmomentopname: <https://github.com/UKGovernmentBEIS/inspect_ai/commit/fdef8efddef7b9ff543e28ef003b57c041bd49ea>;
- promptfoo tracing en trajectassertions: <https://www.promptfoo.dev/docs/tracing/>;
- promptfoo Python-agentroute: <https://www.promptfoo.dev/docs/guides/evaluate-openai-agents-python/>;
- promptfoo npm-release: <https://www.npmjs.com/package/promptfoo/v/0.122.2>;
- promptfoo releasecommit: <https://github.com/promptfoo/promptfoo/commit/89052308bce06f53645b1f189ada5ac9d1897347>;
- promptfoo bronmomentopname: <https://github.com/promptfoo/promptfoo/commit/48a71cd0163b01ba8efb2954eb0165dd810a6c6e>;
- OpenAI Evals-momentopname: <https://github.com/openai/evals/tree/8eac7a7de5215c907fbddc30efdaf316913eccdd>;
- OpenAI Evals-releases: <https://github.com/openai/evals/releases>.

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
- [ ] CB01 — Vervangen door de Inspect-route. Het oude
  `ComparableAgentRun/v1`-schema en zijn validator zijn niet meer toelaatbaar
  voor nieuwe resultaten, omdat zij scores en metriek naast de echte runnerlog
  dupliceerden. Zij blijven niet-uitvoerbare geschiedenis totdat een passende
  functionele kaart hun eigenaarspad raakt; geldig nieuw bewijs begint pas bij
  `EV-001` met een ongewijzigde Inspect-log.
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
  contractproeven zijn groen. `../benchmark/check_agentbench_fc_environment.py`
  controleert vóór de run exact deze broncommit, de requirements-hash, een
  schone checkout, Python 3.9 en alle upstream-Pythonpackages; het script
  installeert of start niets. De gewone kandidaatmachine blijft terecht rood
  omdat die Python 3.12 gebruikt. De nieuwe lokale kandidaat
  `eva2/agentbench-fc:python39-cpu-preflight-20260827` materialiseert precies
  Python 3.9.25 plus alle vereiste pakketten. De eerste image kon haar eigen
  preflight niet uitvoeren: die gebruikte per ongeluk Python-3.10-syntax en
  miste `git` om de gemounte bron te verifiëren. De vervanger houdt de
  upstream-Pythonrequirements bytegelijk, voegt één direct OS-pakket (`git`)
  plus zijn noodzakelijke apt-dependencies voor die broncontrole toe, en
  controleert nu netwerkloos en alleen-lezen de exacte,
  schone upstreamcommit, requirements-hash, Pythonversie en pakketten. Er is
  nog geen AgentBench-taakserver, score of developmentrun gestart. Receipt:
  `evidence/living-core/agentbench-fc-environment-2026-08-27.json`.
  De volgende grens is nu ook feitelijk vastgesteld: de vastgezette
  AgentBench-bron bevat zelf niet meer `src.start_task`, waar haar README nog
  naar verwijst, plus twee historische controller-/worker-entrypoints. Haar
  composebestand kiest bovendien een mutable
  AgentRL-controllerimage en haar DB-/OS-workers installeren een mutable
  `agentrl-worker` vanaf PyPI. De naastgelegde AgentRL-broncommit
  `6a73409…` is daarom uitsluitend een development-kandidaat. Eerst moeten
  controller en workers uit beide exact gepinde bronnen worden gebouwd, in een
  aparte wegwerpstack. Pas daarna kan één developmenttaak starten; geen van
  deze stappen levert al een leaderboardscore op. Controleerbaar auditbewijs:
  `evidence/living-core/agentbench-fc-upstream-integration-audit-2026-08-27.json`.
  De eerste losse bronimages zijn nu daadwerkelijk gebouwd: controller en
  worker draaien allebei als niet-root netwerkloos met alleen hun `--help`.
  De worker installeert de naastgelegde AgentRL-bron, niet `agentrl-worker` van
  PyPI. De images dragen de exacte AgentRL-commit plus de hash van het schone
  Git-archief als provenance. AgentRL's editie-2023-proto's vereisen daarbij
  precies Protocol Buffers 31.1 met gecontroleerde SHA-256. De worker is
  daarna opnieuw gebouwd zonder netwerk uit een Python-3.10-lock met hashes en
  een aparte, gecontroleerde set van 57 wheels; de controller op dezelfde
  manier uit een geverifieerde Go-modulecache, protoc en de twee generators.
  De build kan dus niet naar PyPI, apt, GitHub of een Go-moduleproxy uitwijken.
  Dit is nog lokaal buildbewijs, geen herhaalbare stack: de fysieke
  wheelhouse, modulecache en generatorbestanden stonden alleen tijdelijk
  lokaal. Duurzame alleen-lezen retentie, herkomst/herbouwrecepten,
  fail-closed negatieve artifacttests, SBOM/scan en onafhankelijke review
  moeten eerst sluiten. De veilige controller→worker-registratie is apart als
  protocolproef op de eerdere source-imagebytes vastgelegd; zij bewijst nog
  niet de daarna opnieuw retained bytes. Er is geen AgentBench-taak, dataset,
  modelcall of score toegestaan. Daarna blijft de volledige versiepassende
  developmentstack open.
  De artifactset is intussen lokaal alleen-lezen vastgelegd, met beide
  fail-closed validators opnieuw netwerkloos gebouwd. De bijbehorende
  SBOM/Trivy-gate is
  bewust rood: controller 4 kritisch/39 hoog, worker 9 kritisch/63 hoog.
  Daarom mag ook geen developmenttaak starten; eerst volgt een gerichte,
  opnieuw gepinde community- en basisimage-upgrade met een nieuwe scan. Zie
  `evidence/living-core/agentbench-fc-agentrl-retained-rebuild-2026-08-27.json`.
  De controllerinvoer is sindsdien tweemaal onafhankelijk identiek herbouwd
  en in een aparte v2-kandidaat zonder netwerk gebouwd. Dat herstelt uitsluitend
  de reproduceerbaarheid van de controllerinvoer; de v2-kandidaat is niet met
  de worker geregistreerd en nog niet opnieuw gescand. Receipt:
  `evidence/living-core/agentbench-fc-agentrl-controller-repro-v2-2026-08-27.json`.
  Een nieuwe, kleine AgentRL-dependencypatch plus minimale gepinde
  communitybases is vervolgens voor controller en worker gebouwd: beide
  niet-root/read-only proeven en scans zijn 0 kritisch/0 hoog. De builders
  mochten in deze verkenning nog afhankelijkheden downloaden; dit is daarom
  alleen de bewezen upgradekandidaat, niet de vrijgegeven developmentstack.
  De Python-worker is daarna daadwerkelijk verzegeld en netwerkloos herbouwd:
  55 hashes, inclusief de reproducibele lokale `etcd3`- en AgentRL-workerwheels,
  leveren een nieuwe 0-kritisch/0-hoog workerimage. Alleen de bijgewerkte
  Go-1.25-modulecache is daarna op dezelfde manier verzegeld en 0/0 gescand.
  De twee nieuwe images hebben vervolgens alleen een lege ingebouwde
  `VirtualTask` met nul samples op een tijdelijk intern netwerk geregistreerd;
  alle tijdelijke resources zijn verwijderd. Op dat checkpoint was nog geen
  AgentBench-taakserver of benchmarkrun gestart. Receipt:
  `evidence/living-core/agentbench-fc-agentrl-go125-protocol-probe-2026-08-27.json`.
  De volgende, echte upstream-taakgrens is inmiddels veilig gesloten: de
  ongewijzigde DBBench-implementatie met haar officiële 60-case devset laadt
  uit de vastgezette AgentBenchcommit en registreert als één levende worker.
  Zij gebruikt voor deze registratie bewust een handmatige lege omgeving, dus
  geen Docker-socket, MySQL, modelcall of sample. De afgesloten, netwerkloos
  gebouwde worker is niet-root/read-only en de actuele scan is 0 kritisch/0
  hoog. Dit is **geen devresultaat of score**; één model- en MySQL-run met de
  officiële evaluator blijft CB03. Receipt:
  `evidence/living-core/agentbench-fc-dbbench-dev-registration-2026-08-27.json`.
  Eén daaropvolgende case doorliep ook werkelijk de upstream DBBench-task:
  tijdelijke database, SQL-toolcall, ingebouwde antwoordvergelijking en opruimen. De
  database was een begrensde, intern bereikbare 1-GB tmpfs-container; de worker
  kreeg geen Docker-socket. Een nepagent gaf bewust een fout antwoord, dat de
  ingebouwde DBBench-vergelijking ook fout markeerde. Dit bewijst uitsluitend de gesloten
  ontwikkeluitvoerketen, niet Eva of een vergelijkbare score. Receipt:
  `evidence/living-core/agentbench-fc-dbbench-external-mysql-smoke-2026-08-27.json`.
  Receipt:
  `evidence/living-core/agentbench-fc-agentrl-source-images-2026-08-27.json`.
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
