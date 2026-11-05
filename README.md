# Eva2 — een zelfstandige digitale huisgenoot

Eva is een persoonlijk AI-project voor één herkenbare digitale huisgenoot: ze praat met mensen, begrijpt context, onthoudt wat ertoe doet, neemt passend initiatief en probeert digitale opdrachten **daadwerkelijk af te ronden**. Ze moet op termijn ontbrekende mogelijkheden kunnen ontdekken, maken, veilig beoordelen en later hergebruiken.

**Status:** een werkende Pythonbasis en geïnstalleerde proefomgeving, **geen afgerond consumentenproduct**. Een technisch geslaagde proef, een geïnstalleerd onderdeel en bewezen dagelijks gezinsgebruik zijn niet hetzelfde.

## Wat moet Eva kunnen?

| Productuitkomst | Bewezen basis | Nog open |
| --- | --- | --- |
| **Persoonlijkheid en gesprek** | Gedeelde Eva-identiteit, tekstkanalen en een tweerichtingsspraakroute | Natuurlijk ritme, betrouwbare onderbreking, samenhangend dagelijks gebruik |
| **Geheugen en mensbegrip** | Persoonsgebonden geheugen, bronherkomst, correctie/vergeten, semantisch terugzoeken en gerichte droomverwerking | Meerdaagse relevantie en nut in de volledige gezinsproef |
| **Eigen initiatief** | Begrensde aandacht-/feedbackroutes en technische deelproeven | Passende zelfstandige acties en opvolging over meerdere dagen |
| **Zelfstandig werk en ontwikkeling** | Duurzame taakstatus, ontvangstbewijzen, geïsoleerde ontwikkel- en beoordeelroutes; begrensde E4.16-installatie/readback | Volledige autonome fout → herstel → latere hergebruikcyclus en betrouwbaar resultaat bij gewone opdrachten |

De [103 geregistreerde deeltaken](development/docs/WERKBORD.md) zijn **geen 103 voltooide productfuncties**. In de onderzochte GitHub-status zijn er 83 afgerond, 20 open en slechts vier van 24 oorspronkelijke hoofduitkomsten gesloten. **F2.1 zeven dagen gezinsgebruik** en **F2.2 eigenaaracceptatie** zijn nog open. De daadwerkelijke WSL-installatie moet afzonderlijk worden teruggelezen.

## Architectuur: één Eva, verschillende lagen

```text
Telegram / privéweb / spraak / toegestane gebeurtenissen
                         ↓
                 identiteit en mandaten
                         ↓
       lopend gesprek + persoonscontext en geheugen
                         ↓
        afweging: antwoorden, actie of bewust stilte
                         ↓
         duurzame taak → tools → controle/receipt
                         ↓
        feedback + herinnering + later hergebruik
```

**Geheugen is gelaagd in functie, niet in losse concurrerende databases:**

- **Directe gespreks- en dagcontext:** lopende bedoeling, recente gesprekken en actuele afspraken.
- **Duurzame brongebonden feiten en ervaringen:** PostgreSQL registreert gebeurtenissen, persoon, herkomst, correctie, rechten en verwijdering.
- **Semantisch terugvinden:** Hindsight haalt relevante persoonsgebonden herinneringen op; het is een zoek-/geheugenprojectie, niet een zelfstandige bevoegdheidsbron.
- **Droomfase en leren:** relevante daginformatie en feedback worden geselecteerd, samengevoegd of gecorrigeerd voor later gebruik. Niet iedere chat wordt een eeuwige herinnering.

Die lagen vormen een ontworpen en deels geïnstalleerde keten. Een episodisch wereldmodel of allesbegrijpende kennisgraaf is **geen bewezen volledig product**.

## Technische stack

| Onderdeel | Techniek en rol |
| --- | --- |
| **Kern en contracten** | Python 3.12, Pydantic 2, PydanticAI (`pydantic-ai-slim`), getypeerde gegevens en expliciete tool-/bevoegdheidscontracten |
| **Modellen** | OpenAI-compatibele modeladapters en configureerbare optionele providers; modeltoegang is gescheiden van persoons- en toolrechten |
| **API en bediening** | FastAPI / Uvicorn, lokaal webdashboard en React-interfaces voor werk, geheugen en beheer |
| **Geheugen** | PostgreSQL, SQLAlchemy, psycopg, Alembic en Hindsight; dagcontext, duurzame bronnen, semantische recall en selectieve droomconsolidatie |
| **Agents en taakuitvoering** | PydanticAI, gescheiden achtergrondwerkers, duurzame PostgreSQL-status, claims, checkpoints, herstelaanleidingen en receipts |
| **Leren en AIDE²** | Begrensde reflectie en **AIDE²-geïnspireerde** kandidaat-/variantvergelijking voor gedrag, code en uitbreidingen. Baseline behouden bij verlies of onvoldoende bewijs; latere echte toepassing apart aantonen |
| **Tools en verbindingen** | MCP, versieerbare procedures/skills, Home Assistant, browser, Telegram; camera en WhatsApp zijn optionele integraties |
| **Spraak** | LiveKit/WebRTC, audio- en spraakcomponenten, bestaande native speakers; turn-taking, barge-in en fysieke acceptatie blijven open |
| **Uitvoering en updates** | Docker/OCI, WSL en beheerste images; hashes, gecontroleerde release, rollback, back-up en herstel |
| **Kwaliteitsbewaking** | pytest, contract- en integratietests, geïsoleerde codeproeven, regressiemetingen en een organisatorisch gescheiden reviewer |

De vastgelegde `pyproject.toml` bevat onder andere **PydanticAI 2.37.0**, FastAPI, SQLAlchemy en Alembic. Historische versiecontracten blijven beschikbaar voor oudere releases. Niet iedere connector of provider staat standaard aan en de README zegt niets over live beschikbaarheid of ingeschakelde toestemmingen.

**Zelfverbetering is geen onbeperkte zelfbeschikking.** Eva mag niet vanuit een prompt nieuwe rechten krijgen. Codewijzigingen vragen een controleerbare kandidaat, gescheiden toets, veilige installatie en echt gedrag na installatie. De huidige reviewbinding en refactorbare afleverkant hebben nog aandachtspunten.

## Belangrijkste open technische problemen

De uitvoering volgt [genummerde prioriteiten P0–P3](development/docs/AFRONDINGSPLAN.md), met detail in het [vereenvoudigingsplan](development/docs/VEREENVOUDIGING.md): betrouwbare levering, geen stille fouten, provideruitval, tokengebruik, reviewherkomst, veilig gezinsgebruik en daarna natuurlijke spraak en zelfstandig initiatief. De proef met één stille initiatiefafweging gebruikte bijvoorbeeld **22.633 tokens**; een werkende stiltebeslissing is dus niet automatisch efficiënt.

**Bestaande sterke basis behouden:** actuele mandaten in plaats van bevoegdheid uit tekst, geen herverzending van onzekere externe effecten, hash-/receiptgebonden installaties, bronherkomst in geheugen en stop-/herstelscheiding. Er is nog geen algemene claim dat Eva beter presteert dan OpenClaw, Hermes of andere assistenten.

## Waar staat alles?

- [Projectdoel](development/docs/PROJECTDOEL.md) — één pagina over de vier gewenste uitkomsten.
- [Architectuur](development/docs/ARCHITECTUUR.md) — verantwoordelijkheden en veiligheidsgrenzen.
- [Afrondingsplan met P0–P3-prioriteiten](development/docs/AFRONDINGSPLAN.md) — volgorde, acceptatie en open problemen.
- [Technisch vereenvoudigingsplan](development/docs/VEREENVOUDIGING.md) — precies welke complexe code moet veranderen.
- [Actueel, gegenereerd werkbord](development/docs/WERKBORD.md) — status uit de [enige JSON-bron](development/design/product/execution-workboard-v1.json).
- [Documentatie-index en archiefregels](development/docs/README.md) — wat actief is en wat historisch blijft.
- [AGENTS.md](AGENTS.md) — regels voor ontwikkelaars en AI-uitvoerders.

De broncode staat in `src/`, tests en bewijs in `development/`, installatie in `deploy/`. `start-installer.py` is het installatie-entrypoint. De publieke repository bevat niet de volledige actuele toestand van een draaiende privé-Windows/WSL-installatie.
