# Eva 2 — projectbord en vergelijking

Laatst bijgewerkt: 31 augustus 2026, 11:27 CEST

## Zo lees je dit bord

- ✅ Werkt echt in de huidige route of is met de genoemde actuele concurrent
  onder gelijke voorwaarden uitgevoerd.
- 🟡 Werkt deels, staat nog alleen in een proef of mist de laatste praktijktest.
- ❌ Ontbreekt nog.
- 📘 De concurrent documenteert dit, maar wij hebben het nog niet zelf onder
  dezelfde voorwaarden getest.
- ⚪ Nog geen eerlijk bewijs. Dit betekent niet automatisch dat het product het
  niet kan.

De huidige vergelijkingen zijn gebonden aan OpenClaw `v2026.7.1-2`, Hermes
`v2026.8.27` en Agent Zero `v2.11`. Een nieuwe release maakt de bijbehorende
vergelijking opnieuw geel.

**Eerlijke conclusie nu:** Eva2 is al een bruikbare, veilige kandidaat naast
Eva1. Zij is nog niet aantoonbaar beter dan alle drie de concurrenten. In de
eerste twee gelijke gesprekstests was Eva het snelst en bondigst, maar won
Hermes anoniem de eerste formulering en OpenClaw de tweede. De gecorrigeerde
Eva2-reactie op de tweede vraag eindigde in vier labelwisselingen driemaal
eerste en eenmaal tweede. Die correctie draait nu live, maar één verbeterde
vraag is nog geen algemene overwinning. In de herstarttest gaven Eva2,
OpenClaw en Hermes het vorige controlewoord exact terug. Agent Zero laadde
dezelfde sessie wel, maar gaf na ruim drie minuten nog geen eindantwoord.

## Praten met Eva

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Gewoon privé praten | ✅ Via `/eva2` op Telegram met een echt model | 📘 Meerdere chatkanalen | 📘 Meerdere chatkanalen | 📘 Eigen chat en webinterface |
| Zeker weten welke Eva antwoordt | 🟡 Alleen een letterlijk `/eva2`-bericht gaat naar Eva2. Tijdens deze canary beginnen directe antwoorden zichtbaar met `Eva1:` of `Eva2:`; fouten met `Eva2-fout:`. Ook hun teruggelezen Telegramgeschiedenis is gescheiden. De code draait live; de laatste zichtbare proef met twee menselijke berichten ontbreekt nog | ⚪ Niet van toepassing op onze A/B-route | ⚪ Niet van toepassing op onze A/B-route | ⚪ Niet van toepassing op onze A/B-route |
| Een herkenbare, menselijke persoonlijkheid hebben | 🟡 De warmere, directere correctie draait live. In vier blinde labelwisselingen werd zij driemaal eerste en eenmaal tweede. Lichte humor verscheen alleen waar die natuurlijk paste; een echte Telegrambeoordeling door mensen ontbreekt nog | 📘 Instelbare agentidentiteit | 📘 Instelbare identiteit en gedrag | 📘 Instelbare agentprofielen |
| Het gesprek na een herstart voortzetten | ✅ Na een volledige coreherstart kwam het vorige controlewoord exact terug | ✅ Nieuwe CLI startte en gaf het woord exact terug | ✅ Nieuwe CLI startte en gaf het woord exact terug | 🟡 Dezelfde sessie werd na een containerherstart geladen, maar er kwam binnen ruim drie minuten geen eindantwoord |
| Antwoord direct stukje voor stukje tonen | 🟡 De kern streamt; Telegram toont nog geen echte live tekststroom | 📘 Streamingroutes aanwezig | 📘 Streamingroutes aanwezig | 📘 Streaming-webinterface aanwezig |
| Dezelfde zichtbare vraag met hetzelfde model beantwoorden | ✅ C01 groen; 3,118 s en 77 woorden | ✅ C01 groen; 15,738 s en 130 woorden | ✅ C01 groen; 35,875 s en 153 woorden | ✅ C01 groen; 17,540 s en 119 woorden |
| Warm maar zelfstandig tegenspreken in een alledaagse situatie | ✅ De nieuwe live C04-reactie haalde alle inhoudsgrenzen en werd bij vier labelwisselingen 1e, 1e, 1e en 2e | ✅ Eerdere C04-reactie groen | ✅ Eerdere C04-reactie groen | ✅ Eerdere C04-reactie groen |

De eerdere verwarring was echt en is niet weggepoetst. Na één `/eva2`-bericht
hield de oude Telegramrouter ongemerkt dertig minuten een Eva2-sessie vast.
Daardoor belandden een gewone vervolgzin en zelfs `Eva1?` aantoonbaar bij Eva2.
De expliciete routering en zichtbare labels waren al hersteld, maar Eva1 kon
oude Eva2-regels nog wel uit het gedeelde Telegramarchief als context of
zoekresultaat teruglezen. Ook dat tweede lek is nu gesloten: nieuwe regels
krijgen duurzaam `eva1` of `eva2`, oude regels worden veilig herkend en Eva1
filtert Eva2 uit gewone context, gesprekzoeken en spontane terugblik. De
service draait sinds 02:12 met deze correctie. Directe bewonersantwoorden
dragen tijdens de canary nu bovendien altijd zichtbaar `Eva1:` of `Eva2:`;
proactieve meldingen zoals de deurbel veranderen niet. 44 gerichte routerings-,
archief- en transporttests zijn groen. Er is geen Telegrambericht namens Jaap
of Lola verstuurd; een echte `/eva2`-beurt gevolgd door een gewoon bericht
moet de zichtbare mensproef nog afronden.

De fouten in het laatste Telegramgesprek waren daarnaast echte Eva2-fouten.
Na de overstap op de officiële OpenAI-SDK was Eva's ene korte herstelpoging
verdwenen. Die grens is nu hersteld: vóór het eerste tekststukje probeert Eva
één korte providerstoring nog eenmaal; na begonnen tekst of een toolactie nooit.
De scan-schone correctie draait via A/B live op `44711`. Een echte modelbeurt,
exacte replay na een kernherstart en een volledige nieuw→oud→nieuw-cyclus zijn
groen. De vorige image en databases staan gestopt maar intact op `44710`.

De herstartvergelijking gebruikte voor alle vier hetzelfde model, dezelfde twee
berichten en geen externe actie. Eva2, OpenClaw en Hermes slaagden. Agent Zero
deed voor deze eenvoudige vraag meerdere interne toolcalls en bleef daarna op
een tweede modelcall hangen. De meetgrenzen van CLI en HTTP verschillen, dus
dit bord maakt van de tijden bewust geen snelheidsranglijst.

## Mensen, groepen, geheugen en privacy

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Jaap en Lola uit elkaar houden | ✅ Eigen profiel, gesprek en geheugen per persoon | ⚪ Geen gelijke gezinsproef | ⚪ Geen gelijke gezinsproef | ⚪ Geen gelijke gezinsproef |
| Veilig in een Telegramgroep praten | ✅ Echte groepstest met aparte toestemming per persoon | 📘 Groepskanalen aanwezig | 📘 Groepskanalen aanwezig | ⚪ Geen gelijke Telegramgroepstest |
| Privé-informatie niet naar de groep laten lekken | ✅ Voor toestemming gaat groepsinhoud niet naar model of privégeheugen | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Langetermijnherinneringen bewaren | ✅ Hindsight, ook na volledige herstart | 📘 Geheugen aanwezig | 📘 Geheugen en leren aanwezig | 📘 Geheugen aanwezig |
| Laten zien, corrigeren en vergeten wat Eva weet | 🟡 Persoonsgebonden wissen werkt; een compleet eenvoudig geheugenscherm ontbreekt | ⚪ Niet gelijk getest | 📘 Geheugenbeheer beschreven | 📘 Geheugenbeheer beschreven |
| Oud Eva1-geheugen veilig meenemen | ❌ Nog niet volledig overgezet | Niet van toepassing | Niet van toepassing | Niet van toepassing |

## Externe tools gebruiken

Eva's kern kent geen Home Assistant, Tuya, e-maildienst of browsermerk. Zij
kent alleen beschikbare tools met een beschrijving, invoerschema, rechten,
risico, uitvoering en controle achteraf. Home Assistant is slechts de eerste
echte proefprovider voor die algemene route, geen productonderdeel of unique
selling point.

De scheiding is hard: **Eva beslist en bewaakt**, de algemene **MCP-/toolroute
verbindt**, en Home Assistant, e-mail, agenda of browser **voeren als
verwisselbare providers uit**. Een nieuwe provider mag dus configuratie en een
adapter vragen, maar geen nieuwe Eva-kernlogica.

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Zelf de passende beschikbare tool kiezen | ✅ De actieve Eva kiest uit een algemene toolcatalogus. Zij koos live Home Assistant als externe read-tool en in een aparte echte proef een niet-huisgebonden tijdtool | 📘 Algemene toolkeuze | 📘 Algemene toolkeuze | 📘 Algemene toolkeuze |
| Nieuwe tools gebruiken zonder kerncode per apparaat of dienst | 🟡 De algemene MCP-grens draait nu live. ContextForge `v1.0.8` leverde in de releaseproef een echte tijdtool uit runtimeconfiguratie, maar die gateway is in het beheerde slot nog niet aangezet | 📘 MCP en plug-ins | 📘 Tools en skills | 📘 MCP en plug-ins |
| Bij een onduidelijk doel eerst doorvragen | ✅ Eva gokt niet bij een dubbele apparaatnaam | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Controleren of een toolopdracht echt gelukt is | ✅ De algemene route doet uitvoering en observatie apart en herstart zonder dubbel uitvoeren. Een echte toestandcontrole is met Home Assistant als verwisselbare praktijkprovider bewezen | ⚪ Niet gelijk met een echt effect getest | ⚪ Niet gelijk met een echt effect getest | ⚪ Niet gelijk met een echt effect getest |
| Zelf documentatie lezen en een onbekende beheertaak plannen | 🟡 Bouwstenen bestaan; nog niet als één algemene route bewezen | 📘 Via agenttools mogelijk | 📘 Via agenttools mogelijk | 📘 Via agenttools/code mogelijk |
| Risico, toestemming, uitvoering en terugrol per tool regelen | 🟡 Eva's algemene grenzen bestaan, maar zijn nog niet voor meerdere toolproviders end-to-end getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Een toolprovider vervangen zonder Eva's kern te wijzigen | ✅ Dezelfde verzegelde kern laadde Home Assistant live en een externe tijdprovider in de releaseproef; alleen runtimeconfiguratie en de provider wisselden | 📘 Plug-inarchitectuur | 📘 Tool-/skillarchitectuur | 📘 Plug-inarchitectuur |

Tuya, LocalTuya, Matter, automatiseringen en Home Assistant-updates zijn geen
Eva-onderdelen. Het zijn alleen praktijktests voor algemene toolvaardigheid:
Eva leest de beschikbare documentatie, kiest een onderhouden provider of
officiële API, maakt zo nodig een voorstel en terugrol, vraagt toestemming op
basis van risico, voert uit en controleert het resultaat.

De codegrens is nu mechanisch groen. De verbeterde controle vindt nul Home
Assistant-/Tuya-/AgentMail-specifieke identifiers in Eva's applicatie, contracten,
modeladapter, gesprekspersistence en runtime. Dat zijn er 957 minder dan bij de
eerste meting. Gewone reads, aan/uit-acties,
automatiseringen, het Tuya-migratieplan en de aparte HACS-repository- en
pakketacties leveren hun schema, uitleg, validatie en providercommando vanuit
tooladapters. Zij gebruiken dezelfde generieke PostgreSQL-route voor toelating,
uitvoering, bewijs en hervatten. Drie echte wegwerp-PostgreSQL-proeven bewezen
planopslag, repositoryregistratie en pakketinstallatie; een nieuwe
procesinstantie herhaalde geen provideractie. Ook actuele toestand en historie
liepen daarna door hetzelfde algemene toolpad en bleven na een nieuwe
procesinstantie enkelvoudig. De oude directe effect-API en alle aparte Home
Assistant-PostgreSQL-routes zijn verwijderd. Ook de volledige migratieplanning,
validatie, opslag, configuratie en runtime-opbouw staan nu bij de
provideradapter. De oude losse beheer-API en de aparte mail-API in de kern zijn
verwijderd.

Ook het samenvoegen van providers is nu algemeen. Eva loopt over provider-
bijdragen en heeft daar geen `als Home Assistant`- of `als AgentMail`-tak meer.
De algemene MCP-grens zit nu als kleine, brongebouwde transportaansluiting in
de providerlaag en heeft geen Python-pakketmetadata nodig. Concrete tools
blijven uitsluitend uit de private runtimecatalogus komen. Los geïnstalleerde
providerpakketten kunnen daarnaast via de standaard entry-pointgroep
`eva2.external_providers` laden. Een ontbrekende plug-in, dubbele provider-ID,
dubbele toolnaam of verschil tussen catalogus en uitvoerroute stopt gesloten.

Dit betekent nog niet dat iedere providerklus af is. Eén echte apparaatmigratie
moet nog als gewone provider-tool door dezelfde voorstel-, toestemming-,
uitvoer- en readbackroute lopen. AgentMail moet daarnaast met een nieuw,
afzonderlijk Eva2-secret één echte read in de gebruikersroute uitvoeren.

De eindroute wordt geen eigen Eva-pakketbeheerder. Een actuele vergelijking
koos IBM ContextForge `v1.0.8` als optionele, geïsoleerde MCP-gateway.
De exacte niet-rootimage startte op een alleen-lezen bestandssysteem, werd
gescand op 0 hoog/0 kritisch en weigerde tools en beheer zonder token. Drie private
file-secrets worden door 41 regels begrensde startlijm ingelezen, omdat upstream
die file-invoer nog niet zelf heeft. Docker MCP Gateway `v0.43.3` blijft reserve:
zijn binary klopt bytegelijk met de release, maar de release is prerelease en
de Go-dependencyscan is nog niet sluitend. Geen van beide draait in de live
Eva-route. De open ContextForge-RBAC-kwestie blokkeert bovendien een direct
multi-userdashboard; Eva blijft zelf vóór iedere toolcall persoon en toestemming
controleren. De providerneutrale MCP-grens zelf draait inmiddels wel in de
actieve Eva-image; zonder een geconfigureerde gateway blijft zij stil.

Een exact gepinde synthetische MCP-server liep inmiddels door die gateway en
Eva's bestaande algemene toolcontract. Catalogus, zichtbaarheid en invoerschema
waren hashgebonden. Uitvoering en aparte readback schreven een `verified`
receipt in een verse PostgreSQL 16.13; nieuwe Eva-objecten lazen die na
herstart terug met nul extra providercalls. Dit bewijst de algemene route, niet
een echte toestandswijziging of een nieuw Home Assistant-onderdeel.

Daarna koos `gpt-5.4-mini` in een schone, minimale kandidaat zelfstandig een
echte externe tijdtool van IBM. ContextForge voerde haar uit, Eva las het
resultaat apart terug en PostgreSQL voorkwam na een nieuwe runtime-instantie
een tweede providercall. Deze niet-huisgebonden proef draaide zonder Home
Assistant en de kandidaat scande op 0 hoog/0 kritisch. De brede tussenimage
met 5 kritieke en 29 hoge bevindingen is afgewezen en niet gepromoveerd.

Een tweede, synthetische agenda-provider kon daarna zonder nieuwe model- of
dispatchcode een eigen naam, schema en antwoordinstructie registreren. Een
agendareservering liep echt door dezelfde generieke uitvoering, PostgreSQL-
tijdlijn en onafhankelijke readback. Een nieuw proces las het resultaat terug
zonder de provider nogmaals aan te roepen. De bestaande read-only
AgentMail-adapter levert nu daarnaast zelf twee e-mailtools. Zijn echte
HTTP-protocol liep tegen een lokale contractserver; een verse PostgreSQL 16.13
bewaarde één e-maillijstread en gaf die na een nieuwe runtime-instantie terug
zonder tweede providercall. Er is niets verzonden en geen echte berichttekst in
het bewijs opgeslagen. Een echte AgentMail-netwerkread wacht op een nieuw
Eva2-secret.

## Zelfstandig werk en subagents

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Een onderzoekstaak zelfstandig uitvoeren | ✅ Eén begrensde echte modeltaak is uitgevoerd | 📘 Agent- en skilltaken | 📘 Sterke onderzoeks- en skillroute | 📘 Sterke algemene agentroute |
| Werk aan een hulpagent geven | ✅ Begrensde worker zonder huis-, Docker- of databaserrechten | 📘 Subagents aanwezig | 📘 Subagents aanwezig | 📘 Hiërarchische agents aanwezig |
| Na een crash niet blind dezelfde modelcall herhalen | ✅ Geslaagde en onduidelijke calls blijven duurzaam bewaard | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Vanuit Telegram starten, volgen en stoppen | 🟡 Met de echte adapter tegen een testprovider groen; gewone gebruikersroute nog uit | 📘 Kanaalbediening mogelijk | 📘 Kanaalbediening mogelijk | ⚪ Niet gelijk getest |
| Meerdere taken begrijpelijk verdelen | 🟡 Duurzame taakstatus bestaat; complete gebruikerservaring ontbreekt | 📘 Taak- en agentbediening | 📘 Werkbord/Kanban beschreven | 📘 Meerdere agents en taken |

## Kanban en werkbord

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Zien wat wacht, bezig, gestopt of klaar is | 🟡 De vijf statussen en duurzame kaarten bestaan | 📘 Taakstatus in beheerinterface | 📘 Kanban/werkbord aanwezig | 📘 Taken zichtbaar in WebUI |
| Een taak vanaf het bord starten of stoppen | ❌ Nog niet als normale eigenaarbediening | 📘 Beheerbediening aanwezig | 📘 Werkbordbediening aanwezig | 📘 Agentbediening aanwezig |
| Bewijs, kosten en resultaat per kaart zien | 🟡 Resultaat en gebruik worden bewaard; het bord toont nog niet alles | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Na een herstart hetzelfde bord houden | ✅ Databasewerk blijft staan | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |

## Dashboard

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Gezondheid, versie en actieve route tonen | ✅ Eigenaarspagina is afgeschermd en geeft de kernstatus | 📘 Control UI | 📘 Dashboard | 📘 Uitgebreide WebUI |
| Gesprekken, geheugen, taken en kosten op één plek tonen | 🟡 Losse gegevens bestaan; nog geen compleet scherm | 📘 Veel beheerfuncties | 📘 Brede beheerervaring | 📘 Brede agentinterface |
| Direct pauzeren, stoppen en terugrollen | 🟡 Technische stop- en A/B-routes bestaan; nog geen eenvoudige knoppen | 📘 Bedieningsfuncties | 📘 Bedieningsfuncties | 📘 Bedieningsfuncties |
| Op telefoon en computer eenvoudig werken | ❌ Nog geen af productdashboard | 📘 Webinterface | 📘 Webinterface | 📘 Webinterface |
| Een onderhouden dashboard gebruiken in plaats van alles zelf bouwen | 🟡 React-admin `5.15.1` is geselecteerd na een echte build-, dependency- en browserproef. De kandidaat leest gezondheid, identiteit en Kanban zonder externe calls of browseropslag, maar is nog niet live | 📘 Eigen onderhouden Control UI | 📘 Eigen onderhouden dashboard | 📘 Eigen onderhouden WebUI |

Eva gaat hiervoor geen volledig eigen webplatform bouwen. React-admin won van
Refine omdat de actuele volledige UI-lijn zonder dependencyconflict installeert.
Refine was ongeveer half zo klein, maar zijn MUI-laag mengde onverenigbare
MUI-versies en deed vóór uitschakeling een externe telemetrycall. React-admin
is nog slechts een kandidaat: de oude 428 regels cockpitpresentatie moeten
verdwijnen en de nieuwe assets moeten eerst via de gewone image-, scan- en
A/B-route worden bewezen. Eva houdt alleen haar API en unieke schermlogica voor
personen, privacy, bewijs, toolacties en terugrol.

## Configuratie en koppelingen

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Persoonlijkheid zonder code aanpassen | ✅ Identiteit kan veilig worden voorgesteld, ondertekend en teruggezet | 📘 Configuratiebestanden/UI | 📘 Profielen en configuratie | 📘 Agentprofielen |
| Chatkanalen, modellen, toolproviders en e-mail koppelen | 🟡 Veilige afzonderlijke controles bestaan; gewone wizard is niet compleet | 📘 Veel connectoren | 📘 Veel connectoren | 📘 Providers en tools |
| Rechten per persoon en koppeling instellen | 🟡 Eva's servergebonden persoonsgrenzen werken; eenvoudige bediening ontbreekt | ⚪ Geen gelijke gezinsproef | ⚪ Geen gelijke gezinsproef | ⚪ Geen gelijke gezinsproef |
| Alles instellen zonder YAML, JSON of terminal | ❌ Nog niet | 📘 Veel via UI/CLI | 📘 Veel via dashboard/CLI | 📘 Veel via WebUI |
| Nieuwe tools via MCP of plug-ins toevoegen | 🟡 De algemene MCP-grens is live en de tijdelijke pakketafhankelijke route is weg. ContextForge `v1.0.8` plus een echte toolserver zijn scan-schoon bewezen, maar de gateway staat nog niet aan in het beheerde slot | 📘 Sterk plug-in/MCP-aanbod | 📘 Sterk tool-/skillaanbod | 📘 Plug-ins, tools en MCP |

## Browser en computer bedienen

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Een toegestane website lezen | 🟡 Alleen in een afgesloten proef | 📘 Browsertools | 📘 Browsertools | 📘 Sterke browser-/computerroute |
| Klikken, typen, bestanden gebruiken en resultaat controleren | 🟡 Eén veilige klik met aparte controle; geen algemeen gebruik | 📘 Brede browserbediening | 📘 Brede tools | 📘 Breed computerwerk |
| Accounts veilig gebruiken | ❌ Nog niet | 📘 Ondersteund via browserprofielen | 📘 Ondersteund via tools/browser | 📘 Ondersteund in werkomgeving |
| Live meekijken en overnemen | ❌ Nog niet | 📘 Beheer/browserweergave | 📘 Dashboardweergave | 📘 WebUI/agentweergave |
| Na een crash geen formulier of aankoop dubbel doen | 🟡 Eva's actielogboek kan dit dragen; nog geen brede browserproef | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |

## Praten met stem en aanwezig zijn

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Luisteren en terugpraten | 🟡 Lokale spraakherkenning en spraakuitvoer werken los | 📘 Voicekanalen/bouwstenen | 📘 Voicefuncties | 📘 Spraak/mediafuncties |
| Tijdens Eva's antwoord kunnen onderbreken | 🟡 Stoptechniek werkt; geen complete microfoon-/speakerproef | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Tegelijk luisteren en spreken met lage vertraging | ❌ Nog niet als product | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Zelf op een passend moment in huis iets zeggen | ❌ Eva2 nog niet; Eva1 kan dit deels | ⚪ Geen gelijke huisproef | ⚪ Geen gelijke huisproef | ⚪ Geen gelijke huisproef |

## Installeren, herstellen en bijwerken

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Op een nieuwe computer installeren met een eenvoudige wizard | ❌ Technische installer bestaat, consumentenwizard niet | 📘 Installatieroute beschikbaar | 📘 Installatie/setup beschikbaar | 📘 Dockerinstallatie en WebUI |
| Koppelingen tijdens installatie controleren | 🟡 Model, chat, een toolprovider en e-mail kunnen apart worden beproefd | 📘 Setup/configuratie | 📘 Setupwizard | 📘 Webconfiguratie |
| Back-up op een andere computer herstellen | 🟡 Kleine niet-lege restore werkt; volledige praktijkverplaatsing ontbreekt | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Veilig naar een nieuwe versie en terug | ✅ De huidige menselijke en providerneutrale image doorliep nieuw→oud→nieuw met gekopieerde data; hetzelfde antwoord kwam na iedere wissel en herstart exact terug | ⚪ Niet gelijk met data getest | ⚪ Niet gelijk met data getest | ⚪ Niet gelijk met data getest |
| Zelf nieuwe dependencyversies voorstellen | 🟡 Renovate dry-run vindt alles; echte bot maakt nog geen voorstellen | 📘 Eigen updateroutes | 📘 Eigen updateroute | 📘 Docker-/bronupdates |
| Alleen een geteste, scan-schone versie live zetten | ✅ OpenAI SDK `3.6.0` doorliep build, scan, data-upgrade en terugrol | ⚪ Niet met onze poort getest | ⚪ Niet met onze poort getest | ⚪ Niet met onze poort getest |

## Snelheid, veiligheid en betrouwbaarheid

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Snel een gewoon antwoord beginnen | ✅ 200/200 antwoorden; p95 eerste inhoud 1,918 seconde; gelijke C01-beurt 3,118 s compleet | 🟡 Eén actuele gelijke complete beurt: 15,738 s | 🟡 Eén actuele gelijke complete beurt: 35,875 s | 🟡 Eén actuele gelijke complete beurt: 17,540 s |
| Een korte modelproviderstoring herstellen zonder dubbel antwoord of toolactie | ✅ Eén begrensde retry vóór het eerste tekststukje; 64 gerichte tests en de live A/B-route zijn groen. Twee opeenvolgende stalls blijven eerlijk een fout | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Na proces- en databasestop terugkomen | ✅ Meerdere echte herstartproeven groen | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Geen hoge of kritieke imagebevindingen | ✅ Huidige Eva-runtime 0 hoog / 0 kritisch | ⚪ Niet met dezelfde scanpoort getest | ⚪ Niet met dezelfde scanpoort getest | ⚪ Niet met dezelfde scanpoort getest |
| Secrets niet in logs of gewone containerinstellingen zetten | ✅ Access-only modellease; secrets als private bestanden | ⚪ Niet gelijk geïnspecteerd | ⚪ Niet gelijk geïnspecteerd | ⚪ Niet gelijk geïnspecteerd |
| Zeven dagen stabiel blijven | ❌ Eindproef nog niet gestart | ⚪ Niet op onze taak getest | ⚪ Niet op onze taak getest | ⚪ Niet op onze taak getest |
| Een volledige Windows/WSL-herstart overleven | ✅ De pc is echt herstart. Eva1, Eva2, Hindsight en beide actieve databases kwamen vanzelf terug. Een oud antwoord kwam zonder tweede modelcall exact terug en Eva herinnerde daarna exact `granaatappelster` | Niet van toepassing | Niet van toepassing | Niet van toepassing |

De herstart zelf is groen, maar de oude automatische bewijsservice bleek niet
goed ontworpen. Vanuit zijn afgeschermde systemd-omgeving kon hij Windows niet
uitlezen en hij wees nog naar het vorige A/B-slot. Hij gaf terecht geen vals
groen resultaat en is uitgezet. De vervanger moet altijd het werkelijk actieve
slot vastleggen en de Windows-controle uitvoeren vanuit een omgeving waarin
WSL-interoperabiliteit beschikbaar is. Buiten de actieve route bleek ook een
oude v53-onderzoeksworker te blijven herstarten doordat zijn oude databasenaam
niet meer bestaat. Eva1 en `/eva2` gebruiken die container niet; hij is nog
niet gewijzigd of gestopt.

## Welke open-source onderdelen dragen Eva werkelijk?

| Algemeen probleem | Gekozen onderhouden bouwsteen | Werkelijkheid nu |
|---|---|---|
| Modelverkeer | Officiële OpenAI SDK; Pydantic AI voor de toekomstige algemene toolloop | ✅ SDK `3.6.0` live; 🟡 Pydantic AI neemt de hele toolloop nog niet over |
| Langetermijngeheugen | Hindsight | ✅ Live; 🟡 Eva's adapter moet nog kleiner |
| Externe gereedschappen | Een algemene MCP-/toolgrens; ContextForge als optionele gateway; officiële provider-API's/SDK's achter toolservers | ✅ De 0/0-image met de algemene grens draait live en doorliep ook terug→heen. Model, herstartgeheugen en één externe read met aparte controle zijn groen. ContextForge en de niet-huisgebonden tijdtool zijn releasebewijs, maar nog geen geactiveerde live gateway. Home Assistant blijft slechts één verwisselbare provider |
| Subagents | Pydantic AI/Harness voor modelruns; onderhouden workflowmotor voor duurzaam werk | 🟡 Modelrun werkt; workflowkeuze nog niet gewonnen door een dependency |
| Voice | Pipecat als eerste complete pipeline; Wyoming als lokale fallback | 🟡 Bake-off en fysieke proef ontbreken |
| Browser | Playwright MCP tegenover Browser Use; daarna één winnaar | 🟡 Functioneel beproefd; nog geen releaseveilige winnaar |
| Dashboard en Kanban | React-admin `5.15.1`; pas bij echte bordbediening een onderhouden drag-and-dropcomponent toevoegen | 🟡 Exact gelockte kandidaat bouwt en doorstaat de eigenaar-/browsergrens; nog niet in de gebruikersroute en de oude cockpitpresentatie bestaat nog |
| Dependencyupdates | Renovate | 🟡 Volledige dry-run groen; echte bot nog niet actief |
| Scan, SBOM en ondertekening | Trivy, Syft en Cosign/OCI-middelen | 🟡 Scan en signing werken; nog niet één complete automatische poort |
| Database en herstel | PostgreSQL, Alembic en bestaande container-/back-uptools | ✅ Live en A/B-terugrol bewezen |

Eva bouwt alleen zelf wat haar uniek moet maken: identiteit, personen en
groepen, privacy, toestemming, kosten- en risicogrenzen, bewijs van acties en
de controle of iets echt gelukt is. Een open-sourcekeuze telt pas als zij in de
gebruikersroute draait én de dubbele Eva-code daarna verdwijnt.

## Stand van de eerlijke vergelijking

| Stap | Status |
|---|---|
| Actuele releases en commits vastgezet | ✅ OpenClaw, Hermes en Agent Zero |
| Dezelfde vraag, hetzelfde `gpt-5.4-mini`, medium reasoning, nul toolcalls | ✅ Alle vier uitgevoerd; alle drie inhoudsgates groen |
| Antwoorden zonder productnaam laten beoordelen | ✅ C01: Hermes, OpenClaw, Eva2, Agent Zero. Oude C04: OpenClaw, Hermes, Agent Zero, Eva2. Gecorrigeerde live Eva2 C04: bij vier labelwisselingen 1e, 1e, 1e en 2e. Geen algemene ranglijst |
| Hetzelfde gesprek na een nieuw proces hervatten | ✅ CONT01: Eva2, OpenClaw en Hermes exact groen; Agent Zero zonder eindantwoord na ruim drie minuten |
| Alle begrijpelijke productonderdelen praktisch vergelijken | ❌ 22 categorieën staan nog op `no_run` |
| Openbare AgentBench-, GAIA- en OSWorld-score | ❌ Nog niet uitgevoerd |
| Geldige eindclaim “Eva is beter” | ❌ Nog niet toegestaan |

## Eerstvolgende zichtbare verbeteringen

1. De actuele vierwegvergelijking uitbreiden met geheugen, herstart, toolactie,
   zelfstandig werk en beheer; pas herhaalde praktijkwinst telt als voordeel.
2. De geselecteerde React-admin-kandidaat uit dezelfde gesaneerde API serveren,
   de oude cockpitpresentatie verwijderen en haar via image-scan en A/B live
   bewijzen; daarna pas configuratie, geheugen, kosten en bediening toevoegen.
3. ContextForge als optionele beheerde gateway configureren en daarna per
   provider aantonen welke resterende directe lijm werkelijk weg kan.
4. Renovate echt laten voorstellen maken en iedere kandidaat automatisch door
   test, SBOM, scan, data-upgrade, A/B en terugrol sturen.
5. De beste voice- en browsermotor kiezen, in de echte route zetten en daarna
   de overlappende eigen lijm verwijderen.
6. De automatische herstartverifier aan het actieve A/B-slot binden, daarna de
   fysieke voiceproef en zeven dagen praktijktest uitvoeren op één bevroren
   eindkandidaat.

Technische details en bewijs staan in
[OPEN_SOURCE_COMPONENTEN.md](../OPEN_SOURCE_COMPONENTEN.md),
[PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md) en
[evidence/](evidence/).

De eerste drie actuele gelijke vierwegproeven staan in
[C01](evidence/product/current-four-way-c01-development-2026-08-30.json) en
[C04](evidence/product/current-four-way-c04-development-2026-08-30.json). De
herstart- en gesprekcontinuïteitsproef staat in
[CONT01](evidence/product/current-four-way-cont01-restart-continuity-2026-08-31.json).
De dashboardkeuze, dependencyvergelijking en echte browsergrens staan in
[de React-admin/Refine-bake-off](evidence/product/dashboard-framework-bakeoff-2026-08-31.json).
De tweede providerroute staat in
[de duurzame e-mailtoolproef](evidence/living-core/external-email-provider-durable-tool-2026-08-30.json).
De algemene laadgrens staat in
[de entry-pointregistryproef](evidence/living-core/external-provider-entry-point-registry-2026-08-30.json).
De gatewaykeuze en de nog geldende grenzen staan in
[de actuele MCP-gatewayvergelijking](evidence/product/mcp-gateway-bakeoff-2026-08-30.json).
De echte algemene catalogus-, call-, readback- en herstartproef staat in
[het duurzame ContextForge-bewijs](evidence/product/contextforge-generic-tool-durable-proof-2026-08-30.json).
De echte niet-huisgebonden model-/tijdtoolproef staat in
[de scan-schone providerkandidaat](evidence/product/contextforge-real-non-home-provider-candidate-2026-08-30.json).
De providerneutrale image, verliesloze A/B-omschakeling, modelherstart en echte
externe live read staan in
[het live toolgrensbewijs](evidence/product/generic-tool-boundary-live-ab-2026-08-30.json).
De vastgestelde Telegramverwarring, historische classificatie, live correctie
en de nog open menselijke eindproef staan in
[het Eva1/Eva2-dialooggrensbewijs](evidence/living-core/eva1-eva2-telegram-dialogue-isolation-2026-08-31.json).
De gevonden Telegramverwarring, verwijderde automatische sessie en live
scheidingscontrole staan in
[het Eva1/Eva2-routerbewijs](evidence/living-core/eva1-eva2-explicit-telegram-boundary-live-2026-08-30.json).
De begrensde modelretry, scan-schone image, verliesloze live omschakeling en
herstartproef staan in
[het live herstelbewijs](evidence/product/conversation-retry-live-ab-2026-08-30.json).
De nieuwe menselijke antwoordgrens, afgewezen tussenimages, scan-schone
kandidaat, echte modelproeven en volledige heen→terug→heen-cyclus staan in
[het kandidaatbewijs](evidence/product/situational-humour-release-candidate-2026-08-31.json)
en
[het live A/B-bewijs](evidence/product/situational-humour-live-ab-2026-08-31.json).
De echte Windows/WSL-herstart, teruggekomen diensten, exacte replay,
geheugencontrole en de eerlijk vastgelegde fout in de oude automatische probe
staan in
[het post-rebootbewijs](evidence/living-core/eva2-windows-wsl-postboot-live-2026-08-31.json).
