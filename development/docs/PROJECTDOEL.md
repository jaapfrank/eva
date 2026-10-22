# Eva — één zelfstandige digitale huisgenoot

Bijgesteld op 22 oktober 2026 op verzoek van de eigenaar.
Het [werkbord](../design/product/execution-workboard-v1.json) bepaalt het huidige werk;
het [afrondingsplan](AFRONDINGSPLAN.md) de uitvoering en de [architectuur](ARCHITECTUUR.md)
de verantwoordelijkheden. De [vorige doeltekst](../archive/history/20261022-product-refocus/PROJECTDOEL.md)
is exact bewaard. Bestaande afspraken en oorspronkelijke taakcriteria blijven gelden.

## Het doel

Eva is een interactieve, autonome, grappige en aardige entiteit in huis. Ze kent het
gezin, heeft een herkenbare eigen persoonlijkheid, interesses en projecten, neemt
passend initiatief en helpt met dagelijkse zaken. Ze draagt sociaal en praktisch bij
en bespaart waar mogelijk tijd en geld. Haar eigen leven bestaat uit werkelijk gedaan
werk, ervaringen en geleerd inzicht, met vervolg over meerdere dagen.

De ambitie is dat Eva de digitale werkzaamheden kan leren uitvoeren die een mens op
een pc kan doen. Ze ontdekt bestaande hulpmiddelen, gebruikt ze en maakt ontbrekende
mogelijkheden. Ze houdt een opdracht vast tot er een bruikbaar resultaat is, controleert
dat resultaat en leert van wat goed of verkeerd ging. Ontbrekende toegang vraagt ze
concreet; beschikbare toegang en voldoende informatie benut ze zelfstandig.

## Wat het gezin moet merken

- **Eén persoon:** natuurlijk contact via de gebruikte kanalen, met humor, eigen
  oordeel, samenhang en besef van wat ze werkelijk heeft gedaan. Korte passende
  antwoorden; geen verzonnen belevenissen of technische verslagen als gewone reactie.
- **Geheugen en mensbegrip:** ze onthoudt relevante voorkeuren, afspraken, correcties
  en bedoelingen, begrijpt hun context en gebruikt die later. Mensen kunnen eigen
  informatie bekijken, corrigeren en laten vergeten.
- **Eigen initiatief:** ze onderzoekt kansen, merkt afwijkingen en komende behoeften
  op en kiest een nuttige actie, vraag, voorstel of stilte. Ze draagt afgesproken
  terugkerende verantwoordelijkheden en leert van de reactie van het gezin.
- **Zelfstandig werk en ontwikkeling:** een gewone vraag leidt tot een gecontroleerd
  resultaat. Ze bouwt ontbrekende mogelijkheden, herstelt problemen en gebruikt
  geslaagde oplossingen later opnieuw met steeds minder menselijke begeleiding.

## Eén samenhangende uitvoering

Waarnemen → context begrijpen → afwegen → communiceren of handelen → resultaat
controleren → leren en later toepassen. Bestaande gesprek-, geheugen-, initiatief-
en werkmodules vormen samen deze kringloop. Geen tweede planner, geheugen of aparte
Eva per kanaal, en geen extra permanent proces alleen om meer activiteit te tonen.

Het gewone gesprek blijft beschikbaar tijdens achtergrondwerk. De bestaande
PostgreSQL/Hindsight-geheugenketen houdt herkomst, correctie en wissen bij. Overdag
blijven recente gesprekken en lopende bedoelingen bruikbaar; de droomfase selecteert
blijvend nuttige kennis en verwerkt tegenbewijs zonder alles dubbel op te slaan.

Uitvoering bewaart de oorspronkelijke ontvanger en werkstatus. Stoppen werkt en
onzekere externe effecten worden gecontroleerd voordat ze eventueel opnieuw worden
uitgevoerd. Technische controles hebben één duidelijke eigenaar; dubbele controle-
en modelrondes worden alleen behouden wanneer ze aantoonbaar iets toevoegen.

## Eva's eigen uitbreidingen blijven buiten de kern

Een zelfgemaakte vaardigheid, connector of voorziening wordt standaard een afzonderlijk
versieerbaar pakket via bestaande module- en procedureinterfaces. De kern biedt de
algemene mogelijkheden voor contact, geheugen, afweging, uitvoering en leren. Een
camera-, apparaat- of gezinsvoorbeeld krijgt geen eigen beslisroute in de kern.
Een werkelijk ontbrekende algemene interface kan een afzonderlijk beoordeelde
kernwijziging rechtvaardigen; nuttige nieuwe toepassingscode verhuist niet vanzelf mee.

Een uitbreiding bewaart code, manifest, afhankelijkheden, configuratieschema,
benodigde rechten, eigen gegevens en laatst werkende versie. Back-up en herstel omvatten
ook deze zelfgemaakte mogelijkheden. Geheimen worden versleuteld meegenomen of waar
nodig opnieuw gekoppeld. Op een schone pc herstelt Eva de pakketten, controleert
compatibiliteit en verbindingen en toont dezelfde werking aan. Git alleen is geen
volledige back-up. Dit herstel is onderdeel van oplevering.

Zelfverbetering gebruikt de bestaande route: oorzaak onderzoeken, kandidaat maken,
onafhankelijk vergelijken, bewezen winst beheerd toepassen en het werkelijke resultaat
controleren. Tijdelijke uitval, configuratiefouten en codefouten vragen verschillende
oplossingen. Een mislukte reparatie blijft één begrensde verantwoordelijkheid.
Meer geslaagde wijzigingen geven niet automatisch ruimere toegangsrechten.

Eigen interesses hoeven geen huisprobleem op te lossen. Maximaal twee eigen projecten
krijgen werkelijk vervolg; één compacte actuele Inner State beschrijft waar Eva mee
bezig is. Af en toe deelt ze daar kort iets over via haar bestaande initiatiefroute.
Die state volgt echte activiteit en feedback, bevat geen verzonnen innerlijke monoloog
en blijft onderscheiden van geleerde omgangsbijstellingen en blijvende huisvoorzieningen.
Het gezin kan dit zien, erop reageren en het werk of de uitingen stoppen.

## Eerst bruikbaar gedrag, gericht eenvoudiger bouwen

De eerstvolgende praktijktest is de cameraopdracht: Eva onderzoekt zelf aanwezige
camera's, herkenning en deurbelspraak, realiseert de ontbrekende verbinding en laat
bekende personen passend begroeten. Onbekend blijft onbekend. Dezelfde uitbreiding
moet daarna op een schone installatie herstelbaar zijn. Deze eigenaaropdracht toetst
zelfstandig uitvoeren en bouwen; eigen behoefteontdekking wordt daarnaast afzonderlijk
beoordeeld. Codex maakt de noodzakelijke algemene overdrachten af, niet vooraf de hele
voorbeeldfunctie om die vervolgens als Eva's eigen werk te presenteren.

Vereenvoudiging volgt concrete belemmeringen op die route. Historische hulpmiddelen
worden pas verplaatst nadat imports, entrypoints, installatie, herstel en releasegebruik
zijn gecontroleerd. Grote klassen splitsen op verantwoordelijkheid wanneer dat hun
gebruik werkelijk eenvoudiger maakt. Geen regelaantal als succesdoel en geen massale
herbouw. Bestaande nuttige code en eerder bevestigd gedrag blijven behouden.

## Wanneer het werkt

We volgen afgeronde taken zonder menselijke hulp, herhaal-/correctieverzoeken,
herstelduur, waardering van initiatief en daadwerkelijk hergebruik. Tijd en middelen
worden gemeten waar dat kan; ontbrekende metingen blijven onbekend. Een chatopdracht
van één à twee seconden blijft het streefdoel voor eenvoudige bediening; lang werk
houdt het gesprek niet vast. Gezinsoordeel bepaalt prettig, grappig en nuttig gebruik.

Oplevering vraagt dezelfde geïnstalleerde Eva die de vier uitkomsten in gewoon gebruik
laat zien, een ontbrekende mogelijkheid realiseert en later hergebruikt, én geheugen,
configuratie en uitbreidingen op een schone installatie kan herstellen. De bestaande
gezinsweek en eigenaaracceptatie blijven gelden. Een geslaagde test, documentatie-update
of kleine reparatie sluit het project niet af. Eva1 blijft gestopt; de andere uitvoerder
behoudt de bestaande spraakroute. Optionele accounts en apparaten zijn geen verplichting
voor ieder huishouden. Eerdere technische afrondingen en hun grenzen staan in het
werkbord en de bewaarde doeltekst; ze worden hier niet opnieuw als productwinst geteld.
