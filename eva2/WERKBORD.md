# Eva 2 — uitvoerbaar werkbord

Laatst bijgewerkt: 2026-09-01.

Dit bord is automatisch uit de machineleesbare werklijst gemaakt. Handmatige
afwijkingen worden geweigerd. We ronden één kleine kaart volledig af voordat
de volgende begint.

## Nu

| Volgorde | Kaart | Resultaat | Status |
|---:|---|---|---|
| 20 | `RB-002` — Nieuwe bootstrapcode verzegelen | Release-inputauthority en tests nemen exact de na RB-001 bewezen bootstrapbestanden op. | 🟡 BEZIG |
| 30 | `RB-003` — Kandidaat op bouwer A maken | Een verse daemon bouwt de door RB-002 verzegelde commit offline en levert herleidbaar bewijs. | ⚪ HIERNA |

### Exact werk voor `RB-002`

- `eva2/tools/managed_b_candidate/release_inputs.py`
- `eva2/tests/operations/test_managed_b_release_inputs.py`

Klaar betekent:

- Iedere nieuwe bootstrapbron en aanvalstest is door de release-inputauthority gedekt.
- Een ontbrekend, extra of gewijzigd pad stopt de releasecontrole.
- Een onafhankelijke reviewer geeft GO.

Echte controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_managed_b_release_inputs.py`
- `ruff check tools/managed_b_candidate/release_inputs.py tests/operations/test_managed_b_release_inputs.py`

### Daarna voorbereid: `RB-003`

- `eva2/tools/check_two_builder_reproducibility.py`
- `eva2/tests/operations/test_two_builder_reproducibility.py`

Vooraf vastgelegde controles:

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_two_builder_reproducibility.py`
- `ruff check tools/check_two_builder_reproducibility.py tests/operations/test_two_builder_reproducibility.py`

## Daarna

Deze onderwerpen zijn alleen mijlpalen. Een mijlpaal wordt pas uitvoerbaar
nadat zij als eerstvolgende kleine kaart exacte bestanden en tests heeft.

| Nr. | Onderdeel | Begrijpelijk eindresultaat |
|---:|---|---|
| 40 | `RB-004` — Kandidaat op bouwer B maken | Een onafhankelijke verse daemon bouwt dezelfde commit offline. |
| 50 | `RB-005` — Beide builds vergelijken | Config, lagen en runtime-inhoud zijn gelijk; bewijshergebruik faalt. |
| 60 | `RB-006` — SBOM en kwetsbaarheden controleren | De exacte kandidaat heeft een gebonden SBOM en nul hoog/kritiek. |
| 70 | `RB-007` — Alle verplichte testgroepen uitvoeren | Iedere verzegelde testgroep slaagt op dezelfde commit. |
| 80 | `RB-008` — Volledige wegwerpstack testen | De stack migreert, antwoordt, onthoudt, herstart en ruimt op. |
| 90 | `RB-009` — Fysieke bewijsset beoordelen | Een onafhankelijke reviewer controleert de volledige releaseketen. |
| 100 | `RB-010` — A/B-besluit nemen | Alleen volledig groen mag naar het niet-actieve slot. |
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
| 340 | `CP-001` — Gelijke productmatrix uitvoeren | Actuele concurrenten krijgen hetzelfde model, budget en opdrachten. |
| 350 | `CP-002` — Cross-domainproef uitvoeren | Gesprek, geheugen, tools, browser, subagents en beheer zijn eerlijk vergeleken. |
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

Machineleesbare bron:
[`design/product/execution-workboard-v1.json`](design/product/execution-workboard-v1.json).
