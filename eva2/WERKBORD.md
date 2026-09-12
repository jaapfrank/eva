# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-12.

Doel: [Eva als zelfstandig lerende digitale huisgenoot](PROJECTDOEL.md).
Gebouwd, getest, live en door jou geaccepteerd zijn verschillende stappen.

Geheugen is geaccepteerd en afgesloten. AU-008 is technisch afgerond en live; IN-02 wacht op jouw oordeel. Overig reeds gewenst technisch werk gaat door met afzonderlijke opleveringen.
[Actueel bewijs](evidence/product/AU-008-current-review-delivery-2026-09-12.json).

Technisch werk aan overige reeds gewenste onderdelen gaat door. Open gebruikersproeven staan gebundeld op het dashboard; IN-02 blijft open zonder het overige werk te blokkeren.

### Afzonderlijk stemwerk

De laptoproute met LiveKit is live op http://localhost:44716. Meld aan met het
bestaande eigenaar-dashboardtoken en kies Begin gesprek. Jaap bevestigde op
11 september dat fysiek onderbreken werkt en vond de verhaalinhoud leuk.
Natuurlijke uitspraak en de vernieuwde ontvangstbevestiging wachten nog op zijn luisteroordeel.

Jaaps verduidelijking is leidend: echt onderzoek mag 10–30 seconden of een minuut
duren, mits Eva meteen kort en eerlijk hoorbaar reageert. Voor duidelijke
werkopdrachten zegt zij nu “Ik kijk er even voor je naar” zodra de kern het
spraakverzoek heeft ontvangen. Geen uitvoerings- of succesclaim; eenvoudige
vragen krijgen deze standaardzin niet. De bestaande Astra-kern doet het werk.

Deze begrensde verbetering is gebouwd, gericht getest, beoordeeld en live in de
browser bewezen: eerste audio na 0,95 seconde, onderzoeksantwoord na 73 seconden,
volledige gesproken afronding ontvangen. Dit is één synthetische browserproef,
geen algemene snelheidsgarantie. Onderbreken van de ontvangstbevestiging laat
het latere resultaat intact; een tweede vraag tussendoor en expliciet stoppen
zijn met de echte LiveKit-spraakafhandeling getest. [Ontvangstbewijs](evidence/product/voice-work-receipt-2026-09-11.json).

GPU-versnelling van dezelfde geheugenrangschikker blijft live; 24 vergelijkingen
behielden feiten, volgorde en bronnen. De microfoon stuurt tijdens het spreken
PCM naar de bestaande HA-herkenner; alleen definitieve tekst gaat naar de kern.
[Geheugenversnelling en terugweg](evidence/product/voice-memory-cuda-2026-09-11.json).
[Streaming-invoerbewijs](evidence/product/voice-streaming-input-2026-09-11.json).
De Fenna-stem is een luisterkandidaat, nog zonder eigenaarsoordeel.
Probeer: “Zoek uit wat Home Assistant Cloud doet voor spraak en vertel me eerst
kort wat je gaat uitzoeken.” [Gericht bewijs](evidence/product/voice-natural-turn-candidate-2026-09-11.json).

Het eigenaars-paneel Praten met Eva is op 12 september geïnstalleerd in de bestaande Home Assistant-app/HTTPS-route. Echte HA-aanmelding, remote browserspraak en audio, een getypte paneelvraag, beëindigen, opnieuw verbinden en uitschakelen zijn technisch bewezen. De fysieke telefoonmicrofoon, echo, schermvergrendeling en Jaaps luisteroordeel blijven apart open. Natuurlijke Voice PE-toegang is nog niet opgeleverd. Hergebruik van deze bestaande route is geen verplichte Home Assistant-architectuur.
[Installatiebewijs](evidence/product/voice-phone-installation-2026-09-12.json).
[Actueel voicebewijs](evidence/product/voice-livekit-integration-2026-09-10.json).
De eerdere Voice PE-onbereikbaarheidsfout is hersteld en door Jaap bevestigd;
[herstelbewijs](evidence/product/VOICE-REF-247-repair-2026-09-10.json).

### Afzonderlijk hulpagentwerk — AU-002

Hulpagentwerk is technisch opgeleverd via de bestaande Telegramroute: starten,
stoppen, hervatten, status en opgeslagen resultaat ophalen. Sinds 12 september
zijn de laatste opdracht en het resultaat in gewone tekst leesbaar. De actuele
status volgt de opgeslagen toestand. Het bestaande resultaat is behouden.
Jaaps nieuwe Telegramproef en acceptatie staan open. Bij gelijktijdig stoppen
kan het startantwoord nog later arriveren. De timingcorrectie is nu technisch afgerond en live: een stop tijdens het
opstellen van het eindantwoord wordt vóór opslaan verrekend. Stoppen na de
laatste controle valt buiten die garantie. Jouw Telegrambeoordeling blijft open.
[Timingbewijs](evidence/product/AU-002-concurrent-stop-live-2026-09-12.json).
[Proberen](design/product/AU-002-owner-trial-2026-09-12.md) ·
[Actueel livebewijs](evidence/product/AU-002-readable-status-live-2026-09-12.json) ·
[Eerdere levenscyclusproef](evidence/product/AU-002-telegram-lifecycle-2026-09-11.json).

### Afzonderlijk Home Assistant-wakewordwerk — WAKE-001

Nog geen bruikbare verbetering. Twee begrensde doortrainingskandidaten zijn
geëxporteerd en op identieke lokale proefdata getest, maar afgewezen: minder
valse treffers gaat gepaard met te veel gemiste echte oproepen. Ook kalibratie
op aparte validatiedata levert geen geschikte drempel op. Het oorspronkelijke
model en beide apparaten blijven ongewijzigd; alle rekenprocessen zijn klaar.
Verder werk vraagt nieuwe, onafhankelijk gelabelde lokale oproepen en scripted
niet-oproepen van Jaap, plus de actuele ESPHome-configuratie en USB-herstelroute.
Geen privégesprekken opnemen, geen installatie en geen gebruikersacceptatie.
[Wakewordbewijs en minimale ontbrekende input](evidence/product/WAKE-001-offline-comparison-2026-09-11.json).

### Afzonderlijk browsertransportherstel — 12 september

Transportverlies stopt nu een lopende browseractie. Dat is voor verlies van de
aanvrager, gatewayadres/DNS, worker, proxy en bridge gericht gecontroleerd.
Oude toegang en paginawaarnemingen zijn daarna ongeldig; hervatten vraagt nieuwe
toegang en een verse waarneming. De correctie is beoordeeld en live.
De persoonlijke Chromeproef en jouw acceptatie blijven open.
[Herstelbewijs](evidence/product/chrome-inflight-recovery-2026-09-12.json).

### Spraaksessie bij verbergen en opnieuw verbinden — 12 september

Technisch getest en live: microfoonopname stopt bij verbergen, offline gaan,
opnieuw verbinden en afsluiten. Begin gesprek is nodig om opnieuw te starten.
Alleen de eigen audioruimte wordt opgeruimd; aangenomen kernwerk krijgt hierdoor
geen stopopdracht. De fysieke luister- en schermvergrendelingsproef staat open.
[Gebruikersproef](design/product/VOICE-owner-trial-2026-09-12.md) ·
[Livebewijs](evidence/product/voice-client-lifecycle-2026-09-12.json).

### Connectorproef TL-003 — technisch afgerond

Eén gewone HTTP-beurt is afgerond zonder handmatige hervatting: een nieuwe
CPython-endpoint bij de bekende aanbieder GitMCP aangesloten, documentatie
gebruikt, uitleg opgeslagen en teruggelezen. De proef duurde 146 seconden.
Dit is één synthetische opdracht in een bestaande conversatie; geen onbekende
aanbieder, eigenaaracceptatie of algemene voorsprong bewezen. De onafhankelijke
review heeft GO gegeven. Geen tweede proef gestart.
[Lees Eva’s uitleg](design/product/TL-003-python-contextmanager-result-2026-09-12.md) ·
[Reviewbewijs](evidence/product/TL-003-unassisted-review-2026-09-12.json).
[Uitvoeringsbewijs](evidence/product/TL-003-unassisted-2026-09-12.json).

### Zelf leren — AU-004: audit en proefplan opgeleverd

De bestaande skill- en toolleerroute wordt beoordeeld op hergebruik. Eerst wordt
één kleine ongeziene taak gekozen met een aantoonbaar ontbrekend vermogen. Het
proefplan beschrijft hoe Eva via haar algemene uitvoerder een herbruikbare skill
of tool maakt, gericht controleert en bij een tweede variant toepast.
De audit en het begrensde proefplan zijn opgeleverd ter afstemming. Een
opgeslagen taalgestuurde werkinstructie is mogelijk; een algemene uitvoerbare
code-tool of skillloader is niet aangetoond. De voorgestelde proef gebruikt
SRT naar WebVTT en een tweede ongeziene variant. Geen modelproef gestart;
geen gemaakte skill of eigenaaracceptatie geclaimd.
[Audit en proefplan](design/product/AU-004-small-skill-trial-plan-2026-09-12.md).

### Gatewaycatalogus — technisch afgerond en live

De correctie haalt alle cataloguspaginas op. Na review en uitrol leest een
verse kernopbouw 50 + 3 tools, inclusief de verplichte bestandsleesfunctie,
en bouwt 62 routes. Registratiegroei gevolgd door nieuwe provideropbouw is
gericht getest. Alleen kern en worker zijn vervangen; de zeven beschermde
diensten en instellingen bleven behouden. Geen modelproef voor dit herstel.
Dit sluit de paginatiefix af; bredere integratieproeven en eigenaaracceptatie
blijven apart. AU-004 blijft bij het opgeleverde plan, zonder uitgevoerde proef.
[Livebewijs](evidence/product/MCP-catalog-pagination-live-2026-09-12.json).

### Kwaliteitsborging en samenhang

De regels staan centraal in [AGENTS.md](../AGENTS.md#kwaliteitsborging-en-samenhang--eigenaarbesluit-12-september-2026).
Drie onafhankelijke audits onderzoeken modulariteit, integratie en gereedheid
voor concurrentievergelijking. De coördinator verwerkt concrete bevindingen
in dit werkbord. Gebruikersproeven mogen later en blokkeren onafhankelijk
technisch vervolgwerk niet. Algemene concurrentievoorsprong is niet bewezen.

### Concrete opvolging van de kwaliteitsaudits

[Architectuuraudit](reviews/architecture-whole-system-2026-09-12.md) en
[integratieaudit](reviews/integration-coverage-2026-09-12.md) zijn opgeleverd.
De architectuurpoort meldt 29 bevindingen; dit is geen nieuwe claim dat eerder
geaccepteerde functies defect zijn. Eerst het lopende catalogus-/opstartherstel
en de twee adapter-naar-bootstrap-grenzen afmaken. Daarna de voice-pakketgrens
en gedeelde schema-/registrygrenzen gericht oppakken. Nog ontbrekend ketenbewijs:
spraak + geheugenwijziging + helperwerk samen, en herstel van de samengestelde
installatie. De coördinator prioriteert dit vervolgwerk; de concurrentie-audit
loopt nog. Er is geen algemene groene integratie- of releaseclaim.

### Concurrentievergelijking — audit afgerond, uitvoering ontbreekt

[Vergelijkingsaudit](reviews/comparison-readiness-2026-09-12.md) is opgenomen.
EV-001 bewijst de brug met mockmodellen; EV-002 heeft twee echte antwoorden en
twee toelatingsmislukkingen; EV-003 is een protocol zonder vergelijkingsrun.
Eva1/Bottie is nog niet actueel gepaard vergeleken. Dit bewijst geen voorsprong.
Vervolg binnen CP-001 en de bestaande EV-route: maak een uitvoerbare gepaarde
proef met twee cases, elk tweemaal, eerst Eva2 tegenover Eva1/Bottie. Gebruik
de bestaande Inspectbruggen, vooraf vastgezette grenzen en onafhankelijke
readbacks. Deze runner en vergelijkingsrun zijn nog niet voltooid of uitgevoerd.

De kandidaat voor twee adapter-naar-bootstrap-grenzen is gericht gecontroleerd:
107 controles en op dezelfde bronbasis 27 naar 25 grensbevindingen, zonder
beleid te versoepelen. Onafhankelijke review staat open; nog niet live. De
eerdere audit met 29 bevindingen blijft een afzonderlijke historische meting.

### Documentatie en onderhouden opensource

De actuele afspraak staat in [AGENTS.md](../AGENTS.md#onderhouden-opensource-en-bestaande-besluiten),
met verwijzingen naar de bestaande opensourcekeuzes en documentatie-ingang.
De begrensde documentatie-audit en correcties van de primaire statusverwijzingen
zijn afgerond. Een selectie lezen betekent niet dat alle documentatie is
beoordeeld. Het root-README en componentenbord wijzen nu naar gedateerd bewijs.
[Audit en exacte leesgrenzen](reviews/documentation-alignment-2026-09-12.md).
Npm-dekking van Renovate en aantoonbare botactiviteit staan als afzonderlijk
onderhoudsvervolg open. Geen externe bot is gestart.

### Spraak, geheugen en hulpwerk — beperkt aanvullend testbewijs

Vijf onafhankelijk gecontroleerde tests zijn opgenomen voor gelijktijdige
API/runtime-afhandeling: een geheugencorrectie mag de context of helper van
een spraakbeurt niet overnemen. Persistence, geheugenprovider en model zijn
fakes. Dit bewijst geen echte Hindsight-recall, fysieke audio of duurzaam
herstel en verandert niets aan de liveversie of eigenaaracceptatie.

### AU-004 — eerste proefbeurt mislukt, oorzaakonderzoek loopt

De coördinator heeft uitvoering van het bestaande plan expliciet opgedragen.
Eerst worden twee kleine invoervarianten, ongeldige invoer en verwachte semantiek
verzegeld. Eva maakt in de eerste gewone beurt zelf een procedure en gebruikt
die in een tweede beurt met dezelfde hash. Bestaande grenzen blijven gelden;
geen aangeleverde converter, extra retries, externe berichten of uitrol.
De eerste gewone beurt eindigde na 69,914 seconden met HTTP503 /
RECOVERY_FAILED. Er is geen procedure of uitvoer voor variant A gemaakt;
variant B is niet gestart. Oorzaak: het researchantwoord bevatte 1267
uitvoertokens bij een bestaande grens van 1200. De host weigerde dit terecht.
Dezelfde uitvoerende taak maakt nu de opgedragen offline reparatiekandidaat:
lengte-instructie uit bestaand budget afleiden en gemeten verbruik ook bij
weigering duurzaam bewaren. De harde grens blijft staan; een prompt biedt
geen tokengarantie. Geen nieuwe modelpoging, variant B of uitrol gestart.
Jaap hoeft niets te doen. De onafhankelijke reviewer heeft de juistheid van
het mislukkingsbewijs goedgekeurd; dit is geen product-GO. De reparatie blijft
in uitvoering en AU-004 blijft onvoltooid.
[Bewijs van de eerste mislukte proef](evidence/product/AU-004-procedure-trial-failure-2026-09-12.json).
De AU-004-mijlpaal is niet afgerond of geaccepteerd. IN-02 en eerdere
acceptaties blijven apart.

### AU-004-reparatiekandidaat — bronreview GO, imagecontrole open

De kandidaat leidt de researchlengte-instructie af van het bestaande budget
en bewaart bekend gemeten verbruik in het bestaande RECOVERY_FAILED-event.
De limiet blijft gelijk; promptinstructies geven geen tokengarantie.
143 controles slagen. Eén catalogussnapshotcontrole faalt ook identiek op de
ongewijzigde basis; de fixture is niet aangepast. Geen volledig groene suite
geclaimd. Geen nieuw schema, extra modelpoging, variant B of uitrol. De
eerdere onafhankelijke GO betreft uitsluitend het bewijs van de mislukte proef.

De onafhankelijke bronreview van deze reparatie is GO: 41 gerichte controles
bevestigen limietbehoud, compatibiliteit, replay en verbruiksbehoud zonder
antwoordinhoud of secrets op te slaan. De eerdere catalogusfixturefout blijft
als baselinebevinding zichtbaar. Een begrensde image op d2d589 en geïsoleerde
PostgreSQL-readback worden voorbereid. Geen image-GO, live-uitrol of garantie
op modeluitvoerlengte of volledige kostenaggregatie geclaimd.

De reparatieimage is gereed voor onafhankelijke beoordeling: exact vijf
beoordeelde runtimebestanden gewijzigd, configuratie en eerdere imagelagen
behouden. Eén geïsoleerde PostgreSQL16.13-test slaagt voor opslaan, opnieuw
verbinden en teruglezen van geweigerd verbruik. Dit is geen proef op de live
PostgreSQL18.6. Image-GO en uitrol ontbreken; geen nieuwe modelpoging of B.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 146 | `AU-008` — Zelf kosteloos handelen binnen eigenaarmandaat | Eva kiest uit toegestane context een nuttige bijdrage, voert passende kosteloze stappen via haar gewone taakuitvoerder uit en toont resultaat of concrete blokkade. Stilte blijft een geldige keuze. | 🔵 REVIEW |

Klaar betekent:

- Technisch afgerond: echte zelfgekozen bijdrage teruggelezen; laatste beoordeling zichtbaar en onafhankelijk gecontroleerd.
- Alleen IN-02 blijft open: Jaap beoordeelt het nut en de passendheid van de bestaande bijdrage.

<details>
<summary>Technische uitvoering: bestanden en controles</summary>

### Exact werk voor `AU-008`

- `eva2/experiments/react-admin-dashboard-v1/src/owner-initiatives.jsx`
- `eva2/experiments/react-admin-dashboard-v1/verify-browser.cjs`
- `eva2/src/eva/adapters/api/dashboard_static/assets/index-Bv4zaQUs.js`
- `eva2/src/eva/adapters/api/dashboard_static/assets/index-Ca8HuISF.js`
- `eva2/src/eva/adapters/api/dashboard_static/eva-dashboard-assets.json`
- `eva2/src/eva/adapters/api/dashboard_static/index.html`
- `eva2/src/eva/adapters/postgres/owner_initiatives.py`
- `eva2/tests/postgres/test_owner_initiatives.py`

Echte controles:

- `pytest -q tests/postgres/test_owner_initiatives.py::test_owner_sees_private_review_before_adoption tests/postgres/test_owner_initiatives.py::test_private_review_is_owner_installation_scoped_current_and_hash_checked tests/postgres/test_owner_initiatives.py::test_owner_can_read_result_and_stop_claimed_work_without_resurrection`

</details>

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

- [ ] `VB-004` — Browser na onderbreking veilig hervatten: Herstelbestanden gebouwd; systeeminstallatie automatisch geblokkeerd. Bestaande browser blijft live. Herstel na herstart nog niet uitgevoerd of bewezen.
- [ ] `AU-005` — Passend initiatief en kansen bewijzen: Meer gewaardeerd eigen initiatief dan Bottie: humor, nieuwsgierigheid, zelfgekozen onderzoek, creatieve bijdragen en kansen. Eva begint en vervolgt zelf werk, voert binnen bestaand mandaat uit en controleert het resultaat. Gezin beoordeelt eigenheid, plezier, nut en hinder; alleen meer berichten telt niet als winst.
- [ ] `VB-001` — Full-duplex voice leveren: Met Eva2 praten via de bestaande Telegram- of Home Assistant-aansluiting, waarbij Eva hoorbaar onderbroken kan worden en het gesprek daarna doorgaat.
- [ ] `VB-002` — Passende aanwezigheid leveren: Eva zoekt spontaan contact via de beschikbare speakers en deurbel, met herkenbare identiteit en gevoel voor het moment. Gezin beoordeelt plezier, betrokkenheid en hinder; onderbreken en stop werken.
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
- [x] `AU-007` — Zelf een relevant privaat voorstel uitwerken (done).
  [Bewijs](evidence/product/AU-007-private-initiative-2026-09-10.json).
  [Bewijs](reviews/AU-007-private-initiative-34ed766.md).
- [x] `AU-002` — Duurzame hulpagent bedienen (done).
  [Bewijs](evidence/product/AU-002-telegram-lifecycle-2026-09-11.json).
- [x] `CO-004` — Eigen geheugenbronnen volledig inzien (done).
  [Bewijs](evidence/product/CO-004-CO-005-owner-memory-controls-2026-09-11.json).
- [x] `CO-005` — Bewaarde informatie bewust corrigeren of vergeten (done).
  [Bewijs](evidence/product/CO-004-CO-005-owner-memory-controls-2026-09-11.json).
- [ ] `AU-008` — Zelf kosteloos handelen binnen eigenaarmandaat (review).

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
