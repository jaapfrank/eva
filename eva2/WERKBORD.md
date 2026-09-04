# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-04.

Dit bord is automatisch uit de machineleesbare werklijst gemaakt. Handmatige
afwijkingen worden geweigerd. We ronden één kleine kaart volledig af voordat
de volgende begint.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 103 | `EV-001` — Inspect AI-evalomgeving en bruggen bouwen | Hercontroleer Inspect AI 0.3.263, lock de actuele release exact, bouw Python-agent_bridge en sandbox_agent_bridge en bewijs log/rescore. | 🟡 BEZIG |
| 106 | `EV-002` — Kleine vijfweg-nulmeting uitvoeren | Eva en vier actuele open-sourceconcurrenten krijgen één begrensde, gelijke cross-domainopdracht; dit is de nulmeting voor het eerstvolgende productwerk. | ⚪ HIERNA |

### Exact werk voor `EV-001`

- `docs/eva-repository-inventory.generated.json`
- `docs/eva-repository-inventory.generated.md`
- `eva2/benchmark/inspect_ai_v1/pylock.toml`
- `eva2/benchmark/inspect_ai_v1/bridge_probe.py`
- `eva2/benchmark/inspect_ai_v1/README.md`
- `eva2/tests/benchmark/test_inspect_ai_ev001.py`
- `eva2/design/product/comparable-agent-benchmarks-v1.md`

Klaar betekent:

- De actuele release is gecontroleerd en exact gelockt.
- Beide bruggen en log/rescore slagen in de gerichte test.
- Een onafhankelijke reviewer geeft GO.

Echte controles:

- `pytest -q tests/benchmark/test_inspect_ai_ev001.py`
- `ruff check benchmark/inspect_ai_v1/bridge_probe.py tests/benchmark/test_inspect_ai_ev001.py`

### Daarna voorbereid: `EV-002`

- `eva2/benchmark/inspect_ai_v1/pylock.toml`
- `eva2/benchmark/inspect_ai_v1/bridge_probe.py`
- `eva2/benchmark/inspect_ai_v1/README.md`
- `eva2/tests/benchmark/test_inspect_ai_ev001.py`
- `eva2/design/product/comparable-agent-benchmarks-v1.md`

Vooraf vastgelegde controles:

- `pytest -q tests/benchmark/test_inspect_ai_ev001.py`
- `ruff check benchmark/inspect_ai_v1/bridge_probe.py tests/benchmark/test_inspect_ai_ev001.py`

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

| Nr. | Onderdeel | Begrijpelijk eindresultaat |
|---:|---|---|
| 110 | `CO-001` — Telegram live laten streamen | Tekst verschijnt live en eindigt exact in het duurzame antwoord; vervangen gesprekcode verdwijnt in dezelfde kaart. |
| 120 | `CO-002` — Menselijke identiteit bewijzen | Eva blijft warm, geestig en zelfstandig herkenbaar. |
| 130 | `CO-003` — Persoonsbeheer afmaken | Mensen blijven privé en kunnen geheugen zien, corrigeren en wissen. |
| 140 | `TL-001` — Algemene toolontdekking afmaken | De best onderhouden generieke model- en toolloop laadt providers via configuratie of MCP. Pydantic AI wint die plek alleen bij gemeten voordeel; eigen wrappers en oude modelpaden verdwijnen in dezelfde kaart. |
| 150 | `TL-002` — Actie en readback generaliseren | Twee echte providers gebruiken dezelfde precies-eenmaalroute. |
| 160 | `TL-003` — Onbekende connector zelfstandig toevoegen | Eva kiest onderhouden software uit documentatie en bewijst de adapter. |
| 170 | `AU-001` — Eigenaarmandaat uitvoeren | Eva handelt binnen het afgesproken mandaat zonder steeds opnieuw te vragen. |
| 180 | `AU-002` — Duurzame hulpagent bedienen | Starten, volgen, stoppen en hervatten werken via de gebruikersroute; hoofd- en hulpagent delen één provideradapter. |
| 190 | `AU-003` — Zelfverbetering begrenzen | Alleen gemeten winst mag uit quarantaine promoveren. |
| 200 | `UX-001` — Eigenaar-dashboard leveren | Een onderhouden adminframework toont status, mensen, geheugen, kosten en bewijs. |
| 210 | `UX-002` — Kanban bedienbaar maken | Taken zijn te starten, volgen en stoppen met bewijs per kaart. |
| 220 | `VB-001` — Full-duplex voice leveren | Microfoon en speaker werken tegelijk met snelle onderbreking. |
| 230 | `VB-002` — Passende aanwezigheid leveren | Eva spreekt passend in huis met stilte, cooldown en stop. |
| 240 | `VB-003` — Veilige browsercomputer kiezen | Eén onderhouden browsermotor ondersteunt accounts, meekijken en overname. |
| 250 | `VB-004` — Browserherstel bewijzen | Een crash herhaalt geen externe mutatie blind. |
| 260 | `IN-001` — Fresh install voor gebruikers | Een gewone gebruiker installeert Eva zonder terminal of configuratiebestanden. |
| 270 | `IN-002` — Naar andere computer verplaatsen | Identiteit en data herstellen; connectoren veilig opnieuw koppelen. |
| 280 | `IN-003` — Consumentenupdates leveren | Renovate stelt onderhouden dependencies voor; updates doorlopen dezelfde poort en rollen verliesloos terug. |
| 290 | `CP-001` — Volledige productmatrix opnieuw uitvoeren | De tussentijdse gelijke proeven worden op de gezamenlijke releasekandidaat en actuele concurrenten als één geheel herhaald. |
| 300 | `CP-002` — Cross-domainwinst opnieuw controleren | Gesprek, geheugen, tools, browser, subagents en beheer bevestigen samen de eerder gemeten voordelen. |
| 310 | `CP-003` — Officiële benchmarks uitvoeren | AgentBench, GAIA en OSWorld gebruiken officiële scorers en trajecten. |
| 320 | `FN-001` — Eindkandidaat bevriezen | Image, configuratie en bewijsset zijn onveranderlijk gebonden. |
| 330 | `FN-002` — Computerherstart bewijzen | Alles komt zonder handmatige reparatie correct terug. |
| 340 | `FN-003` — Zeven dagen praktijk bewijzen | Een ongewijzigde kandidaat doorstaat de praktijkproef en vervangbeslissing. |
| 400 | `RB-007` — Verplichte testgroepen uitvoeren | Pas na functionele productkaarten slaagt iedere testgroep tegen dezelfde kandidaat. |
| 410 | `RB-008` — Wegwerpstack testen | De kandidaat migreert, antwoordt, onthoudt, herstart en ruimt volledig op. |
| 420 | `RB-009` — Bewijsset onafhankelijk beoordelen | Een onafhankelijke reviewer controleert de volledige releaseketen. |
| 430 | `RB-010` — Actief en reserveslot beheren | Alleen groen werk gaat naar het reserveslot; controleer het daarna en wissel de route. Bij problemen blijft het huidige slot actief. |

## Vaste regels

- Afgeronde uitvoerkaarten staan eerst, daarna exact één primaire kaart, daarna alleen wachtrijkaarten.
- Alleen de primaire kaart en de direct volgende kaart zijn uitvoerbaar; latere wensen blijven mijlpalen.
- Een kaart noemt exacte bestanden, eigenaren en echte testcommando's.
- Een bouwer keurt zijn eigen taak niet goed; klaar vereist bewijs en onafhankelijke GO.
- Paden blijven in eva2 (of de twee gegenereerde inventory-uitgangen); tijdelijke data gaat via mktemp naar /tmp.
- /home/administrator/project/.eva2-artifacts is alleen een beheerd artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact retentiepad en opruimmoment.
- Releaseherstel wordt compact met zijn evidence- en reviewreferenties afgesloten; daarna volgt productwerk volgens de vastgelegde meetcadans.
- Productcadans: nulmeting → maintained upstream kiezen → bouwen/hergebruiken → gerichte test → echte gebruikersproef → dezelfde nameting → zware releasepoort.
- Iedere functionele kaart werkt ook haar bestandsinventaris bij, verwijdert vervangen code en consolideert dubbele adapters; daarvoor bestaat geen aparte opruimwachtrij.

Machineleesbare bron:
[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).
