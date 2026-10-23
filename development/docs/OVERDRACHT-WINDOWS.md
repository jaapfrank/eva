# Overdracht Eva naar de Windows-app

Deze overdracht is gemaakt op 23 oktober 2026. De actuele taken blijven uitsluitend
in `development/design/product/execution-workboard-v1.json`; dit document is de
startinstructie en wegwijzer voor de nieuwe coördinator.

## Dezelfde installatie openen

Gebruik de officiële ChatGPT Windows-app met de agentomgeving **WSL** en herstart de
app na het omschakelen. Open de bestaande map `/home/administrator/project/Eva2`
via `\\wsl$\<distributie>\home\administrator\project\Eva2`.
Verplaats of clone het project niet: de werkmap bevat belangrijke ongecommitteerde
wijzigingen en de draaiende installatie staat in deze WSL-omgeving.

Officiële uitleg: [Windows en WSL](https://learn.chatgpt.com/docs/app/windows)
en [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).
Gebruik één hoofdchat die subagents aanmaakt. Losse bestaande chats zijn niet vanzelf
ondergeschikte uitvoerders en hun geschiedenis verhuist niet automatisch mee.

## Concrete stand en prioriteit

De eigenaar gaf het herstel van Telegram-gezinscontact voorrang op E4.15. Lola kan
met haar eigen identiteit privé met Eva praten; de eigenaar heeft dit bevestigd.
Er is geen `/link` gebruikt, geen eigenaarsidentiteit samengevoegd en geen groepstoegang
verleend. De actieve ingress is `eva2-telegram.service` met
`deploy/telegram-bridge/standalone.mjs`; de Python-pairingroute is niet de actieve
instap voor een nieuw gezinslid.

Het verholpen tekort was directe verzending vanuit een eigenaaropdracht naar een
apart toegelaten gezinscontact. Eva onderzocht onnodig Home Assistant en beloofde
verzending zonder bewijs. De optionele provider `telegram_contacts` hergebruikt de
bestaande duurzame toolroute, actuele bindings en Telegram-transport. De exacte
installatie- en gebruiksstatus staat in
`development/evidence/product/telegram-family-contact-20261023.json`.
De nieuwe route is geïnstalleerd in kern en worker. Eva koos live zelf de contactenlijst
en verzendtool en verstuurde de openstaande vraag eenmaal met exact ontvangstbewijs.
De expliciete operatoroverdracht liep via de bestaande dashboard-conversatieroute;
dit vervangt geen acceptatie van een gewone nieuwe Telegram-opdracht. Het volledige
gesprek duurde 40,137 seconden. Windows-root heeft de hoofdcoördinatie overgenomen. De onnodige werkmodel-/geheugenovergang na contactontdekking is hersteld en in kern en worker geïnstalleerd; onafhankelijk beoordeelde proeven via de echte handler en kern tonen contactenlijst en gewone vervolgsemantiek zonder nieuwe verzending. De antwoorden zijn lokaal afgevangen. De opvolgende queryverbetering is eveneens onafhankelijk beoordeeld en geïnstalleerd (64 PostgreSQL-controles groen). Dezelfde lokale liveproeven daalden van 13,890/10,913 naar 9,153/6,839 seconden; het 1–2 s doel blijft open. Huidige images en meetgrenzen staan in telegram-family-contact-20261023.json. Een extra nieuwe chat (01a169f2-6ff6-7961-b16f-9fd0b836d3e3) beoordeelt nu de gecorrigeerde E4.15-workerbestanden; de bestaande uitvoerder bereidt alleen een exact configuratie-/bronbindingsvoorstel voor. De oude worker-kandidaat is NO_GO (SyntaxError en deliveryguard na effect), blijft behouden en mag niet geïnstalleerd worden. De reeds beoordeelde E2.9-projectenweergave is mee geïnstalleerd en live alleen-lezen gecontroleerd. Stuur de reeds verstuurde vraag niet opnieuw. Een verzendbewijs betekent
Telegram-acceptatie, niet dat iemand las of antwoordde.

Daarna blijft E4.15 de hoofdtaak: een werkelijk door Eva gemaakte uitbreiding
bruikbaar aansluiten. E4.17 heeft echte bronbytes en drie gelijke offline uitkomsten
na geïsoleerd herstel bewezen; volledige aangesloten herstelbaarheid is nog geen
bewezen uitkomst. E4.18 heeft afgebakende onderhoudsstappen opgeleverd. Niet opnieuw
uitvoeren of deze hele kaarten ten onrechte sluiten.

## Bewaarde E4.15-kandidaat

De camera-uitbreiding is door Eva gemaakt en buiten de kern opgeslagen. SHA256:
`340eef5d94949a20ccb6aedaabcb90f2520de4db5f92d837d1dd7dc446c20773`.
De functie is `propose(event, binding, *, now)`; het is geen JSON-stdin-programma.
De gecorrigeerde koppeling is nu **geïnstalleerd en onafhankelijk teruggelezen**, maar een werkelijke begroeting is nog niet bewezen. De actuele installatie- en gebruiksgrenzen staan in `development/evidence/product/e415-household-procedure-binding-20261023.json`.

Eigen kandidaatbestanden zijn `application/household_procedure.py`,
`providers/home_assistant_procedure.py`, kleine wijzigingen in
`application/free_will.py`, `adapters/postgres/sensory_events.py`,
`bootstrap/household_initiative.py`, `bootstrap/household_initiative_delivery.py`
en één beperkte modelcontext-hunk in `adapters/model/pydantic_conversation.py`
(alle onder `src/eva/`). Dit laatste bestand bevat ook vreemd werk: nooit als geheel
stagen of installeren. De bijbehorende nieuwe tests staan onder
`development/tests/{application,providers,postgres}`; de quiet-delivery-test is uitgebreid.

Private kandidaten, geïnstalleerde baselines, patches en oorspronkelijke probes zijn
bewaard onder `/home/administrator/project/.eva2-artifacts/windows-handoff-20261023/`.
Die map bevat privégegevens en hoort niet op GitHub. Scripts daarin verwijzen deels
nog naar `/tmp`; behandel ze als herbruikbare bron, niet als blind uitvoercommando.
Een configuratievoorbeeld met een `ffff...` reviewhash is een concept, geen toelating.
De kandidaat heeft gerichte model/quiet-delivery/providertests en twee echte
PostgreSQL-vervalproeven doorlopen. De nieuwe onafhankelijke chat heeft de gecorrigeerde worker, bronadoptie en installatie beoordeeld. Kern en worker gebruiken de exacte beoordeelde bestanden en opt-in via de bestaande readonly map. De oude defecte worker-kandidaat blijft NO_GO bewaard. De daaropvolgende routecontrole vond een oude onzekere huishoudbeoordeling van19oktober die nieuwe toelating blokkeerde. Deze is inmiddels eenmaal via de onafhankelijk beoordeelde bestaande eigenaar-beheerroute append-only afgehandeld; alle vier oorspronkelijke event-ID’s/hashes, foutgegevens en onbekend verbruik blijven behouden. De gewone route levert bovendien household_observation_batch; de definitieve twee-bestandskoppeling is gebouwd (44 gerichte controles) en heeft onafhankelijke GO. Nieuwere onbeschikbare/lege beelden en conflicten op dezelfde nieuwste seconde kunnen geen ouder positief beeld meer vrijgeven. Gedeelde installatie wacht nog op de gerichte guardreview: de eerste private bewaking telde historische logische NULL-lifecyclestatussen als lopend werk en heeft niets vervangen. Een later werkelijk lopende modellease blijft ongemoeid. Actuele details staan in e415-household-batch-binding-20261023.json. Geen historische bron als nieuwe proef opvoeren. De vroege camera-/hoorvraag is ingetrokken en de eigenaar kan die proef nu niet doen; eerst de technische keten afmaken, niet opnieuw vragen.
Behoud de bestaande modellen, stilte/STOP en aandacht; geen camera-demoregel in de kern.

## Coördinatie en behoud

Lees `development/coordination/active-executors.json` vóór een bestandsclaim.
`codex-e418` is een zelfstandige eerdere chat, geen automatisch bereikbare subagent.
Zijn afgebakende E4.17-resultaten zijn verwerkt. De niet-bevestigde nieuwe Telegram-
opdracht is overgenomen door de hoofdcoördinator en zijn subagent; die oude chat
mag dezelfde provider niet alsnog parallel beginnen.

Gebruik maximaal twee uitvoerders/reviewers onder één leider. Alleen de leider
integreert en vervangt de gedeelde installatie. De spraakroute blijft bij de bestaande
uitvoerder totdat er een expliciete overdracht is. Gebruik bestaande lokale wijzigingen;
geen brede stash/reset/clean of volledige imagebuild uit deze gemengde werkmap.
Nieuwe installaties moeten de daadwerkelijke images/configuratie als uitgangspunt
nemen en actuele gesprekken, werkclaims en componentbeoordelingen respecteren.

Er was al een afzonderlijke `household_research`-profielafwijking. Controleer de
actuele rapportage, maar los die niet op door vreemde spraak-/researchcode te vervangen.
Werkende gedeelde en private/digitale profielen zijn afzonderlijk te beoordelen.

## Startprompt

```text
Je bent de enige hoofdcoördinator van Eva in /home/administrator/project/Eva2. Werk in de bestaande WSL-installatie; maak geen nieuwe kopie van het project.

Lees eerst AGENTS.md, development/docs/OVERDRACHT-WINDOWS.md, PROJECTDOEL.md, ARCHITECTUUR.md en AFRONDINGSPLAN.md in development/docs/, plus development/design/product/execution-workboard-v1.json en development/coordination/active-executors.json. Controleer daarna Git-status, bestaande uitvoerders en de werkelijk geïnstalleerde route. Behoud alle wijzigingen en gegevens van anderen; neem een oude bestandsreservering niet stil over.

Gebruik expliciet subagents: jij bent één leider met maximaal twee uitvoerders/reviewers tegelijk. Maak de subagents zelf aan en geef elk één concrete uitkomst, exacte bestanden en een eindvoorwaarde. Laat ze via jou terugrapporteren; ik hoef geen berichten tussen chats door te geven. Jij beheert integratie, werkbord, GitHub en als enige de gedeelde installatie. Als deze omgeving geen subagents ondersteunt, zeg dat kort en voer dezelfde taak zelfstandig uit; doe niet alsof losse chats aangestuurd worden.

Maak eerst het actuele gebruikersprobleem uit de overdracht volledig af, daarna de volgende taak van het werkbord. Hergebruik wat werkt. Geen nieuw algemeen framework, tweede Eva, geheugen of planner. Een taak is pas opgeleverd als de gebruiker de uitkomst op de werkelijke installatie kan gebruiken. Implementeer, controleer gericht, laat onafhankelijk beoordelen, installeer en test dezelfde route live. Los concrete fouten op; herhaal geen geslaagde tests zonder reden. Maak geen nieuwe testberichten aan gezinsleden zonder opdracht en herhaal geen mogelijk reeds uitgevoerde acties.

Eva moet één interactieve, autonome, grappige en aardige huisgenoot worden die mensen begrijpt en zelfstandig nuttig werk blijft bijhouden tussen gesprekken. Haar zelfgemaakte uitbreidingen blijven buiten de kern, herstelbaar met hun configuratie en gegevens. Persoonsgrenzen en stoppen blijven werken. /link voegt geen ander gezinslid toe: het koppelt accounts van dezelfde persoon.

Werk zelfstandig door binnen de gegeven opdracht. Geef korte updates over merkbare resultaten. Onderscheid gebouwd, geïnstalleerd, daadwerkelijk gebruikt en door het gezin bevestigd. Vraag alleen informatie of toestemming die echt ontbreekt. Bewaar bij afsluiten een bijgewerkte overdracht in dezelfde bestaande bestanden.
```
