# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-10.

Doel: [Eva als zelfstandig lerende digitale huisgenoot](PROJECTDOEL.md).
Gebouwd, getest, live en door jou geaccepteerd zijn verschillende stappen.

CO-008 is afgerond met onafhankelijke GO en geaccepteerde GE-01. Nu AU-007: Eva werkt vanuit bestaande toegestane context zelf één relevant privaat voorstel uit.
[Actueel bewijs](reviews/CO-008-automatic-memory-ddb0267.md).

Bestaande initiatiefroute compleet gebruiken: echte trigger → eigen keuze → controleerbaar resultaat → Jaaps beoordeling.

### Afzonderlijke storing: bestaande Voice PE-route

Jaap bevestigde op 10 september dat Eva na de reparatie weer hoorbaar antwoordt.
De specifieke onbereikbaarheidsfout door een geheugenbronverwijzing van 247 tekens
is daarmee in de praktijk hersteld. De originele bron blijft behouden; de
persoons- en mandaatcontroles zijn niet verruimd. 108 gerichte tests, onafhankelijke
review en gecontroleerde uitrol zijn vastgelegd in het
[herstelbewijs](evidence/product/VOICE-REF-247-repair-2026-09-10.json).
Natuurlijk doorpraten tijdens werk en gewone spraakonderbreking zijn niet
geaccepteerd: de huidige route blijft opeenvolgende vraag/antwoordbeurten gebruiken.
[Begrensde diagnose en voorstel](design/product/voice-session-diagnosis-2026-09-10.md).
GE-01 blijft geaccepteerd; geen nieuw projectgoal of brede voice-implementatie gestart.

Het [OSS-onderzoek van 10 september](design/product/voice-oss-selection-2026-09-10.md)
is afgerond onder Jaaps harde eis dat de volledige gespreksmechanismen al samen
in een onderhouden applicatie zitten. Geen onderzochte applicatie is volledig
gekwalificeerd. LiveKit 1.8.0: 19 bestaande upstreamtests geslaagd, zonder fysieke
spraakproef. Pipecat heeft uitvoerbare asyncdemo's, maar de onderzochte taken zijn
gesimuleerd en/of alleen via het scherm opgevolgd. Geen eigen gesprekssysteem,
nieuwe dienst of runtimewijziging gestart. Telefoon/laptop gaat bij eventuele
vervolgverificatie voorop; HA is geen selectievoorwaarde. `openai_tts` 3.9.1 heeft
gepubliceerde zinsstreaming, is hier niet geïnstalleerd en blijft ondersteunend.


Jaap heeft de uitvoering expliciet hervat: ontbreken van een complete OSS-app is
geen stopvoorwaarde. [Actieve LiveKit-integratie](design/product/voice-livekit-delivery-2026-09-10.md)
gebruikt de bestaande Eva-kern en providers. Laptop eerst, telefoon/huisapparaten
blijven open; geen nieuwe stemervaring live of geaccepteerd.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 142 | `AU-007` — Zelf een relevant privaat voorstel uitwerken | Eva ziet via een bestaande huiswaarneming een passende kans bij toegestane persoonlijke context, kiest zelf een concrete vervolgstap en werkt één controleerbaar privaat voorstel of hulpmiddel uit. Jaap hoeft de stappen niet voor te zeggen. | 🟡 BEZIG |

Klaar betekent:

- Een bestaande toegestane trigger leidt zonder stapsgewijze gebruikersopdracht tot één relevant, controleerbaar privaat resultaat.
- Resultaat en herkomst zijn beschikbaar voor Jaap; technische oplevering en subjectieve acceptatie zijn apart zichtbaar.

<details>
<summary>Technische uitvoering: bestanden en controles</summary>

### Exact werk voor `AU-007`

- `eva2/src/eva/application/free_will.py`
- `eva2/src/eva/adapters/model/pydantic_conversation.py`
- `eva2/tests/application/test_free_will.py`
- `eva2/tests/model/test_conversation_model.py`
- `eva2/src/eva/adapters/projection/hindsight.py`
- `eva2/tests/projection/test_hindsight.py`

Echte controles:

- `pytest -q tests/application/test_free_will.py`
- `pytest -q tests/model/test_conversation_model.py::test_sensory_model_parses_only_bounded_own_initiative tests/model/test_conversation_model.py::test_passive_house_event_retains_provider_usage_with_bounded_decision`
- `pytest -q tests/projection/test_hindsight.py::test_observation_inherits_authority_from_complete_bounded_source_facts tests/projection/test_hindsight.py::test_household_observations_require_every_source_authority tests/projection/test_hindsight.py::test_private_recall_cannot_hide_shared_observation_sources`

</details>

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

- [ ] `AU-002` — Duurzame hulpagent bedienen: Starten, volgen, stoppen en hervatten werken via de gebruikersroute; hoofd- en hulpagent delen één provideradapter.
- [ ] `VB-004` — Browser na onderbreking veilig hervatten: Herstelbestanden gebouwd; systeeminstallatie automatisch geblokkeerd. Bestaande browser blijft live. Herstel na herstart nog niet uitgevoerd of bewezen.
- [ ] `AU-005` — Passend initiatief en kansen bewijzen: Meer gewaardeerd eigen initiatief dan Bottie: humor, nieuwsgierigheid, zelfgekozen onderzoek, creatieve bijdragen en kansen. Eva begint en vervolgt zelf werk, voert binnen bestaand mandaat uit en controleert het resultaat. Gezin beoordeelt eigenheid, plezier, nut en hinder; alleen meer berichten telt niet als winst.
- [ ] `VB-001` — Full-duplex voice leveren: Met Eva2 praten via de bestaande Telegram- of Home Assistant-aansluiting, waarbij Eva hoorbaar onderbroken kan worden en het gesprek daarna doorgaat.
- [ ] `VB-002` — Passende aanwezigheid leveren: Eva zoekt spontaan contact via de beschikbare speakers en deurbel, met herkenbare identiteit en gevoel voor het moment. Gezin beoordeelt plezier, betrokkenheid en hinder; onderbreken en stop werken.
- [ ] `UX-001` — Eigenaar-dashboard leveren: Een onderhouden adminframework toont status, mensen, geheugen, kosten en bewijs.
- [ ] `UX-002` — Kanban bedienbaar maken: Taken zijn te starten, volgen en stoppen met bewijs per kaart.
- [ ] `AU-004` — Zelf tools en skills leren maken: Een ongeziene digitale taak leren uit documentatie; zelf een tool of skill maken, testen en bij een andere taak hergebruiken zonder nieuwe taakspecifieke kerncode. Fouten en menselijke hulp meetellen.
- [ ] `AU-006` — Project zelfstandig tot resultaat brengen: Een meerdaags cross-domainproject plannen, uitvoeren, bijsturen en na herstart hervatten met zichtbare kosten en stop. Een bedrijfspilot kan als afgesproken project; geen echte betalingen, publicatie of verplichtingen zonder passend mandaat.
- [ ] `AU-003` — Eigen onderdelen beoordelen en verbeteren: Eva ontdekt een overbodig of zwak onderdeel, vergelijkt onderhouden upstream, maakt een geïsoleerde verbetering en bewijst voor/na voordeel en terugrol. Alleen gemeten winst mag promoveren; geen zelfverruiming van rechten.
- [ ] `CO-004` — Optioneel geheugen inzien: Alleen een optionele inzagefunctie; geen voorwaarde voor dagelijks gebruik. Begonnen bronwerk blijft bewaard, niet afgevinkt of live gezet.
- [ ] `CO-005` — Optioneel expliciet vergeten of corrigeren: Alleen op uitdrukkelijk verzoek kunnen corrigeren of vergeten. Geen onderhoudstaak voor Jaap en geen blokkade voor autonoom geheugen.
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
- [x] `UP-001` — Sterke oplossingen van concurrenten hergebruiken (done).
  [Bewijs](evidence/product/UP-001-phase-aware-streaming-2026-09-08.json).
- [x] `CO-007` — Menselijke gesprekroute compleet maken (done).
  [Bewijs](evidence/product/CO-007-live-answer-stream-2026-09-08.json).
- [x] `CO-006` — Geheugenbroncorrectie veilig in gebruik nemen (done).
  [Bewijs](evidence/product/CO-006-autonomous-memory-2026-09-08.json).
- [x] `TL-001` — Algemene toolroute meerdere stappen laten uitvoeren (done).
  [Bewijs](evidence/product/TL-001-candidate-build-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-conversation-chain-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-durable-model-usage-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-final-candidate-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-native-poller-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-owner-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-provider-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-real-model-chain-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-runtime-serialization-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-storage-foundation-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-telegram-stop-2026-09-08.json).
  [Bewijs](evidence/product/TL-001-turn-limit-2026-09-08.json).
- [x] `TL-002` — Handelingen via twee providers echt afmaken (done).
  [Bewijs](evidence/product/TL-002-completion-2026-09-08.json).
- [x] `AU-001` — Eigenaarmandaat uitvoeren (done).
  [Bewijs](evidence/product/AU-001-completion-2026-09-09.json).
- [x] `TL-003` — Onbekende connector zelfstandig toevoegen (done).
  [Bewijs](evidence/product/TL-003-completion-2026-09-10.json).
- [x] `VB-003` — Browser bedienen en overnemen (done).
  [Bewijs](evidence/product/VB-003-completion-2026-09-10.json).
- [x] `CO-008` — Zelf relevant geheugen kiezen en bijwerken (done).
  [Bewijs](evidence/product/CO-008-automatic-personal-memory-2026-09-10.json).
- [ ] `AU-007` — Zelf een relevant privaat voorstel uitwerken (in_progress).

</details>

## Technische review blijft open

- [ ] `CO-003` — CO-003 blijft onder onafhankelijke review, niet afgevinkt of live. RT-001 herstelt nu de verschillende eigenaars- en geheugenscopes en herlaadt de privékoppeling per pagina. Een verse PostgreSQL-proef en een alleen-lezen proef met twee echte bronpagina's plus geweigerde andere eigenaar zijn groen; het oorspronkelijke CO-003-bewijs blijft historisch bewaard.
  [Bestaand bewijs](evidence/product/CO-003-owner-memory-sources-2026-09-07.json).

## Menselijke acceptatie blijft open

- [ ] `CO-002` — CO-002 en CO-007: Jaap meldde dat de gesprekken echt beter zijn; verdere gezinsbeoordeling van warmte, eigen oordeel en continuïteit blijft open. De antwoordstream is op 8 september technisch geïnstalleerd en door Telegram bevestigd. Er is geen nieuwe menselijke beurt of zichtbare animatiebeoordeling verzonnen; het oude incident wordt hier niet opnieuw geopend.

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
