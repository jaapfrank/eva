# Eva 2 — projectbord en vergelijking

Laatst bijgewerkt: 30 augustus 2026, 19:31 CEST

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
Eva1. Zij is nog niet aantoonbaar beter dan alle drie de concurrenten. Twee
nieuwe gelijke gesprekstests zijn uitgevoerd. Eva was beide keren het snelst en
bondigst. Hermes won anoniem de eerste formulering; OpenClaw de tweede. Eva werd
in de tweede test vierde en haar humor was niet merkbaar. Een geïsoleerde
kandidaatcorrectie liet daarna wel lichte humor zien, maar is nog niet live en
telt dus nog niet als gewonnen vergelijking.

## Praten met Eva

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Gewoon privé praten | ✅ Via `/eva2` op Telegram met een echt model | 📘 Meerdere chatkanalen | 📘 Meerdere chatkanalen | 📘 Eigen chat en webinterface |
| Een herkenbare, menselijke persoonlijkheid hebben | 🟡 Warme, jonge en eigenwijze identiteit staat; in C04 was haar humor nog niet zichtbaar. Een geïsoleerde correctie werkt, maar is nog niet live of blind herbeoordeeld | 📘 Instelbare agentidentiteit | 📘 Instelbare identiteit en gedrag | 📘 Instelbare agentprofielen |
| Het gesprek na een herstart voortzetten | ✅ Gesprek blijft in PostgreSQL staan | ⚪ Nog niet gelijk getest | ⚪ Nog niet gelijk getest | ⚪ Nog niet gelijk getest |
| Antwoord direct stukje voor stukje tonen | 🟡 De kern streamt; Telegram toont nog geen echte live tekststroom | 📘 Streamingroutes aanwezig | 📘 Streamingroutes aanwezig | 📘 Streaming-webinterface aanwezig |
| Dezelfde zichtbare vraag met hetzelfde model beantwoorden | ✅ C01 groen; 3,118 s en 77 woorden | ✅ C01 groen; 15,738 s en 130 woorden | ✅ C01 groen; 35,875 s en 153 woorden | ✅ C01 groen; 17,540 s en 119 woorden |
| Warm maar zelfstandig tegenspreken in een alledaagse situatie | 🟡 C04 inhoudelijk groen en 2,368 s, maar anoniem vierde en zonder herkenbare humor | ✅ C04 groen; anoniem eerste | ✅ C04 groen; anoniem tweede | ✅ C04 groen; anoniem derde |

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

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Zelf de passende beschikbare tool kiezen | 🟡 Met één echte MCP-provider bewezen; nog niet over meerdere soorten tools | 📘 Algemene toolkeuze | 📘 Algemene toolkeuze | 📘 Algemene toolkeuze |
| Nieuwe tools gebruiken zonder kerncode per apparaat of dienst | 🟡 Eén dynamische toolcatalogus werkt; de algemene plug-inroute is nog niet compleet | 📘 MCP en plug-ins | 📘 Tools en skills | 📘 MCP en plug-ins |
| Bij een onduidelijk doel eerst doorvragen | ✅ Eva gokt niet bij een dubbele apparaatnaam | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Controleren of een toolopdracht echt gelukt is | ✅ Met Home Assistant bewezen via aparte readback; herhalen voert niet dubbel uit | ⚪ Niet gelijk met een echt effect getest | ⚪ Niet gelijk met een echt effect getest | ⚪ Niet gelijk met een echt effect getest |
| Zelf documentatie lezen en een onbekende beheertaak plannen | 🟡 Bouwstenen bestaan; nog niet als één algemene route bewezen | 📘 Via agenttools mogelijk | 📘 Via agenttools mogelijk | 📘 Via agenttools/code mogelijk |
| Risico, toestemming, uitvoering en terugrol per tool regelen | 🟡 Eva's algemene grenzen bestaan, maar zijn nog niet voor meerdere toolproviders end-to-end getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Een toolprovider vervangen zonder Eva's kern te wijzigen | ❌ Nog niet: `application/advanced_tasks.py`, de gesprekspersistence en runtime bevatten nog expliciete Home Assistant-/Tuya-paden | 📘 Plug-inarchitectuur | 📘 Tool-/skillarchitectuur | 📘 Plug-inarchitectuur |

Tuya, LocalTuya, Matter, automatiseringen en Home Assistant-updates zijn geen
Eva-onderdelen. Het zijn alleen praktijktests voor algemene toolvaardigheid:
Eva leest de beschikbare documentatie, kiest een onderhouden provider of
officiële API, maakt zo nodig een voorstel en terugrol, vraagt toestemming op
basis van risico, voert uit en controleert het resultaat.

De huidige code voldoet nog niet volledig aan die grens. De verbeterde controle
vindt nog 910 Home Assistant-/Tuya-specifieke identifiers in acht kernbestanden.
Dat is 47 minder dan vóór de eerste verplaatsing. De vier gewone lees/aan-uit-
schemas en hun antwoordinstructies komen nu uit de tooladapter. De actuele
kandidaat-runtime registreert zowel de gewone leesacties als de aan/uit-acties via een
generieke externe-toolpoort. De centrale dispatcher kiest daarbij geen
providernaam of opdrachtsoort. De oudere directe effectfallback en de beheer-
en migratielogica staan nog wel in kernbestanden en moeten eveneens verdwijnen.

Een tweede, synthetische agenda-provider kon daarna zonder nieuwe model- of
dispatchcode een eigen naam, schema en antwoordinstructie registreren. Een
agendareservering liep echt door dezelfde generieke uitvoering, PostgreSQL-
tijdlijn en onafhankelijke readback. Een nieuw proces las het resultaat terug
zonder de provider nogmaals aan te roepen. Dit bewijst de algemene route; een
tweede echte externe dienst in de gebruikersroute ontbreekt nog.

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

Eva gaat hiervoor geen volledig eigen webplatform bouwen. De kandidaat is een
bestaand adminframework boven Eva's gesaneerde API; Eva houdt alleen de unieke
schermen voor personen, privacy, bewijs, toolacties en terugrol.

## Configuratie en koppelingen

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Persoonlijkheid zonder code aanpassen | ✅ Identiteit kan veilig worden voorgesteld, ondertekend en teruggezet | 📘 Configuratiebestanden/UI | 📘 Profielen en configuratie | 📘 Agentprofielen |
| Chatkanalen, modellen, toolproviders en e-mail koppelen | 🟡 Veilige afzonderlijke controles bestaan; gewone wizard is niet compleet | 📘 Veel connectoren | 📘 Veel connectoren | 📘 Providers en tools |
| Rechten per persoon en koppeling instellen | 🟡 Eva's servergebonden persoonsgrenzen werken; eenvoudige bediening ontbreekt | ⚪ Geen gelijke gezinsproef | ⚪ Geen gelijke gezinsproef | ⚪ Geen gelijke gezinsproef |
| Alles instellen zonder YAML, JSON of terminal | ❌ Nog niet | 📘 Veel via UI/CLI | 📘 Veel via dashboard/CLI | 📘 Veel via WebUI |
| Nieuwe tools via MCP of plug-ins toevoegen | 🟡 Eén MCP-provider is live; kernonafhankelijk toevoegen en vervangen is nog niet af | 📘 Sterk plug-in/MCP-aanbod | 📘 Sterk tool-/skillaanbod | 📘 Plug-ins, tools en MCP |

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
| Veilig naar een nieuwe versie en terug | ✅ Volledige nieuwe→oude→nieuwe A/B-proef met gekopieerde data | ⚪ Niet gelijk met data getest | ⚪ Niet gelijk met data getest | ⚪ Niet gelijk met data getest |
| Zelf nieuwe dependencyversies voorstellen | 🟡 Renovate dry-run vindt alles; echte bot maakt nog geen voorstellen | 📘 Eigen updateroutes | 📘 Eigen updateroute | 📘 Docker-/bronupdates |
| Alleen een geteste, scan-schone versie live zetten | ✅ OpenAI SDK `3.6.0` doorliep build, scan, data-upgrade en terugrol | ⚪ Niet met onze poort getest | ⚪ Niet met onze poort getest | ⚪ Niet met onze poort getest |

## Snelheid, veiligheid en betrouwbaarheid

| Wat moet dit kunnen? | Eva2 nu | OpenClaw | Hermes | Agent Zero |
|---|---|---|---|---|
| Snel een gewoon antwoord beginnen | ✅ 200/200 antwoorden; p95 eerste inhoud 1,918 seconde; gelijke C01-beurt 3,118 s compleet | 🟡 Eén actuele gelijke complete beurt: 15,738 s | 🟡 Eén actuele gelijke complete beurt: 35,875 s | 🟡 Eén actuele gelijke complete beurt: 17,540 s |
| Na proces- en databasestop terugkomen | ✅ Meerdere echte herstartproeven groen | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest | ⚪ Niet gelijk getest |
| Geen hoge of kritieke imagebevindingen | ✅ Huidige Eva-runtime 0 hoog / 0 kritisch | ⚪ Niet met dezelfde scanpoort getest | ⚪ Niet met dezelfde scanpoort getest | ⚪ Niet met dezelfde scanpoort getest |
| Secrets niet in logs of gewone containerinstellingen zetten | ✅ Access-only modellease; secrets als private bestanden | ⚪ Niet gelijk geïnspecteerd | ⚪ Niet gelijk geïnspecteerd | ⚪ Niet gelijk geïnspecteerd |
| Zeven dagen stabiel blijven | ❌ Eindproef nog niet gestart | ⚪ Niet op onze taak getest | ⚪ Niet op onze taak getest | ⚪ Niet op onze taak getest |
| Een volledige Windows/WSL-herstart overleven | 🟡 Proef staat klaar; pc-herstart ontbreekt | Niet van toepassing | Niet van toepassing | Niet van toepassing |

## Welke open-source onderdelen dragen Eva werkelijk?

| Algemeen probleem | Gekozen onderhouden bouwsteen | Werkelijkheid nu |
|---|---|---|
| Modelverkeer | Officiële OpenAI SDK; Pydantic AI voor de toekomstige algemene toolloop | ✅ SDK `3.6.0` live; 🟡 Pydantic AI neemt de hele toolloop nog niet over |
| Langetermijngeheugen | Hindsight | ✅ Live; 🟡 Eva's adapter moet nog kleiner |
| Tool- en plug-insysteem | MCP plus officiële provider-API's/SDK's achter één generiek toolcontract | ❌ Schema, antwoordinstructie en de kandidaat voor gewone lees/effectdispatch zijn generiek; er resten 910 oudere Home Assistant-/Tuya-specifieke identifiers in acht kernbestanden |
| Subagents | Pydantic AI/Harness voor modelruns; onderhouden workflowmotor voor duurzaam werk | 🟡 Modelrun werkt; workflowkeuze nog niet gewonnen door een dependency |
| Voice | Pipecat als eerste complete pipeline; Wyoming als lokale fallback | 🟡 Bake-off en fysieke proef ontbreken |
| Browser | Playwright MCP tegenover Browser Use; daarna één winnaar | 🟡 Functioneel beproefd; nog geen releaseveilige winnaar |
| Dashboard en Kanban | Bestaand adminframework plus bordcomponent | ❌ Nog niet in de gebruikersroute |
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
| Antwoorden zonder productnaam laten beoordelen | ✅ C01: Hermes, OpenClaw, Eva2, Agent Zero. C04: OpenClaw, Hermes, Agent Zero, Eva2. Geen algemene ranglijst |
| Alle begrijpelijke productonderdelen praktisch vergelijken | ❌ 22 categorieën staan nog op `no_run` |
| Openbare AgentBench-, GAIA- en OSWorld-score | ❌ Nog niet uitgevoerd |
| Geldige eindclaim “Eva is beter” | ❌ Nog niet toegestaan |

## Eerstvolgende zichtbare verbeteringen

1. De actuele vierwegvergelijking uitbreiden met geheugen, herstart, toolactie,
   zelfstandig werk en beheer; pas herhaalde praktijkwinst telt als voordeel.
2. Het bestaande dashboardframework kiezen en gezondheid, configuratie,
   geheugen, kosten en Kanban in één eigenaarsscherm zetten.
3. Alle providerlogica uit applicatie, modelroute, gesprekspersistence en
   runtime halen; daarna dezelfde algemene toolroute met meerdere willekeurige
   providers beproeven.
4. Renovate echt laten voorstellen maken en iedere kandidaat automatisch door
   test, SBOM, scan, data-upgrade, A/B en terugrol sturen.
5. De beste voice- en browsermotor kiezen, in de echte route zetten en daarna
   de overlappende eigen lijm verwijderen.
6. De pc-herstart, fysieke voiceproef en zeven dagen praktijktest uitvoeren op
   één bevroren eindkandidaat.

Technische details en bewijs staan in
[OPEN_SOURCE_COMPONENTEN.md](../OPEN_SOURCE_COMPONENTEN.md),
[PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md) en
[evidence/](evidence/).

De eerste twee actuele gelijke vierwegproeven staan in
[C01](evidence/product/current-four-way-c01-development-2026-08-30.json) en
[C04](evidence/product/current-four-way-c04-development-2026-08-30.json).
