# Eva 2 tegenover OpenClaw, Hermes, Agent Zero en OpenBot

Bijgewerkt: 26 augustus 2026.

## Het korte antwoord

**Vandaag heeft Eva 2 al een aantoonbaar sterke kern en op enkele belangrijke
punten sterke eigen bewijzen. Ze is alleen nog geen af product.**

Na vijf dagen bouwen staat er dus niet “bijna niets”. Er zijn vijftien live
kerntaken geslaagd, negen zwaardere praktijkscenario's historisch live bewezen,
een eerste echte skill van opdracht tot controle achteraf uitgevoerd en
meerdere herstelroutes gebouwd en getest. Die eerste skill gebruikt Home
Assistant, maar de belangrijke bouwsteen is providerneutraal. De algemene
actieketen doorstaat inmiddels alle 75 tests, inclusief herstarten tussen
uitvoering, teruglezen en afronden.

Wat nog ontbreekt zijn vooral de delen die van die sterke kern een compleet
dagelijks product maken: onderbreekbare voice, een publiek bruikbare
browsercomputer, de eerste eigenaar-goedgekeurde provider-/Telegramproef voor
subagents, eenvoudige installatie en het terugbrengen van Eva's spontane
aanwezigheid in huis.

**Wanneer Eva volgens onze eigen definitie klaar is: ja, voor jouw gebruik.**
Dan combineert zij in één assistent wat nu over verschillende producten is
verdeeld: jou en je gezin kennen, in huis aanwezig zijn, zelfstandig
computerwerk doen, zelf nieuwe vaardigheden maken, na storingen veilig
doorgaan en controleren of iets werkelijk gelukt is.

Dat betekent niet dat Eva op ieder los lijstje de meeste functies heeft.
OpenClaw kan bijvoorbeeld meer kant-en-klare kanalen houden en OpenBot kan een
uitgebreider los computerplatform houden. Eva is dan beter als **persoonlijke,
autonome huisassistent**, omdat al die onderdelen bij dezelfde persoon,
hetzelfde geheugen en dezelfde afspraken horen.

Belangrijk: “klaar” betekent hier niet alleen dat de code bestaat. Eva is pas
klaar nadat dezelfde taken tegen de andere agents zijn uitgevoerd, de echte
huis- en voiceproef is geslaagd en jij haar in dagelijks gebruik hebt
goedgekeurd. Tot die tijd zeggen we dus niet dat het hele product al gewonnen
heeft. Dat doet niets af aan de onderdelen die vandaag wel degelijk af en sterk
zijn.

## Wat er na vijf dagen aantoonbaar staat

Dit zijn geen wensen of ontwerpen, maar afgeronde bouwblokken met tests of echt
praktijkbewijs. “Eindversie open” betekent dat het onderdeel later nog één keer
op dezelfde definitieve versie moet worden herhaald; het eerdere resultaat
verdwijnt daardoor niet.

| Afgerond bouwblok | Wat is daadwerkelijk bewezen? | Waarom dit belangrijk of beter is |
|---|---|---|
| Live kerntaken | De bestaande core-live-reeks staat op **15 van 15** | Eva heeft al een brede eigen bewijsset; de concurrenten hebben deze volledige reeks niet doorlopen |
| Eerste complete live skill | Via Home Assistant toestand en historie lezen, apparaten bedienen, automatiseringen maken en daarna opnieuw controleren | Bewijst dat Eva een skill niet alleen kan aanroepen maar het resultaat kan teruglezen; Home Assistant zelf is verwisselbaar en geen unieke hoofdfeature |
| Persoonsgebonden geheugen | Feiten, voorkeuren en correcties blijven na herstart bewaard en zijn per persoon en project gescheiden | Dit is de basis voor één Eva die gezinsleden kent zonder hun privégeheugen door elkaar te halen |
| Duurzame doelen en herstel | Meerdere echte hervattingsscenario's zijn geslaagd; de algemene actieketen staat op **75 van 75 tests** | Eva bewaart wat al gebeurd is en voorkomt daarmee blinde dubbele acties na een storing |
| Eerlijk actiebewijs | Ontvangst, uitvoering, mislukking, teruglezing en werkelijk succes zijn afzonderlijke toestanden | Eva kan minder makkelijk ten onrechte “gedaan” zeggen dan een agent die alleen een toolantwoord vertrouwt |
| Veilige kandidaat naast productie | Back-ups, veel file-secretgrenzen, een reproduceerbare offline image en een gescheiden B-route bestaan; het huidige HA-token van B is nog een expliciete open uitzondering | We kunnen verder bouwen en terugrollen zonder de werkende oude Eva of haar gegevens zomaar te overschrijven, maar secretmigratie is nog niet compleet |
| Zelfverbetering in het klein | Eva heeft één zelfgekozen repositoryverbetering werkelijk uitgevoerd en afgerond | De eerste echte stap naar zelfgemaakte skills bestaat; alleen de automatische herhaalcyclus is nog niet af |
| Zwaardere praktijkscenario's | Negen native scenario's zijn historisch live bewezen, waaronder doelen, herstel, fan-out en zelfverbetering | Deze functies zijn meer dan losse unit-tests; de verplichte herhaling op één eindversie staat nog open |
| Eerste subagentlijn | Eva's commandolaag kan een begrensde onderzoeker starten, volgen en actief stoppen; de officiële-SDK-worker draait in een aparte kandidaatimage en dezelfde modeloproep wordt na stop of crash niet blind herhaald | Dit is een serieuze verticale productkandidaat met duurzaam Kanbanbord; de eigenaar-goedgekeurde providerproef via Telegram/B blijft open |
| Eerste browseractie | Een geïsoleerde Playwright/Chromium las één vaste lokale pagina en klikte via Eva's duurzame logboek precies één vaste knop; een aparte servercontrole bepaalde het succes | Twee providerfoutvensters veroorzaakten geen blinde tweede klik. Publieke sites, accounts, downloads en overname zijn nog niet bewezen |

De sterkste eigen bewijsbasis van de huidige Eva zit dus al in **providerneutraal
controleerbaar handelen, duurzaam geheugen en veilig herstel**. Home Assistant
is daarvan de eerste echte skill, niet het einddoel. De grootste achterstand
zit in **voice, computerbediening, subagents, installatiegemak en
productafwerking**.

## De stand van vandaag

Deze tabel gaat alleen over wat nu bestaat. Plannen tellen hier niet mee.

| Onderdeel | Wat Eva vandaag al kan | Wat nog ontbreekt | Eerlijke stand tegenover de rest |
|---|---|---|---|
| Natuurlijk gesprek | Tekstgesprek werkt; in één kleine geblindeerde proef kreeg Eva gemiddeld de hoogste score en twee van drie beste-stemmen | Meer beurten, echte Telegramacceptatie en een grotere gelijke proef | Een bemoedigende kleine winst, nog geen algemene winnaar |
| Mensen leren kennen | Feiten, voorkeuren en correcties per persoon werken en blijven gescheiden; groepsbescherming is technisch getest | Echte gezinsproef en een rijk, corrigeerbaar totaalprofiel | Eva heeft een sterke privacybasis; Hermes beschrijft de completere leercyclus |
| Skills en echte resultaatcontrole | De Home Assistant-skill kan lezen, bedienen, automatiseren en achteraf controleren | De algemene keten moet dit op de definitieve versie doen en daarna even makkelijk andere skills dragen | Sterk Eva-bewijs, maar geen eerlijke winnaar: de concurrenten kregen niet dezelfde aangesloten skillproef |
| Voice zoals ChatGPT | Dezelfde identiteit en geheugenbasis zijn beschikbaar voor de toekomstige voiceroute | Doorlopend praten, snel antwoord en onderbreken zijn nog niet gebouwd | Concurrenten hebben meer spraakbouwstenen; geen gelijke praktijktest |
| Spontaan in huis praten | De oude Eva bewijst dat de gewenste beleving mogelijk is; Eva 2 kan gebeurtenissen al veilig beoordelen | De definitieve speaker- en deurbelroute ontbreekt | De oude Eva wint vandaag op aanwezigheid |
| Zelfstandig computerwerk | Eén lokale pagina-read en één vaste browseractie via Eva's duurzame logboek en aparte servercontrole zijn technisch en onafhankelijk groen | Een afgeschermde publieke site, grants, live stop, formulieren, accounts, downloads en meekijken/overname | OpenBot en Agent Zero lopen voor als compleet computerproduct; Eva heeft nu wel haar eerste controleerbare browseractie |
| Zelf nieuwe skills maken | Eén zelfgekozen verbetering is live bewezen | Automatisch maken, vergelijken, veilig activeren en terugdraaien als vaste cyclus | Eva heeft een echt begin; Hermes loopt voor op de complete cyclus |
| Chatkanalen | Telegrambasis, meerdere accountbindings en groepsprivacy zijn gebouwd en getest | De echte groepstest en vrij toevoegbare kanalen zoals WhatsApp | OpenClaw en Hermes hebben vandaag meer kant-en-klare kanalen |
| Subagents, dashboard en Kanban | Starten, volgen en actief stoppen via Eva's commandolaag werkt; de officiële-SDK-worker draait in een aparte kandidaatimage en taak/resultaat/Kanbankaart blijven duurzaam bewaard | Eén eigenaar-goedgekeurde echte providerproef via Telegram/B, authenticatie en bordbediening | Eva heeft nu een serieuze verticale productkandidaat; Hermes loopt nog voor in de complete gebruikerservaring |
| Herstel na een crash | Echte herstelgevallen werken; uitvoering, readback en afronding overleven in de algemene keten iedere geteste procesgrens zonder dubbele call | Dezelfde proef via echt Telegram en een echt huisapparaat | **Eva heeft hier al een aantoonbare ontwerpsterkte; een gelijke concurrentenproef ontbreekt** |
| Installeren en bijwerken | Reproduceerbare images, offline build, back-up en A/B-scheiding bestaan | Een gewone installatie- en koppelwizard | De betrouwbare onderlaag is sterk; concurrenten zijn makkelijker te starten |
| Snelheid en kosten | Eva was in twee kleine zichtbare vergelijkingen het snelst en geheugencontext is begrensd | Eindmeting voor tekst, voice, tokens, kosten en zeven dagen gebruik | Voorlopig gunstig, maar nog te klein voor een eindclaim |

De conclusie van vandaag is dus niet “er is bijna niets af”. De eerlijke
conclusie is: **Eva heeft al een sterke kern met een duidelijke ontwerpsterkte
in actiecontrole, geheugen en herstel. Home Assistant is één verwisselbare skill
die dit zichtbaar maakt. Of dit beter is dan de rest moet nog in een gelijke
proef worden bewezen; bovendien ontbreken nog enkele grote gebruikersfuncties.**

## Het eindbeeld: waarom de afgeronde Eva beter is voor jou

Deze tweede tabel gaat alleen over de afgeronde Eva. Een rij telt pas als
“klaar” nadat het bewijs in de laatste kolom er werkelijk is.

| Wat de afgeronde Eva doet | Waarom dit beter is voor jouw gebruik | Verplicht bewijs voordat we “beter” zeggen |
|---|---|---|
| Dezelfde Eva praat via tekst, voice en het huis | Je krijgt niet drie losse bots met verschillend geheugen en gedrag | Jij verkiest haar in blinde gesprekken en accepteert tien minuten onderbreekbare voice plus zeven dagen huisgebruik |
| Zij kent ieder gezinslid afzonderlijk | Voorkeuren helpen het gesprek, terwijl privézaken niet bij de verkeerde persoon of groep belanden | Echte meerpersoons- en groepstest zonder privélek, ook na correctie en herstart |
| Zij gebruikt apparaten, diensten en een computer via verwisselbare skills | Eén opdracht kan van gesprek naar huisactie, website en document lopen zonder van assistent te wisselen of de kern te verbouwen | Dezelfde meerstapstaak slaagt via meerdere adapters met echte controle van de eindtoestand |
| Zij zegt alleen “gedaan” als het controleerbaar waar is | Je hoeft een stoere succesmelding niet zelf na te lopen | Echte huis- en computertaken waarin ontvangst, mislukking en werkelijk resultaat aantoonbaar uit elkaar blijven |
| Zij gaat na een crash veilig verder | Een storing veroorzaakt geen dubbele melding, huisactie, bestelling of betaling | Crashes vóór en na een echte actie, zonder blind herhalen en met een eerlijk eindantwoord |
| Zij maakt zelf ontbrekende kleine skills | Eva zit niet vast aan een skillstore of aan wat wij vooraf hebben ingebouwd | Nieuwe skill wordt gemaakt, apart getest, veilig aangezet en automatisch teruggedraaid wanneer zij slechter werkt |
| Zij kan werk verdelen over subagents | Onderzoek, uitvoering en controle kunnen tegelijk gebeuren zonder dat jij losse agents hoeft te managen | Eén taak met meerdere workers, duidelijk Kanbanbord, gecontroleerde overdracht en herstel na uitval |
| Zij werkt zelfstandig binnen één afspraak | Je geeft doel, grenzen, tijd en eventueel budget één keer; normale tussenstappen vragen niet steeds opnieuw toestemming | Een echte lange taak blijft binnen de afspraak, is direct te stoppen en legt gevolgen en kosten helder vast |
| Zij blijft snel, klein en betaalbaar | Een slimme assistent voelt alleen natuurlijk als antwoorden niet steeds trager en duurder worden | Gemeten tekst- en voicetijden, token- en geheugenplafonds en zeven dagen zonder oplopende vertraging |
| Zij is begrijpelijk te installeren en beheren | Eva wordt een product dat je kunt verplaatsen en herstellen, geen eenmalig serverproject | Verse installatie via gewone vragen, koppelingen, backup, update en rollback op een andere computer |

**Daar zit de echte voorsprong:** andere agents kunnen één of meer losse
onderdelen even goed of beter houden. De afgeronde Eva wint voor jouw gebruik
doordat zij deze onderdelen als één herkenbare, lerende en controleerbare
huisassistent combineert.

Het eindoordeel in één oogopslag:

- **Beste complete product vandaag:** niet Eva 2.
- **Beste beoogde persoonlijke huisassistent voor jou:** de afgeronde Eva.
- **Beste agent voor iedere denkbare gebruiker en iedere losse functie:** dat
  claimen we niet.

## Waarom Eva anders moet worden dan de rest

De concurrenten hebben sterke onderdelen. Eva hoeft die niet allemaal opnieuw
te bouwen. Haar voordeel moet de **combinatie** zijn:

1. **Eén persoon, geen verzameling bots.** De Eva in Telegram, voice, het huis
   en de browser heeft dezelfde identiteit en hetzelfde toegestane geheugen.
2. **Een echte huisgenoot.** Ze wacht niet altijd op een prompt, maar kiest
   soms zelf een passend moment en blijft stil wanneer spreken niet helpt.
3. **Handelen zonder stoerdoenerij.** “Gedaan” betekent dat Eva het resultaat
   opnieuw heeft bekeken. Alleen een technische ontvangstmelding is niet genoeg.
4. **Zelfstandig binnen jouw afspraak.** Je geeft doel, grenzen en stopregels
   één keer aan. Binnen die afspraak mag Eva doorwerken zonder bij iedere klik
   opnieuw toestemming te vragen.
5. **Leren zonder los te slaan.** Eva mag mensen beter leren kennen en zelf
   skills maken, maar niet ongemerkt meer rechten nemen of onbeperkt code,
   geheugen en kosten opstapelen.
6. **Herstellen zonder dubbel effect.** Na een crash zoekt Eva eerst uit wat al
   gebeurd is. Ze drukt niet voor de zekerheid nog een keer op dezelfde knop.

Als één van deze zes punten ontbreekt, is Eva misschien een goede agent, maar
niet de betere levende assistent die we hier proberen te bouwen.

## De concurrenten in gewone taal

| Product | Waar het vandaag sterk in is | Wat Eva daarvan nodig heeft | Waar Eva uiteindelijk beter moet zijn |
|---|---|---|---|
| **OpenClaw** | Veel kant-en-klare chatkanalen en aansluitingen voor apparaten | Bestaande aansluitingen mogen losse handen van Eva worden | Eén herkenbare persoon en één geheugen, plus controle na iedere echte actie |
| **Hermes** | Veel hulpmiddelen en kanalen; leert mensen kennen; maakt skills; gebruikt subagents en een werkbord | Bruikbare losse hulpmiddelen mogen worden hergebruikt | Dezelfde groei combineren met gezinsprivacy, huisaanwezigheid en veilig herstel na een storing |
| **Agent Zero** | Sterk in websites, computerprogramma's, bestanden en subagents | Een afgeschermde computer kan als verwisselbare uitvoerder dienen | De computer blijft een hand van Eva, niet een tweede assistent met een eigen waarheid of geheugen |
| **OpenBot** | Eigen browsercomputer per bot; je kunt live meekijken en overnemen | De browserwerkplek is een goede referentie of mogelijke losse uitvoerder | Computerwerk verbinden met Eva's huis, relaties, initiatief, lange doelen en gecontroleerde resultaten |
| **Oude Eva** | Voelt al aanwezig door spontaan spreken via deurbel en speakers | Die beleving moet behouden blijven | Dezelfde aanwezigheid combineren met beter geheugen, privacy, herstel en controleerbare acties |

## Wat Eva wel en niet hergebruikt

Eva hoeft niet alles zelf te bouwen. Ze mag een bestaande aansluiting voor een
chatkanaal of apparaat gebruiken, een bestaande afgeschermde browser inzetten
en een extra geheugenhulpmiddel toevoegen wanneer metingen aantonen dat dit
echt helpt.

Daar is nu een harde volgorde aan gekoppeld. Voice begint bij Pydantic AI
Realtime, subagents bij het compatibele Pydantic AI Harness, browserwerk bij
Microsoft Playwright MCP, duurzame scheduling bij DBOS, nieuwe kanalen bij een
gepinde OpenClaw-plugin of officiële SDK en geheugenbeheer bij Hindsight's
bestaande Control Plane. Renovate, Trivy, SBOM- en signingtools doen updates en
scans; Eva bouwt daarvoor geen eigen platform. Pas wanneer zo'n afgeschermde
proef de gebruikersuitkomst, privacy, snelheid of herstelgrens niet haalt, mag
er gerichte eigen code bijkomen.

Dit is inmiddels meer dan een lijstje links. De officiële OpenAI-SDK neemt in
een gereviewde kandidaat al één handgeschreven transportroute over. Harness en
de publieke Pydantic-runner voeren in een tweede kandidaat echte toolvrije
subagents uit; 45 tests bewaken context, tools, calls, tokens en time-out. De
eerste providerloze lijn is daarna uitgebreid met duurzame opslag, commandostart,
volgen, actieve stop, een aparte officiële-SDK-workerimage en het werkbord. De
officiële MCP-SDK is juist na twee echte proeven afgewezen omdat zij voor Eva's
smalle route meer packages en meer code opleverde. HA-MCP is gestart en diep
vergeleken, maar een directe wissel bespaart nog te weinig code. Geen van deze
nieuwe kandidaten is stil in productie gezet.

Die onderdelen zijn alleen haar handen en hulpmiddelen. Eva besteedt nooit uit
wie zij is, wie zij voor zich heeft, wat privé is, welke afspraak geldt, welke
taak loopt, of iets echt gelukt is en wat zij uiteindelijk aan jou antwoordt.
De precieze namen, versies en veiligheidsregels staan in het technische
bewijsbord.

Concreet betekent dit dat we de volgende grote stukken **niet opnieuw vanaf nul
bouwen**:

| Nodig voor Eva | Bestaand communityproject dat eerst de kans krijgt | Wat Eva zelf nog doet |
|---|---|---|
| Live voice | Pydantic AI Realtime `2.34.0` | De juiste persoon en herinneringen koppelen, onderbreken echt laten stoppen en huisacties controleren |
| Subagents | Pydantic AI Harness `0.25.0` plus de officiële-SDK-worker; starten, volgen, actief stoppen, aparte kandidaatimage en duurzame Kanbankaart zijn groen | Eigenaar-goedgekeurde providerproef via Telegram/B, authenticatie en productpromotie |
| Browsercomputer | Microsoft Playwright MCP `0.0.79`; lokale read en één vaste actie via Eva's logboek zijn onafhankelijk groen. Harness-browser blijft alleen een wegwerpvergelijking | Publieke websites en bestanden afschermen, direct stoppen, meekijken/overnemen en het eindresultaat controleren |
| Langlopende achtergrondtaken | Huidige DBOS-route; eerst een kleine rebase naar `2.31.0+eva.1` | Betekenis van de taak, toestemming en wat te doen na een onduidelijke externe actie; stock 2.31 is hiervoor afgewezen |
| Geheugenscherm | De bestaande lokale Hindsight Control Plane | Bepalen van wie een herinnering is en wie haar mag zien, corrigeren of vergeten |
| Dashboard en Kanban | React-Admin OSS plus `dnd-kit` | Alleen Eva's gesaneerde API tonen; iedere bordwijziging blijft een gecontroleerde Eva-opdracht |
| Home Assistant-hulpmiddelen | De ingebouwde HA MCP Server en eventueel HA-MCP `8.3.0` | Veilige doelkeuze en onafhankelijke controle achteraf; HA-MCP vervangt de huidige route pas bij semantische dekking, duidelijk eigenaarschap en verwijdering van de overlappende oude route |
| Updates en beveiligingsscan | Renovate, Trivy en bestaande OCI-/SBOM-middelen | Alleen geteste upgrades promoveren en altijd kunnen terugrollen |

Een dependency wint dus niet op sterren of een mooie demo. Zij wint pas wanneer
dezelfde Eva-taak slaagt, zij een duidelijke verantwoordelijkheid overneemt en
onverklaarde dubbele routes verdwijnen. De eerste diepe HA-MCP-controle is daar
een goed voorbeeld van: de geschatte 174–192 vervangbare regels zijn alleen een
observatie. De omschakeling wordt beslist op semantische dekking, beheerlast,
actief upstreamonderhoud, eigenaarschap en tests — nooit op dat aantal regels.

## Wanneer we eerlijk mogen zeggen dat Eva beter is

We mogen niet zeggen “Eva is beter” omdat haar ontwerp uitgebreider klinkt. De
claim mag pas na deze vier soorten bewijs:

1. **Dezelfde openbare tests:** AgentBench, GAIA en OSWorld met de officiële
   scorer, dezelfde release en heropenbare resultaten.
2. **Dezelfde concurrentietaken:** Eva, OpenClaw, Hermes, Agent Zero en OpenBot
   krijgen hetzelfde model, budget, gereedschap en dezelfde taak.
3. **Echt dagelijks gebruik:** zeven dagen met echte gesprekken, huisgebeurtenissen,
   voice, computerwerk, stiltemomenten, crashes en herstel.
4. **Jouw oordeel:** jij moet Eva natuurlijk, snel, behulpzaam en herkenbaar
   vinden. Een technische groene vink kan dat niet vervangen.

Tot die tijd is de eerlijke formulering:

> Vandaag is Eva 2 nog niet de beste complete agent. Als al het verplichte
> vergelijkings-, huis- en gebruikersbewijs slaagt, is zij klaar en mogen we
> zeggen dat zij voor jouw persoonlijke huisassistent de betere keuze is.

## Waar de technische details staan

Deze pagina blijft bewust leesbaar. De controleerbare details zijn niet
verdwenen:

- volledige gates, checkpoints en wat nog niet bewezen is:
  `TECHNISCHE_PRODUCTGATES.md`;
- wat daadwerkelijk af is, alle hoofdprojecten en de eerlijke resterende
  ramingen: `PROJECTEN_EN_PLANNING.md`;
- wat vandaag echt werkt en wat ontbreekt: `EVA2_MOGELIJKHEDEN_EN_GATEN.md`;
- oude Eva tegenover Eva 2: `OUDE_EVA_VS_EVA2.md`;
- technische architectuur: `design/architecture/living-core-v1.md`;
- open-sourcekeuzes, pins, toelatingsregels en wat niet zelf wordt gebouwd:
  `OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`;
- benchmarkregels: `design/product/comparable-agent-benchmarks-v1.md`;
- actuele voortgang: `PROJECT_STATUS.md`;
- onafhankelijke beoordelingen en echte bewijzen: `reviews/` en
  `evidence/living-core/`.
