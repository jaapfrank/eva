# Architectuurcontrole EVA 2 — 12 september 2026

## Uitkomst en grenzen

De modulaire basis bestaat, maar de bestaande architectuurpoort is nu **rood**. Dit is geen bewijs dat de werkende productfuncties defect zijn; het betekent dat recente integraties de afgesproken importgrenzen niet overal behouden. Kleine gerichte reparaties verdienen voorrang boven een nieuwe architectuur of een brede herschrijving.

Read-only audit van de gedeelde werkboom bij HEAD `f8c21c1abb421176fd0ac91816ba423120066208`; andere agents werken gelijktijdig. Uitgevoerd: `python3 eva2/tools/check_architecture.py`, exit 1, 351 gecontroleerde en 1010 ontdekte Pythonbestanden. Resultaat: 29 bevindingen (17 externe imports, 10 lokale importgrenzen, 1 nieuwe providerspecifieke schuld, 1 onbekend pakket). Deze aantallen zijn diagnostiek, geen productvoortgang of codekwaliteitsquotum. Geen runtime-, model- of databaseaanroepen; alleen dit rapport geschreven.

## Bestaande waarborgen behouden

- `design/architecture/package-boundaries-v1.json` legt domein/applicatie, adapters, compositie en geïsoleerde connectors vast. `tests/architecture/test_package_boundaries.py` heeft reeds negatieve tests voor omgekeerde imports, relatieve/dynamische imports, onbekende pakketten en groeiende providerspecifieke schuld.
- `tools/run_gate.py:114` neemt diezelfde checker al op. Geen tweede architectuurframework of nieuwe goedkeuringsadministratie nodig.
- Het geheugen blijft bereikbaar via applicatiecontracten en persistence-/Hindsight-adapters; in de huidige checkeruitvoer staat geen domein- of applicatie-import naar infrastructuur. De recente geheugenacceptatie en gerichte functionele bewijzen worden niet door deze audit heropend.
- De timingreparatie gebruikt een providergebonden `reply_guard`, met exacte ontvangstbewijzen en eigenaarcontrole. Zij voegt geen helpernaamafhankelijke dispatch aan de centrale dispatcher toe. Haar afzonderlijke GO is gedragsbewijs; geen verklaring dat de volledige bestaande architectuurschuld nul is.
- Chrome blijft een afzonderlijke bridge/proxy en LiveKit gebruikt de kern via HTTP. Die scheiding is nuttig. Het feit dat een dienst los draait vervangt echter niet de bron-/pakketcontrole.
- `docs/eva-repository-inventory.generated.md` is een bestaande snapshot met 188 policybevindingen, geen actuele telling uit deze audit. Niet opnieuw als live releasebewijs gebruiken. `design/architecture/living-core-v1.md` noemt nog een groene poort met 299 bestanden; dat historische resultaat wijkt af van de bovenstaande huidige meting.

## 1. Adapters construeren of importeren de compositielaag

**Bewijs:** `src/eva/adapters/postgres/delegated_work_actions.py:22` importeert `DelegatedWorkOwnerService` uit `eva.bootstrap`; `src/eva/adapters/api/household_voice_session.py:7,22` importeert en construeert `NativeHouseholdResponder` uit dezelfde laag. Beide verschijnen als concrete `local_import_boundary`-bevinding. Het bootstrap-ownerbestand importeert zelf persistence en een bootstrap-providerbuilder: dit is geen pure contractmodule.

**Gevolg:** opslag/HTTP-adapters hangen van uitvoerbare samenstelling af. Een wijziging aan de samenstelling kan daardoor gewone adaptertests en hergebruik beïnvloeden. Dit is vooral relevant nu dezelfde helperdienst door oude Telegramcommando’s en generieke tools wordt gebruikt.

**Kleinste correctie:** behoud die ene gedeelde ownerdienst; geef de helperadapter een smal applicatie-Protocol voor de reeds gebruikte `available_for`, `validate_authority` en `execute`, en injecteer het bestaande object vanuit bootstrap. Laat de household-route een al gemaakte responder ontvangen; de huidige bootstrap maakt hem. Niet dezelfde autorisatielogica kopiëren en niet het hele bootstrapbestand simpelweg naar application verplaatsen zolang het concrete persistence importeert.

**Bewijs voor afronding:** beide importmeldingen verdwenen onder ongewijzigde grenzen; bestaande helper-owner/replay/timingtests en household-toegangsproeven blijven groen. Voeg in de bestaande architectuurtest één negatief geval adapter→bootstrap toe als gerichte bescherming. Geen brede releaseproef nodig voor deze kandidaat alleen.

## 2. Persistence leunt op concrete connectorimplementaties en private validatie

**Bewijs:** `src/eva/adapters/postgres/initiative_execution.py:9,50` importeert en gebruikt de private `_validate_schema_value` uit de MCP-netwerkadapter. Hetzelfde bestand importeert credential-file-I/O op regel 11. `src/eva/adapters/postgres/mcp_connector_registry.py:20,32,33` importeert concrete connectorconfiguratie en providerbinding/parser. Deze grenzen staan alle in de huidige checkeruitvoer.

**Gevolg:** de duurzame registratie en initiatiefbevoegdheden hangen af van details van één transportadapter. Wijzigingen aan netwerkvalidatie krijgen gevolgen voor opgeslagen besluiten; een private helper wordt feitelijk een gedeeld contract zonder dat de bronstructuur dat uitdrukt.

**Kleinste correctie, in afzonderlijke stukken:** begin met de pure schemasubset-validatie: geef de bestaande gedeelde implementatie een openbare plek in een connectorneutraal contract/applicatiemodule en laat beide callers dezelfde functie gebruiken. Behoud de strengere initiatiefregels en bestaande foutsemantiek. Maak daarna alleen de benodigde immutable registry-bindinggegevens connectorneutraal; parser, credential-load en concrete gatewayconstructie blijven buiten de opslagadapter en worden geïnjecteerd. Geen tweede parser, schema-engine of nieuwe repositorylaag toevoegen.

**Bewijs voor afronding:** dezelfde toegestane/geweigerde schema’s, argumenten en patronen leveren dezelfde resultaten bij beide callers; registratie/readback/intrekken/herstart blijven werken. Gebruik bestaande initiatief- en registrytests plus de bestaande importchecker. Een groene test door simpelweg alle `eva.adapters`/`eva.providers` in postgres toe te staan is geen oplossing.

## 3. De aparte voice-service heeft nog geen verklaarde architectuurgrens

**Bewijs:** de checker meldt `unexpected_package` voor `src/eva/voice_service`. `agent.py:10–11` importeert HTTPX en LiveKit; dit is terecht infrastructuur, geen zuivere applicatiecode. `check_architecture.py:366` slaat de importcontrole binnen onbekende pakketten over nadat het onbekende pakket is gemeld. Daardoor is de poort wel rood, maar zijn de feitelijke toegestane afhankelijkheden van deze nieuwere dienst nog niet afdwingbaar beschreven.

**Gevolg:** huidige audio-, lifecycle- en reconnecttests bewijzen specifiek gedrag, maar verhinderen nog niet dat de afzonderlijke spraakdienst later rechtstreeks bootstrap, databases of private core-adapters gaat gebruiken. Dat zou de nuttige huidige HTTP-grens ongedaan maken.

**Kleinste correctie:** declareer `voice_service` in het bestaande beleid als buitenste transportpakket, met uitsluitend de werkelijk noodzakelijke externe libraries en een smalle lokale allowlist. Handhaaf communicatie met de kern via het bestaande protocol. Classificeer de al bestaande component in de bestaande componentcatalogus indien die registratie ontbreekt; maak geen tweede register. Verplaats niet nu alle voicebestanden om alleen de mapnaam te verbeteren.

**Bewijs voor afronding:** alle voice-Pythonbestanden worden daadwerkelijk op imports gecontroleerd; een negatieve fixture die bijvoorbeeld `eva.bootstrap.runtime` of `eva.adapters.postgres` importeert moet falen. Bestaande 50 lifecyclechecks en de relevante protocoltests blijven leidend voor gedrag; een fysieke telefoonproef blijft apart eigenaarbewijs.

## Uitvoervolgorde

Repareer eerst de kleine adapter→bootstrap-grenzen, daarna het voice-pakketbeleid, daarna de gedeelde schemasubset/registrygrens in beperkte slices. Registreer de overige huidige checkerbevindingen in de bestaande uitvoerbron; veel externe-librarymeldingen kunnen een ontbrekende nauwkeurige declaratie zijn, terwijl omgekeerde imports echte codeverplaatsing/injectie vragen. De policy niet breed versoepelen om de teller op nul te krijgen.

Laat bij iedere relevante kandidaat dezelfde architectuurchecker meelopen en vermeld nog bestaande bevindingen eerlijk. Kies code op verantwoordelijkheid, herbruikbaarheid en aantoonbaar gedrag, niet op aantallen regels, bestanden of tests. Dit rapport kent geen nieuwe eigenaaracceptatie toe en start geen brede refactor.
