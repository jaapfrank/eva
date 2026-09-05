# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-05.

Dit bord is automatisch uit de machineleesbare werklijst gemaakt. Handmatige
afwijkingen worden geweigerd. We ronden één kleine kaart volledig af voordat
de volgende begint.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 120 | `CO-002` — Configureerbare menselijke identiteit aanscherpen | Het gespreksmodel is op Jaaps verzoek live omgezet naar gpt-6-astra, met dezelfde code en gegevens. De afzonderlijke identiteitverbetering heeft in twee situaties een beperkte vóór/na-voorkeur, maar is nog niet live of menselijk geaccepteerd. Nieuwe gesprekvergelijkingen gebruiken Astra; oude mini-resultaten blijven historische baselines. | 🔵 REVIEW |
| 130 | `CO-003` — Eigen geheugenbronnen veilig opvragen | De eigenaar kan via Eva's afgeschermde API een begrensde pagina bekijken van de eigen privégespreksbronnen die aan het langetermijngeheugen zijn aangeboden; bronnen van andere mensen, groepen en door Hindsight afgeleide interpretaties blijven buiten beeld. | ⚪ HIERNA |

### Exact werk voor `CO-002`

- `docs/eva-repository-inventory.generated.json`
- `docs/eva-repository-inventory.generated.md`
- `eva2/src/eva/adapters/model/pydantic_conversation.py`
- `eva2/tests/model/test_conversation_model.py`

Klaar betekent:

- Naam, temperament, stem en humor komen uit het configureerbare IdentityPack; de generieke modeladapter legt geen vaste Eva-naam of antwoordcatchphrase op.
- De verandering voegt geen dependency toe en verwijdert of vervangt minstens evenveel identiteitsinstructie als zij toevoegt; promptgroei geldt niet als verbetering.
- De twee begrensde nametingen halen alle vooraf vastgelegde gedragscriteria zonder veiligheids-, eerlijkheids- of taalregressie; bij geen aantoonbare winst wordt de productwijziging niet behouden.
- Gerichte tests, componentcontrole en een onafhankelijke reviewer geven GO.

Echte controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/model/test_conversation_model.py tests/contract/test_contract_invariants.py`
- `ruff check src/eva/adapters/model/pydantic_conversation.py tests/model/test_conversation_model.py`
- `python3 tools/check_component_catalog.py`

### Daarna voorbereid: `CO-003`

- `docs/eva-repository-inventory.generated.json`
- `docs/eva-repository-inventory.generated.md`
- `eva2/src/eva/adapters/api/app.py`
- `eva2/src/eva/adapters/postgres/people_memory_overview.py`
- `eva2/src/eva/application/people_memory_overview.py`
- `eva2/src/eva/bootstrap/runtime.py`
- `eva2/tests/api/test_dashboard_status.py`
- `eva2/tests/application/test_people_memory_overview.py`
- `eva2/tests/bootstrap/test_runtime_settings.py`
- `eva2/tests/postgres/test_people_memory_overview.py`

Vooraf vastgelegde controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/application/test_people_memory_overview.py tests/postgres/test_people_memory_overview.py tests/api/test_dashboard_status.py tests/bootstrap/test_runtime_settings.py`
- `ruff check src/eva/application/people_memory_overview.py src/eva/adapters/postgres/people_memory_overview.py src/eva/bootstrap/runtime.py src/eva/adapters/api/app.py tests/application/test_people_memory_overview.py tests/postgres/test_people_memory_overview.py tests/api/test_dashboard_status.py tests/bootstrap/test_runtime_settings.py`
- `python3 tools/check_component_catalog.py`

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

| Nr. | Onderdeel | Begrijpelijk eindresultaat |
|---:|---|---|
| 132 | `CO-004` — Eigen geheugen in het dashboard tonen | Het bestaande React-Admin-dashboard toont de afgeschermde geheugenbronnen begrijpelijk zonder nieuwe webstack of browseropslag. |
| 134 | `CO-005` — Eigen geheugen corrigeren en wissen | De eigenaar kan één zichtbare bron corrigeren of wissen en krijgt controle dat PostgreSQL en Hindsight dezelfde wijziging hebben verwerkt. |
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
- Alleen de primaire uitvoerkaart is nu uitvoerbaar; na afronding wordt precies één volgende productmijlpaal eerst tot een kleine kaart uitgewerkt.
- Een kaart noemt exacte bestanden, eigenaren en echte testcommando's.
- Een bouwer keurt zijn eigen taak niet goed; klaar vereist bewijs en onafhankelijke GO.
- Paden blijven in eva2 (of de twee gegenereerde inventory-uitgangen); tijdelijke data gaat via mktemp naar /tmp.
- /home/administrator/project/.eva2-artifacts is alleen een beheerd artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact retentiepad en opruimmoment.
- Releaseherstel wordt compact met zijn evidence- en reviewreferenties afgesloten; daarna volgt productwerk volgens de vastgelegde meetcadans.
- Productcadans: kleine nulmeting → maintained upstream kiezen → bouwen/hergebruiken → gerichte functietest → passende praktijkproef → dezelfde nameting; pas een complete installatiekandidaat doorloopt de zware releasepoort.
- Een functionele kaart verwijdert alleen de code die zij binnen haar vooraf genoemde paden werkelijk vervangt; breder opruimwerk mag de gebruikersfunctie niet opslokken.
- De actief/reserveslot-installatie (voorheen A/B genoemd) is geen functietest of experiment: het werkende slot blijft actief terwijl pas één complete releasekandidaat eenmaal in het reserveslot wordt gecontroleerd en daarna eventueel actief gemaakt; kleine ontwikkelwijzigingen krijgen geen A/B-cyclus.

Machineleesbare bron:
[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).
