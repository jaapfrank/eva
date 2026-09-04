# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-04.

Dit bord is automatisch uit de machineleesbare werklijst gemaakt. Handmatige
afwijkingen worden geweigerd. We ronden één kleine kaart volledig af voordat
de volgende begint.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 110 | `CO-001` — Telegram toont Eva's antwoordvorming live | Een privéchat toont meteen Telegrams officiële tijdelijke Thinking-draft. Alleen modeltekst uit een reeds veilige, toolvrije eindfase groeit live; daarna blijft exact één bestaand duurzaam eindantwoord staan, zonder tweede modelcall. | 🟡 BEZIG |

### Exact werk voor `CO-001`

- `docs/eva-repository-inventory.generated.json`
- `docs/eva-repository-inventory.generated.md`
- `eva2/src/eva/adapters/api/app.py`
- `eva2/src/eva/adapters/connectors/telegram.py`
- `eva2/src/eva/application/conversation.py`
- `eva2/src/eva/application/telegram_ingress.py`
- `eva2/src/eva/bootstrap/telegram_ingress.py`
- `eva2/tests/api/test_memory_api.py`
- `eva2/tests/application/test_conversation.py`
- `eva2/tests/application/test_telegram_ingress.py`
- `eva2/tests/connectors/test_telegram.py`
- `eva2/tests/operations/test_telegram_ingress_wiring.py`

Klaar betekent:

- Een toegelaten privéturn toont vóór het kernantwoord een tijdelijke Thinking-draft; veilige toolvrije modeltekst kan binnen Telegrams 4096-karaktergrens begrensd meegroeien.
- Een direct antwoord met beschikbare tools, groepsbericht of lokaal commando lekt geen gedeeltelijke modeltekst en doet geen tweede modelcall.
- De bestaande duurzame finale sendMessage blijft de enige blijvende levering; drafts zijn best-effort, verlopen vanzelf en voegen geen crash- of replaystaat toe.
- Gerichte functie-, privacy-, limiet- en fouttests blijven groen en een onafhankelijke reviewer geeft GO.

Echte controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/api/test_memory_api.py tests/application/test_conversation.py tests/application/test_telegram_ingress.py tests/connectors/test_telegram.py tests/operations/test_telegram_ingress_wiring.py`
- `ruff check src/eva/adapters/api/app.py src/eva/adapters/connectors/telegram.py src/eva/application/conversation.py src/eva/application/telegram_ingress.py src/eva/bootstrap/telegram_ingress.py tests/api/test_memory_api.py tests/application/test_conversation.py tests/application/test_telegram_ingress.py tests/connectors/test_telegram.py tests/operations/test_telegram_ingress_wiring.py`
- `python3 tools/check_component_catalog.py`

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

| Nr. | Onderdeel | Begrijpelijk eindresultaat |
|---:|---|---|
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
- Alleen de primaire uitvoerkaart is nu uitvoerbaar; na afronding wordt precies één volgende productmijlpaal eerst tot een kleine kaart uitgewerkt.
- Een kaart noemt exacte bestanden, eigenaren en echte testcommando's.
- Een bouwer keurt zijn eigen taak niet goed; klaar vereist bewijs en onafhankelijke GO.
- Paden blijven in eva2 (of de twee gegenereerde inventory-uitgangen); tijdelijke data gaat via mktemp naar /tmp.
- /home/administrator/project/.eva2-artifacts is alleen een beheerd artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact retentiepad en opruimmoment.
- Releaseherstel wordt compact met zijn evidence- en reviewreferenties afgesloten; daarna volgt productwerk volgens de vastgelegde meetcadans.
- Productcadans: kleine nulmeting → maintained upstream kiezen → bouwen/hergebruiken → gerichte functietest → passende praktijkproef → dezelfde nameting; pas een complete installatiekandidaat doorloopt de zware releasepoort.
- Een functionele kaart verwijdert alleen de code die zij binnen haar vooraf genoemde paden werkelijk vervangt; breder opruimwerk mag de gebruikersfunctie niet opslokken.
- A/B betekent alleen veilig installeren: het werkende slot blijft actief terwijl de complete kandidaat in het reserveslot wordt gecontroleerd; kleine ontwikkelwijzigingen krijgen geen A/B-cyclus.

Machineleesbare bron:
[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).
