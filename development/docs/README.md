# Eva — documentatie-index

**Lees niet alle documenten.** Voor huidig ontwikkelwerk zijn er vier gezaghebbende ingangen:

| Leesvolgorde | Document | Functie |
| --- | --- | --- |
| 1 | [PROJECTDOEL.md](PROJECTDOEL.md) | Wat Eva voor het gezin moet worden — geen voortgangscijfers |
| 2 | [AFRONDINGSPLAN.md](AFRONDINGSPLAN.md) | Concrete, genummerde **P0–P3**-prioriteiten en bewijsregels |
| 3 | [ARCHITECTUUR.md](ARCHITECTUUR.md) | Grenzen van kern, kanalen, geheugen en bevoegdheden |
| 4 | [WERKBORD.md](WERKBORD.md) | Kort **gegenereerd** overzicht van de actuele JSON-status |

**Enige statusbron:** [execution-workboard-v1.json](../design/product/execution-workboard-v1.json). Daar staan de 103 oorspronkelijke en latere taakkaarten inclusief exacte criteria, bewijs en verlies. `currentWork` en `audit.deliveryPlan` blijven gezaghebbend. [AGENTS.md](../../AGENTS.md) bevat regels voor de uitvoerders. [VEREENVOUDIGING.md](VEREENVOUDIGING.md) bevat de technische P0-hotspots.

## Overige actieve referenties — alleen gericht lezen

| Bestand | Waarom behouden |
| --- | --- |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Bijdrage-, test- en veiligheidsregels |
| [mcp-catalog-extension.md](mcp-catalog-extension.md) | Werkelijk configuratiecontract voor optionele MCP-catalogus |
| [camera-observer-20261019.md](camera-observer-20261019.md) | Oorspronkelijke E4.15-kamer-/bronacceptatie is nog open |
| [eva-continuous-development-20261017.md](eva-continuous-development-20261017.md) | **Bronhashgebonden** ontwikkelscope in bestaande taakuitbreiding; historische ontwerptekst, geen huidige opdracht |
| [OVERDRACHT-WINDOWS.md](OVERDRACHT-WINDOWS.md) | Historische WSL-overdracht; expliciet genoemd door het huidige coördinatiebestand. Niet als actuele status lezen |
| [OPEN_SOURCE_COMPONENTEN.md](OPEN_SOURCE_COMPONENTEN.md) | Gedateerde dependency- en licentiebesluiten; geen actuele upstream- of kwetsbaarheidsscan |
| [repository-registration-20261017.md](repository-registration-20261017.md) | Versiegebonden E3-inventaris, ook uitvoerpad van inventaris-tool |
| [eva-repository-inventory.generated.json](eva-repository-inventory.generated.json) | Grote maar **actief gebruikte** inventaris voor catalogus-/hashvalidatie; niet handmatig aanpassen of verwijderen |
| [reading-register-20261013.json](reading-register-20261013.json) | Historisch leesbewijs en herleidbaarheid; niet als actuele omvangmeting gebruiken |

## Opgeruimd op 5 november 2026

Zes verouderde `development/docs/`-bestanden zijn **uit de actieve documentatiemap verwijderd**, maar hun **exacte Git-blob** is bewaard in [historische verslagen](../archive/history/20261105-documentatie-opruiming/historische-verslagen/):

- `aide-quality-recovery-20261013.md` — oude HUE6-herstelproef
- `e32-core-review-20261017.md` — afgesloten kerninventaris
- `e33-adapters-installation-review-20261017.md` — afgesloten adapterbeoordeling
- `e34-tests-docs-history-review-20261017.md` — afgesloten E3-documentbeoordeling
- `project-reassessment-20261013.md` — oude herbeoordeling en toenmalige plannen
- `repository-pruning-20261013.md` — verslag oude opschoonronde

De **vier oude uitgebreide hoofddocumenten** zijn ook [exact bewaard](../archive/history/20261105-documentatie-opruiming/). De nieuwe versies vervangen hun actieve functie; ze wissen niet het historisch bewijs.

## Verwijderbeleid

- **Verwijderen uit actieve docs** kan bij afgeronde historisch-only rapporten zonder runtime, test-, hash- of productverwijzing; kopieer eerst exact naar het archief en controleer bronhash.
- **Niet verwijderen:** taak-/scope-JSON, afgesloten test-/reviewbewijs, installatielocks, herstelbestanden, archiefbronbindingen, gegenereerde inventaris of documenten waarvan de inhoud/hash door code wordt gelezen.
- De historische bewijsbestanden kunnen nog **het oorspronkelijke pad** noemen. Dat is een oud feit, geen actuele bestandslocatie: de bovenstaande index wijst naar het archief. Herschrijf die bewijsstukken niet achteraf.
- **Nieuwe werkdocumenten:** houd de hoofdpagina's klein; wijzig de bestaande pagina in plaats van elk probleem tot een nieuw doorlopend rapport te maken. Alles wat taakstatus is, hoort uitsluitend in het JSON-werkbord.
