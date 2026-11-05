# Eva2 — herbeoordeling en actieve bronindeling · 13 oktober 2026

## Aanvullende verwijdering — 13 oktober

De oude volledige Eva1-applicatie en twee dubbele migratiebackups zijn daarna
uit de actieve checkout verwijderd: 857 bestanden, 43.586.937 bytes. Ze blijven
bytegelijk terug te halen vanaf Git-revisie `eab83747a3c33a83f2f63f38180dc7d6330635ec`.
De 33 nog door benchmarktests gebruikte Eva1-inputbestanden blijven lokaal.
Historische bronbindings, installatieinputs, dashboardreferenties en gesloten
bewijs zijn behouden. De onderstaande migratiebeschrijving is een eerdere
snapshot; oorspronkelijke kopieën hoeven na deze opruiming niet lokaal te staan.
Zie [besluiten en beperkingen](repository-pruning-20261013.md) en de
[exacte verwijderlijst](../archive/pruning-20261013.json).

## Historische rootgroepering — 13 oktober

De zichtbare root heeft drie hoofdmappen: `src` (code), `deploy` (installatie,
locks, schema's en migraties), `development` (documentatie, tests, benchmarks,
projectdashboard en archief). `src/eva` is de Python-applicatie; de repository
zelf is Eva2. De helpers die Eva importeert blijven in `src/tools`, met dezelfde
importnamespace. Het ontwikkeldashboard staat in `development/tools/project-dashboard`.

De [tweede behoudregistratie](../archive/layout-migration-20261013.json) bindt
alle 5.052 uitgangsbestanden aan hun nieuwe pad; aangepaste operationele bestanden
hebben een exacte oorspronkelijke kopie. Gesloten resultaat-/review-/benchmark-JSON
is bytegelijk behouden. De eerste migratieregistratie blijft historische herkomst.
Imports, verpakking, installerselectie, bronbindingscontrole en dashboardlocators
zijn gekoppeld aangepast; geen nieuwe uitvoerder of productfunctie.

118 gerichte unitcontroles slagen. Architectuurcontrole houdt dezelfde 110 bestaande
bevindingen; geen nieuwe bevinding door de indeling. De huidige release-inputauthority
heeft negen al vóór deze groepering ontbrekende oude bestanden. Dat is geen bewijs
van een complete nieuwe installerrelease. Bestaande gepinde images en runtimegegevens
zijn behouden. Geen betaalde Eva-modelproef of vergelijkingsserie gestart.


## Besluit

De bestaande Pythonbasis blijft; de actieve bron staat direct in `src/eva` in de
repositoryroot. De bestaande uitvoerder, poorten, geheugen en verbeterroute worden
hergebruikt. Eva1 is uitsluitend historische vergelijkingsreferentie; lokaal resteren alleen gebruikte benchmarkinputs.
De afzonderlijke voorbereidingsbranch `codex/eva2-root` wordt na review naar `main`
gebracht. Branch en map zijn verschillende zaken: een geneste `eva2/`-map was geen
voorwaarde voor een afzonderlijke branch.

Een nieuw platform is niet onderbouwd als betere oplossing voor het huidige tekort.
Bestaande vergelijkingen tonen beperkte geheugenvoordelen, taakverliezen en gelijke
resultaten. Ze bewijzen geen algemene winnaar. Er is geen nieuwe vergelijkingsserie
of Eva-modelproef gestart voor deze reorganisatie.

## Wat blijft, wat gaat naar backup?

| Onderdeel | Besluit | Praktisch gevolg |
| --- | --- | --- |
| Python `application`, contracts, domain en bootstrap | Behouden op root | Eén samenstelling en algemene uitvoering, geen tweede kern. |
| PostgreSQL + Hindsight | Behouden | Gegevens en echte geheugenacceptaties blijven; geen herbouw van geheugen. |
| Providers en connector SDK | Behouden als optionele mogelijkheden | Een huisgenoot realiseert een gewenste mogelijkheid; HA/browser zijn voorbeelden. |
| Duurzame uitvoering en resultaatterugmelding | Behouden, laatste overdracht afmaken | Het volledige opgeslagen resultaat moet bij de oorspronkelijke ontvanger leesbaar zijn. |
| Verbetering/evaluatie/toepassing | Behouden | AIDE² omvat aansturing, code en uitbreidingen; echte kwaliteitswinst en later gebruik blijven voorwaarden. |
| Gebruikte gateway en dashboard | Naar `deploy/gateway` en `development/tools/project-dashboard` | Gebruikte onderdelen staan niet langer verstopt tussen experimenten. |
| Gebruikte releaseproefhelpers | Naar `development/validation/release-acceptance` | Installerafhankelijkheden blijven bestaan zonder tweede productuitvoerder. |
| Oude Eva1, gedateerde plannen, ongebruikte experimenten | Naar `backup` | Referentie blijft beschikbaar; oude opdrachten sturen geen nieuw werk aan. |
| Evidence/reviews en frozen benchmarkcriteria | Bytegelijk behouden | Historische verliezen, UNKNOWN en acceptaties worden niet herschreven. |

Alle 4.907 oorspronkelijke gevolgde bestanden hebben een doelpad en oorspronkelijke
hash in [het behoudmanifest](../archive/migration-manifest.json). Gewijzigde
operationele bestanden hebben daarnaast hun oorspronkelijke bytes in backup.
Lokale caches en privé-proefbestanden worden niet naar GitHub toegevoegd.

## Waar voortgang verloren ging

| Aangetroffen oorzaak | Bron | Verandering / gevolg |
| --- | --- | --- |
| Oude AU-008-aansturing en actuele HUE-aansturing naast elkaar; 41/54 naast 43/54 | [Oorspronkelijk bord](../archive/history/eva2/design/product/execution-workboard-v1.json) | Eén actuele kaart en doel; historie apart. |
| Meerdere gedateerde “Actueel”-secties blijven opdrachten suggereren | [Oude AGENTS](../archive/history/root/AGENTS.md) en [oud doel](../archive/history/eva2/PROJECTDOEL.md) | Korte huidige afspraken; backup is referentie. |
| Bron, evaluatiehost en gepinde toepassing hebben verschillende bindings | [Toepassingsmodule](../../src/eva/operations/reviewed_component_application.py) | Eén coherent pakket gebruiken bij volgende toepassing; hashes niet versoepelen. |
| ACK op een samenvatting bewijst niet dat het volledige artifact bruikbaar is teruggebracht | [Resultaatterugmelding](../../src/eva/adapters/postgres/delegated_work_delivery.py) | Eerst die bestaande route volledig afmaken; geen extra samenvattingsmodel. |
| Technische deelbewijzen werden als volledige productvoortgang gelezen | [Huidig bord](../design/product/execution-workboard-v1.json) | Vier gebruikersuitkomsten; 43/54 kleine afrondingen zijn geen vier afgeronde producten. |

## Modulariteit en resterende schuld

[ARCHITECTUUR](ARCHITECTUUR.md) beschrijft de werkelijke moduleverantwoordelijkheden
en interfaces. De ongewijzigde importgrenscontrole meldt 110 bevindingen op zowel de oude als de nieuwe
indeling: geen nieuwe overtreding door de verhuizing. De eerste voorbereidingssnapshot
miste drie geïsoleerde mappen; die snapshot is aangevuld vóór de eindvergelijking. Die 110 bestaande bevindingen zijn echte schuld;
dit is geen claim dat alles al modulair of foutloos is. Het catalogusonderzoek en
import/toepassingstests toetsen de verhuizing, niet alle productfunctionaliteit.

De bestaande draaiende kern-/werkerimages en databases blijven dezelfde. Een
bronverplaatsing verandert hun gepinde inhoud niet. Lokale startpaden en het
projectdashboard gebruiken de root; een volgende functionele wijziging moet als
één coherent bestaande release worden geïnstalleerd.

## Afmaakvolgorde — geen nieuwe voorbeeldfuncties

1. Maak het **volledige opgeslagen resultaat** bereikbaar voor de oorspronkelijke
   ontvanger, met bestaande resultaatbinding en zonder nieuwe uitvoerroute.
2. Rond **HUE6 / AIDE²** af: onafhankelijke kwaliteitswinst, toepassing en later
   hergebruik van aansturing, code en uitbreidingen via bestaande modules.
3. Rond **HUE5** af: echte ontvangerreactie beïnvloedt later initiatief of stilte.
4. Lever de vier uitkomsten samen in gewoon chatgebruik; technische afronding,
   onafhankelijke review en echte gezinsacceptatie blijven afzonderlijk zichtbaar.

Spraak en nieuwe concurrentenseries blijven geparkeerd. De oorspronkelijke 54
kleine taak-ID’s, statuswaarden en eindvoorwaarden blijven onveranderd; 43 zijn
technisch afgerond. De oorspronkelijke subset blijft 39/48. Er is geen nieuwe
productfunctie opgeleverd door deze reorganisatie.

## Documentatiecontrole en bewijsgrens

490 projecteigen Markdownbronversies zijn geïnventariseerd. 466 zijn volledig
gelezen: 285 eerdere volledige lezingen met dezelfde hash hergebruikt en 181
nieuw volledig gelezen. **24 grote historische documenten zijn niet volledig
gelezen** (1.803.658 bytes); dit wordt niet als volledig onderzoek gepresenteerd.
De actuele leidende documenten zijn daarnaast volledig gelezen. De leesregistratie
bevat elke bronhash, status en bestemming: [leesregister](reading-register-20261013.json).

De complete historie is bewaard. Verdere chronologielezing zonder beslissende vraag
wordt niet als productvoortgang uitgevoerd. Concrete review, checks, beperkingen en
verbruik staan in [het reassessmentrecord](../evidence/reassessment/repository-root-20261013.json).
Ontwikkelcredits zijn niet afzonderlijk meetbaar; onbekende kosten blijven onbekend.
