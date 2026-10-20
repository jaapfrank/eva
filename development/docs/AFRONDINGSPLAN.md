# Eva2 — begrensd afrondingsplan

15 oktober 2026. Dit plan maakt [het volledige huisgenootgoal](PROJECTDOEL.md)
uitvoerbaar zonder de ambitie te verkleinen of telkens een nieuwe bouwronde te openen.
De [architectuur](ARCHITECTUUR.md) geeft de modulegrenzen. Het canonieke
[werkbord](../design/product/execution-workboard-v1.json) bevat taakstatus, nu/hierna,
echte blokkades, bewijs en eigenaarhandelingen. Dit document is het plan, geen tweede bord.

## Waar we beginnen — gecontroleerde feiten en beperkingen

| Bevinding | Bron | Gevolg voor de afmaakroute |
| --- | --- | --- |
| Vier productuitkomsten zijn afgesproken: persoon, geheugen/mensbegrip, initiatief, zelfstandig werk/ontwikkeling. | PROJECTDOEL; huidige werkregistratie. | Voltooi samenhangend gedrag; aantal tools, reparaties of kleine kaarten is geen productmaat. |
| HUE5/HUE6 zijn binnen hun oorspronkelijke eindvoorwaarden afgerond; dagelijkse bruikbaarheid is daarmee niet als geheel bewezen. | [HUE5](../evidence/product/hue5-feedback-later-use-outcome-20261014.json), [HUE6](../evidence/product/hue6-quiet-learning-outcome-20261014.json). | Gebruik bestaand bewijs en doe die gesloten proeven niet opnieuw. |
| De gewone initiatiefcyclus gebruikte op 14 oktober tijd, acht historische waarnemingen en drie eerdere eigen verplichtingen en koos stilte. | [Geïnstalleerde context](../evidence/product/daily-life-context-installed-20261014.json). | Afweging draait; nuttig zelfstandig dagelijks handelen, actuele bronraadpleging en passende herbeoordeling blijven concreet werk. |
| Eva1-agent is gestopt; Telegram gebruikt een eigen Eva2-dienst. De fotoprovider blijft een tijdelijke oudere bibliotheekafhankelijkheid. | [Autonome beschikbaarheid](../evidence/product/autonomous-household-availability-20261014.json), `deploy/telegram-bridge/README.md`. | Geen Eva1-ontwikkeling. Verwijder de afhankelijkheid pas met behoud van de bestaande fotoroute en verbruiksmeting. |
| De oude actieve instructies bevatten tegelijk HUE5/HUE6 afgerond en een lagere opdracht om HUE6 opnieuw af te maken. | [Exacte snapshots](../archive/history/20261015-authority-consolidation/snapshot.json). | Eén korte actuele aansturing; oude chronologie stuurt niet meer automatisch uitvoering. |
| De vorige opruiming verwijderde 857 bewezen dubbele historische bestanden, geen volledige codekwaliteitsschouw. 110 grensbevindingen en negen oude ontbrekende release-inputs bleven toen geregistreerd. | [Opruimrecord](repository-pruning-20261013.md), [herbeoordeling](project-reassessment-20261013.md). | Niet aannemen dat de huidige code al netjes of al overbodig is. Onderzoek werkelijke actuele afhankelijkheden en leg besluiten per bestand vast. |
| Op revisie `8b40dacdfe021bfd7a604892e5e6b9982005002f` zijn 4.638 gevolgde bestanden geteld: 560 onder `src`, 453 onder `deploy`, 3.620 onder `development`, vijf overige rootbestanden. | `git ls-files`; inventarisatie tijdens deze planherziening. | Dit is enumeratie, geen volledige inhoudelijke lezing, kwaliteits- of verwijderoordeel. Gewijzigde/ongetrackte projecteigen bestanden worden expliciet toegevoegd aan de eindinventaris. |
| Enkele huidige modules zijn groot: modelgesprek 4.344 regels, gespreksacties 3.544, runtimecompositie 2.343. | Huidige bronlezing/regelmeting van `pydantic_conversation.py`, `conversation_actions.py`, `runtime.py`. | Prioriteit voor een samenhang-/duplicatiebeoordeling; lengte alleen rechtvaardigt geen splitsing of verwijdering. |

Deze feiten zijn gedateerde startinformatie. Werkelijke actuele bronversies en
gebruikseffecten worden bij de geraakte uitvoering opnieuw vastgesteld. De huidige
kleine-taakteller blijft bij de bestaande criteria horen; dit plan voegt geen vijf
fictieve productafrondingen toe en zet oude groene voorwaarden niet terug op rood.

## Vijf eindige fasen

| Fase | Geleverde uitkomst | Wat eerst moet bestaan | Inspanning en onzekerheid |
| --- | --- | --- | --- |
| **E1 — Gewone autonome Eva** | De bestaande Eva neemt daadwerkelijk waar, zoekt benodigde context, kiest relevant vervolg, voert uit en komt terug; aandacht/stilte werkt. | Bestaande gesprek-, geheugen-, taak-, initiatief- en providerroute. | Middelgroot; hangt af van de ontbrekende verbindingen, niet van nieuwe voorbeeldfuncties. |
| **E2 — Samenhangende bediening en ingangen** | Eén bruikbare beheeromgeving voor het dagelijkse Eva-gebruik, met optionele verbindingen en hergebruik van de huidige spraakroute. | E1-route werkt; bestaande beheer- en installatieonderdelen bekend. | Middelgroot tot groot; bestaande frontend/API-dekking en externe aanmeldmogelijkheden moeten eerst worden vastgesteld. |
| **E3 — Bestandsinventaris en onderbouwde besluiten** | Alle huidige bestanden hebben een verklaarde rol, consument, lees-/reviewstatus en behoud-/wijzigingsbesluit. | Reproduceerbare bronstand en feitelijke installatie-/buildpaden. | Groot door aantal bestanden; geen nieuwe productbouw of regel-voor-regel herlezing van al geverifieerd dezelfde bron. |
| **E4 — Gerichte vereenvoudiging en onderhoud** | De eindige bewezen opruim-/refactorlijst is afgewerkt; grenzen, updates en efficiëntie zijn duidelijker en meetbaar. | Goedgekeurde E3-besluiten en werkende gebruikersroutes. | Omvang volgt uitsluitend uit E3; per cluster vooraf begrensd, geen vrije herbouw. |
| **E5 — Eén eindkandidaat en gezinsacceptatie** | Eén geïnstalleerd geheel met gebundelde onafhankelijke eindreview, begrijpelijk gebruik en een echt eindoordeel. | Verplichte E1–E4-uitkomsten technisch gereed. | Gerichte gezamenlijke controle plus de bestaande zeven dagen gezinsgebruik; het menselijke oordeel is geen door Codex in te vullen resultaat. |

De inspanning is een ruwe omvang, geen tijd- of creditbelofte. Geef vóór ieder
uitvoerbaar cluster de concrete geraakte bestanden, afhankelijkheden en meetbare
eindvoorwaarde. Onzekerheid wordt in dat cluster opgelost. Geen willekeurige
60-minutengrens en geen budgetplafond als voorspelde kosten presenteren.

## E1 — Gewone autonome Eva afmaken

1. Leg de werkelijke route vast: invoer/waarneming en tijd → relevante persoonscontext
   → besluit → communicatie of bestaande duurzame uitvoering → verificatie →
   oorspronkelijke ontvanger → feedback en later gebruik. Gebruik bestaande modules;
   begrijp de complete overdracht vóór een wijziging.
2. Maak de concrete gaten af: actuele beschikbare bronnen zelfstandig raadplegen,
   werkelijke taak-/bedoelingsstatus begrijpen en zelf een passende volgende
   aandachtstijd kiezen. Gewone herhaling mag geen betaalde modelstorm worden.
3. Laat dezelfde route zonder vooraf gekozen voorbeeldregel een relevante situatie
   afhandelen. Brontekst of eerder geheugen is geen nieuwe eigenaaropdracht.
   Nog ontbrekende informatie leidt tot een gerichte vraag of eerlijk onbekend;
   gewoon voldoende werk wordt zelfstandig afgemaakt en kort teruggebracht.
4. Bewaar passend initiatief én bewuste stilte. Beoordeel nut, hinder en timing
   afzonderlijk. De bestaande echte feedback wordt hergebruikt, niet opnieuw gevraagd
   of aan een andere bijdrage gekoppeld. I1.2 behoudt haar oorspronkelijke voorwaarde.

**Eindvoorwaarde:** gewone gesprek/geheugen- en taakroutes zijn operationeel; een
gewone autonome afweging kan aantoonbaar relevante actuele context raadplegen en
vervolg plannen, een werkelijk toegestaan resultaat via de native route terugbrengen
en op passend moment ook stil blijven. Later feedbackgebruik is gebonden aan de
juiste persoon en bijdrage. Een gezonde container, kloktrigger, geforceerde creatie
of vrijblijvende stiltescore alleen sluit E1 niet.

**Huidige bewijsgrens:** E1.1-bronvaardigheid is geïnstalleerd en onafhankelijk
[afgerond](../evidence/product/e11-semantic-source-use-outcome-20261015.json): de zelfgekozen
bronvraag is gelezen via de bestaande uitvoerder en inhoudelijk gebruikt bij de volgende
gewone afweging. De oorspronkelijke onzekere onderzoekstaak blijft als zodanig staan.
Modelgekozen vervolg-aandacht E1.2 is eveneens geïnstalleerd en onafhankelijk
[afgerond](../evidence/product/e12-next-attention-outcome-20261015.json): keuze/bewaring,
één hervatting en voorrang voor gesprek zijn met twee echte modelbesluiten in een
geïsoleerde technische proef aangetoond. De logische klok liep versneld; dit was
geen productiecrash- of dagelijkse gezinsproef. E1.3 is vervolgens technisch
[afgerond](../evidence/product/e13-native-initiative-result-20261015.json): nieuw zelfgekozen
werk uitgevoerd, teruggelezen en privé afgeleverd na een transparant gestimuleerde
start. I1.2 is op 16 oktober eveneens
[geïnstalleerd en onafhankelijk afgerond](../evidence/product/i12-quiet-attention-installed-20261016.json):
ongevraagde berichten volgen de lopende voorgrond en bestaande nachtrust, ook na
de laatste broncontrole. Echte eerdere feedback over nut, hinder en timing blijft
gescheiden. E1 is hiermee technisch afgerond binnen deze beperkte bewijsgrenzen;
geen werkelijk slapend/druk gezin, herhaald spontaan gezinsnut of volledige
gezinsacceptatie is aangetoond. Dat laatste blijft in E5/F2, geen nieuwe kaart.
Een recente speakersessie is geen voorwaarde
voor zelfstandig initiatief of een passend gedeeld bericht via de ingestelde
huisuitgang; privé-inhoud blijft in de toegelaten privéroute.

**Afgrenzing:** geen nieuwe autonome daemon, tweede geheugen/taakstore of losse
vooraf gebouwde afspraak-/afval-/lampfunctie. Gebruik waargenomen fouten om alleen
het concrete algemene tekort te herstellen. Andere onafhankelijke uitvoerbare
kaarten gaan door als één gebruikersreactie ontbreekt.

## E2 — Dagelijkse bediening en optionele mogelijkheden

1. Vergelijk bestaande productpagina's en API's met de gewenste bediening. Hergebruik
   de werkende chat-, geheugen-, taak- en initiatiefonderdelen; maak ontbrekende
   overdrachten zichtbaar vóór nieuwe frontendbouw.
2. Verbind gesprekken, personen, brongeheugen/afgeleide herinneringen, opdrachten en
   resultaten, initiatief/feedback, dromen/reflecties en toegepaste verbeteringen.
   De gebruiker kan in één omgeving volgen wat Eva denkt, voorstelt, doet of al heeft
   opgeleverd en eigen gegevens corrigeren. Status komt uit de echte backend.
3. Maak optionele verbindingsinstellingen begrijpelijk: toevoegen, toegelaten aanmelding,
   controleren, kiezen en uitschakelen. Bestaande providerroute blijft de basis.
   Werk het providerneutrale pad uit voor Claude/Anthropic en andere providers en
   het gedeelde kanaalpad voor WhatsApp. Verifieer actuele officiële aansluitvoorwaarden
   bij implementatie; geen fictieve autorisatie, ongeoorloofd hergebruik van inloggegevens
   of veronderstelde werkende koppeling.
4. Stem de bijna afgeronde spraakroute af met haar uitvoerder. Integreer de bestaande
   route met dezelfde personen, context, taken, stoppen en terugmelding. Eén functie
   houdt twee apparaatcontroles; maak geen nieuw spraakplatform en laat optionele
   telefoonspraak de chatkern niet blokkeren.

**Eindvoorwaarde:** één werkende Eva-beheeromgeving dekt bovenstaande productinformatie
en bediening zonder projectstatus voor runtimegedrag aan te zien. De gekozen ingangen
zijn dezelfde Eva. Een gekozen en toegelaten extra provider/kanaal is via het gedeelde
pad aansluitbaar en gecontroleerd; ontbrekende externe toegang wordt concreet als
niet aangesloten getoond. Niet-gekozen accounts of hardware zijn geen verplichte
installatiestap. Werkende tekst-/fotogesprekken blijven behouden.

**Niet automatisch afgerond:** een instellingenformulier bewijst geen verbinding;
een modulemanifest bewijst geen ondersteund model of echt berichttransport. Nog
niet gemaakte gewenste adapters blijven expliciet bouwstatus, geen "alleen inloggen".

E2.6 richt zich op live bellen naar Eva's eigen WhatsApp-nummer. De optionele
Hypermeow/Meowcaller-client en het PCM-audiotransport hergebruiken de bestaande
Eva-/LiveKit-spraakdienst en oorspronkelijke tekst-/resultaatbinding. Protocol-,
codec- en geïsoleerde echte PCM-controles zijn technisch bewijs; geen bewijs van
een echte WhatsApp-oproep. Eerst is een apart nummer nodig dat een verificatie-sms
of oproep kan ontvangen. De eigenaar heeft dit nog niet. Daarna wordt Eva's account
vanaf haar telefoon via de private koppelpagina aangemeld en wordt één inkomend
gesprek vanaf het toegestane eigen contact gecontroleerd. Geen persoonlijke
zelfchat, geen extra Business Platform-abonnement en geen wijzigingen aan speakers.
E2.6 blijft open tot de werkelijke account-/belroute en oorspronkelijke tekst-
terugmelding zijn bevestigd. Dit optionele account blokkeert het andere werk niet.

De aanvulling van 17 oktober [blijvende ontwikkeling](eva-continuous-development-20261017.md)
krijgt één afzonderlijke E2.7-uitkomst: dezelfde toegelaten activiteit, eigen opdrachten,
initiatief, interesses en leeruitkomsten samen kunnen terugvinden en kort beschrijven.
De oorspronkelijke reader was beperkt tot openbare sessies en de afgelopen 24 uur.
De [openbare kalenderdaguitbreiding van 17 oktober](../evidence/product/subscription-voice-20261013/calendar-activity-20261017.json)
is inmiddels geïnstalleerd en gericht gecontroleerd door de andere uitvoerder; die
selectie wordt hergebruikt. De gezamenlijke persoonsgebonden overdracht van privéwerk,
initiatief, interesses en leeruitkomsten blijft E2.7. E3.3/E3.4 beoordelen de bestaande
consumers eerst. Geen nieuwe geheugenbank, dubbele dagselectie of spraakherbouw.


### Gerichte spraakafronding

De eigenaar heeft op 19 oktober de volgende vier werkpakketten en uitvoering
gevraagd. Ze gebruiken de bestaande spraakroute, identiteit, geheugen en uitvoerder.

| Taak | Concreet resultaat |
| --- | --- |
| C4.1 (bestaand) | Natuurlijk onderbreken op beide speakers, zonder eigen stem als nieuwe opdracht te herkennen. |
| C3.4 | De oorzaak van onbedoelde spreekpauzes meten en herstellen, met behoud van woorden en natuurlijke rust. |
| C3.5 | Zachte beginwoorden en korte vervolgen behouden, met correcte beurtgrenzen. |
| C3.6 | Standby en herverbinden na tijdelijke uitval herstellen zonder microfoon uit/aan. |

De drie nieuwe eindvoorwaarden zijn bevroren in
[de spraakuitbreiding](../design/product/voice-fluency-task-scope-20261019.json).
De bestaande C3.1/C4.1-criteria en eerdere acceptaties blijven behouden. Elke
functie houdt beide apparaatcontroles bij elkaar. Een WSL-herstart is geen
voorwaarde voor deze uitvoerbare spraakverbeteringen. Hardwaregedrag wordt op de
werkelijke route gecontroleerd; tests en containergezondheid vervangen dat niet.
De afzonderlijke E4.10/E5.2-opdracht behandelt gedeelde kern- en actielatentie.

Op 20 oktober is de [gecombineerde HA-bronvraag](../evidence/product/subscription-voice-20261013/ha-query-latency-20261020.json) geïnstalleerd en via de echte native softwareketen gebruikt. De weersvraag koos één provideractie en twee modelrondes; antwoordaudio begon na 14,008 s, tegenover de historische 22,823 s. De bronvervolgvraag gebruikte één modelronde en behield de bronnaam. Dit is een gerichte verbetering met ongepaarde metingen; fysieke gesprekskwaliteit en volledige spraakacceptatie blijven bij de bestaande kaarten.

## E3 — Alle bestanden beoordeelbaar maken

### Eén complete, herleidbare inventaris

Neem een vaste uitgangsrevisie plus wijzigingen van de actuele checkout en de
werkelijk gebruikte installatie. Registreer alle gevolgde bestanden en alle
projecteigen ongetrackte bestanden/documentatie die voor product, installatie of
ontwikkeling relevant zijn. Bekijk ook genegeerde projectdocumentatie. Dependency-
installaties, caches, secrets en runtimegegevens krijgen een expliciete uitsluiting
of afzonderlijke private metadataregistratie; zij verdwijnen niet stil uit de telling.
Bewaar privé-inhoud en afgeschermde eindgevallen buiten de publieke registratie.

Leg **per bestand** vast:

| Veld | Betekenis |
| --- | --- |
| Pad, bronrevisie en SHA256 | Welke exacte versie is beoordeeld; nieuwe/gewijzigde bestanden worden een expliciete delta. |
| Categorie | Runtime, adapter/connector, deploy/migratie/schema/lock, test/fixture, frontend, tool, documentatie, historisch bewijs, beschermd eindgeval, vendored code of gegenereerd bestand. |
| Moduleverantwoordelijkheid en eigenaar | Waarom het bestand bestaat en welk onderdeel de betekenis bezit. |
| Werkelijke consumenten | Imports én dynamische registratie, entrypoints, build/package-data, systemd/containers, installer, migraties, tests, dashboard en externe aanroep. |
| Lees-/reviewstatus | Alleen geteld, inhoud gelezen, eerder volledig gelezen met dezelfde hash, afhankelijkheden gecontroleerd, door onafhankelijke reviewer beschermd beoordeeld of onderbouwd niet-actieve referentie. |
| Besluit en reden | Behouden, gericht vereenvoudigen/samenvoegen, vervangen, archiveren of verwijderen; concrete gebruiks-/onderhoudswinst en bewijsverwijzing. |
| Afhankelijkheid en gevolg | Wat zou breken bij verwijdering; vervangend pad en benodigde gegevens-/contractmigratie. |
| Uitvoering en verificatie | Nog open, uitgevoerd, gericht gecontroleerd en reviewreferentie; niet verwarren met een afgeronde gezinsfunctie. |

Alleen tellen is niet inhoudelijk beoordelen. Lees de actieve productcode en actuele
leidende documentatie inhoudelijk per bestand. Hergebruik eerdere volledige lezing
uitsluitend bij exact dezelfde hash. Voor vendored/gegenereerde inhoud beoordeel je
herkomst, licentie, versie en echte consumer; bouw geen eigen variant zonder reden.
Historisch bewijs en afgeschermde gevallen krijgen hun eigen reviewvorm; de bouwer
krijgt geen geheime eindvragen om een "gelezen"-kolom te vullen. De 24 historische
Markdownversies die in het oude leesregister nog niet volledig waren gelezen blijven
als zodanig zichtbaar; dit plan schrijft geen nieuwe algemene geschiedenisronde voor.

### Prioriteit en besluiten

Onderzoek eerst de daadwerkelijk gebruikte kern, model-/taakovergangen, contextkeuze,
resultaatterugmelding, initiatief en samenstelling. Kijk daarna naar optionele adapters,
installatie, interfaces, tests en documentatie. Test- en deploybestanden zijn niet
vanzelf overbodig omdat de gebruiker ze niet ziet. Een oud migratiebestand kan nog
nodig zijn voor bestaande gegevens of een installatiepad.

Verwijderen vereist bewezen afwezig gebruik in de relevante import-, dynamische,
installatie-/build-/migratie- en testpaden, of een bewezen werkende vervanging met
expliciete overgang. Leg dubbele verantwoordelijkheden en onnodige omwegen vast
met concrete bronplaatsen; niet slechts "grote file" of "voelt oud". Een tweede
proefharnas is alleen dubbele productcode als het werkelijk dezelfde uitvoerroute
onnodig dupliceert. Bevroren criteria, reviews en oude foutuitkomsten blijven exact.

**Eindvoorwaarde:** ieder bestand in de vastgelegde scope heeft een categorie,
verantwoordelijkheid, reviewvorm, consument/onderbouwd referentiedoel en gemotiveerd
besluit. Alle actieve productbron is inhoudelijk beoordeeld; ongelezen historie en
beschermde inhoud zijn eerlijk onderscheiden. Dezelfde manifesttelling en deltalijst
verklaren de actuele checkout. De reviewbare opruim-/refactorlijst is eindig en gekoppeld
aan werkbordkaarten; "misschien nog meer verbeteren" maakt die lijst niet onbeperkt.

## E4 — De bewezen schuld gericht wegnemen

Verwerk de E3-lijst in kleine samenhangende clusters, telkens één gebruikers- of
onderhoudsuitkomst. Begin met bewezen dood/dubbel werk en fouten met runtime- of
creditgevolg. Daarna volgen echte grensschendingen en onnodig verweven code. Splits
of combineer alleen wanneer verantwoordelijkheid en interfaces er eenvoudiger van
worden; geen massale stijlrewrite, nieuw framework, tweede planner of nieuwe permanente
service om een bestaande modulegrens te omzeilen.

Per cluster:

1. Bind de bestaande werking en de concrete tekortkoming aan de geraakte bestanden.
   Leg de beoogde netto vereenvoudiging en de gegevens-/contractgevolgen vast.
2. Behoud de publieke interface waar mogelijk; geef een echte incompatibele wijziging
   een versie en expliciete overgang. Verplaats provider- of kanaalspecifieke details
   naar hun adapter en houd persoon/geheugen/werk in de kern.
3. Verwijder vervangen routes en ongebruikte configuratie wanneer hun consumenten
   zijn overgezet. Leg verwijderde paden/hashes/referentierevisie vast. Geen eeuwige
   oude en nieuwe uitvoering naast elkaar en geen importalias naar de Eva1-agent.
4. Voer de gerichte contract-/regressiecontrole en één passende echte uitvoerroute uit;
   bundel onafhankelijke review. Installeer de werkende wijziging met behoud van
   actuele configuratie, gegevens en gelijktijdig werk van andere uitvoerders.
5. Sluit het cluster met bewijs en werkelijke gebruiks-/onderhoudswinst. Heropen alleen
   bij een nieuwe aantoonbare fout of relevante oorzaakwijziging; geen nieuwe reeks
   gelijksoortige proeven om een groenere tabel te krijgen.

### Efficiëntie zonder kwaliteitsverlies

| Eva-runtime meten | Ontwikkelwerk afzonderlijk meten |
| --- | --- |
| Kwaliteit/voltooiing en benodigde menselijke hulp per gewone uitkomst. | Afgeronde geïnstalleerde gebruikersuitkomsten en concrete resterende blokkades. |
| Antwoordstart, totale taakduur en bij spraak de werkelijk geraakte kanaalvertraging. | Geraakte/verwijderde bestanden, gewijzigde verantwoordelijkheid en netto eenvoud. |
| Invoer/uitvoer, model-/toolaanvragen, herhaalde context en onnodige herhaling. | Ontwikkelaanvragen en gerichte controletijd voor zover observeerbaar, niet optellen als Eva-gebruik. |
| Achtergrondwerk, contextomvang, opslag/CPU/geheugen waar meetbaar. | Terugkerende foutoorzaken, build/installatieherhalingen en tijd tot bruikbare oplevering. |
| Herstel, stoppen, dubbele acties en gegevensbehoud. | Review-/testherhalingen alleen bij gewijzigde oorzaak of ontbrekend bewijs. |

Gebruik de vóór wijziging vastgelegde relevante referentie. Kwaliteit, correcte
persoon/resultaatbinding en stoppen zijn voorwaarden; minder tokens alleen is geen
winst als Eva dommer of onbruikbaar wordt. Vergelijk gemeten totale betrokken
modelkosten met dezelfde definitie; credits niet raden en UNKNOWN niet wegpoetsen.
Geen extra concurrentenserie of algemeen optimalisatieplatform. Nieuwe relevante
prestatieproef alleen wanneer die de concrete clusterbeslissing kan veranderen.

### Onderhoud en updates

Elke actieve module heeft één verantwoordelijkheid, expliciete contractversies,
herkenbare configuratie, gegevensbezit en gerichte verificatie. Leg vast hoe optionele
modules geïnstalleerd, gecontroleerd, bijgewerkt en uitgeschakeld worden. Houd locks,
build- en installatiemetadata reproduceerbaar. Een upstream-update krijgt een reden
(kwaliteit, mogelijkheden, correctheid, veiligheid of ondersteuning), effectbeoordeling
en gerichte compatibiliteitscontrole; recentheid alleen maakt code niet beter.
Gebruik onderhouden upstream waar passend in plaats van eigen gelijkwaardige
infrastructuur, maar onderzoek een keuze alleen bij een concreet tekort.

Werk de tijdelijke oude fotoproviderafhankelijkheid en actuele runtime-afhankelijkheden
van historische proefhelpers af waar E3 dat nodig maakt. Een oud release-inputgat
wordt hersteld of als aantoonbaar vervangen/afgesloten route verklaard, niet opnieuw
geproduceerd via een hele benchmarkserie. Wijzig geen licentie zonder eigenaaropdracht.

**Eindvoorwaarde:** de vastgelegde E3-opruimlijst heeft geen onverklaarde open
verwijder-/refactorbesluiten; alle verplichte besluiten zijn uitgevoerd of een
onderbouwd behoudsbesluit vervangt de wijziging. Overblijvende niet-blokkerende
verbeterideeën staan apart als vervolgwerk. De echte kernroutes zijn behouden,
module-/updateafspraken en gemeten efficiëntie zijn vastgelegd. Het streven naar
perfecte code houdt de release niet oneindig open.

E4.5 is de afzonderlijke gebruikersuitkomst uit het eigenaarsontwerp van 17 oktober:
één echte omkeerbare softwareverbetering zelfstandig van aanleiding via onafhankelijke
beoordeling tot installatie, gedragscontrole en later gebruik brengen. Hergebruik de
bestaande component-/release-/updaterketen; sluit de ontbrekende automatische herstel-
overdracht gegevensveilig. Geen tweede updater, permanente onderhoudsagent of routine
van terugzetten bij iedere ontwikkelwijziging. De eerste klasse bevat geen destructieve
datamigratie; herstelbewijs vraagt geen kunstmatige storing in het gezin. E4.4 blijft
het onderhoudscontract, E4.5 bewijst de uitvoering. Bestaande HUE6/L3-acceptaties blijven
binnen hun oorspronkelijke scope geldig. Beide nieuwe kaarten gaan mee in E5/F2.

### Gerichte eigenaaruitbreiding: snelle bediening en Home Assistant-module

De eigenaar heeft op 19 oktober 2026 taken gevraagd voor de te trage gewone
lichtopdracht en voor Home Assistant als module buiten Eva's kern. De livevergelijking
van 18 oktober toont verplichte geheugenwachttijd en meerdere modelrondes rond een
korte echte schakelcall. Bestaande provider-, API-, Assist-MCP- en eventcode blijft
de basis. De zes nieuwe kaarten staan uitsluitend op het bestaande werkbord:

| Taak | Concrete uitkomst |
| --- | --- |
| E4.6 | HA-specifieke verantwoordelijkheid achter de optionele providergrens; kern en overige modules werken zonder HA. |
| E4.7 | Eén capabilityprofiel met actuele officiële API/MCP-dekking en bestaande bevoegdheden. |
| E4.8 | Actuele apparaatcontext hergebruiken, met verversing en correct herstel na onderbreking. |
| E4.9 | Doelvalidatie, één uitvoering en verse controle samen binnen één moduleactie. |
| E4.10 | Gedeelde geheugenvoorbereiding versnellen met behoud van relevante context, identiteit en persoonsgrenzen. |
| E5.2 | De geïnstalleerde combinatie via gewone chat en bestaande spraak live bewijzen. |

De ongeveer één seconde uit de Eva1-gebruikservaring is een ontwikkeldoel, geen
opnieuw gemeten referentie. Voor bekende ondubbelzinnige chatopdrachten is de nieuwe
eindgrens mediaan maximaal 1 seconde en p95 maximaal 2 seconden van ontvangst tot
verse HA-eindbevestiging bij een gereedstaande installatie. Leg steekproef en
omstandigheden vooraf vast, rapporteer koude context afzonderlijk en verlaag geen
kwaliteit om de klok te halen. Spraak meet zowel vanaf einde audio als vanaf herkende
tekst. Een snelle wachtzin telt niet als uitgevoerde actie; een HA-waarneming bewijst
op zichzelf geen fysieke hoorbaarheid. Niet gehaald blijft open.

De officiële [MCP-documentatie](https://www.home-assistant.io/integrations/mcp_server/)
en [LLM API](https://developers.home-assistant.io/docs/core/llm/) bepalen welke
mogelijkheden en toegangsgrenzen de geselecteerde API biedt. De
[REST API](https://developers.home-assistant.io/docs/api/rest/) en
[WebSocket API](https://developers.home-assistant.io/docs/api/websocket/) leveren
aanvullende toestands-, actie- en updatefuncties. De lokale versie en configuratie
moeten die functies werkelijk ondersteunen; MCP is geen automatische snelheidswinst.

De oorspronkelijke E4.1–E4.5-criteria en hun beperkte afronding blijven behouden.
Deze nieuwe scope is afzonderlijk bevroren in
[de taakuitbreiding](../design/product/home-module-latency-task-scope-20261019.json).
Er komt geen zesde fase. E4.6 is de eerstvolgende taak van deze uitbreiding;
E4.10 kan onafhankelijk worden voorbereid binnen de bestaande teamomvang.
Lopende E5.1-opvolging van de andere uitvoerder blijft actief. De gezamenlijke
eindoplevering omvat ook E5.2; gezinsacceptatie blijft F2.1/F2.2.

## E5 — Eén gezamenlijke oplevering

1. Stel één kandidaat samen uit de werkelijk actuele bron, dependencies, configuratie,
   optionele modulekeuze en gegevensschema's. Neem de afgeronde bestaande spraakroute
   gecoördineerd over, zonder die opnieuw te bouwen. Leg verplichte scope en open
   niet-gekozen opties vast; eerdere echte acceptaties blijven geldig binnen hun scope.
2. Voer de benodigde gerichte gezamenlijke controles en één gebundelde onafhankelijke
   eindreview uit op de vier gebruikersuitkomsten en gewijzigde interfaces. Hergebruik
   geldig bestaand bewijs; alleen geraakt of aantoonbaar ontbrekend bewijs opnieuw
   controleren. Oorspronkelijke criteria en afgeschermde eindgevallen blijven onafhankelijk.
3. Installeer het bruikbare geheel en geef duidelijke bediening/instructies. Toon in
   productbediening én projectbord wat werkelijk draait, wat opgeleverd is, wat nog
   ontbreekt en welke eventuele eigenaarhandeling nodig is. Geen nieuwe green claim
   op basis van alleen imagegezondheid of een gereed proefrapport.
4. Laat de bestaande zeven dagen gezinsgebruik volgen en bewaar Jaaps echte eindoordeel.
   Scheid een technische fout, gemiste nuttigheid, persoonlijkheidsvoorkeur en hinder.
   Alleen concrete releaseblokkerende fouten herstellen; een relevant gewijzigd deel
   krijgt gerichte herbeoordeling, niet automatisch een volledig nieuwe testweek of matrix.

De bestaande E5.1-oplevering en F2.1/F2.2-gezinsproef beoordelen dezelfde vijf
samenhangende aspecten over de bestaande zeven dagen:

| Aspect | Wat het echte gebruik moet laten zien |
| --- | --- |
| Dagelijkse continuïteit | Eva kent afspraken, beloften, activiteiten en open zaken en komt er passend op terug. Status en uitvoering zijn controleerbaar. |
| Eigen leven | Dezelfde zelfgekozen interesses en projecten krijgen werkelijk vervolg, ook zonder nieuwe eigenaaropdracht; resultaten en lessen blijven bewaard. |
| Gezinsbijdrage | Per deelnemend gezinslid afzonderlijk: echte hulp, betrokkenheid, plezier, timing en hinder. Geen verzonnen oordeel namens iemand die niet deelnam. |
| Huisbewaking | Werkelijk aangesloten bronnen, afgesproken aandacht, meldmomenten, toegelaten eigen oplossingen en ontbrekende dekking zijn duidelijk; de algemene afweging gebruikt ze passend. |
| Eén Eva | Aangesloten tekst- en spraakingangen delen identiteit, toegelaten herinneringen en opdrachten, met persoonsgrenzen. Niet-gekozen ingangen vragen geen nieuwe proefmatrix. |

Leg concrete vervolgacties en benodigde aansporingen, herhaalde uitleg en menselijke
ingrepen vast. De eindbeoordeling gaat over blijvende betrokkenheid, nuttige hulp en
minder noodzakelijke begeleiding. Losse technische afrondingen bewijzen dit niet.
Herbruik geldige eerdere acceptaties; begin M1.2, M3.3, HUE5/HUE6 of I1.2 niet opnieuw.
Dit scherpt de bestaande gezamenlijke oplevering aan; het voegt geen bouwfase,
voorbeeldspecifieke functie of tweede plan toe. Spraak blijft bij haar uitvoerder.

**Eindvoorwaarde:** de volledige PROJECTDOEL-eindvoorwaarden zijn aan dezelfde
geïnstalleerde kandidaat en geldige bestaande bewijsstukken gebonden. Vier
productuitkomsten, complete leercyclus, zelf gerealiseerde/hergebruikte mogelijkheid,
AIDE²-route, persoonsgrenzen/stop/gegevensbehoud, bediening, bestands-/onderhoudsbesluiten
en echte gezinsacceptatie zijn controleerbaar. Niet-gekozen optionele toegang en
historische vergelijkingsonzekerheid worden eerlijk getoond, niet als winst ingevuld.
Een afgebakende kleine taak, opschoning of zelfstandige review sluit het hele goal niet.

## Bijhouden zonder nieuwe ondoorgrondelijke lijsten

De oorspronkelijke 18 concrete kaarten zijn geregistreerd als E1.1–E1.3,
E2.1–E2.6, E3.1–E3.4, E4.1–E4.4 en E5.1. Het ontwerp van 17 oktober voegt alleen
E2.7 (eigen geschiedenis) en E4.5 (zelfstandig softwareonderhoud) toe aan diezelfde
fasen; de oorspronkelijke criteria en afrondingen veranderen niet. Ze staan onder dezelfde vijf fasen in
**Taken per onderdeel**. De zeven bestaande open kaarten blijven behouden; HUE5/HUE6
worden niet opnieuw aangemaakt. F2.1/F2.2 blijven de echte gezinsproef en acceptatie.
De historische 45/52 wordt hierdoor 45/70 geregistreerde taken; het werk is zichtbaar
gemaakt, geen bestaande afronding ingetrokken. De twee expliciete nieuwe uitkomsten
brengen de geregistreerde scope van 70 op 72; geen voltooide taak is heropend.
Status staat alleen in het werkbord;
het scopebestand bewaart uitsluitend de oorspronkelijke nieuwe eindvoorwaarden.


Het bestaande bord krijgt voor deze vijf fasen een groepering en de noodzakelijke
concrete clusters. Elke clusterkaart noemt uitkomst, bestaande werking, tekort,
geraakte bestanden, afhankelijkheden, eindvoorwaarde, eigenaar, bewijs en volgende
handeling. Behoud oorspronkelijke taak-ID's, doneWhen, fouten en acceptaties.
Nieuwe eigenaarwensen krijgen herkenbare dekking; kaartgroepering verandert geen
oude teller in een nieuw productpercentage. Historische milestones met een oude
vergelijkingsmatrix zijn geen actieve opdracht zolang de eigenaar die serie parkeert.

Gebruik kleur voor echte status: nog werk, bezig, concrete blokkade, technisch
opgeleverd, geïnstalleerd/gebruikt en eigenaaracceptatie. De gebruiker ziet bovenaan
laatste bruikbare oplevering, huidig resultaat, eerstvolgende stap en noodzakelijke
handeling. Een open menselijke beoordeling maakt ander uitvoerbaar werk niet geblokkeerd.

Nieuwe ideeën vergroten de lopende cluster niet automatisch. Alleen een aangetoond
tekort dat de afgesproken uitkomst voorkomt, verandert die cluster met bron en reden.
Alle overige ideeën blijven vervolgwerk. Dit begrenst de eindfase zonder relevante
fouten te verbergen of de huisgenootambitie kleiner te maken.
