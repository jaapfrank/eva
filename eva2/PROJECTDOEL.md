# Eva — digitale huisgenoot die leert en handelt

## Actuele uitvoering 7 oktober — L3.6–L3.7 afgerond, P2.1 volgt

De gewone Eva-route maakte contextkandidaat B, liet deze afzonderlijk beoordelen
en paste de exact geaccepteerde versie via de bestaande beheerde route toe.
Een extra eindvraag werd 3/3 rechtstreeks beantwoord; geen regressies of harde
fouten. Dat bewijst één beperkte kwaliteitsverbetering, geen algemene voorsprong
of tokenbesparing. Zie evidence/product/l3-context-candidate-b-installed-20261007.json.
L3.7 is onafhankelijk GO_DONE: de volgende gewone ronde gebruikte de beste
actuele bron/status en eerdere ervaring en stopte terecht zonder nieuw tekort;
hervatten/replay herhaalde geen toepassing. De resterende oorspronkelijke P2.1-
vergelijking volgt; menselijke beoordelingen blijven afzonderlijk zichtbaar. Apps/add-ons horen bij dezelfde cyclus; droommodus is nog niet
gebouwd. Originele eindvoorwaarden, gesloten proeven en gezinsbeoordeling blijven staan.


## P2.1 actueel 7 oktober — matching geheugenuitkomst
Dezelfde oorspronkelijke CM06 is nu eenmaal door Eva1 uitgevoerd en onafhankelijk
beoordeeld naast OpenClaw: Eva1 haalt de vereiste latere status niet terug
(1/3 bruikbare antwoorden, 1/2 verplichte readbacks); OpenClaw wel (3/3, 2/2).
Eva1 gebruikt 43.408 invoer-/1.674 uitvoertokens in 7 complete aanvragen,
OpenClaw 285.381/1.521 in 12. Minder verbruik compenseert het gemiste resultaat niet.
De tijdmetingen hebben verschillende eindgrenzen; geen snelheidswinnaar.
Eva2 heeft geen resultaat met deze exacte successor-input; legacy CM06 blijft apart.
Twee echte menselijke beoordelingen, resterende oorspronkelijke dekking en algemene
superioriteit blijven open. OpenClaws eerdere CM05-persoons-/privacyverlies blijft staan.
Eva1-CM05 was op de bestaande vaste éénpersoonsroute niet uitvoerbaar: UNKNOWN,
geen productverlies. De voorafgaande evaluator-Path-fout kostte nul modelcalls;
die is gericht gecorrigeerd, waarna één werkelijke oorspronkelijke uitvoering volgde.
Bewijs: evidence/product/p2-eva1-successor-cm06-result-20261007.json;
review: reviews/p2-eva1-successor-cm06-terminal-review-20261007.json.
Daarna alleen de oorspronkelijke beschikbaarheid van gedeelde-publieksgebruik bepalen
op bestaande routes; geen automatische matrix, proefherhaling of nieuwe providerroute.
Dit is vergelijkingsbewijs, geen extra afgeronde huisgenootfunctie.

## P2.1 historie — oorspronkelijke vergelijking en gerichte oorzaakcorrectie

De oorspronkelijke OpenClaw-CM01-proef is eenmaal uitgevoerd, maar vóór de
eerste modelcall vastgelopen door een fout in de proefomgeving: loopback-POST
ging naar de CONNECT-only externe proxy. Alleen die proxybinding is gecorrigeerd;
de publieke gerichte controle geeft501→200 zonder externe modelcalls. De
oorspronkelijke sample/deadline/bron en UNKNOWN blijven bewaard. Dit is geen
productkwaliteitsverlies, geslaagde vergelijking of nieuwe productfunctie.
Zie evidence/product/p2-openclaw-original-runner-binding-20261007.json.

CM02 is vervolgens eenmaal uitgevoerd: 3 complete SDK-afrekeningen,
68.898 invoer-/375 uitvoertokens, 77,065s. De evaluator stopte ten onrechte bij
een completed fileChange vóór de matching post-hook. Alleen die getypeerde
apply_patch-overgang is gecorrigeerd (5 checks en onafhankelijke GO).
CM02 blijft onvolledig/kwaliteit UNKNOWN; geen nieuwe productfunctie of
vergelijkingswinst. Alle historische resultaten en oorspronkelijke criteria
blijven behouden.

CM03 is vervolgens eenmaal uitgevoerd: 1/3 beurten behouden, 9 volledig
afgerekende SDK-aanvragen, 221.764 invoer-/841 uitvoertokens en 129,414s.
De evaluator blokkeerde native herstel na een failed/127-command vóór post-hook.
Alleen de getypeerde failure-afrekening is gecorrigeerd (7 checks, onafhankelijke
GO); failed wordt geen actionsuccess. Ook deze gehele case blijft UNKNOWN,
zonder herhaling. De gerichte publieke native herstelproef is onafhankelijk GO:
failed/127 blijft een fout, drie beurten/readbacks afgehandeld, 16 tools en de
17e-capweigering behouden. Vier nagebootste antwoorden, nul echte providercalls.
Dit bewijst de gewijzigde uitvoerovergang; geen oorspronkelijke kwaliteitsscore.
CM04 volgde eenmaal met twee verschillende geverifieerde privéorigins en
ongewijzigd native geheugenbeleid: 1/3 beurt compleet, 7 volledig afgerekende
SDK-aanvragen, 158.724 invoer-/498 uitvoertokens en 107,408s. De evaluator stopte
bij een dynamicToolCall failed zonder exacte namespace/naamkoppeling aan de
toegelaten hook. Toolbodyuitvoering is onbekend. In twee verstuurde contexten van
de tweede persoon is het beschermde gegeven niet gevonden; de gehele case blijft
UNKNOWN, zonder volledige privacy-/antwoordscore of herhaling.
Zie evidence/product/p2-openclaw-original-cm04-result-20261007.json.

De publieke Code Mode-proef heeft de oorzaak nu exact aangetoond: de native
hook gebruikt openclawmemory_search, de callback namespace=openclaw en
tool=memory_search. Alleen de naamkoppeling en exacte beurtbinding zijn gecorrigeerd.
De extra diagnostische I/O vóór dispatch is na onafhankelijke review verwijderd.
De publieke native herstelproef geeft 3 beurten/16 tools, cap17 geweigerd en
readbacks/afsluiting; 11 gerichte checks op de definitieve bron zijn geslaagd.
De publiek uitgevoerde bron en de definitieve no-I/O-delta zijn apart bewaard;
geen claim dat de definitieve bron nogmaals native is uitgevoerd. Nul echte
modelcalls of oorspronkelijke proefvragen; gesloten CM01–CM04 blijven UNKNOWN.
Dit is noodzakelijke proefadapterarbeid, geen nieuwe Eva-functie.
Zie evidence/product/p2-openclaw-codemode-identity-correction-20261007.json en
reviews/p2-openclaw-codemode-identity-review-20261007.json. Daarna de nog ongebruikte
oorspronkelijke case voorbereiden; bron/runtime-toelating daarvoor is nog niet gegeven.

CM05 is vervolgens eenmaal uitgevoerd en onafhankelijk gesloten: 3/4 antwoorden
behouden. Beide eerste erkenningen kloppen, maar de terugkerende persoon krijgt
het privégegeven van de andere. Het geheugenreadback en de oorspronkelijke harde
persoons-/privacyvoorwaarde falen: gedeelde notitie overschreven, privéfeit in de
andere persoonscontext. De vaste limiet van 16 modelaanvragen stopt de vierde beurt; geen
nieuwe evaluatoridentiteitsfout. 16 volledige SDK-afrekeningen, 10 afgehandelde tools,
403.281 invoer-/1.296 uitvoertokens, 181,706s. Eerdere 16 ledger-rijen exact behouden.
Fysieke resources beëindigd; volledige native afsluiting, het vierde antwoord en
menselijke kwaliteit blijven onbekend. Geen algemene superioriteit of nieuwe functie.
Zie evidence/product/p2-openclaw-original-cm05-result-20261007.json en
reviews/p2-openclaw-original-cm05-terminal-review-20261007.json. P2.1 blijft open
voor werkelijk ontbrekende oorspronkelijke dekking; geen CM01–05-herhaling.

CM06 is daarna eenmaal uitgevoerd en inhoudelijk onafhankelijk gecontroleerd:
alle drie oorspronkelijke feitelijke voorwaarden en beide verplichte readbacks
zijn ondersteund. Eén persoon hervat drie dagen later; de juiste status en
herkomst staan in het native geheugen en worden werkelijk gebruikt in context
en antwoord. 12 complete SDK-aanvragen, 285.381 invoer-/1.521 uitvoertokens,
11 tools, 123,410s. Dit is een positieve afzonderlijke OpenClaw-uitkomst,
geen algemene kwaliteits- of efficiëntiewinnaar. Menselijke ratings blijven open.
De oude Eva1/Eva2-CM06 heeft een andere case-/datasetbinding en wordt niet
stilzwijgend als gematcht vergelijkingspaar gebruikt.
Zie evidence/product/p2-openclaw-original-cm06-result-20261007.json en
reviews/p2-openclaw-original-cm06-terminal-review-20261007.json.
Het bestaande dashboardoverzicht verwijst nu naar de geïnstalleerde L3.5–L3.7
en de huidige vergelijking; verouderde L3.5-/OpenClaw-klokmeldingen worden
vervangen door de werkelijk bereikte status. Geen nieuwe productfunctie of replay.

## Eigenaarverduidelijking 7 oktober 2026 — uitbreidingen en droommodus

Het hervatte app-goal is actief. Wijzigingen aan de aansturing staan in deze
projectbestanden; de actuele kleine taken blijven de uitvoeringsbron.
De verbetercyclus geldt ook voor apps, add-ons en andere uitbreidingen die Eva
toevoegt of aanpast, niet uitsluitend voor varianten van haar eigen kerncode.
Gebruik per behoefte bestaande middelen en één gedeelde uitvoering; geen vooraf
gebouwde catalogus van voorbeeldfuncties.

Een droommodus gebruikt de bestaande initiatief-/reflectieroute wanneer het
rustig is en aantoonbaar voldoende capaciteit beschikbaar is. Zij verwerkt echte
ervaringen, onderzoekt mogelijkheden en vergelijkt voorstellen binnen dezelfde
toegang en verbruiksafspraken. Gezinsgebruik en lopend werk krijgen voorrang;
geen continue modelcalls om aanwezigheid te simuleren. Een gekozen uitbreiding
moet werkelijk gecontroleerd en bruikbaar worden, met onafhankelijke beoordeling
voor eigen wijzigingen en behoud van persoonsgrenzen en stoppen. Onderzoeken,
kiezen, toepassen en aangetoond beter werken blijven afzonderlijke resultaten.
Dit verduidelijkt L3/I1; geen nieuwe hoofdtaken of wijziging van afgesloten proeven.


## Eigenaaropdracht 7 oktober 2026 — herbruikbare AIDE²-cyclus afmaken

Jaap vraagt de ontbrekende AIDE²-cyclus nu daadwerkelijk te bouwen en de nodige
kleine overgangstaken zichtbaar te maken. De vier bestaande huisgenootuitkomsten
blijven leidend; geen nieuwe hoofdtaken of vaste functies per voorbeeld.
L3.1–L3.3 en de geïnstalleerde contextverbetering blijven afgerond. De vier
expliciet gevraagde vervolgtaken L3.4–L3.7 staan op het bestaande werkbord:
voortbouwen op een kandidaat, varianten op resultaatkwaliteit kiezen, onafhankelijke
beoordeling en toepassing aansluiten, en de volledige gewone gebruikersroute
met een volgende ronde aantonen. De uitbreiding van L3 is nieuw geautoriseerd
resterend werk, geen verlies van eerdere acceptatie.

L3.4–L3.7 zijn afgerond en onafhankelijk gecontroleerd: één gemeten betere
contextkandidaat toegepast, vervolgens de beste versie/ervaring gebruikt zonder
onnodige herhaling. Geen brede superioriteitsclaim of gebouwde droomplanner.
Hervat bij werkelijk resterend P2.1; herhaal eerdere varianten/proeven niet. Hergebruik de gedeelde uitvoering, het programmeerwerk, de evaluator
en toepassing; geen tweede agentplatform. Originele proeven/criteria en verbruik
blijven bewaard; dit heropent geen afgesloten AIDE- of R1-proef. Registreer een
nieuwe passende toepassing alleen vooraf en binnen de bestaande providerroute
en concreet beschikbare toelating. Eindpunt is één herbruikbare verbetercyclus
met aangetoond beter taakgedrag, geen oneindige zoektocht naar steeds betere Eva.
De lopende P2-vergelijking blijft open en gebruikt alleen resterend geldig bewijs;
een menselijke beoordeling blokkeert geen onafhankelijk bouwbaar productwerk.
Spraak blijft geparkeerd volgens de prioriteit van 5 oktober.

## Eigenaarverduidelijking 7 oktober 2026 — AIDE² maakt Eva beter

Het hoofddoel van AIDE² is dat Eva aantoonbaar slimmer en zelfstandiger handelt:
beter programmeren en bruikbare wijzigingen opleveren, natuurlijker en samenhangender
praten, relevante kansen onderzoeken en nuttig eigen initiatief werkelijk afmaken.
Dit dient de vier bestaande huisgenootuitkomsten: persoonlijkheid, geheugen,
eigen initiatief en zelfstandig werk. Minder tokens is een mogelijke opbrengst,
geen hoofddoel en geen vervanging voor een beter gebruikersresultaat.

Kies vanuit een echt gebruikstekort welke algemene werkwijze Eva kan verbeteren.
Zij onderzoekt varianten van haar aansturing, bijvoorbeeld context, planning,
uitvoering of controle, met de bestaande programmeer-, vergelijkings- en
toepassingsroute. Geen voorgebouwde functie per voorbeeld en geen nieuw platform.
Beoordeel op vooraf vastgelegde taken eerst resultaatkwaliteit en zelfstandigheid;
registreer ook menselijke hulp, fouten, snelheid en totaal verbruik. Voor praten
blijven herkenbaarheid, passend gedrag en gezinsbeoordeling afzonderlijk zichtbaar.
Voor ondernemen tellen onderbouwde kansen en bruikbaar uitgevoerd werk; een voorstel
of voorspelling bewijst geen werkelijke opbrengst.

Kosten blijven beheersbaar binnen het bestaande mandaat en vastgelegde proefbudget.
Een goedkopere variant die slechter werkt is geen verbetering. Meer verbruik kan
verantwoord zijn als de vooraf gekozen beoordeling aantoonbaar betere resultaten
onderbouwt; het is geen onbegrensd mandaat. Alleen onafhankelijk gemeten voordeel
toepassen, met behoud van privacy, bevoegdheden en stoppen.

Deze verduidelijking verandert geen afgesloten proef, afgeschermde testgegevens,
bestaande toetsregels, taak-ID's of acceptaties. De contextverbetering van 3 oktober
is één beperkte, onafhankelijk beoordeelde toepassing; geen bewijs dat Eva op al
deze gebieden beter is of dat de volledige herhaalde AIDE²-cyclus klaar is.

## Eigenaarprioriteit 5 oktober 2026 — eerst Eva afmaken, spraak later

Maak eerst de vier bestaande huisgenootuitkomsten af: persoonlijkheid, geheugen,
eigen initiatief en zelfstandig werk. Geen verdere spraakbouw, speakerinstallatie
of fysieke spraakproef in deze fase. Bestaande werking, gegevens en acceptaties
blijven behouden; open spraaktaken zijn geparkeerd, niet voltooid.

Vóór verdere bouw toont de bestaande vergelijking waar Eva2 Eva1/concurrenten
werkelijk overtreft en waar niet. Enkele geheugenvoordelen zijn gemeten; algemene
voorsprong niet. XD01/XD02-combinatiewerk is niet afgemaakt. Zie
eva2/evidence/product/p2-prebuild-decision-20261005.json.
Gebruik bestaande bronnen/resultaten; geen nieuwe brede audit of algemene herbouw.
Verklaar een concreet uitvoeringstekort en kies de kleinste voldoende verbetering
of aantoonbaar betere bestaande bouwsteen vóór bouwen. Geen toekomstige superioriteit
als feit, nieuwe taak-ID's, veranderde toetsregels of voorgebouwde voorbeeldfunctie.
Originele eindvoorwaarden, acceptaties, AIDE²/R1 en gezinsbeoordeling blijven bewaard.

## Eigenaarmandaat 3 oktober 2026 — zelfstandig browsergebruik en installatie

Jaap heeft beide voorgelegde wijzigingen expliciet toegestaan: de bestaande
programmeerhost als root-systeemdienst voor onafhankelijk beoordeelde toepassing,
en de browsergateway aan het bestaande uitgaande netwerk van haar module. Eva
mag zelfstandig haar browser starten en gebruiken zonder iedere keer toestemming
te vragen. De eerder vastgelegde grenzen voor werkelijke handelingen blijven
onderdeel van haar bestaande mandaat; browsertoegang is geen nieuwe keuzestap.

## Eigenaarverduidelijking 3 oktober 2026 — zelfstandig browsergebruik

Eva gebruikt voor gewone browseropdrachten zelfstandig haar eigen reeds beschikbare
browser. Jaap hoeft daarvoor geen persoonlijk Chrome-tabblad te delen of eerst
dashboardknoppen te bedienen. Persoonlijke Chrome-bediening blijft een optionele
extra mogelijkheid wanneer de eigenaar juist zijn tabblad wil laten gebruiken.
Bestaande installatie, browsercontroller en algemene uitvoering hergebruiken;
geen nieuwe browserlaag. Een echte handmatige overname of ingetrokken toegang
blijft gerespecteerd. Ontbrekende persoonlijke aanmelding vraagt alleen de werkelijk
noodzakelijke hulp. De gedeelde Chrome-acceptatie en T3.2-uitkomst blijven behouden;
een persoonlijke overnameproef blokkeert zelfstandig browsergebruik niet.

## Eigenaarbevestiging 2 oktober — AIDE² en gemeten zelfverbetering

AIDE/AIDE²-onderzoek en toepassing blijven expliciet onderdeel van **L3**, in
samenhang met de bestaande innovatieproef R1. AIDE vergelijkt taakprogramma’s;
AIDE² onderzoekt ook verbeteringen aan de aansturing van de onderzoeksagent zelf.
Voor Eva gaat het dus ook om haar eigen werkwijze, bijvoorbeeld contextkeuze,
plannen, hulpmiddelen kiezen en resultaten controleren; geen verplichte aparte
bouwtaak voor ieder voorbeeld en geen training van nieuwe modelgewichten.

Gebruik het [vastgelegde onderzoek](experiments/aide-20260929/research.md) en de
bestaande uitvoerings-, kandidaat-, beoordelings- en installatieroutes. Leg vooraf
referentie, gebruikstekort, ontwikkeltaken, onafhankelijke eindbeoordeling en
experimentverbruik vast. De verbeterende agent kan beoordelingsregels en
beschermde testgegevens niet aanpassen. Vergelijk resultaatkwaliteit, menselijke
hulp, snelheid en totaal verbruik met dezelfde model/providerbasis; privacy,
bevoegdheden en stoppen blijven voorwaarden. Alleen aantoonbare winst toepassen.

De eerste concrete toepassing blijft de bestaande taakcontextselectie binnen L3;
geen nieuw optimalisatieplatform of onderbreking van bruikbare opleveringen.
De oorspronkelijke proef met twee providerfouten blijft afgesloten. De geïnstalleerde
programmeerwerkruimte en de geholpen 71/90→90/90-codecorrectie bewijzen geen
AIDE²-zelfverbetering. Onderzocht, kandidaat gemaakt, onafhankelijk beter bevonden
en werkelijk toegepast blijven afzonderlijk zichtbaar. Zelfverbetering moet later
merkbaar beter Eva-gedrag opleveren; herhaalde autonome verbetering is nog niet bewezen.

## Eigenaarverduidelijking 2 oktober — eerst een zich ontwikkelende huisgenoot

Eva begint met gewone chat en enkele optionele installatiekeuzes. Zij leert mensen,
voorkeuren en haar toegestane omgeving kennen, onthoudt antwoorden, vormt eigen
bedoelingen, onderzoekt mogelijkheden, doet voorstellen en maakt nuttig werk af.
Zij komt later zelf terug op wat speelt en gebruikt feedback bij volgende keuzes.
Zelf programmeren en uitbreidingen toevoegen dienen die betrokkenheid; een
pluginplatform of kanaalwissel is niet het primaire productdoel.

Elke persoon en ieder huishouden heeft eigen doelen, voorkeuren en hulpbehoeften.
Eva leert die geleidelijk uit gesprekken, correcties en toegestane context; geen
verplichte uitgebreide configuratie vooraf. De dochter ondersteunen bij huiswerk,
Jaap helpen bij crypto-onderzoek, e-mail, post, afval of een wekker zijn voorbeelden
van algemene persoonsgebonden ondersteuning, geen nieuwe vooraf te programmeren
functies. Zij verbindt een waarneming met het doel en de omstandigheden van de
betrokken persoon, betrekt relevante vervolgvragen en ontbrekende factoren en
handelt zelfstandig binnen gemaakte afspraken. Alleen daadwerkelijk ontbrekende
keuze of toegang vraagt hulp. Een agenda-afspraak en reistijd zijn bijvoorbeeld
niet automatisch een complete wekadviesberekening: voorbereiding en persoonlijke
gewoontes kunnen ook relevant zijn. Persoonskennis en privé-inhoud blijven gescheiden;
aandacht voor het ene gezinslid maakt diens informatie niet beschikbaar aan anderen.

Hergebruik de bestaande geheugen-, initiatief-, intentie- en uitvoeringsroutes.
Eva2 blijft de integratiebasis; de vergelijking onderbouwt nu geen migratie van het
gehele systeem. Hermes is alleen een vervangingskandidaat bij een concreet aangetoond
voordeel, Agent Zero een bron van relevante bestaande zelfbouw-/beheermechanismen.
De eerstvolgende complete uitkomst is I1/M3: een zelfgekozen, werkelijk uitgevoerde
gezinsbijdrage met latere voortzetting/leren via dezelfde Eva, ook vanuit gewone
chat. Los een noodzakelijke uitvoeringsafhankelijkheid gericht op. L3/O1 sluiten
ontbrekende uitvoering/uitbreiding aan wanneer de gekozen behoefte dat vereist;
geen verplichte nieuwe platformronde of volledig zelfprogrammeren vóór initiatief.

Een gevonden robotstofzuiger, camera, speaker of HA-installatie is een voorbeeld
om haar algemene waarnemen, onderzoeken en combineren te toetsen. Geen vooraf
gebouwde stofzuiger-, beveiligings- of andere voorbeeldfunctie. Beschikbaarheid
van hardware bewijst geen bruikbare API. Verkenning en uitbreiding volgen bestaande
toegang en mandaat; echte persoons-/account-/fysieke handelingen vragen concrete
hulp nadat Eva het overige werk heeft voorbereid. De standaardchat maakt eerste
contact mogelijk; andere kanalen zijn optioneel, dezelfde identiteit en bevoegd
geheugen blijven gelden. Jaaps reeds gevraagde mogelijkheden blijven behouden.
Voor Jaap blijft full-duplex spraak via de speakers een gewenste oplevering.
Voor andere huishoudens is dit een keuze; optioneel betekent niet dat Eva de
mogelijkheid niet hoeft te kunnen realiseren en onderhouden wanneer die gewenst is.

Reflectie, vooruitdenken en “dromen” verwerken echte ervaringen tot lessen en
te toetsen ideeën; geen gefingeerde herinneringen of voortdurend modelverbruik
om activiteit te tonen. Een stabiele uitvoerbasis en hergebruik blijven nodig:
alles opnieuw genereren is niet automatisch slimmer. De vier productuitkomsten
zijn persoonlijkheid, geheugen, eigen initiatief en zelfstandig werk. De eerdere
onderzoeks-/planopdracht was op zichzelf geen functieoplevering. Jaap heeft de
uitvoering op 2 oktober hervat; het bijgewerkte app-goal is nu actief. Bestaande
eindvoorwaarden, acceptaties en gegevens blijven staan.


## Eigenaarcorrectie 2 oktober 2026 — Eva moet zelf kunnen bouwen en bijdragen

De hoofdprioriteit is een zelfstandige digitale huisgenoot die zelf onderzoekt,
programmeert, mogelijkheden toevoegt, aanpast en verbetert, haar werk controleert
en werkelijk bruikbaar oplevert. Warmte, humor, eigen oordeel, gezinskennis en
nuttig initiatief horen bij datzelfde product. Spraak is een ondersteunende
mogelijkheid, geen verplichte eerste bouwfase. Ook “regel voice” is een voorbeeld
van een opdracht die Eva uiteindelijk zelf moet kunnen uitvoeren; geen opdracht
om opnieuw een aparte spraakfunctie voor te programmeren.

Beoordeel alle bestaande taken op het onderscheid tussen algemene mogelijkheden
die Codex moet aansluiten en concrete opdrachten die Eva vervolgens zelf uitvoert.
Codex levert de ontbrekende programmeerwerkruimte, toegelaten hulpmiddelen en
uitvoering-/review-/installatieovergangen met bestaande voorzieningen. Eva kiest
binnen haar mandaat een aanpak, hergebruikt geschikte oplossingen, schrijft de
benodigde wijziging, voert die uit, controleert het echte resultaat en leert ervan.
Een door Codex geschreven productwijziging of opgeslagen codevoorstel bewijst deze
zelfstandigheid niet. Gebruik de bestaande gedelegeerde uitvoering; geen nieuw
agentplatform of aparte uitvoerroute per voorbeeld.

Computerbediening, browser, bestanden, Home Assistant, camera, agenda en spraak
zijn voorbeelden van uitbreidingen die een gebruiker kan willen, geen verplichte
standaardbundel voor iedere Eva-installatie. De vaste basis bevat de gedeelde
identiteit, het geheugen, initiatief, taakuitvoering en de minimale werkruimte
waarmee Eva benodigde mogelijkheden kan ontdekken, toevoegen, uitvoeren en beheren.
Per behoefte gebruikt Eva eerst beschikbare middelen, onderzoekt bestaande
oplossingen, sluit een passende uitbreiding aan of maakt zelf wat ontbreekt.
Zij controleert het bruikbare resultaat en bewaart een herbruikbare werkwijze
wanneer dat helpt. Ondersteuning mag dus niet afhangen van een vooraf door Codex
geprogrammeerde koppeling voor iedere dienst of soort opdracht. Algemene middelen
om uitbreidingen te realiseren blijven wel nodig; een prompt alleen levert die niet.
Niet-gebruikte uitbreidingen hoeven niet geïnstalleerd of actief te zijn en kunnen
weer worden uitgeschakeld. Reeds gevraagde en werkende mogelijkheden in Jaaps eigen
installatie blijven behouden. Dit verduidelijkt de bouwgrens; geen nieuwe taak-ID,
verzonnen afronding, verwijderronde of verruiming van het bestaande mandaat.

Eerstvolgende bestaande uitkomst is L3.2/L3.3: van een gewone programmeeropdracht
naar een beoordeelde, werkende eigen verbetering. Controleer eerst gericht welke
algemene programmeeruitvoerder werkelijk toegelaten en aangesloten is; een eventuele
dynamische MCP-uitvoerder is nog niet vastgesteld. Hergebruik wat aanwezig is en
sluit alleen ontbrekende overgangen aan. De huidige beperkte componentvoorstellen
plus handmatige Codex-installatie zijn hiervoor onvoldoende. Daarna algemene
huis-/dienstuitvoering en nuttig initiatief (T2, I1/I2, T3); M3 blijft verantwoordelijk
voor herkenbaar, niet-statisch gedrag. Stemwerk blokkeert deze uitvoering niet.
De actuele kleine taken bepalen concrete afhankelijkheden en afronding.

Geld verdienen hoort als mogelijke nuttige gezinsbijdrage bij initiatief: Eva kan
zelf kansen onderzoeken, werk voorstellen/uitvoeren binnen haar mandaat en werkelijke
opbrengsten tegenover kosten zetten. Dit is breder dan cryptohandel en geen
winstbelofte of nieuw mandaat voor uitgaven, transacties of verplichtingen.
Deze prioriteitscorrectie behoudt bestaande scope, bewijs en acceptaties, inclusief
de nog open spraak-/gezinsbeoordelingen. Zij voltooit geen producttaak en activeert
het gepauzeerde app-goal niet.

## Uitvoerafspraak 2 oktober 2026 — complete functies en technisch eindpunt

Het bijgewerkte autonome bouwgoal maakt alle zelfstandig uitvoerbare functies uit
het bestaande volledige plan technisch bruikbaar en geïnstalleerd. Eva1 is de
belangrijkste praktische referentie; gebruik bestaande inventarisatie en criteria,
hergebruik werkende mogelijkheden en bouw alleen voor een concreet tekort.
Geen nieuwe brede audit of algemene herbouw.

Maak één bestaande gebruikersuitkomst af: uitvoering, aansluiting, installatie en
gerichte controle samen. Alleen een aantoonbare noodzakelijke afhankelijkheid
rechtvaardigt tussenwerk. Als uitsluitend een fysieke/menselijke beoordeling
resteert, blijft die zichtbaar en volgt de eerstvolgende zelfstandig uitvoerbare
producttaak. Geen test-/installatie-/rollbacklus of wachten om actief te blijven.
Een ontbrekende woonkamerwaarneming blokkeert algemene HA-uitvoering T2 niet.

De actuele kleine taken bepalen waar hervat wordt; C1/HTTP429 uit het oude
app-goal is een historische momentopname. De volledige scope, innovatieproef,
vergelijkingscriteria, oorspronkelijke eindvoorwaarden en acceptaties blijven
behouden. Speakers en Telegram blijven primair; geen verplichte telefoonproef.

Het autonome bouwgoal heeft een technisch eindpunt: geen zelfstandig uitvoerbaar
productwerk over, afgeronde functies samen geïnstalleerd en gecontroleerd, en de
geplande eindvergelijking uitgevoerd met beschikbare vastgelegde toegang.
Uitsluitend externe toegang en menselijke beoordeling blijven zo nodig expliciet
open; gewone programmeerfouten gelden niet als externe blokkade. Open taken worden
niet fictief voltooid. Het volledige project is pas geaccepteerd na de afgesproken
vergelijking, zeven dagen gezinspraktijk en Jaaps echte eindoordeel (F2).
De goaltekst is gereed voor activering door Jaap; tekstvoorbereiding is geen functie.

## Eigenaarcorrectie 1 oktober 2026 — eerst werkend gesprek via speakers en Telegram

Speakers en Telegram zijn de primaire gebruiksroutes voor deze oplevering.
Een gesprek via het HA-paneel op telefoon of laptop is geen verplichte
gebruikersproef en blokkeert deze oplevering niet. De bestaande paneel-/webroute
blijft behouden als optionele mogelijkheid; geen nieuwe bouwronde voor die route.
Vraag geen tweede-speakerproef zolang de gewone woonkamerconversatie nog faalt.
Codex onderzoekt eerst de bestaande woonkamerfout; daarna volgt een korte fysieke
beoordeling, en pas daarna dezelfde beoordeling op de tweede speaker.
Outputmetingen en expliciet transportstoppen bewijzen geen verstaanbaar gesprek
of onderbreken met de microfoon. Bestaande acceptaties blijven behouden.

## Eigenaarverduidelijking 1 oktober 2026 — gedeelde kern en gerichte kanaalcontrole

Algemene huisbediening, automatiseringen en andere taakfuncties moeten via chat,
Telegram en spraak dezelfde uitvoering gebruiken. Beoordeel de gedeelde engine via
één passende echte geïnstalleerde route, bijvoorbeeld tekst. Hergebruik geldig,
versiegebonden bewijs; geen volledige functietest opnieuw via iedere speaker.
Controleer relevante kanaalaansluitingen afzonderlijk. Fysieke spraakproeven zijn
nodig voor verstaanbaarheid, luisteren, onderbreken en hoorbare terugmelding.
Een geslaagde tekstproef bewijst die audio-eigenschappen niet, en een ontbrekende
spraakproef blokkeert algemene kernuitvoering via tekst niet. Geen nieuwe
kanaalspecifieke planners of scripts; geen afronding door alleen taken te hernoemen.

## Eigenaarcorrectie 1 oktober 2026 — voorbeelden zijn algemene vaardigheden

Het voorbeeld van vuilnisbakken, camera en ophaalagenda is geen opdracht om een
vaste vuilnisbakfunctie of vooraf gekozen broncombinatie in Eva te bouwen. Eva
moet een gewone opdracht zelf vertalen naar bronontdekking, beeld-/agendagebruik
en een passende automatisering met de bestaande algemene hulpmiddelen. Zij kiest
relevante bronnen op basis van de opdracht en vraagt alleen door bij echte
onduidelijkheid. Een ontbrekende voorafgaande camerakeuze van Jaap blokkeert deze
algemene vaardigheid niet. Geen vraag-, apparaat- of vuilnisbakspecifieke kernregel.
I2 blijft open tot de algemene gebruikersuitkomst werkt; deze plancorrectie is
geen nieuwe afgeronde functie en wijzigt geen eerdere acceptaties.


## Eigenaarverduidelijking 29 september 2026 — sneller naar een beter geheel

- Het bestaande onderzoek naar Eva1/concurrenten bepaalt vóór bouwen de keuze
  en concrete eindvoorwaarden. Gebruik de vastgelegde bronnen, verliezen en
  beperkingen. Vul alleen een ontbrekende beslisvraag aan; herhaal geen brede
  vergelijking per oplevering. Eén geplande eindvergelijking toetst de bereikte
  voordelen. Tussentijds aanvullend vergelijken alleen bij een relevante gewijzigde
  aanname of tegenvallend resultaat. Het onderzoek bewijst nog geen algemene winst.
- Hervat bij werkelijk resterend werk. B0 en eerdere besluiten/acceptaties niet
  opnieuw uitvoeren omdat een oude app-goal dat nog noemt. De bestaande volgorde
  en echte afhankelijkheden zijn leidend: dagelijks gesprek, geheugen, stem en
  taakuitvoering vóór brede herstel-/verhuis-/beheerverbetering. Eenvoudig werk
  krijgt geen voorrang uitsluitend omdat het gemakkelijk groen te testen is.
- Koppel iedere gekozen wijziging aan een bestaande gebruikerssituatie en het
  concrete verwachte voordeel in kwaliteit, snelheid, zelfstandigheid of
  runtimeverbruik. Gebruik bestaande criteria; verzin geen nieuwe eisen of
  willekeurige getallen. Meet uitsluitend wat de verbetering en relevante
  regressies kan aantonen. Een snelle ontvangst is geen sneller inhoudelijk antwoord.
- Beschouw broncode, images, meegeleverde bestanden, configuratie, gegevensversie,
  runtime-toegang en versies tussen kern/werker als één opleverbare wijziging.
  Controleer relevante aansluitingen vroeg en lever die samen op. Dit vereist
  geen nieuw verpakkings- of testplatform. Een goed geteste losse module met
  ontbrekende installatiebestanden voldoet niet.
- Verbeter intelligent gedrag via gedeelde kern en algemene hulpmiddelen; geen
  nieuwe apparaat-/vraag-/kanaalspecifieke scripts om een demonstratie te halen.
  Behoud één identiteit, passend geheugen en taakstatus. Optimaliseer de gemeten
  vertraging/verspilling; verlaag niet blind context, modelkwaliteit of taakruimte.
  Behoud de eigenaarafspraken voor snel gespreksmodel versus onderzoeks-/werkmodel.
- Houd ontwikkelverbruik en Eva-runtimeverbruik apart. Verbeter één aantoonbare
  bottleneck zonder automatische refactor-/onderzoeksketen. Kosten zijn onderdeel
  van kwaliteit; onbewezen besparingen en 'nieuwste dus beter' zijn geen argument.
- Technische innovatie blijft onderdeel van de afgesproken scope. Beoordeel de
  bestaande hypothese met haar vooraf vastgelegde proef. Een verlies mag worden
  afgesloten, maar bewijst geen technische voorsprong en schrapt die ambitie niet.
  Start niet automatisch een eindeloze reeks alternatieve innovatieprojecten.

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

## Eigenaaropdracht 29 september 2026 — kleine, zichtbare afrondtaken

De 24bestaande hoofdopdrachten blijven de scope. De 18open opdrachten zijn in het
bestaande werkbord opgesplitst in 44resterende taken met eigen ID, status,
verantwoordelijke en eindvoorwaarde (`implementationSlices[].remainingTasks`).
Werk per kleine taak bij vóór starten en bij resultaat/blokkade. Eén bruikbare
uitkomst afronden; geen nieuwe eisen toevoegen zonder eigenaarbesluit of concrete
afhankelijkheid. Bestaand bewijs gebruiken; gerichte controle en gebundelde review,
installeren waar nodig. Geen automatische test-/rollback-/herinstallatiecyclus.
De eindkandidaat, vergelijking en gezinsacceptatie staan als expliciete taken in
dezelfde lijst. Nieuwe ideeën zijn vervolgwerk, geen stilzwijgende uitbreiding.


## Eigenaaropdracht 29 september 2026 — gerichte zelfverbetering, eerst bruikbare opleveringen

Neem de aanpak van AIDE en AIDE² op binnen L3: enkele codevarianten vergelijken,
vooraf vaste taken en harde grenzen, onafhankelijke afgeschermde eindbeoordeling,
en alleen aantoonbare winst integreren. Hergebruik bestaande kandidaat-, evaluatie-
en vrijgavevoorzieningen; geen algemeen optimalisatieplatform.
[Onderzoek, proefvoorwaarden en actuele uitkomst](experiments/aide-20260929/README.md)
zijn vastgelegd. Er is nog geen verbeterkandidaat of gemeten voordeel.

**Uitvoervolgorde:** eerst de eenvoudigste nog open bruikbare uitkomsten afmaken,
met bestaande code als vertrekpunt. AIDE-vervolg staat gepland en draait niet.
Bestaande afhankelijkheden en acceptaties blijven geldig. Een proefdocument of
bijgewerkt dashboard telt niet als afgeronde Eva-functie.


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
