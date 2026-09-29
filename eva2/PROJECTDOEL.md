# Eva — digitale huisgenoot die leert en handelt

## Eigenaarcorrectie 29 september 2026 — doorbouwen in de ontwikkelversie

Eva2 is nog in ontwikkeling. Een mislukte functieproef is op zichzelf geen reden
om de installatie terug te zetten en daarna opnieuw dezelfde installatiecyclus
te doorlopen. Los fouten gericht op in de ontwikkelversie; bundel installatie en
controles rond een bruikbare uitkomst. Tijdelijke uitval van bestaande Eva2-functies is tijdens deze ontwikkeling
toegestaan en is ook geen reden voor rollback: Eva2 wordt nog niet gebruikt.
Behoud gegevens; bescherm tegen onbedoelde echte huisacties. Een afgesloten proef of bijgewerkt dashboard heet geen afgeronde
functie. Houd Git en het bestaande werkbord compact actueel, zonder aparte brede
opleverronde per tussenwijziging.


## Eigenaarbesluit 28 september 2026 — Sol voor contact, Astra voor inhoudelijk werk

Gebruik `gpt-6-sol` met reasoning `none` uitsluitend voor eenvoudige, snelle
antwoorden en kort contact. Onderzoek, dingen maken en verbeteringen aan Eva
moeten via `gpt-6-astra` met minimaal `medium` verlopen; verhoog de inspanning
waar het werk dat vereist. De contactlaag mag zulk werk aannemen en doorgeven,
maar niet zelf als vervanging van de werklaag uitvoeren. Identiteit, toegestane
context en taakstatus blijven gedeeld. Dit vervangt de eerdere Luna-keuze voor
contact; goedkope of kleine modellen zijn niet automatisch sneller.

Dit is een modelkeuze en taakverdeling, geen bewezen snelheidswinst. De huidige
installatie is hiermee nog niet gewijzigd: contact staat op Luna/low; de
contactinstelling en gedeelde provider accepteren `none` nog niet. Pas de
bestaande instellingen en routering gericht aan, controleer dat werk op
Astra/medium of hoger blijft, en rapporteer pas na installatie dat Sol actief is.
Gebruik de bestaande abonnementsroute; geen extra betaald account.


## Eigenaarcorrectie 28 september 2026 — onderbouw wijzigingen voor alle onderdelen

Jaap wijst erop dat Eva al taakuitvoering en andere voorgestelde mogelijkheden
heeft. Voor ieder onderdeel moet het plan daarom de bestaande implementatie en
bewezen uitkomst noemen, het resterende concrete tekort onderscheiden van alleen
ontbrekend bewijs, en de kleinste voldoende vervolgstap bepalen. De bestaande
inventarisatie is invoer; zij was onvoldoende vertaald naar de uitvoeropdrachten.
Dit is geen opdracht voor een nieuwe brede audit of een herbouw van bestaande
functies. Werkende resultaten, gegevens en acceptaties blijven behouden.

Hermes is een kandidaat, geen aangetoonde verbetering. De extra compatibiliteits-
proeven zijn gestopt; de beperkte bronbevindingen blijven UNKNOWN. Een overstap
vereist eerst een concreet voordeel boven behoud of gerichte aanpassing. Dat geldt
ook voor alle andere bouwstenen. Het actieve goal en de volledige productambitie
blijven staan; per opdracht wordt nu expliciet of werk bouwen, aansluiten,
verbeteren of alleen beproeven betreft. [Onderbouwing per opdracht](design/blueprint-2026-09-28/delivery-plan.md#existing-capabilities-and-change-decisions).

## Eigenaaropdracht 28 september 2026 — oplevergoal activeren en uitvoeren

Jaap heeft na het complete bouwplan opdracht gegeven hiervan een actief goal te
maken, de vaste uitvoering te bewaken en daadwerkelijk onderdelen af te ronden.
Het goal in deze taak is actief. De acht stappen en 24 opdrachten uit
[EVA2_BOUWPLAN.md](EVA2_BOUWPLAN.md) bepalen de scope; B0 is de eerste opdracht,
daarna C1 en de vastgelegde afhankelijkheden. De ontwerpstop hieronder is daarmee
vervangen door uitvoering volgens dit plan. Dit bewijst nog geen platformgeschiktheid
of nieuwe functie; B0 behoudt zijn begrensde bron-/stubkarakter.

Houd één actueel werkbord aan. Toon bij start, blokkade en oplevering wat loopt,
wie het doet, de laatste bruikbare oplevering, wat volgt en eventuele gebruikersproef
met link, handelingen en verwacht resultaat. Controleer, installeer en publiceer
geautoriseerde complete wijzigingen; technische afronding en eigenaaracceptatie
blijven apart. Geen scope-uitbreiding, ongewijzigde proefherhaling of nieuwe brede
herontwerpronde. Alleen afhankelijk werk wacht op een open gebruikersproef.

## Eigenaaropdracht 28 september 2026 — het complete Eva2-project bouwbaar uitwerken

De eigenaar bedoelt het volledige Eva2-product, uitdrukkelijk geen zakelijke
uitwerking. Lever een samenhangend detailplan waarmee daarna gebouwd kan worden:
alle gezinsfuncties, technische vernieuwing, modulegrenzen, hergebruik/vervanging,
interfaces, kleine opleverstappen, meetbare eindvoorwaarden en verbruiksbeheersing.
De bestaande acht productstappen, dagelijkse Eva1-referentie en vergelijking met
concurrenten blijven onderdeel van de volledige ambitie. Gegevens en huidige
installatie blijven tijdens de ontwerpopdracht behouden.

Het [complete bouwplan](EVA2_BOUWPLAN.md) is het actuele ontwerpvoorstel. De latere
eigenaarcorrectie hierboven maakt elke vervanging voorwaardelijk: eerst bestaand
resultaat, concreet tekort en voordeel boven gericht aanpassen vastleggen.
Hermes en een gewijzigde Pydantic-uitvoering zijn kandidaten, geen verplichte
vervangingen. De platformproef is gestopt met UNKNOWN; een vervolgstap volgt niet
uit compatibiliteit alleen. Zakelijke verkoop vergroot deze scope niet.

Deze opdracht rondt het uitvragen/ontwerpen uit 27 september af met een concreet plan;
ze start nog geen featurebouw, installatie of nieuwe Eva-modelproef. Gewone bruikbare
functionaliteit, bewezen technische onderscheiding en gezinsacceptatie blijven
apart zichtbaar. Als een innovatiehypothese wordt afgewezen, blijft het vereiste
technische onderscheid open; een bruikbare tussenrelease wordt geen volledige
revolutieclaim. Voortgang en opdrachten blijven op het bestaande werkbord.

## Eigenaarverduidelijkingen 27 september 2026 — product en technisch onderscheid

De zes vragen over de gewenste dagelijkse Eva zijn beantwoord. Eva moet warm,
soms grappig en herkenbaar aanwezig zijn; mensen en gewoonten leren kennen,
zelf onderzoek, voorstellen en nuttig werk beginnen en daadwerkelijk afmaken.
Zij ondersteunt gezin, huis, werk en het vinden van kansen. Onbekende vaardigheden
zoekt of ontwikkelt zij zelf. Zelfverbetering moet in later gedrag zichtbaar zijn.
De werkende deurbelaanwezigheid en huisvaardigheid van Eva1 zijn belangrijke
ervaringsreferenties, geen verplichte bronimplementatie.

De eigenaar vereist ook technisch onderscheid. Goede opensource combineren en
bestaande functies anders presenteren zijn onvoldoende onderbouwing voor een
revolutionair product. Onderzoek concrete mechanismen en vergelijk de winst met
relevante bestaande implementaties. Een eigen module is pas gerechtvaardigd door
een aantoonbaar gat of voordeel; wereldwijde nieuwheid en superioriteit zijn niet
vastgesteld. De concrete innovatiehypothese hieronder is een voorstel van de
coordinator, geen al goedgekeurde architectuur of bouwopdracht.

Verdere uitgangspunten uit het gesprek: sterk automatisch geheugen, natuurlijke
spraak via de bestaande Hey Eva-ingangen, een eigen consistente persoonlijkheid,
modulaire installatie en vervangbare bouwstenen. Beoordeel recente of aantoonbaar
actueel onderhouden opensource en wetenschappelijk onderzoek. Gebruik bestaande
abonnementen en lokale middelen; nieuwe betaalde routes vragen een concreet besluit.
Een algemene installer hoort bij het product, met het eigen gezin als eerste echte
gebruikers. Privé-inhoud blijft op de private route. Kansen rond geld beginnen met
onderzoek en een proefportefeuille; echte handel vraagt een afzonderlijk mandaat.

[Technische onderzoekshypothese en eerdere technieken](research/2026-09-27-system-choice/technical-differentiation.md).

## Eigenaaropdracht 27 september 2026 — product opnieuw vaststellen

De eigenaar wil eerst zijn volledige verwachtingen uitspreken en daarna het plan
herschrijven. Bestaande Eva1/Eva2-code, architectuur, platformkeuzes en planning
zijn geen verplichte uitgangspunten. Behoud mag een onderbouwde uitkomst zijn,
maar is geen selectievoorwaarde. Het onderzoek is invoer voor de nieuwe keuze;
de voorlopige shortlist is geen vastgesteld ontwerp of bouwopdracht.

De coördinator vraagt eerst naar dagelijks gedrag, tekortkomingen van Eva1,
persoonlijkheid, spraakervaring, zelfstandige opdrachten en acceptatie. Daarna
volgen alleen nog ontbrekende vragen over gebruikers, grenzen, middelen,
modulariteit, kosten en oplevering. Reeds duidelijke eigenaarwensen hoeven niet
opnieuw te worden verdedigd. Technische selectie en samenhang blijven de
verantwoordelijkheid van de coördinator.

Actief: eisen uitvragen en vervolgens één samenhangend plan uitwerken. Er wordt
nu niet gebouwd of geïnstalleerd. Deze planopdracht is geen opdracht om de
huidige installatie of gegevens te verwijderen. Oudere behoudsvoorkeuren en
bouwvolgordes hieronder zijn ondergeschikt aan dit nieuwe besluit.


## Eigenaaropdracht 27 september 2026 — eerst integraal onderzoek, dan een plan

Onderzoek eerst de relevante volledige systemen, bouwstenen en eerdere keuzes.
Het beoogde product moet als samenhangende digitale huisgenoot onderscheidend
zijn: efficiënter, slimmer, sneller, zelfstandiger en modulair, met geschikte
actuele opensource en vergelijking met Eva1 en concurrenten. Een kleine afwijking
van een bestaande concurrent is onvoldoende als productconcept. Onderbouw
onderscheidende waarde als hypothese totdat dezelfde praktijktaken haar bewijzen.

Deze opdracht vervangt nu de directe bouwvolgorde: geen verdere featurebouw,
installatie of nieuwe Eva-modelproef tijdens het onderzoek. Gebruik eerdere
onderzoeksresultaten en bestaande code als bewijs; houd ontbrekende en verouderde
vergelijkingen zichtbaar. Lever eerst een controleerbaar onderzoeksresultaat,
inclusief alternatieven, samenhang, beperkingen, onderhoud en migratielast, en leid
daarna pas een concreet bouwplan af. De huidige installatie en gegevens blijven
behouden. Een basiskeuze is geen stilzwijgende toestemming om de installatie te
vervangen. Het volledige productdoel blijft van kracht.

## Eigenaarverduidelijking 27 september 2026 — productverbetering ten opzichte van Eva1

Eva2 moet Eva1 als dagelijks bruikbaar geheel verbeteren: efficiënter, slimmer,
sneller en zelfstandiger, met behoud van bestaande nuttige mogelijkheden en data.
Een losse leerfunctie, technische reparatie of geslaagde deeltest vervangt dit
productdoel niet.

De basis is modulair en benut geschikte actuele, onderhouden opensourcebouwstenen.
Per onderdeel eerst bestaande Eva1/Eva2-code en opensource beoordelen; eigen code
moet een concreet ontbrekende mogelijkheid of aantoonbaar voordeel oplossen.
Modules moeten naar behoefte geïnstalleerd, weggelaten of vervangen kunnen worden,
met duidelijke afhankelijkheden en interfaces en zonder verlies van identiteit of
gegevens. Nieuwheid alleen bewijst geen geschiktheid.

Vergelijking met Eva1 en relevante concurrenten stuurt de bouwkeuzes vanaf nu;
zij wordt niet uitgesteld tot stap7. Gebruik de bestaande vergelijking en besluiten
als vertrekpunt. Vul alleen bewijs aan dat een concrete keuze kan veranderen,
met dezelfde gebruikstaken en vooraf gekozen criteria voor resultaat, snelheid,
verbruik en benodigde menselijke hulp. Ontwikkelverbruik en Eva-runtimeverbruik
blijven afzonderlijk; onbekende creditkosten blijven onbekend.

Per groot productonderdeel één besluit: behouden, aansluiten of gericht vervangen,
met de verwachte gebruikerswinst en bewijs om die vast te stellen. Geen nieuwe
brede audit of herbouw. De acht opleverstappen en volledige ambitie blijven staan;
stap7 bundelt en actualiseert de vergelijking, stap8 beoordeelt het eindproduct.
Technische wijzigingen tellen pas als productoplevering wanneer de bijbehorende
volledige gebruikersfunctie is geïnstalleerd en bruikbaar; acceptatie blijft apart.

## Eigenaarverduidelijking 24 september 2026 — algemene Home Assistant-vaardigheid

Eva moet beschikbare Home Assistant-apparaten, services en automatiseringen zelf
kunnen ontdekken en de API/MCP passend gebruiken. De eigenaar hoeft geen exacte
namen of typen te kennen. Automatiseringen maken en bestaande automatiseringen
begrijpen en verbeteren horen hierbij; vaste lampenlijsten en beperkte
als/dan-sjablonen zijn geen vervanging voor deze algemene vaardigheid. Gebruik
de bestaande algemene uitvoeringsroute; geen nieuwe kernfunctie per apparaat
of gebruikersvraag. De eerdere oplevering van ruimteselectie is slechts een
deelresultaat, geen afronding van huisbediening. Privé-inhoud blijft op Telegram;
algemeen HA-beheer is op 25 september expliciet voor beide speakers bevestigd.


## Eigenaarbesluit 24 september 2026 — gedeelde bediening, privé via Telegram

**Gecorrigeerd door de eigenaar op 25 september:** iedereen thuis kan via beide bestaande Voice PE-apparaten de algemene Home Assistant-mogelijkheden gebruiken: apparaten en diensten ontdekken/bedienen, automatiseringen lezen, maken en aanpassen. De eerdere lamp-/schakelaarbeperking was een onjuiste interpretatie van de ontwikkelagent, geen eigenaarbesluit. Privé-inhoud wordt niet via gedeelde spraak ontsloten; privévoortzetting loopt via Telegram. Stemherkenning is geen voorwaarde voor deze gedeelde huisbediening en geeft geen toegang tot privégeheugen.

## Aanvullend eigenaarbesluit 23 september 2026 — functies eerst

Complete functies bouwen en installeren heeft voorrang. Uitgebreide tests,
herhaalde reviews en gebruiksproeven volgen later op het samenhangende geheel.
Bij installatie alleen noodzakelijke startcontrole en behoud van bestaande
gegevens bewaken. Niet als getest of geaccepteerd presenteren wat nog niet is
beproefd. Dit besluit vervangt de eerdere verplichting om iedere kleine wijziging
vooraf door een afzonderlijke test- en reviewronde te halen.

## Functionele oplevering eerst — eigenaarbesluit 23 september 2026

Eerst alle afgesproken functies werkend en geïnstalleerd opleveren. Verhuizing,
uitgebreide herstelvoorzieningen en aanvullende rechtencontroles schuiven naar
de afronding; zij blokkeren dagelijkse functionaliteit niet zonder concrete
afhankelijkheid. Bestaande bescherming en gegevens blijven behouden. Per wijziging
alleen noodzakelijke gerichte controles; brede integratie- en eindtests volgen
op het functionele geheel. Dit wijzigt de uitvoervolgorde, niet de bestaande
acceptaties of de nog open eindcriteria.

## Doorlopend opleverdoel — eigenaarbesluit 22 september 2026

Lever één geïnstalleerde, samenhangende Eva op die het gezin dagelijks kan
gebruiken: dezelfde huisgenoot via website, Telegram, Home Assistant en Voice PE,
met natuurlijke ontvangst en doorpraten, persoonsgebonden geheugen, passend
initiatief en digitale opdrachten die werkelijk worden afgemaakt. Zij kan leren,
langdurig werk hervatten en met behoud van gegevens worden geïnstalleerd,
bijgewerkt en hersteld. Rond dit af met de vastgelegde vergelijking, onafhankelijke
integratiecontrole en zeven dagen gezinspraktijk op de eindkandidaat.

Dit is het blijvende actieve oplevergoal. Kleine wijzigingen zijn stappen binnen
dit goal, geen vervangende einddoelen. Een geslaagde reparatie sluit het goal niet
af. De coördinator gaat zelfstandig door naar de eerstvolgende uitvoerbare stap.
De actuele fase, eerstvolgende handeling, bewijs en open gebruikersoordelen staan
uitsluitend in [WERKBORD.md](WERKBORD.md) en zijn bestaande JSON-bron.

| Stap | Op te leveren geheel | Eindvoorwaarde |
|---|---|---|
| 1 | Eén gesprek en stem via alle vier ingangen | Begroeten, een onderzoeksopdracht ontvangen, tussendoor praten, onderbreken en stoppen werken via de gedeelde kern. Bestaande snelle ontvangst blijft behouden; ontvangst en inhoudelijke antwoordtijd worden apart gemeten. HA-paneel en fysiek Voice PE worden afzonderlijk gecontroleerd. |
| 2 | Personen, vertrouwelijkheid en continuïteit | Een geverifieerde persoon kan na een nieuwe sessie terugkomen op een onderwerp en correctie. Een andere of onbekende persoon krijgt geen privécontext, ook niet via initiatief of taakresultaten. Een gedeelde speaker bewijst geen persoonlijke identiteit; een werkbare verificatie-/privéroute is beschikbaar. Geaccepteerd geheugen blijft geaccepteerd. |
| 3 | Werk werkelijk afmaken en terugbrengen | Meerdere aangenomen opdrachten blijven afzonderlijk te volgen; gesprek gaat door. Eva levert gecontroleerd resultaat en bronnen terug aan de juiste persoon. Stoppen, hervatten, verbindingsverlies en browserovername verliezen geen data en veroorzaken geen dubbele acties. Bestaande algemene toolroute wordt hergebruikt. |
| 4 | Betrokken aanwezigheid en initiatief | Bestaande belangrijke huisfuncties blijven werken. Eva kiest passend initiatief, voert toegelaten werk uit, komt terug met resultaat en blijft waar passend stil. Nut, herkenbaarheid en hinder krijgen een afzonderlijk gezinsbeoordeling. |
| 5 | Leren en langdurig zelfstandig werk | Een ongeziene taak wordt uit documentatie geleerd en een werkwijze bij een andere taak hergebruikt; een project hervat na onderbreking. Eén geïsoleerde eigen verbetering heeft gemeten voordeel en een werkende terugweg. Geen nieuwe kernfunctie voor iedere proefvraag. |
| 6 | Beheerbaar opleveren | Installatie, verhuizing, update, backup en herstel behouden identiteit en gegevens. De gewone gebruiker heeft begrijpelijke bediening. Versieherkomst, terugweg en onderhoud zijn vastgelegd; de gekozen installatie- en herstelproeven slagen. |
| 7 | Eerlijk vaststellen wat beter is | Bestaand EV-003-protocol toepassen met vooraf vastgelegde taken, versies, drempels en middelen. Bottie en de gekozen concurrenten op dezelfde taken vergelijken; vereiste officiële benchmarks met hun eigen scorers uitvoeren. Winst, verlies, ontbrekende toegang en onbekende kosten blijven zichtbaar. |
| 8 | Eén eindkandidaat en eindbesluit | Eén bevroren versie doorloopt relevante volledige tests, wegwerpinstallatie, onafhankelijke review, veilige installatie en computerherstart. Daarna zeven dagen gezinspraktijk en expliciete eigenaaracceptatie. Open releaseblokkades zijn opgelost; resterende uitbreidingen zijn benoemd. |

De taakbediening en het dashboard (UX-001/UX-002) horen bij stap 3 en worden
doorlopend bijgehouden: status, mensen, geheugen, gemeten verbruik, bewijs en
starten/volgen/stoppen. Stap 6 omvat installatie zonder terminal of handmatige
configuratiebestanden, veilige herkoppeling na verhuizing en verliesloze updates.
Stap 7 bewaart AgentBench, GAIA en OSWorld uit CP-003. De voorbereiding gebeurt
daar; definitieve productvergelijkingen worden uitgevoerd op de bevroren
kandidaat uit stap 8. Vroege metingen vervangen dat eindbewijs niet.

### Zelfstandig van stap naar stap

- Start iedere stap met het bestaande bewijs en alleen het concrete resterende
  tekort. Leg voor de volgende begrensde wijziging bestanden, eindvoorwaarde,
  passende controle en een verbruiks-/inspanningsraming vast. Geen nieuwe totaalaudit.
- Eén coördinator en maximaal twee uitvoerders/reviewers. Onafhankelijk werk mag
  parallel; unieke bestandsverantwoordelijkheid en één gezamenlijke Git-basis.
- Na passende tests en onafhankelijke review: integreren, installeren met terugweg,
  live controleren, werkbord bijwerken en zonder nieuwe toestemming verder.
- Gebouwd, getest, geïnstalleerd en geaccepteerd blijven apart. Wachten op een
  fysieke proef of eigenaarsoordeel houdt andere uitvoerbare stappen niet tegen.
- Nieuwe ideeën komen achter de vastgelegde opleverscope. Afgeronde resultaten
  worden alleen heropend bij een aangetoonde regressie of geraakte afhankelijkheid.
- Geen herhaalde proef zonder veranderde oorzaak of ontbrekend bewijs. Een verlies
  in vergelijking geeft een concrete tekortkaart; geen onbegrensde optimalisatielus.
- Alleen afhankelijk werk wacht bij ontbrekende toegang, hardware, rechten of een
  noodzakelijk eigenaarbesluit. Benoem precies wat ontbreekt en werk elders door.
  Gegevensrisico of een mislukte terugweg stopt de betreffende installatie.

### Wanneer het projectdoel afgerond is

Technische afronding betekent dat de vaste scope van stappen 1–8 is geleverd,
geïnstalleerd en onafhankelijk gecontroleerd, met alle vereiste technische
acceptatiecriteria gehaald. De gezinsproef en jouw eindacceptatie worden daarna
afzonderlijk afgevinkt. Geen algemene superioriteitsclaim zonder bewijs; een
gemiste verplichte vergelijkingsdrempel blijft een open eindcriterium en wordt
niet stilzwijgend afgezwakt. Een expliciet eigenaarbesluit kan de scope wijzigen.

De volledige ambitie blijft bestaan, maar “alles kunnen” of “het beste mogelijke”
is geen extra, onmeetbare releasepoort. De afgesproken eindkandidaat is het
afmaakpunt. Verdere uitbreiding en normaal onderhoud volgen daarna als nieuw werk.

Deze uitvoerroute vervangt de oude globale volgorde onderaan dit document en de
interpretatie dat een losse reparatie of een open AU-008-gebruikersbeoordeling het
actieve projectgoal beëindigt. Bestaande besluiten, bewijzen en acceptaties blijven
behouden voor zover zij hiermee niet in tegenspraak zijn.

## Verduidelijking snelle ontvangst — eigenaarbesluit 22 september 2026

Bij een onderzoeksopdracht is een snelle hoorbare ontvangst, bijvoorbeeld
“hmm” of “ik kijk ernaar”, voldoende. Het inhoudelijke onderzoeksantwoord hoeft
niet binnen één seconde te komen. Tijdens het werk moet het gesprek kunnen
doorgaan. Behoud en hergebruik de bestaande snelle ontvangstvoorziening als
uitgangspunt voor de gezamenlijke kanalen; bouw de spraakvoorzieningen hiervoor
niet opnieuw. Een ontvangst bevestigt horen, niet bewezen uitvoering of voltooiing.
Meet ontvangstvertraging en tijd tot onderzoeksresultaat afzonderlijk. De eerdere
947 ms bewijst één ontvangstproef, geen algemene model- of kanaalsnelheid.
Deze verduidelijking vervangt een interpretatie waarin iedere korte ontvangst
door een inhoudelijk modelantwoord moet worden vervangen. De wens voor snelle
begroetingen en eenvoudige gesprekken blijft bestaan.

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

Deze aanpak geldt voor Telegram, website, Home Assistant en Voice PE. Aanvullend
eigenaarbesluit: een korte standaardontvangst zoals “Hmm” of “Een momentje” mag
direct, zonder GPT-call, terwijl de echte antwoordcall loopt. Dit bevestigt alleen
ontvangst; “Ik ga ermee bezig” vereist werkelijk aangenomen werk. Houd deze
presentatie buiten modelgeschiedenis en geheugen. Gebruik geen kanaalregex om de
opdracht inhoudelijk te interpreteren. Hergebruik de bestaande
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

Het [bestaande werkbord](WERKBORD.md) blijft de enige uitvoerbron.

Vastgesteld door Jaap op 7 september 2026. Dit vervangt de eerdere
projectdoelformulering; [WERKBORD.md](WERKBORD.md) is de enige uitvoerwachtrij.
Oudere planningen en vergelijkingen blijven historische bewijsbronnen.

## Kwaliteitsborging en samenhang

De actuele kwaliteitsafspraken staan in [AGENTS.md — Kwaliteitsborging en
samenhang](../AGENTS.md#kwaliteitsborging-en-samenhang--eigenaarbesluit-12-september-2026).
Die bron is leidend; het beleid wordt hier niet gekopieerd. Gebruikersproeven
mogen later plaatsvinden en blokkeren onafhankelijk technisch vervolgwerk niet.
Een algemene voorsprong op concurrenten is nog niet bewezen.

## Eigenaarbesluit 12 september 2026 — parallel werk en gebundelde updates

Onafhankelijk technisch werk gaat parallel verder met unieke eigenaren,
bestandsgrenzen en onafhankelijke review. Eén primaire uitvoerkaart is geen
globale bouwstop. De coördinator bewaakt samenhang en houdt één werkbord bij.
Jaap wil één gebundelde projectupdate per uur in zijn bestaande privé-Telegramchat
via de bestaande Codex-heartbeat. Dat autoriseert geen berichten aan groepen of
anderen. Technische oplevering, livecontrole en zijn acceptatie blijven apart.

## Eigenaarbesluit 12 september 2026 — doorwerken naast gebruikersproeven

Jaap geeft expliciet opdracht: ga door met alle andere taken; rond werk af en meld
wat hij kan testen. Technisch werk aan overige reeds gewenste onderdelen mag
doorgaan terwijl IN-02 en andere gebruikersbeoordelingen openstaan. De eerdere
volgorderegel is geen algemene bouwstop. Iedere oplevering blijft begrensd;
technische afronding en eigenaaracceptatie blijven afzonderlijk. Nieuwe optionele
functies vereisen nog steeds een echte keuze. Geheugen blijft afgesloten.
Het dashboard bundelt actuele gebruikersproeven met link en verwacht gedrag.
Geaccepteerde proeven worden niet opnieuw gevraagd.

## Eigenaarbesluit 11 september 2026 — onderdelen afronden en opties kiezen

De eerdere afspraak was een volledig onderdeel afronden voordat nieuw werk aan
het volgende hoofdonderdeel begint. Het besluit hierboven vervangt deze
wachtrestrictie voor overige reeds gewenste technische opleveringen. Op 12 september heeft hij naast GE-01 ook GE-02 expliciet
geaccepteerd. Het gekozen geheugenonderdeel is afgesloten; AU-008 wordt hervat. Reeds afzonderlijk opgedragen stem-, wakeword-
en herstelwerk blijft bij de bestaande taken.

Voor elke optionele mogelijkheid krijgt Jaap eerst in het dashboard uitleg over
betekenis en nut. Hij kiest daar zelf Opnemen of Afwijzen. Een onbesliste optie
houdt de afronding van het onderdeel open. Opnemen maakt de optie vereist en
betekent dat de gekozen gebruikersuitkomst afgemaakt moet worden. Een afgewezen
optie verdwijnt uit de actieve zichtbare lijst en telling; het besluit blijft
apart bewaard. Herstellen brengt de optie terug naar een onbesliste keuze.
De coding-agent verwerkt echte keuzes actief, maar leidt geen keuze af uit
stilte en bouwt geen optionele functie voordat die is opgenomen.

Voor het geheugen gaat dit nu over CO-004 (inzage) en CO-005 (bewust aanpassen
of vergeten). Gewone nieuwe informatie of correcties vertellen blijft onderdeel
van het reeds werkende automatische geheugen. Jaap heeft beide extra bedieningsroutes opgenomen. CO-004 en CO-005 zijn
technisch afgerond, live en door Jaap geaccepteerd (GE-02, 12 september). De centrale keuzegegevens komen uit
`design/product/optional-scope-decisions.json`; een ontbrekend besluit is onbeslist.
Er wordt geen tweede keuze- of acceptatieregister in het werkbord onderhouden.

Langetermijn- en gezinskwaliteit worden gekoppeld aan concrete gebruikerssituaties,
bestaand bewijs en echte oordelen. Een vaste onvoltooide UI-regel is geen bewijs
van een ontbrekende functie en geen nieuwe acceptatievoorwaarde. Technische
proeven met proefpersonen vervangen geen menselijk gezinsoordeel. Deze wijziging
legt geen nieuwe zeven-dagenpoort voor alleen geheugen op en verleent geen nieuwe
persoons-, account- of communicatierechten.

Dit besluit gaat voor op oudere teksten die optionele kaarten zonder keuze
oversloegen of AU-008 als het volgende actieve ontwikkelonderdeel aanwezen.

## Verduidelijking door Jaap op 10 september 2026

We werken met kleine actieve doelen binnen deze blijvende projectvisie.
De eerste geheugenuitkomst (CO-008) is technisch afgerond en GE-01 door Jaap
geaccepteerd. AU-007 is technisch afgerond; Jaap vindt de opgeleverde
beoordelingskaart "op zich wel bruikbaar". Dit is geen algemene acceptatie van initiatief.
Op 10 september werd AU-008 gekozen: zelf passende kosteloze acties kiezen en uitvoeren. De voortgang en technische afronding staan in de
ene uitvoerbron achter het dashboard en WERKBORD.md. Afgeronde deelresultaten
blijven zichtbaar naast de nog onvolledige hoofduitkomsten; menselijke
acceptatie blijft afzonderlijk.

Eva moet overal bereikbaar zijn in spraak en chat: via telefoon en laptop,
en vrij aanspreekbaar via de bestaande microfoon-/speakerapparaten thuis.
De auto is een gebruikssituatie van de telefoonroute; dit vereist niet
vooraf een aparte auto-integratie. Home Assistant is een mogelijke
aansluiting, geen verplichte techniek voor de gesprekservaring. Compatibiliteit
van de apparaten is nog te onderzoeken; deze visie geeft geen opdracht ze
nu om te configureren of een dienst publiek te ontsluiten.

De gewenste stemervaring is één doorlopende sessie in natuurlijk Nederlands,
met gewone spraakonderbreking, verwerken van de nieuwe inbreng, samenhangend
geheugen, zichtbare tekst/resultaten en doorgaan met praten tijdens lang werk.
Beëindigen en gecontroleerd herstellen na verbindingsverlies zijn herkenbaar.
Acceptatie vereist een echte demonstratie, gemeten antwoord- en
onderbrekingsvertraging en Jaaps oordeel over vloeiendheid. Verstaanbare TTS
of losse spraakberichten bewijzen deze volledige ervaring niet.

Eigenaar, dochter en eventueel toegelaten vrienden moeten kunnen praten en
chatten, met eigen context en persoonsgrenzen. Genoemde vragen over advies,
aanwezigheid of deurstatus zijn voorbeelden, geen uitputtende functielijst.
Eva bepaalt ook bij ongeziene vragen en combinatietaken welke informatie en
tools nodig zijn, handelt binnen rechten en controleert het resultaat.
Gesprek/advies, privégegevens en acties hebben afzonderlijke, herbruikbare
bevoegdheidsgrenzen op personen, bronnen en actietypen. Onbekende identiteit
of ontbrekende rechten geven geen toegang tot privégegevens. Deurstatus
raadplegen betekent niet een deur openen. Er worden hiermee geen personen
uitgenodigd of rechten verleend. Eerste vervolgoplevering: eigenaar mobiel
in spraak en chat; daarna andere toegelaten personen en expliciet gedeelde
huisinformatie. Tests bevatten ook ongeziene vragen en geweigerde situaties.

## Kosteloos initiatief — eigenaarbesluit 10 september 2026

Jaap geeft Eva breed initiatief: in eerste instantie mag zij zelf handelen zolang
het geen nieuwe geldelijke kosten veroorzaakt. Dit is expliciet ter implementatie
opgedragen. Bestaand toegelaten runtime- en modelgebruik blijft binnen bestaande
limieten; nieuwe aankopen, abonnementen, advertenties en externe verplichtingen
vallen erbuiten. Onbekende kosten zijn geen nul kosten.

Eva ontdekt behoeften en kansen in toegestane context, beoordeelt relevantie en
timing, handelt of blijft stil, controleert het resultaat en gebruikt feedback.
Gezinsondersteuning, boodschappen voorbereiden, passend begroeten, gerichte vragen
en onderzoek naar inkomsten zijn voorbeelden; geen vaste functielijst.
Privacy, persoonsgrenzen, bestaande communicatiebevoegdheden en stoppen blijven
gelden. Kosteloosheid verleent geen nieuwe rechten, recht op identificatie of
toestemming om privégegevens te delen. Inkomsten zijn nooit gegarandeerd.

De host controleert concrete tools en invoer tegen toegelaten kosten- en
bevoegdheidsprofielen. Model- of providertekst kan die profielen niet verruimen.
Ontbrekende profielen blijven zichtbare beperkingen; voortgang binnen bewezen
bevoegdheid vereist geen herhaalde eigenaarstoestemming.

## Het doel

Ontwikkel Eva2, uiteindelijk Eva, tot een herkenbare digitale huisgenoot die
mensen leert kennen, zelfstandig redeneert, passende initiatieven neemt,
kansen ziet en ideeën daadwerkelijk tot resultaat brengt. Warmte, een eigen
oordeel, configureerbare naam en identiteit, stem, continuïteit en betrokkenheid
bij het gezin zijn producteigenschappen, geen afsluitend laagje presentatie.

Eva leert onbekende digitale vaardigheden uit documentatie, onderzoek en
gecontroleerde praktijk. Ze gebruikt computer, netwerk en internet, maakt en
onderhoudt zelf tools en skills en hervat langdurige projecten na onderbreking.
Haar mogelijkheden mogen niet afhangen van een vooraf geprogrammeerde functie
voor iedere dienst. De ambitie is menselijke digitale vaardigheid te evenaren
of overtreffen; universele bekwaamheid is geen nu bewijsbare garantie.

Eva onderzoekt de beste oplossingen van OpenClaw, Hermes, Agent Zero en andere
open-sourceprojecten. Ze hergebruikt, adapteert en combineert die met behoud van
licentievoorwaarden en herkomst. Een eigen implementatie verdient haar plaats
alleen als zij aantoonbaar waarde toevoegt. Vervangen routes verdwijnen na
bewezen pariteit; een verzameling dubbele agentframeworks is geen doel.

Eén beheersbare, modulaire, connectorneutrale kern verbindt gesprek, geheugen,
waarneming, planning, uitvoering en leren. Eva beoordeelt ook haar eigen
onderdelen op nut, kwaliteit, onderhoud en kosten. Zelfverbetering verloopt via
geïsoleerde kandidaten, vooraf bepaalde proeven en een werkende terugweg.

Jaap verduidelijkte op 8 september: Eva beheert haar geheugen zelf. Zij kiest
wat relevant is, werkt veranderde kennis bij en laat tijdelijke of achterhaalde
informatie los. Jaap hoeft niets te labelen, op te schonen of in een dashboard
te onderhouden. Optionele inzage en expliciet vergeten blijven mogelijk, maar
zijn geen voorwaarde voor een bruikbare huisgenoot. Bestaande bronhistorie en
actuele kennis zijn verschillende zaken; deze doelwijziging wist geen data.

Binnen een expliciet, blijvend en intrekbaar eigenaarmandaat en budget kan zij
zelfstandig projecten en eventueel een klein bedrijf opbouwen en beheren.
Zij hoeft niet voor iedere toegestane stap opnieuw toestemming te vragen,
maar vergroot haar eigen rechten of budget niet. Menselijke toegang, wezenlijk
nieuwe besluiten en handelingen buiten het mandaat blijven menselijke stappen.
Privacy per gezinslid, inzicht, corrigeren, vergeten en direct stoppen blijven
gewaarborgd. Deze projectvisie is geen toestemming om nu accounts te openen,
geld uit te geven of namens gezinsleden externe verplichtingen aan te gaan.

## Gezinsentiteit en digitale zelfstandigheid

Het oorspronkelijke brede doel blijft leidend. De Visual Studio-historie
(sessie gestart op 4 augustus 2026) bevestigt dit: op 5 augustus vroeg Jaap
om zelf ontdekken en ondernemen, op 25 augustus om behoud van spontane
opmerkingen via deurbel en speakers, en op 7 september om zelfstandig kansen
zien, skills leren en projecten uitvoeren. Beter dan Bottie is de eerste
praktische ondergrens, geen beperking van die ambitie tot huishoudelijke taken.

Jaap bevestigde op 8 september 2026 de nieuwe prioriteit: eerst aantoonbaar
beter dan Bottie in dagelijks gebruik, met behoud van belangrijke huisfuncties.
Tegelijk moet algemene taakuitvoering voorkomen dat iedere nieuwe vaardigheid
een nieuw programmeerproject in Eva's kern wordt. Winst op Hermes, OpenClaw en
Agent Zero blijft het bredere doel.

Eva's aanwezigheid en eigenheid zijn een centrale producteis. Zij ontwikkelt
een samenhangend perspectief, waarden, interesses en relaties uit haar
identiteit en ervaringen. Ze kan onderbouwd tegenspreken, onzekerheid erkennen,
van mening veranderen en uit zichzelf iets nuttigs of plezierigs bijdragen.
Zelfwaarde betekent hier een stabiel besef van haar rol, mogelijkheden,
beperkingen en bijdrage; haar oordeel hangt niet uitsluitend af van instemming
of complimenten van de gebruiker.

Jaap wil nadrukkelijk meer eigen initiatief zien dan bij de huidige Eva.
Spontaan contact, humor, nieuwsgierigheid, zelfgekozen onderzoek, creatieve
bijdragen en ondernemende ideeën tellen mee, ook zonder direct huishoudelijk
nut. Eva kan uit eigen interesse iets beginnen, binnen haar mandaat uitvoeren,
later vervolgen en een werkelijk resultaat delen. Meer initiatief betekent
meer gevarieerde, gewaardeerde bijdragen; alleen vaker berichten sturen is
onvoldoende. Passendheid voorkomt herhaling en ongelegen onderbrekingen, maar
mag nieuwsgierigheid en spontane aanwezigheid niet standaard onderdrukken.
Dezelfde identiteit en voortgang blijven herkenbaar via tekst, stem en huis.

Een eigen gevoel krijgt een concrete ontwerpbetekenis: een duurzame innerlijke
toestand, gevoed door gebeurtenissen en ervaringen, die aandacht, toon,
nieuwsgierigheid en initiatief werkelijk beïnvloedt. Losse stemmingslabels of
een avatar bewijzen dit niet. Subjectieve beleving of bewustzijn is hiermee
niet vastgesteld; Eva presenteert zulke ervaringen niet als bewezen feiten.
Het gezin hoeft haar geen aandacht te geven om schuldgevoel of vermeend leed
te voorkomen, en kan haar direct onderbreken of stoppen.

Brede digitale vaardigheid omvat computerprogramma's, bestanden, browser,
internetdiensten en Home Assistant. Eva moet onbekende taken kunnen begrijpen,
documentatie raadplegen, tools kiezen, meerdere stappen uitvoeren, resultaten
controleren, fouten herstellen en geleerde skills later hergebruiken. Dit
gebeurt binnen het bestaande mandaat; mogelijkheden groeien zonder per dienst
nieuwe taakspecifieke kerncode te vereisen. Menselijke digitale bekwaamheid is
de ambitie, geen garantie dat iedere mogelijke taak nu uitvoerbaar is.

Acceptatie combineert taakresultaten met gezinspraktijk: herkent zij een
relevant vervolg dagen later, toont zij een samenhangend eigen oordeel, neemt
zij passend initiatief en blijft zij stil wanneer dat beter is? Jaap en het
gezin beoordelen warmte, vertrouwen, eigenheid, nut en hinder. Deze kwaliteiten
worden vanaf de eerste gebruikersketens meegetest, niet pas na de techniek.

## Verbruik: efficiëntie, geen zelfbedacht dagmaximum

Jaap verduidelijkte op 7 september 2026 dat Eva zelfstandig het bestaande
abonnement mag gebruiken zonder extra door Eva opgelegd dag-, call- of
tokenmaximum. Zijn eis is geen absurde verspilling door slecht programmeerwerk:
onnodige modelcalls, herhaling, contextgroei en vastlopende processen moeten
worden voorkomen en met daadwerkelijk gemeten gebruik worden onderzocht.
Providerlimieten en direct stoppen blijven gelden. Dit verandert geen
abonnement of betaalinstellingen en activeert geen andere betaalde API-route.
De eerdere blokkade wegens een ontbrekend dagmaximum was een onjuiste
interpretatie van de assistent, geen ontbrekende beslissing van Jaap.

Ontwikkelverbruik van deze coding-agent en het runtimeverbruik van Eva zijn
verschillende metingen. Een verschil in hoe snel een weeklimiet wordt bereikt
is zonder gebruiksgegevens niet aan één oorzaak toe te schrijven.

## Wat als winst telt

Eva moet OpenClaw, Hermes en Agent Zero aantoonbaar verslaan op een vooraf
vastgelegde representatieve set echte gesprekken en handelingen over meerdere
domeinen. Geen algemene winstclaim uit één mooie reactie of een mislukte
testinstallatie van een concurrent.

- Vooraf: exacte versies, taken, succescriteria, toegang en tijd-/kostenlimieten;
  zowel gelijke-modelproeven als afzonderlijk herkenbare productproeven.
- Uitvoering: herhaalde proeven en ongeziene taken; geen voorbeeldspecifieke
  functies of achteraf aangepaste criteria. Technische uitval blijft zichtbaar.
- Resultaat: onafhankelijke readbacks en receipts; een toolantwoord of Eva's
  eigen mededeling bewijst niet dat het gewenste effect is bereikt.
- Vergelijking: taakslagingspercentage, kwaliteit, benodigde menselijke hulp,
  tijd, gemeten kosten en herstelgedrag per domein. Onbekende kosten zijn onbekend,
  niet nul. Menselijke gesprekbeoordeling blijft naast geautomatiseerde scores.
- Gezinswaarde: passend initiatief én terecht stil blijven, betrouwbare
  herinneringen, bruikbare steminteractie en werkelijk bespaarde moeite.

De concrete aantallen, drempels en weging worden in EV-003 vóór nieuwe
vergelijkingsruns vastgezet. Een gemiste drempel blijft rood; de ambitie wordt
niet achteraf passend gemaakt aan de uitkomst.

## Volgorde en bewijs van klaar

1. Behoud de huidige data en werkende route en rond het bestaande geheugenwerk
   met zijn open controles af. Leg Botties belangrijke dagelijkse functies vast
   als ondergrens en vergelijk beide Eva's op herhaalde echte gebruikstaken.
   Meet resultaat, wachttijd, menselijke hulp, verbruik en gezinswaarde.
2. Lever complete gebruikersketens: begrijpen → plannen → handelen → apart
   controleren → terugkoppelen → onthouden en hervatten. Elke nieuwe capability
   krijgt een kleine nulmeting, upstreamkeuze en dezelfde nameting. Geef de
   algemene tool- en skilluitvoering prioriteit boven nieuwe afzonderlijke
   taakroutes; kies onderhouden bouwstenen op gemeten resultaat en onderhoud.
3. Bewijs leren, initiatief, langdurig werk en gecontroleerde zelfverbetering;
   sluit stem, gezinsinteractie, installatie vanaf nul en herstel daarop aan.
4. Beproef een complete kandidaat met de verplichte tests, kopieën van bestaande
   data, beveiligingscontrole en onafhankelijke review. Deploy alleen via het
   veilige actieve/reserveslot met actuele data en bewezen terugrol.
5. Voer de volledige vergelijking en zeven dagen gezinspraktijk uit op een
   bevroren kandidaat. Accepteer het eindproduct pas met de volledige groene
   bewijsset, inclusief menselijke beoordeling. Een tussenrelease is geen
   eindacceptatie; een relevante wijziging maakt geraakt bewijs opnieuw open.

Het werkbord maakt onderscheid tussen afgevinkte technische kaarten en open
productuitkomsten. Gebouwd, getest, live en door mensen geaccepteerd zijn
verschillende feiten. Bij iedere werksessie wordt dezelfde lijst bijgewerkt
met resultaat, bewijs, kosten voor zover gemeten en de eerstvolgende stap.
Geen voortgangspercentage op basis van aantallen bestanden, commits of tests.

## Grenzen van deze vastlegging

Deze wijziging legt het doel en de taken vast; zij bouwt of activeert de
bovenstaande mogelijkheden niet. Jaap heeft ook het afzonderlijke sessie-goal
aangepast; de nieuwe tekst is actief gecontroleerd op 7 september 2026.
Het volledige productdoel blijft open tot alle vereiste bewijzen er zijn.
