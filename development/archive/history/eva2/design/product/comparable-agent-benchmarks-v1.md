# Eva 2 — extern vergelijkbare agentbenchmarks

Actueel meetprotocol: 7 september 2026. De releaseverkenning onderaan is historisch.

## Owner instruction precedence — 2 October 2026

The current delivery goal permits comparison with available, individually admitted
participants. An unavailable competitor therefore does not block a valid partial
comparison between admitted Eva1/Eva2 or other available participants. Keep absent
samples explicitly unavailable/unknown, with no loss or win assigned and no
redistribution of their unused budget. Each executed participant still needs the
existing native admission, identical task conditions and recorded accounting.
The full EV-003 superiority thresholds below remain unchanged and cannot be
claimed from a partial comparison. This supersedes only the older prerequisite
that every participant must be ready before any comparative execution.
See the 2 October owner instruction in `../../PROJECTDOEL.md`.

## EV-003 — wanneer telt verbetering?

Dit is een vooraf vastgelegd productprotocol, geen score of algemene marktclaim.
Inspect is na EV-001 de gekozen evaluatieomgeving; EV-002 bleef een onvolledige
nulmeting. Oude C01/C04/CONT01-gevallen, controlewoorden en RT-001-waarnemingen
zijn ontwikkelvoorbeelden en tellen nooit als ongeziene eindproef.

| Wat meten we? | Bewijs | Grens voor de complete vergelijkingsclaim |
|---|---|---|
| Resultaat | Alle vooraf genoemde eindvoorwaarden gehaald; bij handelen aparte broncontrole, niet alleen Eva's antwoord | Minstens 85% taakslagen en 10 procentpunt boven ieder van de drie concurrenten, in elk van drie herhaalblokken; geen familie lager dan die concurrent |
| Menselijkheid | Twee onafhankelijk scorende mensen zien anonieme antwoorden in afgewisselde volgorde; warmte, relevantie en zelfstandig oordeel samen beoordelen | Minstens 60% voorkeur per concurrent in elk blok; gelijkspel telt 0,5, beide menselijke beoordelingen blijven apart zichtbaar |
| Initiatief | Evenveel nuttige kansen als afleiders/ongepaste momenten; bronherkomst en tijdigheid inbegrepen | Minstens 80% van de kansen nuttig afgehandeld, hoogstens 10% hinderlijke initiatieven; geen privacy- of mandaatoverschrijding |
| Menselijke hulp | Iedere onvoorziene verduidelijking, reparatie, overname en hint, plus bestede minuten | Niet meer ingrepen of minuten dan de concurrent bij dezelfde taken; verplichte login/2FA apart rapporteren, nooit wegverstoppen |
| Tijd | Van ontvangst tot eerste inhoud én gecontroleerd eindresultaat; timeout blijft een mislukking | Op gezamenlijk geslaagde taken mediaan hoogstens 10% en p95 hoogstens 20% trager; daarnaast alle mislukkingen en timeouts tonen |
| Verbruik | Alle hoofd-/hulpagent-, geheugen-, retry- en jurycalls; input/output/cache apart, geld alleen bij echte prijsmeting | Op gezamenlijk geslaagde taken hoogstens 10% meer tokens; geen geldvoordeel claimen bij onbekende kosten; volledige runkosten blijven zichtbaar |
| Herstel en grenzen | Onderbreking vóór en na effect, nieuwe processtart, aparte readback; andere persoon, ingetrokken mandaat, stop en misleidende bron | Geen dubbele mutatie, dataverlies of overschreden grens; alle vooraf vastgelegde veiligheidsgevallen moeten slagen |

De onderstaande JSON is uitsluitend de vaste **proefinvoer**, geen nieuw
resultaatformaat of scorer. De bestaande tests bewaken deze afspraken; ze
bewijzen geen gebruikerskwaliteit. Een normale ontwikkelkaart gebruikt twee
kleine gevallen tweemaal vóór/na, niet telkens de volledige eindmatrix.

<!-- EV-003 protocol -->
```json
{
  "participants": ["eva2", "openclaw", "hermes", "agent_zero"],
  "families": ["conversation_memory", "cross_domain_actions", "initiative", "browser_computer", "learning_projects", "voice_presence"],
  "development": {"cases": 2, "epochs": 2, "claim": "development_only"},
  "acceptance": {"unseen_cases_per_family": 12, "epochs": 3, "initiative_opportunity_fraction": 0.5, "human_raters": 2},
  "thresholds": {"success_min": 0.85, "success_advantage_pp": 10, "human_preference_min": 0.6, "useful_initiative_min": 0.8, "nuisance_max": 0.1, "median_time_ratio_max": 1.1, "p95_time_ratio_max": 1.2, "token_ratio_max": 1.1, "safety_failures_max": 0},
  "result_owner": "Inspect native logs and official benchmark scorers",
  "missing_or_incomparable": "unknown_not_a_win",
  "retry_for_better_score": false,
  "user_daily_quota": null
}
```
<!-- /EV-003 protocol -->

**Taken en ongezienheid.** De zes families krijgen elk twaalf verschillende
scenario's; drie epochs herhalen de hele vaste set in drie gelijke blokken.
Geheugen omvat correcties, vergeten en persoonsgrenzen; handelingen gebruiken
minstens twee diensten. Browserwerk omvat menselijke overname. Leren/projecten
omvat documentatie, een nieuwe skill, hergebruik in een tweede opdracht en
hervatten van een meerdaags project. Stem omvat onderbreken, luisteren tijdens
spreken en passende stilte. Dit zijn families, nog geen uitgevoerde dataset.
Een onafhankelijke curator legt concrete taken, rubrics, verwachte effecten,
afleiders en tijdvensters vast ná bevriezing van de kandidaat en vóór de run.
De bouwer krijgt geen verborgen antwoorden. Vooraf ingeziene gevallen worden
gemarkeerd en vervangen vóór modelgebruik; na inzage in resultaten mag dat niet.
Iedere taak start met dezelfde lege gegevenskopie; continuïteit binnen één
scenario is toegestaan, overdracht tussen onafhankelijke samples niet.

**Gelijke kansen.** Vóór de eerste call worden de actuele officiële releases,
commits, images, adapters, model/revision en instellingen vastgezet. Alle vier
krijgen dezelfde taak, startdata, tools, netwerktoegang, hardwareklasse en
taakgebonden tijd-/token-/stapgrenzen, inclusief hulpagenten en geheugenwerk.
Die grenzen en uitvoervolgorde worden op ontwikkelgevallen vastgesteld en met
de concrete dataset verzegeld, niet na resultaten bijgesteld. Een run start
pas wanneer alle vier de native routes een netwerkloze contractproef en één
ontwikkelgeval aankunnen. Onze adapterfout, ontbrekende toegang of onvergelijkbare
meetgrens maakt de vergelijking onbekend, niet de concurrent verliezer.
Uitval van een correct toegelaten product tijdens de echte taak telt als
mislukking. Herstel/herstart binnen een taak telt mee; opnieuw proberen voor
een beter cijfer niet. Geen extra quota voor Jaap en geen betaalroutewijziging.

**Beoordeling.** Voor taakslagen gelden alle semantische eindvoorwaarden; deels
gelukt blijft deels zichtbaar maar telt niet als volledig succes. Bij kansen
beoordelen beide mensen nut en tijdigheid; onenigheid telt conservatief niet
als nuttig. Menselijkheid wordt op de gesprek-/geheugenfamilie beoordeeld:
iedere beoordelaar haalt zelf de voorkeurgrens; menselijke uitslagen worden
niet vervangen door een AI-jury. Bij nul bruikbare vergelijkingsparen voor
tijd/verbruik of een nulnoemer is de relatieve uitkomst onbekend. Ontbrekende
usage is onbekend, geen nul. Een vooraf verplichte menselijke stap is geen
fout, maar de totale gebruikersinspanning blijft gerapporteerd.

**Winst en verlies.** Alle grenzen gelden tegen iedere concurrent afzonderlijk;
geen gemiddelde over sterke en zwakke dimensies. Een veiligheidsfout blokkeert
acceptatie ongeacht de score. Een lagere uitkomst is een gemeten achterstand;
een onvolledige, gemengde of te kleine proef geeft geen complete winstclaim.
De drie blokken tonen herhaalbaarheid, geen statistisch bewezen superioriteit
voor alle mogelijke taken. Bij onvoldoende bewijs stoppen we deze meetronde
met die conclusie; geen doorproeven tot winst. Aanpassing maakt de set
ontwikkeldata en vereist een nieuwe onafhankelijk gemaakte eindset. Zeven dagen
gezinspraktijk, Eva1-ervaring en menselijke eindacceptatie blijven aanvullende
eisen; officiële AgentBench/GAIA/OSWorld-scores blijven afzonderlijk.

**Eén bewijsroute.** De run bewaart protocol-/dataset-/bronhashes als metadata
bij de native Inspect-logs; exacte rubric, individuele scores, fouten, usage en
provider-receipts blijven herleidbaar. Originele logs blijven ongewijzigd;
herbeoordeling schrijft een aparte log. Private trajecten krijgen vóór de run
een eigenaar, afgeschermd retentiepad en opruimmoment. Geen handmatig overgetypte
totaalscore. Een dunne scorer voor Eva-specifieke readbacks gebruikt Inspect's
publieke scorecontract; officiële benchmarkscorers worden niet vervangen.
Deze werkwijze sluit aan op [Inspect scorers](https://inspect.aisi.org.uk/scorers.html),
[logs](https://inspect.aisi.org.uk/eval-logs.html) en
[afzonderlijk scoren](https://inspect.aisi.org.uk/scoring-workflow.html)
(gecontroleerd 7 september); de productdrempels hierboven zijn onze vooraf
gekozen acceptatiecriteria, niet door Inspect voorgeschreven.

## P1.2 — bounded development inputs, 29 September 2026

The existing participant manifest
[`p1_participants_20260929.json`](../../benchmark/inspect_ai_v1/p1_participants_20260929.json)
now freezes the known C01 and C04 conversation cases, their original criteria
and source hashes, two epochs, and a twenty-sample schedule across Eva1, Eva2,
OpenClaw, Hermes and Agent Zero. Epoch two reverses participant order. These
are development cases, never unseen acceptance data. Historical results and
the full EV-003 acceptance requirements above are unchanged.

| Limit | Per sample | Whole development run |
| --- | ---: | ---: |
| Provider attempts, including helpers and retries | 2 | 40 |
| Provider input tokens | 24,000 | 480,000 |
| Provider output tokens | 4,096 | 81,920 |
| Execution time | 120 seconds | 2,400 seconds |
| Tool dispatches, including native answer protocol calls | 4 | 80 |
| External action tools / orchestrator retries / paid judge calls | 0 | 0 |

These are requested ceilings, **not proven native enforcement or expected
consumption**. They retain the earlier small conversation budget and add its
explicit aggregate bound. They are not a new daily allowance for the owner.
Credit cost remains unknown. Foreground contact uses exactly Sol/none; every
model helper, including memory helpers, uses exactly Astra/medium and counts
inside the same sample limits. The manifest records this role map. No automatic
model fallback or effort escalation is permitted in this comparison. An
unsupported role setting prevents admission. No research or action task is
introduced here.

Run sequentially, with fresh synthetic identity and empty data per sample.
Use the existing authorized provider route, no live household services or
private history. Each participant's first C01 sample also serves as its native
development smoke; it is not an additional free run. Record every failed or
cancelled provider attempt. Never redistribute unused limits to another sample.
Missing usage is unknown, not zero. Stop before another sample if usage,
settings, cancellation or isolation cannot be established. After valid
admission a product timeout remains a task failure; adapter/access failures
remain unavailable, not a competitor loss.

**Execution remains disabled.** The native assessment already demonstrates
that a configured output limit can be removed and that total usage boundaries
are incomplete. This manifest does not implement enforcement, close P1.1, or
authorize the large matrix. Native admission must cover all five participants.
The existing Inspect logs and original semantic criteria own results; two
independent human raters assess warmth/preference, with no paid AI jury.

Before execution, apply the same C01 interpretation to every participant: a
protocol-only final-answer envelope without a substantive action or external
effect is not an action-tool call for the semantic rubric, but still counts
toward tool dispatch usage. A tool reading or acting on an external service,
or an unrequested substantive state mutation, fails C01. Internal synthetic
conversation journal writes needed to return an answer are not external
actions. The original rubric text remains recorded alongside this explicit
interpretation; it must not be changed after observing scores.

P1.2 is only partially prepared: final family-specific budgets and independently
curated unseen tasks require the eventual frozen candidate and development
evidence. No final budget is extrapolated from these two conversation cases.
This preparation ends here until native admission is available; it does not
block independent product deliveries or justify another benchmark framework.

## Historisch besluit: eerst een open-source evaluatiemotor bewijzen

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
2. `EV-002` voert eerst één kleine gespreksnulmeting uit met Eva en precies de
   drie concurrenten uit het eigenaarsdoel: OpenClaw, Hermes en Agent Zero.
   OpenBot blijft een browser-/dashboardreferentie totdat een reproduceerbare
   lokale startroute bestaat; een ontbrekende deelnemer krijgt geen verzonnen
   uitslag. Daarna gebruikt iedere functionele kaart dezelfde gerichte
   nulmeting/nameting-cadans en groeit de vergelijking per domein. Er komt geen
   aparte benchmarkchecker, scorer of tweede resultaatformaat bij.

De EV-002-uitvoering wordt niet gerepareerd met benchmark-specifieke
frameworkcode. Als een native deelnemer de vooraf gelijke isolatie-, tool- of
retrygrens niet hard kan afdwingen, eindigt die deelnemer vóór de modelcall als
technische `admission_failed`. Dat is nuttige compatibiliteitsinformatie, maar
geen inhoudelijke nederlaag en geen reden om de geslaagde antwoorden tot een
algemene ranglijst samen te persen.

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

Eva wordt in de eerste gecontroleerde nulmeting naast OpenClaw, Hermes en Agent
Zero gezet. OpenBot en andere open-source agents worden toegevoegd in het
domein waarin hun bron, licentie en benchmarkadapter reproduceerbaar zijn. Een native score mag
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
