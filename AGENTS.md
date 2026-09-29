# EVA 2 — projectvisie en opleverafspraken

## Eigenaaropdracht 29 september 2026 — begrensd uitvoeren, resultaat vóór tussenwerk

Eigenaarcorrectie: de door Codex bedachte60minutengrens is afgewezen. Er geldt
geen willekeurige tijdgrens, maximaal aantal pogingen of automatische pauze om
die reden. De opdracht is beter voorbereiden, minder fouten maken en complete
functies afronden zonder test-/installatie-/herstartlussen. De huidige expliciete
projectpauze blijft gelden; deze instructiewijziging hervat geen productwerk.

1. **Eén complete gebruikersuitkomst.** Werk vanuit één bestaande kleine taak
   naar een concrete handeling die Jaap daarna werkelijk kan uitvoeren. Houd
   het grote onderdeel en zijn resterende eindvoorwaarden zichtbaar. Onderhoud
   krijgt alleen voorrang als het aantoonbaar nodig is voor die uitkomst.
2. **Begrijp de bestaande route vóór wijzigen.** Controleer gericht bronversie,
   draaiende versie en relevante koppelingen van invoer tot resultaat. Gebruik
   bestaande werking, bewijs en besluiten. Geen brede audit of herbouw. Ontbrekende
   acceptatie bewijst geen ontbrekende functie.
3. **Ontwerp de hele wijziging vóór bouwen.** Leg compact vast wat behouden
   blijft, wat precies ontbreekt, welke modules/interfaces geraakt worden en hoe
   de wijziging daadwerkelijk beschikbaar wordt. Beoordeel relevante versies,
   gegevensformaten, afhankelijkheden, verpakte bestanden, runtimegebruikers en
   rechten vooraf. Open technische aannames eerst gericht uitzoeken; niet pas
   tijdens de laatste installatie ontdekken. Geen nieuw omvangrijk plandocument.
4. **Verifieer vroeg op de werkelijke uitvoerroute.** Controleer de noodzakelijke
   aansluitingen met de echte te installeren code en representatieve gegevens
   voordat dure volledige proeven draaien. Een losse test of fixture bewijst
   niet dat het geheel is aangesloten. Gebruik bestaande testvoorzieningen;
   bouw geen extra platform voor een kleine oplevering.
5. **Repareer de oorzaak, niet telkens de hele installatie.** Lokaliseer een fout
   tot de falende overgang, bepaal de oorzaak en pas de kleinste voldoende
   correctie toe. Geen speculatieve wijzigingen of ongewijzigde herhalingen.
   Bewaar bruikbare tussenresultaten; hervat bij de falende stap zodra vaststaat
   dat eerdere resultaten nog geldig zijn. Begin niet opnieuw zonder concrete
   reden. Geen automatische rollback of herinstallatie na een ontwikkelfout.
6. **Bundel controle, review en installatie.** Review de volledige bedoelde
   gebruikersroute en de werkelijk te installeren versie. Hergebruik geldig
   bewijs; controleer wijzigingen en relevante combinaties gericht. Installeer
   een samenhangend bruikbaar resultaat en controleer die installatie. Los
   gevonden fouten gericht daarin op. Geen brede test- of releaseronde per
   tussenwijziging en geen nieuwe toestemmingsvraag binnen bestaand mandaat.
7. **Afronden vóór verbreden.** Maak de gekozen uitkomst af; voeg geen nieuwe
   doelen, architectuurwerk of onafhankelijke reparatiewachtrij toe. Alleen een
   aangetoonde afhankelijkheid rechtvaardigt noodzakelijk extra werk; leg die
   kort bij de bestaande taak vast. Vraag alleen een eigenaarbesluit bij een
   echte product-/scopekeuze of ontbrekend mandaat, niet vanwege een teller of
   normale programmeerfout. Gebruik standaard één uitvoerder; extra uitvoerders
   alleen voor onafhankelijk werk aan dezelfde uitkomst binnen bestaande limiet.
8. **Resultaat eerlijk zichtbaar.** Technisch klaar betekent dat de afgesproken
   functie op de geïnstalleerde versie bruikbaar is, met passende controle en
   concrete gebruiksinstructies. Tests, documenten, commits en exports zijn
   bewijs of tussenwerk; geen extra afgeronde productfunctie. Bewaar eerdere
   acceptaties; fysieke proeven en gezinsacceptatie blijven expliciet apart.
   Werk de kleine taak en het bestaande dashboard bij; verhoog tellers niet door
   opsplitsen of hernoemen. Rapporteer meetbaar verbruik, onbekende credits blijven
   onbekend. Geen verzonnen tijd-/creditquota of garantie op foutloos bouwen.

## Owner instruction 29 September 2026 — individually tracked small outcomes

Use `audit.deliveryPlan.implementationSlices[].remainingTasks` in the existing
workboard for the current remaining work. Each item has its own ID, status, owner
and finish condition. Update the actual small task when starting, blocked or done;
parent status must not invent child completion. Finish one useful outcome with
appropriate focused verification and bundled review/deployment. Do not run a new
full test, rollback or reinstallation cycle for every item. Preserve earlier
acceptance; do not silently grow scope. Final comparison and family acceptance
are already explicit work, not hidden additional build rounds.


## Eigenaarcorrectie 29 september 2026 — doorbouwen in de ontwikkelversie

Eva2 is nog in ontwikkeling. Een mislukte functieproef is op zichzelf geen reden
om de installatie terug te zetten en daarna opnieuw dezelfde installatiecyclus
te doorlopen. Los fouten gericht op in de ontwikkelversie; bundel installatie en
controles rond een bruikbare uitkomst. Tijdelijke uitval van bestaande Eva2-functies is tijdens deze ontwikkeling
toegestaan en is ook geen reden voor rollback: Eva2 wordt nog niet gebruikt.
Behoud gegevens; bescherm tegen onbedoelde echte huisacties. Een afgesloten proef of bijgewerkt dashboard heet geen afgeronde
functie. Houd Git en het bestaande werkbord compact actueel, zonder aparte brede
opleverronde per tussenwijziging.


## Eigenaarcorrectie 28 september 2026 — bestaand resultaat vóór elke bouwopdracht

Voor alle onderdelen geldt: koppel de geplande opdracht aan de bestaande Eva1/Eva2-
implementatie en het beschikbare bewijs. Benoem wat al werkt, welk concreet
gebruikstekort resteert en of bouwen, aansluiten, gericht verbeteren of alleen
beproeven nodig is. Ontbrekende acceptatie of een niet-uitgevoerde proef bewijst geen
ontbrekende functie. Compatibiliteit van Hermes of een andere bouwsteen bewijst geen
voordeel van vervanging. Vervangen vereist een onderbouwd voordeel ten opzichte van
de kleinste voldoende aanpassing; de goedgekeurde ambitie is geen herbouwmandaat.
Gebruik de bestaande inventarisatie en bewijsregistratie; geen nieuwe brede audit.
Deze correctie gaat voor automatische platformvervanging in het bouwplan.

## Actief oplevergoal — eigenaaropdracht 28 september 2026

Voer het complete Eva2-bouwplan uit: `eva2/EVA2_BOUWPLAN.md` en
`eva2/design/blueprint-2026-09-28/`. Geen zakelijke uitwerking. Acht productstappen,
24 begrensde opdrachten; B0 eerst, daarna C1 en de vastgelegde afhankelijkheden.
De planfase is afgerond; de eigenaar heeft het oplevergoal geactiveerd. De nieuwste
besluiten bovenaan `eva2/PROJECTDOEL.md` en de actuele `audit.deliveryPlan` zijn
leidend boven oudere behoudsvoorkeuren, reparatiewachtrijen en ontwerpstops.
Bewaar gegevens en acceptaties, houd één uitvoeringsverantwoordelijke per taak,
werk het bestaande dashboard bij en rond complete bruikbare uitkomsten af.
Een basisproef of document is geen nieuwe productfunctie.

## Eigenaarcorrectie 25 september 2026 — alle HA-functies via beide speakers

Eva moet via beide bestaande speakers alle Home Assistant-apparaten, diensten en
automatiseringen kunnen gebruiken, inclusief automatiseringen maken en aanpassen.
De eerdere bevestiging over lampen/schakelaars was geen exclusieve beperking.
Gebruik de bestaande algemene HA-API; geen nieuwe apparaat-specifieke scripts of
herhaalde toestemmingsvraag voor deze reeds toegestane huisfuncties. De bestaande
geregistreerde huishoudroute blijft leidend. Privégesprekken en privégeheugen
blijven gescheiden en worden niet via de gedeelde speakers ontsloten.

## Gezamenlijke Git-basis — eigenaaropdracht 22 september 2026

De coördinator beheert één actuele integratiebasis op GitHub. Nieuwe bouwtaken
starten vanaf de expliciet gecontroleerde integratiecommit; controleer vóór
dispatch dat eva2/src, tests, locks en actuele eigenaarafspraken aanwezig zijn.
Een werkmap kopiëren is alleen herstel bij een aangetoond ontbrekend checkpoint.
Normaal gebruikt ieder onderdeel een branch vanaf dezelfde basis en levert het
een begrensde commit terug. Alleen de coördinator integreert gedeelde bestanden.
Na passende tests en onafhankelijke controle worden bronwijzigingen naar GitHub
gepubliceerd. Een broncheckpoint is geen runtime-release of eigenaaracceptatie.
Niet-gecommitte werk blijft niet ongemerkt de enige actuele productbron.
Bij onderbroken staging eerst index en werkelijke bestanden bewaren; geen
automatische verwijderingen, reset of force-push. Gebruik voor deze WSL-repository
Git binnen WSL, zodat Windows- en Linux-pad/regelafhandeling niet door elkaar lopen.
Open taken melden basiscommit en bestandsverantwoordelijkheid op het bestaande
werkbord. Voice bezit conversation.py/runtime.py; dashboard levert zijn wiring
als apart integratiefragment. Review blijft gebundeld binnen de afgesproken bezetting.


## Persoonlijke continuïteit en vertrouwelijkheid — eigenaarseis 22 september 2026

Jaap moet later op de dag of weken later kunnen terugkomen op een eerder onderwerp.
Toegestaan persoonlijk geheugen en context horen bij de geverifieerde persoon,
niet uitsluitend bij een apparaat, model of kanaal. Zijn dochter krijgt zijn
privégesprekken niet via vragen, samenvattingen, hints of ongevraagd initiatief.
Alleen expliciet gedeelde informatie mag gezinsgeheugen worden. Controleer wie
spreekt én wie het antwoord kan horen voordat privécontext aan een model wordt
gegeven of een resultaat wordt uitgesproken. Bij onbekende of onzekere spreker
geen privégeheugen. Een naam noemen bewijst geen identiteit.

De huidige lokale browsertoegang is een eigenaarroute die de lokale computer
vertrouwt; tokenvrij is geen persoonherkenning. Een gedeeld apparaat mag daarom
niet als bewijs van Jaaps aanwezigheid worden aangemerkt. De huidige Voice PE-
route kent het huisapparaat, niet automatisch de individuele spreker. Betrouwbare
persoonherkenning en privévoortzetting via gedeelde spraak blijven expliciet open.

## Eigenaarbesluit 22 september 2026 — snel contact, passend model voor het werk

Eva houdt het gesprek gaande met een snel gespreksmodel. Dat begroet natuurlijk,
beantwoordt eenvoudige vragen direct en neemt moeilijkere opdrachten aan.
De bestaande kern registreert aangenomen werk en kiest op de achtergrond het
passende model en de benodigde hulpmiddelen. Onderzoek blokkeert het gesprek niet;
meerdere taken mogen tegelijk lopen. Nieuwe vragen, correcties, voortgang en
expliciet stoppen blijven beschikbaar terwijl werk loopt.

Het snelle model en werkmodellen zijn onderdelen van één Eva: dezelfde identiteit,
bevoegde herinneringen, taakstatus en eigenaargrenzen. Een werkmodel communiceert
zijn resultaat via die gezamenlijke context; geen tweede gesprekspersoon of
onafhankelijke waarheid per kanaal. Eenvoudige antwoorden hoeven niet eerst door
een duur routerings- of onderzoeksmodel. Bevestig alleen werkelijk aangenomen werk.

Deze aanpak geldt voor Telegram, website, Home Assistant en Voice PE. Geen extra
kanaalregexen of standaardzinnen om wachttijd te maskeren. Hergebruik de bestaande
gespreks-, provider- en achtergrondtaakroutes; voeg alleen ontbrekende koppelingen
toe. Selecteer een snel model met gemeten reactietijd én kwaliteit op de bestaande
toegestane providerroute; geen stilzwijgende nieuwe betaalde route of abonnement.
“Liefst instant” is de gebruikerswens, geen al behaalde prestatieclaim.

## Eigenaarbesluit 22 september 2026 — één Eva via alle kanalen

Home Assistant, Voice PE, Telegram en de lokale website zijn ingangen tot
dezelfde Eva. Identiteit, relevant geheugen, oordeel, initiatief, context,
taakuitvoering en stoppen volgen één gezamenlijk beleid in de bestaande kern.
Kanaaladapters verzorgen alleen invoer, uitvoer, toegang en presentatie; zij
krijgen geen eigen persoonlijkheid, opdrachtinterpretatie of reactiescriptjes.
Noodzakelijke tekst-/audioafhandeling mag verschillen, inhoudelijk gedrag niet
alleen vanwege het kanaal. Eén entiteit heft persoons- en huishoudgrenzen niet op.

De gedeelde runtime bestaat al. Concrete nog open verschillen betreffen recente
context, tussenreacties tijdens werk en taakstop. Gebruik de bestaande gezamenlijke
gespreksroute om die verschillen op te lossen. Breid de voice-only ontvangstregex
niet verder uit als oplossing voor andere kanalen. Verplaats gedrag pas wanneer
de gezamenlijke route hetzelfde werkende gedrag aantoonbaar overneemt; verwijder
daarna de vervangen route. Dit autoriseert geen algemene herbouw.

Een proef in het Home Assistant-paneel is geen Voice PE-proef. Kanaalgelijkheid
vereist dezelfde representatieve gesprek-, correctie-, werk- en stopgevallen via
de bedoelde echte ingangen. Ontbrekende firmware-/audiomogelijkheden blijven open;
zij mogen niet als opgelost worden afgevinkt door alleen de laptop te testen.
Voor de lokale testpagina is geen handmatig dashboardtoken nodig. Bestaande
servercredentials en interne eigenaar-/persoonsgrenzen blijven onder beheer.

## Eigenaarbesluit 22 september 2026 — voortbouwen op wat er staat

Behoud en gebruik wat al gemaakt is. Verander bestaande onderdelen alleen bij
een aantoonbaar tekort ten opzichte van Jaaps vastgelegde wensen, of wanneer
zij het oplossen van zo'n tekort concreet belemmeren. Architectuurvoorkeur,
minder code, nieuwere dependencies of een rode documentatiecheck zijn op zichzelf
geen reden voor ombouw. Behoud gegevens, werkende mogelijkheden en acceptaties.

Ga verder met de bestaande open gebruikerswensen. Gebruik bestaand bewijs eerst;
controleer alleen wat voor het concrete tekort nog onbekend is. Noteer per wijziging
welke eigenaarseis niet wordt gehaald, het bewijs daarvoor, de kleinste voldoende
oplossing en wanneer die klaar is. Ontbrekend bewijs is niet automatisch een defect.
Geen nieuwe brede audit, verplichte nulmeting of herbouwronde.

De herbeoordeling van 21 september blijft een onderzoeksbron. Haar aanbeveling
tot gerichte vereenvoudiging is geen uitvoeropdracht. GZ-001 en het gezinsuitje
zijn hoogstens een voorbeeld voor samenhang, geen vervangend productdoel of
verplichte nieuwe oplevering. De daarbij genoemde 14–28 uur is geen actieve
werkraming. De volledige ambitie en bestaande kwaliteitsafspraken blijven gelden.

Eén coördinator en maximaal twee gelijktijdige uitvoerders. Onafhankelijke review
blijft gebundeld en passend bij de concrete wijziging.

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

## Onderhouden opensource en bestaande besluiten

Bij selectie en wijziging van algemene bouwstenen geldt
`eva2/OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`: beoordeel eerst hergebruik,
actief onderhoud, ondersteunde versies, licentie, beveiligingsmeldingen,
compatibiliteit, exacte versiepin en geteste upgrade en terugweg.
Onderhoudsclaims krijgen een bron en controledatum; activiteit alleen is geen
beveiligingsgarantie. Leg de volgende onderhoudscontrole en verantwoordelijke
vast. Gebruik bestaande update- en scanvoorzieningen; onderscheid geplande
voorzieningen van aantoonbaar werkende controles. Eigen bouw motiveert het
concrete gat dat een beproefde bestaande oplossing niet afdekt.

Gebruik `eva2/DOCUMENTATIE_EN_ONDERHOUD.md` als documentatie-ingang. Lees bij
een oplevering de relevante bestaande ontwerpen en besluiten, naast het actuele
projectdoel en werkbord. Oudere planningen en vergelijkingen zijn historische
bronnen wanneer recente eigenaarbesluiten ze vervangen. Registreer conflicten
en herstel verwijzingen in de primaire bron; maak geen tweede werkbord.
Claim niet dat alle documentatie is gelezen wanneer alleen een selectie is
beoordeeld.

## Implementation language — 22 September 2026

Use English for new code identifiers, comments, docstrings, test descriptions and
developer documentation. Preserve existing API/schema contracts and historical
evidence. User-facing Dutch conversation, prompts and owner decisions remain
valid; keep implementation language separate from user language. See
[CONTRIBUTING.md](CONTRIBUTING.md). Do not start a repository-wide translation
or change Eva's conversation language as part of ordinary feature work.

## Complete delivery — owner instruction, 22 September 2026

For authorized changes, successful tests and independent review are followed by
installation, live verification, a working rollback and updated dashboard/Git
status. Do not stop at a tested source candidate or ask for the same deployment
permission again. Preserve existing data and unrelated services; investigate
concrete installation blockers rather than introducing a broad new review cycle.
Technical delivery and the owner's acceptance remain separate. Keep bounded
goals moving while user evaluation is open, and report meaningful outcomes or
blockers rather than seeking approval for every routine step.

## Project dashboard updates — 23 September 2026

Update the existing execution-workboard-v1.json deliveryOverview when starting or switching work, encountering a blocker, and completing a delivery, before reporting that transition. Record the actual current assignment; queued work must not appear actively running. Refresh reviewedAt only after checking the represented progress, never from polling or file modification time. Preserve acceptance and distinguish dashboard changes from Eva functionality. Keep the current summary in WERKBORD.md aligned; do not add another workboard.
