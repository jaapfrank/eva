# EVA 2 herbeoordeling: architectuur en historische besluiten

Datum: 21 september 2026. Onderzoeker: architecture_audit. Status: onderbouwde deelanalyse voor de coördinator; geen release- of gezinsacceptatie.

## Advies

Behoud de huidige Pydantic AI-, PostgreSQL-, Hindsight- en LiveKit-basis en vereenvoudig gericht rond één dagelijkse gezinsketen. De basis heeft bruikbare grenzen en waardevolle herstel-/persoonssemantiek. De actuele werkboom is echter geen aantoonbaar samenhangende release: de modulepoort faalt, de componentcatalogus loopt achter en zestien kernmodules verschillen van de draaiende kern. Een volledige platformmigratie is hierdoor niet automatisch beter; ongewijzigd doorbouwen evenmin.

De belangrijkste tegenwerping tegen behouden is inhoudelijk: de huidige documentdoelroute is een vooraf begrensde twee-stappenuitvoering, geen algemene planner. Brede taakuitvoering, procedureleren en zelfstandig meerdaags werk mogen niet als afgedekt worden beschouwd omdat Pydantic AI aanwezig is. Een onderhouden algemene uitvoerder blijft een serieuze toekomstige vervanger. Kies die op dezelfde gebruikersopgaven, inclusief stop/herstel en gezinsgrenzen, wanneer de dagelijkse keten een concreet tekort laat zien. Deze deelanalyse voert geen nieuwe upstreamvergelijking uit; actuele upstreambronnen en het definitieve architectuurbesluit staan in de centrale herbeoordeling.

## Leesdekking en bewijskwaliteit

[De leesregistratie](../evidence/reassessment/architecture-reading-2026-09-21.json) bevat alle 97 Markdownpaden uit de initiële inventaris binnen docs/, eva2/design/architecture/, eva2/design/product/, eva2/deploy/, eva2/experiments/ en experiments/. Daarvan zijn 90 projecteigen prozadocumenten volledig inhoudelijk gelezen, inclusief historische en genegeerde documenten. Truncaties in tooluitvoer zijn opnieuw uitgelezen. Ook de twee gegenereerde historische inventarissen zijn volledig inhoudelijk gelezen. De runtime-contractkaart is onverkort in opeenvolgende tekstbatches gelezen. De repository-inventaris is verliesvrij voor inhoud gededupliceerd: alle 4876 unieke tabelcelwaarden en alle niet-tabeltekst zijn modelzichtbaar gelezen, zonder truncatie. Alleen Markdownmargewitruimte is genormaliseerd; iedere oorspronkelijke tabelrij is met bronhash, regelnummer, tekenoffsets en geordende cel-ID’s in de registratie terug te vinden. Dit dekt alle 3707 tabelrijen en 217 niet-tabelregels; herhaalde identieke cellen zijn niet apart opnieuw gelezen. Daarmee zijn alle 92 projecteigen documenten in deze scope inhoudelijk gelezen. Vijf meegeleverde LiveKit-vendordocumenten zijn uitgesloten en expliciet geregistreerd.

Per pad staan SHA-256, bytes, regels, leeswijze, classificatie en bevinding. Alle hashes waren bij registratie nog gelijk aan de initiële inventaris. Volledige documentlezing betekent niet volledige broncodelezing of heruitvoering van historische proeven. Relevante broncode, locks en live bestanden zijn gericht gecontroleerd. Historisch beschreven bewijs is als zodanig gebruikt; menselijke acceptatie is niet opnieuw ingevuld.

De aanvullende inhoudelijke lezing bevestigt de historische verwevenheid: de runtimekaart bevat 76 modulecontracten en een dynamische runtimecyclus van 37 modules. De repositorykaart combineert Eva1 en Eva2 en vermeldt 2512 bestanden, 188 toenmalige policyfouten en archive/integrate/isolate/delete/quarantine-besluiten. Deze gegenereerde uitkomsten blijven historische bronnen, geen actuele verwijderopdracht of nieuwe runtimecontrole. De expliciete beperking van letterlijke randdetectie blijft gelden: een ontbrekende heuristische rand bewijst geen ontbrekende caller. Voor deze laatste leesaanvulling zijn geen audits, tests of experimenten herhaald.

## Diagnose met praktisch gevolg

| Bevinding | Bron | Praktisch gevolg |
|---|---|---|
| Er bestaan verschillende uitvoeropdrachten met het woord actueel of bindend | [Aug20 hoofdkaart](../../docs/eva2-build-map-2026-08-20.md), [platformkaart](../../docs/eva2-build-map-platform.md), [living-core](../design/architecture/living-core-v1.md) | De Aug20 Noor-vertical verbood stem/HA en vereiste eerst een omvangrijke platformpoort. Recente eigenaarprioriteit is dagelijks geheugen, gesprek en stem. Oude kaarten blijven incident-/ontwerparchief, niet een tweede uitvoerwachtrij. |
| Historische prototypefouten worden gemakkelijk permanente platformoordelen | [ADR-0001](../../docs/adr-0001-eva2-living-core.md), [bakeoffresultaten](../../docs/eva2-architecture-bakeoff-results-2026-08-20.md) | DBOS-v1 had concrete adapter- en prototypefouten; één veilige readbackretry was later wel bewezen. Restate-erfenis van secrets bewijst een foute workeropstelling. Onderscheid upstreambeperking, kandidaatfout en fixturefout voordat een keuze wordt heropend. |
| Het geheugenbesluit is door de tijd veranderd | [historische presencekaart](../../docs/eva2-build-map-presence-evidence.md), [memoryproef](../../experiments/eva2-architecture-bakeoff/memory/README.md), [tiered memory](../design/product/tiered-memory-and-person-model-v1.md) | Oude SQL-only/Hindsight-afwijzing was geen actuele semantische vergelijking. Behoud inmiddels bewezen geheugenfunctie en eigen bron-/privacygrens; voeg geen tweede memoryauthority toe. |
| Embeddingdocumentatie spreekt zichzelf tegen | [living-core](../design/architecture/living-core-v1.md), [Managed-B hostcontract](../deploy/living-core/MANAGED_B_HOST_CONTRACT.md) | Cloud text-embedding-3-small versus lokaal384-dimensionaal databehoud mag niet op basis van proza worden omgeschakeld. Bind vóór migratie actuele bank/config/schema en herbouwbewijs; deze audit heeft geen geheime config gedumpt. |
| Voicevolgorde is herhaaldelijk gewijzigd | [conversational voice](../design/product/conversational-voice-v1.md), [OSS selectie](../design/product/voice-oss-selection-2026-09-10.md), [LiveKit oplevering](../design/product/voice-livekit-delivery-2026-09-10.md) | Oudere miniapp-/Pipecat-/Realtime-voorwaarden gaan niet boven latere laptop/telefoon-LiveKitkeuze. Rond één gebruikersroute af; fysieke satellietexperimenten blijven apart. |
| De modulegrenspoort is nu rood | python3 eva2/tools/check_architecture.py: 375 gecontroleerde Pythonbestanden,1087 ontdekt,25 bevindingen | Het eerder genoteerde groene moment is historisch. Herstel echte geraakte afhankelijkheidsbreuken; verander geen allowlist alleen om een cijfer groen te krijgen. |
| Componentregistratie beschrijft de actuele boom niet volledig | check_component_catalog.py:97 bevindingen,52 inventory-drift,37 ontbrekende owners,7 owner-pathpatronen,1 padsetverschil | Dit zijn geen97 runtimebugs. Veel ontbrekende voice/HA-bestanden en globinterpretaties vragen juiste verantwoordelijkheid/inventaris, geen featurebouw. |
| Gebouwd en draaiend zijn verschillende kandidaten | Read-only containerhashvergelijking:258 live Pythonfiles,269 lokaal;242 gelijk,16 verschillend,11 alleen lokaal | Testbewijs moet aan exact artifact/bronset worden gekoppeld. Maak eerst een reproduceerbare kandidaat; neem lokale voice-, restore- of documentwijzigingen niet aan als live. |
| Er bestaat nog taakgebonden uitvoerlogica | [document_goal_plan.py](../src/eva/providers/document_goal_plan.py), [runtime.py](../src/eva/bootstrap/runtime.py) | Twee hostgekozen stappen, beperkte flat /proof-state txt/md-paden en12kB inhoud bewijzen veilige begrensde uitvoering, geen ongeziene algemene taakplanning. Nieuwe vragen horen niet ieder een nieuwe bootstraproute te krijgen. |
| Er bestaat wél bruikbare generieke uitvoering | [provider runtime](../src/eva/providers/runtime.py), [orchestration](../src/eva/application/orchestration.py) | Factories/manifests en prepare/execute/observe vormen herbruikbare grenzen. Ambigue effecten worden eerst teruggelezen; vervang deze semantiek niet door blind frameworkretry. |
| Grootte verbergt geen eenvoudige ene eigenaar | conversation_actions, pydantic_conversation, bootstrap/runtime en Hindsight-adapter hebben brede verantwoordelijkheden | Aantallen regels zijn alleen zoekhulp. Splits waar een verantwoordelijkheid of afhankelijkheid werkelijk verkeerd ligt; nieuwe wrappers zonder verdwenen overlap voegen onderhoud toe. |

Concrete architectuurmeldingen omvatten een API-adapter die bootstrap importeert (household_voice_session), Postgres-adapters die model/provider/connectors importeren, operationele browsercode die FastAPI opzet, en niet-geregistreerde externe imports. Sommige externe meldingen kunnen beleidsactualisatie betreffen, andere zijn echte afhankelijkheidsomkering. Classificeer per geraakte keten; deze audit heeft ze bewust niet gerepareerd.

De waargenomen live distributies zijn pydantic-ai-slim2.37.0, pydantic2.12.5, openai3.6.0, cryptography50.0.1, fastapi0.141.1 en sqlalchemy2.0.52. Die geselecteerde versies komen overeen met de lokale directe pins. Gelijke dependencies betekenen nog geen gelijke applicatie. De zestien verschillende modules omvatten bootstrap/runtime, document_goal_plan, voice_turns, durable_goals en Home Assistant voice. De elf alleen lokaal gevonden bestanden omvatten de aparte voice_service; het ontbreken daarvan in de kerncontainer is op zichzelf geen storing, omdat die service een eigen image heeft. De volledige padsets staan in de JSON-registratie.

## Waarom reparaties terugkomen

De historische bronnen tonen een patroon, niet alleen een vermoeden. [Het herbouwplan](../../docs/eva-rebuild-plan-2026-08-20.md) beschrijft een geslaagde keukenhandeling die als totale mislukking werd teruggekoppeld en vervolgens zes ongerelateerde vervolgbeurten kreeg; een andere dag telde410 provider429-beurten. [De roadmap](../../docs/eva-endbeeld-roadmap.md) bewaart tegenbewijs van driemaal heropend voltooid werk en herhaalde lexicale lineagepatches. [Het testimportincident](../../docs/incident-2026-08-13-ledger-test-import.md) laat zien dat tests ooit de productiedatabase konden migreren. Deze oude runtimefouten zijn geen actuele Eva2-foutclaims, maar verklaren waarom outcome, replay en isolatie zo zwaar zijn geworden.

Daar bovenop is veel controle per incident toegevoegd. [De Eva1-runtimecontracten](../../docs/eva-runtime-contracts.md) beschrijven vier Telegrammachines, meerdere authority-/ACKketens en afzonderlijke perceptie-, nieuwsgierigheid- en werksettlement. De oude importgraaf zonder cycli betekende dus geen eenvoudige runtime. Eva2 heeft betere expliciete ports, maar bootstrap en taakadapters worden opnieuw breed. Extra contracten lossen eigenaarschapsproblemen alleen op wanneer de oude route daadwerkelijk verdwijnt.

Ontwikkelverbruik en runtimeverbruik zijn verschillende zaken. Herhaald documenten bijwerken, checkerdrift repareren en microreviews betalen ontwikkeltijd; extra model-/toolrondes tijdens één gebruikersopdracht betalen runtimevertraging en gebruik. Deze audit kan geen totale historische credits reconstrueren. Geen onbekende creditprijs is als feit geschat.

## Behoud-, vervangings- en parkeerbesluiten

| Onderdeel | Besluit | Bewijs/voorwaarde |
|---|---|---|
| Canonieke gesprekken, relaties, correcties, mandaat en effecthistorie | Behouden | PostgreSQLdata met provenance exporteren; bestaande persoonsgrenzen en stop/herstel als invarianten. |
| Pydantic AI model-/tooladapter | Behouden en verantwoordelijkheid beperken | Gebruik de onderhouden loop; geen tweede algemene planner of antwoordpersoon naast Eva. |
| Hindsight | Behouden als vervangbare semantische geheugenadapter | Geen nieuwe memoryauthority; migratie alleen met actuele bron/bankmapping en verliesvrije proef. |
| LiveKit | Eerste dagelijkse voiceketen afronden | Media/onderbreking bij upstream; eigen code voor gesprek-/persoonsbinding en noodzakelijke levering blijft gerechtvaardigd. |
| Eigen browser plus Chrome-eigenaarstab | Voorlopig beide behouden | [Owner-browser](../deploy/owner-browser/README.md) en [Chrome-bridge](../deploy/chrome-bridge/README.md) bedienen verschillende situaties; shared-tab heeft een smalle toolset. Consolideer gedeelde policy, niet blind de gebruiksvorm. |
| Specifieke documentdemo-/prototypepaden | Niet uitbreiden; vervangen zodra algemene route pariteit heeft | Exacte oude requests en data behouden; één cutover, daarna oude actieve dispatch verwijderen. Geen langdurige twee planners. |
| Native duplex/Pipecat/ESP speakerprotocol | Experiment bewaren, uitbreiding parkeren | [Native duplex](../experiments/native-duplex-v1/README.md) en [wire](../experiments/native-duplex-v1/speaker-wire-v1.md) vragen aanzienlijke eigen framing/lifecycle/firmware. Fysieke route heeft eigen herstel- en luisterbewijs nodig. |
| Historische DBOS/Restate/customSQL-bakeoffcode | Archiveren als bewijs | Niet tot productbasis verheffen zonder nieuw concreet tekort; ook geen automatische totale workflowmigratie. |
| MCP SDK/typed transport | Heropen alleen bij echte protocolverantwoordelijkheid | [MCP SDK-proef](../experiments/mcp-sdk-v1/README.md) en [typesproef](../experiments/mcp-types-v1/README.md) wijzen een extra wrapper af; geen principiële upstreamafwijzing voor groeiende discovery/catalogus. |
| Volledige installer/cross-platformrelease | Apart vervolgdoel | Restore/rollback van huidige installatie nodig; alle historische cross-OSpoorten zijn geen voorwaarde voor iedere dagelijkse deeloplevering. |

Eigen code is gerechtvaardigd voor unieke Eva-semantiek: persoonsbinding, mandaat, bewaren van correcties, proportionele verificatie van echte effecten en koppeling tussen gesprek en lang werk. Eigen audio-/browser-/modeltransport, discovery en generieke execution moeten eerst bestaande onderhouden implementaties benutten. Een absoluut geen-eigen-codebeleid is even ongeschikt als alles zelf bouwen.

## Doelgrenzen voor de eerstvolgende oplevering

De centrale GZ-001-keten is een verdedigbaar beperkt resultaat: een gezinsactiviteit bespreken, onderzoeken, tijdens werk blijven praten, corrigeren en later met dezelfde context verdergaan. Haar interfaces moeten bestaande ports hergebruiken:

1. Kanalen leveren geauthenticeerde gespreks-/persoonsverwijzingen, tekst/audio en onderbreking. Zij bezitten geen geheugen of planner.
2. De Eva-applicatie bezit gesprek, identiteit, relevante herinneringen en de keuze voor werk/initiatief. Alleen hier wordt bepaald wat de gebruiker nog nodig heeft.
3. Eén uitvoeringseigenaar biedt begin/status/resultaat/stop/hervatten voor een taak. Externe effecten blijven achter het bestaande prepare/execute/observe-contract.
4. PostgreSQL bewaart taakstatus, berichten en brongebonden beslissingen; Hindsight levert alleen toegestane recall. Toolresultaten zijn geen nieuwe rechten.
5. Bootstrap injecteert concrete adapters. API- en opslagadapters importeren geen bootstrap of elkaars businesslogica.

Geen nieuw universeel schema ontwerpen voordat bestaande contracten zijn beoordeeld. Geen groene documentdemo als bewijs voor skillleren gebruiken: een procedure moet in een andere ongeziene opdracht aantoonbaar worden hergebruikt. Dat mag zichtbaar vervolgwerk zijn. GZ-001 bewijst evenmin algemene concurrentiewinst.

[EV-003](../design/product/comparable-agent-benchmarks-v1.md) heeft voor die brede claim zes families, twaalf gevallen, drie blokken en vier deelnemers:864 taakruns vóór eventuele judges. Gebruik voor gerichte ontwikkeling de bestaande twee-cases/twee-epochsgrens. Een volledige benchmark na iedere kleine reparatie veroorzaakt verspilling; een kleine proef mag omgekeerd de algemene winpoort niet vervangen. Fysiek horen, warmte en gezinswaarde blijven een afzonderlijk menselijk oordeel.

## Uitvoering, verbruik en beperkingen

Vooraf geraamd: één volledige documentpass, gerichte bron-/lockcontrole, twee bestaande statische checkers en read-only versie-/hashinspectie. Uitgevoerd binnen die scope. De catalogusuitvoer is een tweede keer samengevat vanwege outputtruncatie; er is geen nieuwe vergelijkingshypothese of volledige suite gedraaid.

Gemeten door handelingen: nul nieuwe Eva-modelcalls, nul deployments, nul productiemutaties, nul nieuwe concurrentproeven. Ontwikkeltokens en credits zijn niet betrouwbaar beschikbaar en blijven onbekend. Alleen dit rapport en zijn leesregistratie zijn geschreven. De dirty Git-index is niet gewijzigd. Bestaande installatie en gebruikersgegevens zijn intact gebleven.
