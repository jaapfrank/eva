# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-02.

Dit bord is automatisch uit de machineleesbare werklijst gemaakt. Handmatige
afwijkingen worden geweigerd. We ronden één kleine kaart volledig af voordat
de volgende begint.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 70 | `RB-007` — Alle verplichte testgroepen uitvoeren | Iedere verzegelde testgroep slaagt op dezelfde commit. | 🟡 BEZIG |
| 80 | `RB-008` — Volledige wegwerpstack testen | De stack migreert, antwoordt, onthoudt, herstart en ruimt op. | ⚪ HIERNA |

### Exact werk voor `RB-007`

- `eva2/tools/run_managed_b_required_tests.py`
- `eva2/tools/run_managed_b_browser_release_subgate.py`
- `eva2/tests/operations/test_run_managed_b_required_tests.py`
- `eva2/tests/operations/test_run_managed_b_browser_release_subgate.py`

Klaar betekent:

- De runner accepteert uitsluitend een apart gematerialiseerde, alleen-lezen source tree waarvan sourceCommit en release-inputauthority exact overeenkomen met de door RB-005 gekozen kandidaat en het geslaagde RB-006-bewijs; de huidige checkout wordt nooit als testbron gebruikt.
- Collectie en uitvoering dekken exact alle verzegelde modules in zeven niet-overlappende groepen. De twaalf gewone databases en de afzonderlijke browserdatabase hebben fysiek gebonden cluster- en database-identiteiten, beginnen leeg en worden volledig opgeruimd; iedere failure, error, skip, xfail, ontbrekende of extra test faalt gesloten zonder passing receipt.
- De browser-subgate gebruikt exact één verse ouderuitdaging in namen, labels, database en receipt; hergebruik, een verkeerde uitdaging, extra gelijk-gelabelde resources, daemonfouten of onvolledige opruiming kunnen geen groen receipt opleveren.
- Het passing receipt bindt kandidaatimage, sourceCommit, authorityhash, onveranderlijke source-snapshot, toolchains, wheelhouses, groepsresultaten en het RB-006-bewijs; een onafhankelijke reviewer geeft GO.

Echte controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_run_managed_b_required_tests.py tests/operations/test_run_managed_b_browser_release_subgate.py`
- `ruff check tools/run_managed_b_required_tests.py tools/run_managed_b_browser_release_subgate.py tests/operations/test_run_managed_b_required_tests.py tests/operations/test_run_managed_b_browser_release_subgate.py`

### Daarna voorbereid: `RB-008`

- `eva2/tests/support/managed_b_runtime_smoke.py`
- `eva2/tests/operations/test_managed_b_runtime_smoke.py`

Vooraf vastgelegde controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_managed_b_runtime_smoke.py`
- `ruff check tests/support/managed_b_runtime_smoke.py tests/operations/test_managed_b_runtime_smoke.py`

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

| Nr. | Onderdeel | Begrijpelijk eindresultaat |
|---:|---|---|
| 90 | `RB-009` — Fysieke bewijsset beoordelen | Een onafhankelijke reviewer controleert de volledige releaseketen. |
| 100 | `RB-010` — A/B-besluit nemen | Alleen volledig groen mag naar het niet-actieve slot. |
| 103 | `EV-001` — Inspect AI-bruggen geïsoleerd bewijzen | Een exact gelockte evalomgeving bewijst de benodigde Python- en sandbox/CLI-agentbruggen, bewaart de oorspronkelijke Inspect-loghash en schrijft her-scoring naar een afzonderlijk gehasht log. |
| 106 | `EV-002` — Actuele vijfwegmeting uitvoeren | Eva, OpenClaw, Hermes, Agent Zero en een relevante open-sourcekandidaat doorlopen dezelfde echte proef en Inspect-scorer. |
| 109 | `EV-003` — Vergelijking per productkaart afdwingen | Productwerk kan pas starten na een actuele nulmeting en alleen sluiten na dezelfde nameting met aantoonbare winst. |
| 110 | `OS-001` — Inventaris en bereikbaarheid afdwingen | Elk gevolgd bestand heeft één eigenaar en aantoonbare noodzaak. |
| 120 | `OS-002` — Pydantic-wrapper consolideren | Pydantic doet generiek modelwerk; Eva houdt alleen unieke grenzen. |
| 130 | `OS-003` — Child-provider consolideren | Hoofd- en hulpagent delen één provideradapter. |
| 140 | `OS-004` — Oude modelpaden verwijderen | Alle modelverkeer gebruikt de gekozen onderhouden route. |
| 150 | `OS-005` — Updatevoorstellen automatiseren | Renovate stelt voor; de releasepoort beslist en kan terugrollen. |
| 160 | `CO-001` — Telegram live laten streamen | Tekst verschijnt live en eindigt exact in het duurzame antwoord. |
| 170 | `CO-002` — Menselijke identiteit bewijzen | Eva blijft warm, geestig en zelfstandig herkenbaar. |
| 180 | `CO-003` — Persoonsbeheer afmaken | Mensen blijven privé en kunnen geheugen zien, corrigeren en wissen. |
| 190 | `TL-001` — Algemene toolontdekking afmaken | Providers laden via configuratie of MCP zonder merklogica in de kern. |
| 200 | `TL-002` — Actie en readback generaliseren | Twee echte providers gebruiken dezelfde precies-eenmaalroute. |
| 210 | `TL-003` — Onbekende connector zelfstandig toevoegen | Eva kiest onderhouden software uit documentatie en bewijst de adapter. |
| 220 | `AU-001` — Eigenaarmandaat uitvoeren | Eva handelt binnen het afgesproken mandaat zonder steeds opnieuw te vragen. |
| 230 | `AU-002` — Duurzame hulpagent bedienen | Starten, volgen, stoppen en hervatten werken via de gebruikersroute. |
| 240 | `AU-003` — Zelfverbetering begrenzen | Alleen gemeten winst mag uit quarantaine promoveren. |
| 250 | `UX-001` — Eigenaar-dashboard leveren | Een onderhouden adminframework toont status, mensen, geheugen, kosten en bewijs. |
| 260 | `UX-002` — Kanban bedienbaar maken | Taken zijn te starten, volgen en stoppen met bewijs per kaart. |
| 270 | `VB-001` — Full-duplex voice leveren | Microfoon en speaker werken tegelijk met snelle onderbreking. |
| 280 | `VB-002` — Passende aanwezigheid leveren | Eva spreekt passend in huis met stilte, cooldown en stop. |
| 290 | `VB-003` — Veilige browsercomputer kiezen | Eén onderhouden browsermotor ondersteunt accounts, meekijken en overname. |
| 300 | `VB-004` — Browserherstel bewijzen | Een crash herhaalt geen externe mutatie blind. |
| 310 | `IN-001` — Fresh install voor gebruikers | Een gewone gebruiker installeert Eva zonder terminal of configuratiebestanden. |
| 320 | `IN-002` — Naar andere computer verplaatsen | Identiteit en data herstellen; connectoren veilig opnieuw koppelen. |
| 330 | `IN-003` — Consumentenupdates leveren | Updates doorlopen dezelfde poort en rollen verliesloos terug. |
| 340 | `CP-001` — Volledige productmatrix opnieuw uitvoeren | De tussentijdse gelijke proeven worden op de gezamenlijke releasekandidaat en actuele concurrenten als één geheel herhaald. |
| 350 | `CP-002` — Cross-domainwinst opnieuw controleren | Gesprek, geheugen, tools, browser, subagents en beheer bevestigen samen de eerder gemeten voordelen. |
| 360 | `CP-003` — Officiële benchmarks uitvoeren | AgentBench, GAIA en OSWorld gebruiken officiële scorers en trajecten. |
| 370 | `FN-001` — Eindkandidaat bevriezen | Image, configuratie en bewijsset zijn onveranderlijk gebonden. |
| 380 | `FN-002` — Computerherstart bewijzen | Alles komt zonder handmatige reparatie correct terug. |
| 390 | `FN-003` — Zeven dagen praktijk bewijzen | Een ongewijzigde kandidaat doorstaat de praktijkproef en vervangbeslissing. |

## Vaste regels

- Afgeronde uitvoerkaarten staan eerst, daarna exact één primaire kaart, daarna alleen wachtrijkaarten.
- Alleen de primaire kaart en de direct volgende kaart zijn uitvoerbaar; latere wensen blijven mijlpalen.
- Een uitvoerkaart noemt vóór start exacte bestanden, eigenaren en echte testcommando's.
- Een bouwer keurt zijn eigen taak niet goed.
- Klaar vereist commitgebonden tests, bewijs en onafhankelijke beoordeling.
- Een gebruikersvoorbeeld test een bestaande capability en maakt geen providerspecifieke kernroute.
- De enige projectingang en schrijfboom is /home/administrator/project/eva2; dit is één koppeling naar de bestaande fysieke Git-werkboom en alle agents delen diezelfde bron met exclusieve bestandspaden.
- Er worden geen Eva2-worktrees of andere Eva2-bronmappen als sibling onder /home/administrator/project gemaakt.
- Tijdelijke test- en buildmappen staan via mktemp onder /tmp en worden opgeruimd.
- /home/administrator/project/.eva2-artifacts is alleen een beheerd artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact retentiepad en opruimmoment.
- Centrale projectdocumenten hebben steeds precies één aangewezen schrijver.
- De vastgezette funderingsvolgorde kan niet vanuit alleen het bord worden omzeild; releaseherstel en de echte evaluatiebasis gaan vóór nieuw productwerk.
- Nieuwe uitvoerkaarten mogen het afgekeurde ComparableAgentRun-scoreformaat of zijn validator niet meer als veranderpad of testcommando gebruiken.

Machineleesbare bron:
[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).
