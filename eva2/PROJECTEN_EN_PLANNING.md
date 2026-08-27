# Eva 2 — projecten, wat af is en hoeveel werk nog resteert

Bijgewerkt: 27 augustus 2026.

## Het eerlijke overzicht

Eva 2 is geen leeg prototype en ook nog geen af product. De betrouwbare kern
heeft meerdere afgeronde en live bewezen onderdelen. De grote gebruikersdelen
— voice, computerwerk, subagents, eenvoudige installatie en volledige
huisaanwezigheid — zijn afzonderlijke projecten die nog niet af zijn.

Vanaf nu wordt voortgang op **projectniveau** bijgehouden. Een project heeft
een begrijpelijke gebruikersuitkomst, één vaste eigenaar, een onafhankelijke
reviewer, een eindproef en een duidelijke status. Kleine codecheckpoints blijven
nodig, maar vervangen niet meer het projectbord.

Voor ieder algemeen technisch onderdeel geldt bovendien **dependency-first**:
eerst een onderhouden open-sourceproject pinnen en in een kleine verticale
proef gebruiken. Alleen Eva's identiteit, privacy, mandaat, ledger, readback en
eindantwoord blijven eigen kern. De selectie en afwijzingsregels staan in
`OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`.

De planning gebruikt niet langer menselijke, seriële werkdagen. Iedere raming
is opgesplitst in:

- **bouwen + gerichte tests:** actieve AI-uren van de bouwstromen;
- **integratie + review:** samenvoegen, aanvallen op de wijziging, repareren en
  dezelfde commit opnieuw uitvoeren;
- **extern/live:** echte wachttijd op accounts, apparaten, providergoedkeuring,
  jouw oordeel of een verplichte duurproef.

Drie bouwstromen kunnen tegelijk werken; de vierde vaste rol bewaakt review en
release. Daardoor zijn uren niet bij elkaar op te tellen tot een kalenderdatum.
Ook worden gedeelde bouwstenen, zoals mandaat, herstel en readback, maar één
keer gebouwd en daarna door meerdere projecten gebruikt. Een zevendaagse proef
blijft wel echt zeven kalenderdagen duren, hoe snel de code ook wordt gemaakt.

## Wat vandaag live bewezen is

Onderstaande uitkomsten hebben een echte provider-, netwerk- of praktijkroute
doorlopen. Zij moeten nog wel samen op de uiteindelijke eindimage worden
herhaald.

| Live bewezen oplevering | Wat jij daarvan terugziet | Bewijsgrens |
|---|---|---|
| Bestaande live kernreeks | Vijftien afgesproken kerntaken zijn werkelijk geslaagd | **15/15 live bewezen**; herhaling op de uiteindelijke alles-in-één-versie blijft verplicht |
| Eerste complete skill: Home Assistant | Eva kan toestand en historie lezen, apparaten bedienen, automatiseringen maken en het resultaat opnieuw controleren | Live bewezen op meerdere echte taken; Home Assistant is een verwisselbare skill, niet Eva's unieke hoofdfeature |
| Persoons- en projectgeheugen | Feiten, voorkeuren en correcties blijven na herstart bewaard zonder personen en projecten samen te voegen | Live bewezen; de echte Telegram-groepproef is een apart open project |
| Duurzame doelen | Lopende doelen, wachten, hervatten en verschillende eindstatussen blijven na een herstart begrijpelijk | Meerdere native praktijkscenario's live bewezen |
| Historische native reeks | Fan-out, herstel, doelen, cross-channelgeheugen en één zelfgekozen verbetering hebben echt gewerkt | **9/9 historisch live bewezen**, verdeeld over meerdere versies; eindversie moet ze gezamenlijk herhalen |
| Eerste zelfverbetering | Eva heeft één eigen kleine repositoryverbetering gekozen, uitgevoerd en afgerond | Historisch live bewezen; automatische skillgroei en rollbackcyclus zijn nog niet af |

## Wat technisch af of sterk getest is, maar nog niet live als eindroute

Deze bouwblokken zijn waardevol, maar krijgen bewust niet de status `AF`:

| Technisch bouwblok | Wat al is bewezen | Wat de live status nog blokkeert |
|---|---|---|
| Algemene skill- en actieketen | Eva onderscheidt bij iedere geschikte skill ontvangst, uitvoering, fout en gecontroleerde eindtoestand; de volledige action-chainreeks is 75/75 groen | Onafhankelijke review en dezelfde totaalroute via echt Telegram-B en één echte skill; Home Assistant is de eerste testadapter |
| Ontwikkel- en herstelbasis | Productie A blijft staan, kandidaat B is gescheiden, back-ups bestaan en een offline kandidaatimage kan afgeschermd worden gebouwd en gestart | Definitieve buildclosure, adoptie en releasebeslissing |
| Geïntegreerde cockpitbasis | Eén visuele 3D-pagina toont status, identiteit, Mijn kring, bewijs, dependencies en het duurzame read-only Kanbanbord; een kleine `SOUL.md` toont veilig alleen een identiteitsvoorstel | Eigenaar-authenticatie, ondertekenen/activeren van een voorstel, bordwrites, gesprekken, kosten, geheugenbediening en live B-deploy |
| Eerste dependencyvervanging | De officiële OpenAI-SDK neemt in een gereviewde kandidaat één gewone modeltransportroute over; 18 SDK-tests en 63 gerichte regressies zijn groen | Echte provider- en snelheidspariteit, supply-chainbewijs, offline image en veilige productiepromotie |
| Eerste dependencybesluiten | De MCP-SDK is gemeten afgewezen; HA-MCP bespaart bij een directe swap te weinig; Pydantic/Harness neemt de subagentmotor over. Microsoft Playwright MCP heeft nu één vaste lokale pagina gelezen én één vaste actie via Eva's duurzame logboek uitgevoerd | Pydantic Realtime krijgt de volgende verticale proef. Voor de browser blijven publieke websites, accounts, overname en de live route open. Stock DBOS 2.31 is afgewezen; alleen een kleine `2.31.0+eva.1`-rebase gaat door |

## Het projectbord

Statussen:

- `AF`: de genoemde gebruikersuitkomst is live bewezen;
- `TECHNISCH AF`: code en gerichte tests zijn groen, maar de echte eindroute
  of jouw acceptatie ontbreekt;
- `IN BOUW`: ontwerp en een deel van de implementatie bestaan;
- `DEPENDENCYPROEF`: een bestaande dependency of sidecar wordt eerst tegen
  Eva's echte contract en budget beproefd; eigen platformbouw staat stil;
- `ONTWORPEN`: de uitkomst en eindproef zijn beschreven, de bouw moet nog
  beginnen.

| Project | Status | Wat is al af? | Wat moet nog gebeuren? | Bouwen + tests | Integratie + review | Extern/live |
|---|---|---|---|---:|---:|---|
| 1. Veilige eindruntime en B-release | `IN BOUW` | A/B-scheiding, back-ups, offline imagebouw en file-secrets bestaan. De nieuwste offline kandidaatimage startte twee keer veilig tegen een nieuwe tijdelijke PostgreSQL-database; de tweede start herhaalde geen schemawerk. De image controleerde alle 121 database-relaties, en stopte daarna netjes toen één relatie expres ontbrak. De normale PostgreSQL-DSN gebruikt de aanwezige driver; een eerdere kandidaat gaf al een groene `/healthz` én `/readyz` webkerncheck | HA-secret roteren, de volledige beheerde startconfiguratie aan dezelfde image binden, B gecontroleerd adopteren en daarna de echte eindproef uitvoeren | 3-6 uur | 3-6 uur | 2-6 uur eigenaar en omschakelvenster |
| 2. Gezinschat en geheugen | `IN BOUW` | Persoonsgeheugen, projectisolatie, accountbinding en groepsprivacy bestaan. De normale Telegrambron herkent nu na herstart een dynamisch gekoppeld account. Een eventuele Eva1-geheugenproef kan eerst alleen aantallen en hashes voorvertonen uit een consistente private SQLite-snapshot; pas na expliciete bevestiging gaat hoogstens 50 gekoppelde regels naar een wegwerp-Eva2-database als niet-gespreksgeheugen | Op eind-B zetten en echte gezins-/groepstest uitvoeren. Voor de Eva1-proef is nog één bewuste oude-persoon→nieuwe-persoonkoppeling nodig | 2-4 uur | 2-4 uur | 45-90 min eigenaar plus accounts/testgroep |
| 3. Algemene skilluitvoering en bewijs | `TECHNISCH AF` | De providerneutrale lees→actie→teruglees→vervolgketen is lokaal 75/75 groen; Home Assistant is de eerste live skill. HA-MCP is exact onderzocht, maar een directe swap verwijdert naar schatting slechts 174–192 regels | Eén kleine HA-MCP state→service→stateproef; alleen bij echte semantische pariteit en minimaal 300 regels nettowinst promoveren. Daarna Telegram→skill→readback→vervolg en crash live uitvoeren | 2-5 uur | 2-4 uur | 2-4 uur met veilige testskill/-entiteit |
| 4. Spontane huisaanwezigheid | `IN BOUW` | Oude Eva bewijst de beleving; Eva 2 heeft events, stiltekeuze en duurzame aandacht als bouwstenen | Bestaande HA/Wyoming-/speakercomponenten hergebruiken voor één speaker, deurbel, quiet hours, cooldown, stop en herstel | 4-10 uur | 3-6 uur | Gedeelde 7-daagse huisproef |
| 5. Conversationele voice | `IN BOUW` | Identiteit, privacy en definitieve transcriptroute staan klaar; Pydantic AI `2.34.0` is geïsoleerd beproefd; de nieuwe dunne afspeelgrens stopt alleen de exact gebonden onafgemaakte antwoordgeneratie en wacht op echte sink-stopbevestiging | De bestaande realtime-/audio-uitvoerder op een host met microfoon en speaker koppelen, één echte barge-in meten en pas daarna reconnect en skillreadback toevoegen | 2-5 uur | 2-4 uur | 4-8 uur gebruikers-/apparaatmeting |
| 6. Browser en zelfstandige computer | `IN BOUW` | Microsoft Playwright MCP `0.0.79` las in een geïsoleerde Chromium één vaste lokale pagina. Daarna liep één vaste klik door Eva's bestaande PostgreSQL-logboek en onafhankelijke servercontrole. De eigenaar kan de kandidaat nu vóór call-start werkelijk stoppen; erna wordt alleen een eerlijk stopverzoek met verplichte controle bewaard. Twee procesfouten veroorzaakten geen blinde tweede klik; checkpoint `72625e0` is onafhankelijk groen | Eén afgeschermde publieke testsite, grants en live stop; daarna pas formulieren, downloads, testaccount, meekijken/overname, Telegram/B en automatisch herstel van een onduidelijke actie | 2-6 uur | 2-4 uur | Testaccount en gedeelde 7-daagse autonomierun |
| 7. Mensen leren kennen en skills maken | `IN BOUW` | Persoonsfeiten, correcties, reflectie en één eigen verbetering werken | Hindsight-UI eerst hergebruiken; alleen inzien/vergeten, usage/kosten, skillquarantaine, evaluatie, promotie en rollback toevoegen | 10-24 uur | 6-12 uur | Gedeelde 7-daagse groeiproef |
| 8. Subagents, dashboard en Kanban | `IN BOUW` | Eva's commandolaag kan een begrensde subagent starten, volgen en actief stoppen. De officiële-SDK-worker draait in een aparte kandidaatimage; taak, resultaat en veilige Kanbankaart blijven bewaard en een stop of crash veroorzaakt geen blinde tweede modeloproep | Eén door de eigenaar goedgekeurde echte providerproef via Telegram/B, eigenaar-authenticatie en daarna bordbediening met React-Admin OSS + `dnd-kit` | 2-4 uur | 2-4 uur | 1-3 uur gebruikerssessies |
| 9. Eigenaarmodus | `ONTWORPEN` | Mandaat-, stop- en auditbouwstenen bestaan deels | Na projecten 6-8 één ruim maar intrekbaar mandaat boven browser, skills en subagents sluiten | 3-6 uur | 3-6 uur | 1-3 uur risicoacceptatie |
| 10. Installatie, update en verplaatsen | `IN BOUW` | Images, file-secrets, back-up, schema-installatie en A/B-onderdelen bestaan. Een nieuwe read-only installatiecontrole vergelijkt vóór iedere wizard de lokale computer met de gepinde release: zij start of wijzigt niets en blokkeert zolang dit geen werkelijk bewezen platform is. De tijdelijke loopback-wizard gebruikt éénmalige lokale toegang en kan nu alleen die veilige uitslag tonen; er zijn bewust nog geen connector- of secretvelden. Een gevalideerde portable export kan als één versleuteld bestand heen en terug; een fout wachtwoord of gewijzigde byte laat geen halve restore achter. De kleine operatorroute leest het wachtwoord alleen uit een private file. De decoder en writer bewaken contract, hash, primary key, serialiseerbare exclusieve lege target en eigen rollback. Een nieuwe proef exporteerde een niet-lege synthetische Eva-installatie en herstelde identiteit, gesprek, geheugen en bewijs in een tweede nieuwe PostgreSQL-database. Een oude connector komt bij restore alleen terug als onbruikbaar “opnieuw koppelen” | Bestaande Compose/OCI-, signing-, SBOM- en scanmiddelen verbinden aan de wizard; een echt gevalideerd platform, staged connector-/secretvragen, opnieuw-koppelstap, object-CAS-kopie, veilige vertaling van geredigeerde oude attempts en daarna de volledige tweede-installatierestore, fresh installs, update en rollback | 6-16 uur | 5-9 uur | Twee machines; 1-3 kalenderdagen testvenster |
| 11. Snelheid, omvang en kosten | `IN BOUW` | Codegrens, eerste geheugenlimieten en twee gunstige kleine snelheidsmetingen bestaan | OpenTelemetry, Renovate, Trivy en SBOM-output hergebruiken; Eva voegt CI-budgetten, p50/p95, tokens/kosten en 200-beurtentest toe | 4-10 uur | 3-6 uur | Metingen lopen mee in gedeelde 7 dagen |
| 12. Eerlijke openbare benchmarks | `IN BOUW` | LAB-36, drie ontwikkelcases en publicatieregels bestaan. De GAIA-ontwikkeladapter leest nu veilig de lokale JSONL- of actuele Parquet-vorm; een eigen hash-gebonden benchmarkomgeving installeerde offline en las aantoonbaar geen verborgen antwoordkolom | Officiële GAIA, AgentBench en OSWorld-runs plus gecontroleerde vijf-agentmatrix | 12-32 uur | 8-16 uur | Dataset/compute 1-10 dagen, parallel waar mogelijk |
| 13. Documentatie als controleerbaar product | `IN BOUW` | Leesbare overzichten, technische gates, reviews en padinventaris bestaan | Componentregister, ownershipchecker, unieke gate-/bewijskoppeling en dashboardprojectie | 4-10 uur | 3-6 uur | Geen belangrijke wachttijd |
| 14. Vrije kanalen en connectoren | `DEPENDENCYPROEF` | Connectorneutrale kern en veilige read-only MCP-basis bestaan | Eerst gepinde OpenClaw-plugin of officiële provider-SDK; Eva voegt persoonsbinding, consent, deliveryledger, stop en rollback toe | 2-6 uur | 2-4 uur | Providergoedkeuring 0-6+ weken; blokkeert V1 niet |
| 15. Eindfreeze en veilige overgang | `IN BOUW` | A/B, back-ups, kandidaatbuild en veel losse receipts bestaan | Eén image bevriezen, alle eindproeven herhalen, gebruikersacceptatie en rollbackceremonie | 3-6 uur | 6-10 uur | Minimaal één gedeelde 7-daagse ongewijzigde run |

## Wat kan parallel en wat bepaalt de einddatum?

Na het veilig sluiten van runtime/B kunnen drie bouwstromen echt naast elkaar
lopen:

1. gezinschat, algemene skilluitvoering en huisaanwezigheid;
2. voice en reactietijd;
3. browserautonomie, leren en daarna subagents/eigenaarmodus.

Installer, benchmarkadapters en documentatiecheckers kunnen gedeeltelijk
parallel worden gebouwd, maar hun eindbewijs wacht op de bevroren versie. De
eindversie kan pas worden bevroren wanneer de blokkerende onderdelen niet meer
veranderen. De eerste browser- en subagentdependencyproeven hebben inmiddels
laten zien dat hun gekozen lokale kandidaatroutes passen; voice moet dat nog
binnen uren aantonen. Alleen een gemeten functioneel,
privacy-, latency- of beheerprobleem rechtvaardigt daarna eigen platformcode.
Iedere noodzakelijke fix tijdens de officiële zevendaagse proef start die klok
opnieuw.

De dependency-first herplanning bevat ongeveer **125-279 actieve agenturen** voor
bouwen, integreren en reviewen. Omdat drie bouwstromen parallel lopen en veel
werk wordt hergebruikt, is dit geen optelsom in kalenderdagen. De eerste drie
projecten vertegenwoordigen samen 15-31 agenturen; parallel bouwen en reviewen
maakt daar onderstaande verstreken tijd van.

Mijn huidige raming bij tijdige toegang tot accounts en apparaten:

- **Eerste veilig beheerde B-alpha met gezinschat en één echte skillketen:**
  ongeveer 0,5-2 verstreken werkdagen met drie parallelle bouwstromen en review,
  plus het afgesproken omschakelvenster.
- **Veilige vervanger van de oude Eva**, inclusief presence, voice en de
  gezamenlijke zevendaagse eindproef: ongeveer 8-14 kalenderdagen. Daarvan zijn
  minimaal zeven dagen echte, niet-versnelbare praktijktest.
- **Volledige concurrerende productbelofte**, inclusief browser, leren,
  subagents/Kanban, installer, benchmarks en eindproef: ongeveer 2-4
  kalenderweken, los van externe providergoedkeuring.
- **Een extra kanaal zoals WhatsApp:** de bouw past in bovenstaande uren, maar
  externe providergoedkeuring kan afzonderlijk 0-6+ weken duren en blokkeert
  de eerste release niet.

Dit zijn geen menselijke programmeerschattingen en geen beloftes. Na P1.1 en
P2.1 vervangen we deze banden door de werkelijk gemeten tijd per kleine slice.
Codegeneratie kan zeer snel gaan; onafhankelijke review, hardwaregedrag en een
duurproef worden niet kunstmatig honderd keer korter gerekend.

## De eerstvolgende twee zichtbare mijlpalen

1. **P1.1 — één versiepassende releasekandidaat.** De statische 39-package-
   bouwinput is nu onafhankelijk groen op checkpoint `0bbfe3d`. De fysieke
   wheelhouse, imagebuild, start, herstart, schemadrift en secret-nonleakproef
   volgen nog. Dit verandert A of B niet.
2. **P2.1 — echte Eva 2-gezinschat.** De normale Telegrambron herkent nu op
   checkpoint `187f797` een dynamisch gekoppeld account veilig en ook na
   herstart. Na de veilige B-adoptie demonstreert
   Chandrasekhar via een eigen Telegram-B-bot: eigenaar plus tweede toegestaan
   account, verschillende onschuldige voorkeuren, herinnering na procesherstart,
   groep eerst dicht, bewuste toestemming en directe intrekking, geen privélek
   en één afsluitende read-only skillvraag. Home Assistant is daarvoor de eerste
   beschikbare adapter, niet het unieke productdoel. Productie A blijft actief.

Voor P2.1 zijn vooraf nodig: een eigen B-bottoken als file-secret, twee
geautoriseerde accounts, één testgroep, onschuldige geheugen-canaries, een
geroteerd least-privilege HA-token voor één testentiteit, een werkende stop- en
rollbackroute en een geplande gebruikerssessie van 60–90 minuten. Een lokale
Telegramfixture telt niet als deze demo.

## Het vaste team vanaf nu

Ieder actief project houdt van begin tot eind dezelfde vier rollen:

| Rol | Vaste bezetting | Blijvende verantwoordelijkheid |
|---|---|---|
| Product- en integratielead | Codex-hoofdagent | Houdt jouw uitkomst, afhankelijkheden, planning en samenhang met de rest van Eva vast |
| Core- en reliabilitylead | Fermat the 2nd | Bewaakt database-authority, effecten, herstel, secrets, A/B en releaseveiligheid |
| Experience- en capabilitylead | Chandrasekhar the 2nd | Bewaakt chat, voice, presence, browser, leren, workers, dashboard en installer als zichtbare productuitkomsten |
| Onafhankelijke verificatie- en releaselead | Curie the 2nd | Ontwerpt eindbewijs mee, controleert tussentijds en geeft onafhankelijk stop/go op het geïntegreerde checkpoint |

Canonieke, opnieuw activeerbare agenttaken: Fermat is
`/root/continuous_team_model`, Chandrasekhar is `/root/delivery_estimates` en
Curie is `/root/project_status_audit`. Zij zijn op 25 augustus 2026 expliciet
aan deze rollen gekoppeld. Continuïteit betekent dat dezelfde agenttaak en
projectkaart bij iedere relevante stap terugkomen; niet dat een proces zonder
werk permanent CPU blijft gebruiken.

De primaire projectlead is expliciet verdeeld:

| Vaste lead | Hoofdprojecten |
|---|---|
| Codex-hoofdagent | 12 openbare benchmarks, 13 documentatieproduct en 15 eindfreeze; integratielead voor alle projecten |
| Fermat the 2nd | 1 eindruntime/B-release, 3 algemene skilluitvoering en bewijs en 11 performance/kosten |
| Chandrasekhar the 2nd | 2 gezinschat, 4 presence, 5 voice, 6 browser, 7 leren/skills, 8 subagents/Kanban, 9 Eigenaarmodus, 10 installer en 14 vrije kanalen |
| Curie the 2nd | Onafhankelijke verificatie en releasebesluit voor ieder project; schrijft geen eigen goedkeuring |

Bij vervanging van een agentsessie wordt deze rol met een expliciete handoff op
dezelfde projectkaart overgenomen; het project krijgt niet stil een nieuwe
eigenaar of nieuwe bewijsgrens.

Jij blijft producteigenaar voor keuzes en gebruikersacceptatie. Een technische
reviewer kan niet namens jou beslissen dat Eva natuurlijk praat of goed als
huisgenoot voelt.

Agents werken niet gelijktijdig in dezelfde bestanden. Elk project krijgt een
eigen file-scope of geïsoleerde Git-werkboom; de integratielead voegt alleen een
groen en beoordeeld checkpoint samen. De vaste projectrollen blijven betrokken
tot en met de live eindproef, ook wanneer een andere agent een kleine
implementatieslice uitvoert.

## Waarom dit eerder niet zichtbaar was

Het werk was georganiseerd rond kleine veiligheids- en bewijsstappen. Dat was
nuttig om dataverlies en onterechte claims te voorkomen, maar had drie nadelen:

1. afgeronde bouwblokken verdwenen tussen honderden technische regels;
2. reviewers kwamen vaak pas aan het einde van een kleine stap in plaats van
   gedurende het hele project;
3. er was geen leesbaar bord dat meerdere gebruikersprojecten naast elkaar
   liet zien.

Dat proces wordt nu gecorrigeerd. Technische gates blijven bestaan als controle,
maar dit document wordt het primaire project- en planningsbord.

## Wanneer een project op `AF` mag

Een project is pas `AF` wanneer:

1. de gebruikersuitkomst werkt;
2. positieve, negatieve, crash- en herstarttests groen zijn;
3. een andere agent ontwerp en uitvoering heeft gecontroleerd;
4. een echte provider, apparaat of netwerkroute is gebruikt wanneer dat bij de
   belofte hoort;
5. bewijs en rollback bestaan;
6. jij het waarneembare gedrag hebt geaccepteerd wanneer smaak of natuurlijkheid
   onderdeel van de uitkomst is.
