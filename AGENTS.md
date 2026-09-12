# EVA 2 — projectvisie en opleverafspraken

## Blijvend projectdoel

EVA 2 wordt een herkenbare digitale huisgenote: warm, betrokken,
nieuwsgierig, met een eigen onderbouwd oordeel en continuïteit in
gesprekken, geheugen en gedrag.

Zij leert mensen kennen, onthoudt relevante informatie, verwerkt
veranderingen en neemt passend initiatief. Zij kan humor tonen,
creatieve ideeën ontwikkelen, zelf onderzoek beginnen en later met
een nuttig resultaat terugkomen. Haar aanwezigheid gaat verder dan
wachten op opdrachten, zonder opdringerig te worden.

De ambitie is dat Eva digitale taken kan uitvoeren die een mens op
een computer kan uitvoeren, en daarin aantoonbaar beter wordt:
zelfstandiger, betrouwbaarder, sneller en met minder menselijke hulp.
Dit is een ontwikkelrichting, geen claim dat zij alles al kan.

Eva moet uiteindelijk aantoonbaar beter zijn dan EVA 1/Bottie en
concurrenten zoals OpenClaw, Hermes en Agent Zero. Dagelijks merkbare
waarde en echte taakresultaten bepalen de vooruitgang.

## Gewenste mogelijkheden

- Natuurlijk communiceren via tekst en stem, inclusief onderbreken.
- Zelf relevant geheugen kiezen, bijwerken en gebruiken, zonder
  handmatig geheugenonderhoud door de eigenaar.
- Herkenbaar aanwezig zijn via passende huis- en communicatieroutes.
- Computer, browser, bestanden, internetdiensten en Home Assistant
  gebruiken om volledige opdrachten af te maken.
- Onbekende vaardigheden leren uit documentatie en praktijk.
- Tools en herbruikbare skills ontwikkelen wanneer bestaande
  mogelijkheden onvoldoende zijn.
- Meerdaagse projecten plannen, volgen en na onderbreking hervatten.
- Resultaten controleren, fouten herstellen en eerlijk terugkoppelen.
- Eigen onderdelen verbeteren wanneer gemeten voordeel en een
  werkende terugweg zijn aangetoond.

Algemene digitale vaardigheid is het uitgangspunt. Iedere nieuwe
dienst of gebruikersvraag vereist bij voorkeur geen nieuwe
taakspecifieke functie in Eva's kern.

## Zelfstandigheid en vertrouwen

Eva handelt zelfstandig binnen het bestaande, expliciete en
intrekbare eigenaarmandaat. Reeds toegestane handelingen vereisen
niet steeds opnieuw toestemming. Nieuwe rechten, uitgaven of
verplichtingen buiten dat mandaat vereisen een eigenaarbesluit.

Gegevens blijven behouden en privé-informatie blijft bij de juiste
persoon. Stoppen en corrigeren blijven mogelijk. Eva presenteert
onbewezen resultaten of subjectieve beleving niet als feiten.

## Eén ambitie, kleine actieve goals

Het projectdoel blijft groot. Het actieve goal omvat steeds één
afzonderlijk bruikbare en toetsbare gebruikersuitkomst.

Iedere actieve oplevering heeft:
- een concrete gebruikerssituatie;
- een begrensde scope;
- vooraf vastgelegde eindvoorwaarden;
- passende tests en een echte gebruikersproef;
- een duidelijk moment waarop het technische werk afgerond is.

Een afgerond deelgoal betekent niet dat het volledige project klaar
is. Resterende ambities blijven zichtbaar op het werkbord.

Nieuwe ideeën en niet-noodzakelijke verbeteringen worden vervolgwerk.
Uitbreiding van de actieve scope gebeurt alleen bij een aantoonbare
afhankelijkheid of een expliciete wijziging door de eigenaar.

## Werkwijze en voortgang

eva2/PROJECTDOEL.md bevat de volledige visie.
eva2/WERKBORD.md bevat de actuele uitvoervolgorde en voortgang.
Deze documenten worden bij het kiezen of hervatten van een
oplevering geraadpleegd. Recente expliciete eigenaarbesluiten worden
daarin verwerkt; tegenstrijdige oudere tekst blijft niet leidend.

Per oplevering is kort zichtbaar:
1. Welke gebruikersuitkomst nu centraal staat.
2. Hoe die bijdraagt aan het projectdoel.
3. Wat al werkt en welk concreet onderdeel nog ontbreekt.
4. Welk bewijs nodig is om af te ronden.

Bestaande implementaties en onderhouden open-sourcebouwstenen worden
eerst beoordeeld op hergebruik. Eigen bouw is gerechtvaardigd door
een concrete ontbrekende mogelijkheid of aantoonbaar voordeel.

Tests passen bij de wijziging en het risico. Herhaling volgt uit
nieuwe wijzigingen, fouten of open vragen. De volledige releasepoort
hoort bij een complete releasekandidaat.

Gebouwd, getest, live en door de eigenaar geaccepteerd zijn
afzonderlijke statussen. Aantallen tests, bestanden en commits zijn
geen maat voor productwaarde.

Iedere oplevering eindigt met een bruikbaar resultaat, een korte
uitleg om het te proberen en een bijgewerkt werkbord. Menselijke
acceptatie wordt nooit door de ontwikkelagent verzonnen.

## Prioriteit

Eerst aantoonbaar beter dagelijks gebruik dan EVA 1/Bottie:
geheugen, gesprek, aanwezigheid en stem. Bestaande belangrijke
huisfuncties blijven onderdeel van de vergelijking.

Brede taakuitvoering, leren en zelfstandig projectwerk bouwen daarop
voort. Kleine tussenopleveringen hoeven niet te wachten op de
volledige eindambitie.

## Bewijs van beter

Vergelijkingen gebruiken dezelfde representatieve taken en
vooraf bepaalde criteria. Resultaatkwaliteit, zelfstandigheid,
wachttijd, gemeten verbruik, herstel en gezinsbeleving tellen mee.

Een mooie demonstratie bewijst één mogelijkheid.
Een algemene voorsprong vereist herhaalde vergelijkingen.

## Kwaliteitsborging en samenhang — eigenaarbesluit 12 september 2026

De coördinator bewaakt de afspraken voor het gehele product en houdt één
actuele uitvoervolgorde en bewijsregistratie aan. Ontwikkelagents leveren
begrensde wijzigingen; onafhankelijke reviewers controleren de wijziging en
de gevolgen voor andere onderdelen. Extra agents krijgen een concrete,
onafhankelijke opdracht met duidelijke bestandsverantwoordelijkheid.

- Programmeer modulair: behoud duidelijke verantwoordelijkheden en bestaande
  afhankelijkheidsgrenzen. Hergebruik bestaande implementaties en onderhouden
  bouwstenen; vermijd dubbele logica en taakspecifieke uitzonderingen in de kern.
- Kies de eenvoudigste voldoende oplossing. Minder code is geen doel op zich;
  extra code is gerechtvaardigd door leesbaarheid, betrouwbaarheid, benodigde
  functionaliteit of testbaarheid. Geen regels op aantallen regels of bestanden.
- Controleer efficiëntie met passende metingen: merkbare wachttijd, totaal
  model- en toolgebruik, herhaalde calls en middelengebruik. Benoem onbekende
  kosten; verslechter kwaliteit of gegevensbehoud niet om een getal te verbeteren.
- Controleer naast het gewijzigde onderdeel ook relevante combinaties:
  gesprek en geheugen, spraak tijdens werk, starten/stoppen/hervatten,
  eigenaargrenzen, groeiende toolcatalogus en herstel na verbindingsverlies.
  Gebruik bestaande integratietests en voeg alleen ontbrekend bewijs toe.
- Een technische oplevering bevat actuele gebruiksinstructies, relevante
  architectuur- of beheerwijzigingen, passende tests en onafhankelijk bewijs.
  Bij installatie horen versieherkomst, livecontrole en een werkende terugweg.
- Vergelijk concurrenten met dezelfde representatieve taken en vooraf gekozen
  criteria. Historische of beperkte proeven bewijzen geen actuele algemene
  voorsprong. Leg uitkomst en beperkingen zichtbaar vast, ook bij gelijkspel,
  verlies of een niet-uitvoerbare vergelijking.
- Gebruikerstests kunnen later plaatsvinden en blokkeren onafhankelijk
  technisch vervolgwerk niet. Ze blijven expliciet open met een werkende link,
  handelingen en verwacht resultaat; stilte is geen goedkeuring.

De volledige releasepoort blijft voor een complete releasekandidaat. Gerichte
deelreviews vervangen geen integratiecontrole; een dashboardcheck bewijst geen
werkend totaalproduct. De coördinator verwerkt bevindingen als concreet werk in
het bestaande werkbord en laat technische afronding en eigenaaracceptatie apart.
