# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-08.

Doel: [Eva als zelfstandig lerende digitale huisgenoot](PROJECTDOEL.md).
Gebouwd, getest, live en door jou geaccepteerd zijn verschillende stappen.

Het meetprotocol is onafhankelijk goedgekeurd: herhaalde ongeziene taken, menselijke voorkeur, initiatief, hulp, tijd, verbruik en herstel blijven afzonderlijk zichtbaar. Geen uitgevoerde vergelijking of winstclaim. UP-001 onderzoekt nu actuele open-sourcegesprek-/toolroutes en neemt één bewezen bruikbaar patroon over in Eva's bestaande adapter.
[Actueel bewijs](evidence/product/EV-003-comparative-acceptance-protocol-2026-09-08.json).

Jij kunt verder praten met /eva2 voor ieder bericht. Ik onderzoek concrete upstreamcode en licenties voor snellere, begrijpelijke antwoordvorming met tools; daarna een kleine voor/naproef en de complete gesprekroute. Geen nieuwe agentloop of brede opruimklus.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 133 | `UP-001` — Sterke oplossingen van concurrenten hergebruiken | Onderzoek de actuele gesprek-/toolroutes van OpenClaw, Hermes en Agent Zero; neem één bewezen bruikbaar patroon over in de bestaande Eva-adapter. Andere capabilities volgen in hun eigen functionele kaarten, met dezelfde herkomst- en voor/naproef. | 🟡 BEZIG |

Klaar betekent:

- Minstens één herbruikbaar upstreamprincipe verbetert aantoonbaar de bestaande gesprekroute; alle drie concurrenten zijn op relevante actuele bron onderzocht.
- Dezelfde gerichte proeven vóór en na tonen het concrete voordeel en bewaken persoon, stop, toolreceipts, retry en verbruik; geen algemene concurrentiewinst claimen.
- Onafhankelijke review keurt de bronkandidaat en herkomst goed. CO-007 blijft eigenaar van de complete gezins-/Telegramervaring; een nieuwe deployment vereist de normale kandidaatpoort.

<details>
<summary>Technische uitvoering: bestanden en controles</summary>

### Exact werk voor `UP-001`

- `eva2/design/product/performance-and-complexity-v1.md`
- `eva2/src/eva/adapters/model/pydantic_conversation.py`
- `eva2/tests/model/test_pydantic_conversation_model.py`

Echte controles:

- `pytest -q tests/model/test_pydantic_conversation_model.py tests/model/test_conversation_model.py`
- `ruff check src/eva/adapters/model/pydantic_conversation.py tests/model/test_pydantic_conversation_model.py`
- `python3 tools/check_component_catalog.py`

</details>

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

- [ ] `CO-007` — Menselijke gesprekroute compleet maken: Echte gezinsgesprekken beoordelen op warmte, eigen oordeel, identiteit en continuïteit; live tekststukjes met tools apart bewijzen. CO-001 bronstatus en CO-002 menselijke beoordeling zijn geen voltooiing van deze gebruikersuitkomst.
- [ ] `CO-004` — Eigen geheugen in het dashboard tonen: Het bestaande React-Admin-dashboard toont de afgeschermde geheugenbronnen begrijpelijk zonder nieuwe webstack of browseropslag.
- [ ] `CO-005` — Eigen geheugen corrigeren en wissen: De eigenaar kan één zichtbare bron corrigeren of wissen en krijgt controle dat PostgreSQL en Hindsight dezelfde wijziging hebben verwerkt.
- [ ] `CO-006` — Nuttig geheugen en retentie bewijzen: Bestaande Hindsight-consolidatie gebruiken; gewijzigde voorkeuren, bronherkomst en vergeten semantisch testen. Bronbewaring, compactere kennis en werkelijk schijfgebruik afzonderlijk meten; geen tweede geheugensysteem.
- [ ] `VB-001` — Full-duplex voice leveren: Microfoon en speaker werken tegelijk met snelle onderbreking.
- [ ] `VB-002` — Passende aanwezigheid leveren: Eva spreekt passend in huis met stilte, cooldown en stop.
- [ ] `AU-005` — Passend initiatief en kansen bewijzen: Een nuttige kans herkennen, onder bestaand mandaat uitvoeren en controleren; bij een afleider, privacygrens of ongepast moment terecht niets doen. Gezin beoordeelt nut en hinder.
- [ ] `TL-001` — Algemene toolontdekking afmaken: De best onderhouden generieke model- en toolloop laadt providers via configuratie of MCP. Pydantic AI wint die plek alleen bij gemeten voordeel; eigen wrappers en oude modelpaden verdwijnen in dezelfde kaart.
- [ ] `TL-002` — Actie en readback generaliseren: Twee echte providers gebruiken dezelfde precies-eenmaalroute.
- [ ] `AU-001` — Eigenaarmandaat uitvoeren: Eva handelt binnen het afgesproken mandaat zonder steeds opnieuw te vragen.
- [ ] `TL-003` — Onbekende connector zelfstandig toevoegen: Eva kiest onderhouden software uit documentatie en bewijst de adapter.
- [ ] `VB-003` — Veilige browsercomputer kiezen: Eén onderhouden browsermotor ondersteunt accounts, meekijken en overname.
- [ ] `VB-004` — Browserherstel bewijzen: Een crash herhaalt geen externe mutatie blind.
- [ ] `AU-002` — Duurzame hulpagent bedienen: Starten, volgen, stoppen en hervatten werken via de gebruikersroute; hoofd- en hulpagent delen één provideradapter.
- [ ] `UX-001` — Eigenaar-dashboard leveren: Een onderhouden adminframework toont status, mensen, geheugen, kosten en bewijs.
- [ ] `UX-002` — Kanban bedienbaar maken: Taken zijn te starten, volgen en stoppen met bewijs per kaart.
- [ ] `AU-004` — Zelf tools en skills leren maken: Een ongeziene digitale taak leren uit documentatie; zelf een tool of skill maken, testen en bij een andere taak hergebruiken zonder nieuwe taakspecifieke kerncode. Fouten en menselijke hulp meetellen.
- [ ] `AU-006` — Project zelfstandig tot resultaat brengen: Een meerdaags cross-domainproject plannen, uitvoeren, bijsturen en na herstart hervatten met zichtbare kosten en stop. Een bedrijfspilot kan als afgesproken project; geen echte betalingen, publicatie of verplichtingen zonder passend mandaat.
- [ ] `AU-003` — Eigen onderdelen beoordelen en verbeteren: Eva ontdekt een overbodig of zwak onderdeel, vergelijkt onderhouden upstream, maakt een geïsoleerde verbetering en bewijst voor/na voordeel en terugrol. Alleen gemeten winst mag promoveren; geen zelfverruiming van rechten.
- [ ] `IN-001` — Fresh install voor gebruikers: Een gewone gebruiker installeert Eva zonder terminal of configuratiebestanden.
- [ ] `IN-002` — Naar andere computer verplaatsen: Identiteit en data herstellen; connectoren veilig opnieuw koppelen.
- [ ] `IN-003` — Consumentenupdates leveren: Renovate stelt onderhouden dependencies voor; updates doorlopen dezelfde poort en rollen verliesloos terug.
- [ ] `CP-001` — Volledige productmatrix opnieuw uitvoeren: De tussentijdse gelijke proeven worden op de gezamenlijke releasekandidaat en actuele concurrenten als één geheel herhaald.
- [ ] `CP-002` — Cross-domainwinst aantonen: Herhaalde ongeziene gesprekken en handelingen toetsen de vooraf vastgelegde drempels tegen alle drie concurrenten. Resultaat, menselijk oordeel, hulp, tijd, kosten en herstel bepalen de uitkomst; winst is niet verondersteld.
- [ ] `CP-003` — Officiële benchmarks uitvoeren: AgentBench, GAIA en OSWorld gebruiken officiële scorers en trajecten.
- [ ] `FN-001` — Eindkandidaat bevriezen: Image, configuratie en bewijsset zijn onveranderlijk gebonden; daarna volgen de releasepoort en pas na veilige installatie de eindpraktijkproeven.
- [ ] `RB-007` — Verplichte testgroepen uitvoeren: Pas na functionele productkaarten slaagt iedere testgroep tegen dezelfde kandidaat.
- [ ] `RB-008` — Wegwerpstack testen: De kandidaat migreert, antwoordt, onthoudt, herstart en ruimt volledig op.
- [ ] `RB-009` — Bewijsset onafhankelijk beoordelen: Een onafhankelijke reviewer controleert de volledige releaseketen.
- [ ] `RB-010` — Actief en reserveslot beheren: Alleen groen werk gaat naar het reserveslot; controleer het daarna en wissel de route. Bij problemen blijft het huidige slot actief.
- [ ] `FN-002` — Computerherstart bewijzen: De veilig geïnstalleerde eindkandidaat komt zonder handmatige reparatie terug met dezelfde gegevens en zonder dubbele acties.
- [ ] `FN-003` — Zeven dagen praktijk en eindacceptatie bewijzen: Een ongewijzigde kandidaat doorstaat zeven dagen gezinspraktijk. De volledige bewijsset, vergelijking en menselijke beoordeling zijn groen vóór vervangbeslissing; geraakt bewijs wordt na relevante wijzigingen opnieuw uitgevoerd.

<details>
<summary>Afgeronde en eerdere uitvoerkaarten met bewijs</summary>

## Uitvoerkaarten en bewijs

Afgevinkt betekent alleen: de vastgelegde kaart is afgerond, niet het hele product.

- [x] `GOV-001` — Klein en controleerbaar werkbord afdwingen (done).
  [Bewijs](evidence/product/GOV-001-execution-workboard-tests-2026-09-01.json).
- [x] `GOV-002` — Bordovergangen statusvast maken (done).
  [Bewijs](evidence/product/GOV-002-status-stable-workboard-tests-2026-09-01.json).
- [x] `RB-001` — Verzegelde basisimages offline bruikbaar maken op een verse bouwer (done).
  [Bewijs](evidence/product/RB-001-sealed-oci-layout-empty-builders-2026-09-01.json).
- [x] `RB-002` — Nieuwe bootstrapcode verzegelen (done).
  [Bewijs](evidence/product/RB-002-bootstrap-release-authority-2026-09-01.json).
- [x] `RB-003` — Kandidaat op bouwer A maken (done).
  [Bewijs](evidence/product/RB-003-builder-a-offline-admission-2026-09-01.json).
- [x] `RB-004` — Kandidaat op bouwer B maken (done).
  [Bewijs](evidence/product/RB-004-builder-b-offline-admission-2026-09-01.json).
- [x] `RB-005` — Beide builds vergelijken (done).
  [Bewijs](evidence/product/RB-005-two-builder-reproducibility-2026-09-01.json).
- [x] `RB-006` — SBOM en kwetsbaarheden controleren (done).
  [Bewijs](evidence/product/RB-006-candidate-artifact-analysis-2026-09-01.json).
- [x] `GOV-003` — Eva2-werk tot één zichtbare schrijfboom begrenzen (done).
  [Bewijs](evidence/product/GOV-003-canonical-workspace-containment-tests-2026-09-02.json).
- [x] `CP-000` — Open-source evaluatiemotor voorselecteren (done).
  [Bewijs](evidence/product/CP-000-open-source-eval-engine-selection-2026-09-02.json).
- [x] `RR-001` — Hermetische kandidaatbron herstellen (done).
  [Bewijs](evidence/product/RR-001-hermetic-candidate-source-2026-09-02.json).
- [x] `RR-002` — Nieuwe schone kandidaat op twee verse bouwers maken (done).
- [x] `EV-001` — Inspect AI-evalomgeving en bruggen bouwen (done).
  [Bewijs](evidence/product/EV-001-inspect-ai-bridge-probe-2026-09-04.json).
  [Bewijs](evidence/product/EV-001-inspect-ai-bridge-probe-v2-2026-09-04.json).
- [x] `EV-002` — Kleine vierweg-gespreksnulmeting uitvoeren (done).
  [Bewijs](evidence/product/EV-002-controlled-baseline-result-2026-09-04.json).
- [x] `CO-001` — Telegram toont Eva's antwoordvorming live (done).
  [Bewijs](evidence/product/CO-001-telegram-live-drafts-contract-2026-09-04.json).
- [x] `UX-000` — Grip op Eva: één visueel dashboard (done).
  [Bewijs](evidence/product/UX-000-project-dashboard-2026-09-07.json).
- [x] `RT-001` — Zelfstandige runtime veilig herstellen (done).
  [Bewijs](evidence/product/RT-001-autonomous-admission-wait-2026-09-07.json).
- [x] `EV-003` — Eerlijke succescriteria vastzetten (done).
  [Bewijs](evidence/product/EV-003-comparative-acceptance-protocol-2026-09-08.json).
- [ ] `UP-001` — Sterke oplossingen van concurrenten hergebruiken (in_progress).

</details>

## Technische review blijft open

- [ ] `CO-003` — CO-003 blijft onder onafhankelijke review, niet afgevinkt of live. RT-001 herstelt nu de verschillende eigenaars- en geheugenscopes en herlaadt de privékoppeling per pagina. Een verse PostgreSQL-proef en een alleen-lezen proef met twee echte bronpagina's plus geweigerde andere eigenaar zijn groen; het oorspronkelijke CO-003-bewijs blijft historisch bewaard.
  [Bestaand bewijs](evidence/product/CO-003-owner-memory-sources-2026-09-07.json).

## Menselijke acceptatie blijft open

- [ ] `CO-002` — Jaap meldt op 7 september dat de gesprekken met Eva2 echt een stuk beter zijn: positieve menselijke feedback, geen volledige gezinsacceptatie. De fout van 15:24:21 wordt onder RT-001 onderzocht; de oorspronkelijke exception ontbreekt. Gesprek, geheugen na kernherstart en terugrol waren gecontroleerd. Live tekststukjes en verdere menselijke beoordeling blijven open.

<details>
<summary>Werkafspraken en machineleesbare bron</summary>

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

</details>
