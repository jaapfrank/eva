# Eva 2 — projecten, wat af is en hoeveel werk nog resteert

Bijgewerkt: 25 augustus 2026.

## Het eerlijke overzicht

Eva 2 is geen leeg prototype en ook nog geen af product. De betrouwbare kern
heeft meerdere afgeronde en live bewezen onderdelen. De grote gebruikersdelen
— voice, computerwerk, subagents, eenvoudige installatie en volledige
huisaanwezigheid — zijn afzonderlijke projecten die nog niet af zijn.

Vanaf nu wordt voortgang op **projectniveau** bijgehouden. Een project heeft
een begrijpelijke gebruikersuitkomst, één vaste eigenaar, een onafhankelijke
reviewer, een eindproef en een duidelijke status. Kleine codecheckpoints blijven
nodig, maar vervangen niet meer het projectbord.

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
| Kleine beheerbasis | Een gesaneerde statuspagina en veilige identiteitspreview zijn getest | Authenticatie, compleet dashboard, Kanban, geheugen- en taakbediening en live B-deploy |

## Het projectbord

Statussen:

- `AF`: de genoemde gebruikersuitkomst is live bewezen;
- `TECHNISCH AF`: code en gerichte tests zijn groen, maar de echte eindroute
  of jouw acceptatie ontbreekt;
- `IN BOUW`: ontwerp en een deel van de implementatie bestaan;
- `ONTWORPEN`: de uitkomst en eindproef zijn beschreven, de bouw moet nog
  beginnen.

| Project | Status | Wat is al af? | Wat moet nog gebeuren? | Bouwen + tests | Integratie + review | Extern/live |
|---|---|---|---|---:|---:|---|
| 1. Veilige eindruntime en B-release | `IN BOUW` | A/B-scheiding, back-ups, offline imagebouw en file-secrets bestaan. Het nieuwe checkpoint bindt reproduceerbaar 31 release-inputs en 56 testbestanden en maakt de oude 38 tegenover huidige 39 dependencies zichtbaar | De 39-set als één bouwinput sluiten, volledige kandidaat bouwen/starten, HA-secret roteren en B gecontroleerd adopteren | 10-20 uur | 6-12 uur | 2-6 uur eigenaar en omschakelvenster |
| 2. Gezinschat en geheugen | `IN BOUW` | Persoonsgeheugen, projectisolatie, accountbinding en groepsprivacy zijn gebouwd; kerngeheugen werkt live | Dynamisch gekoppeld account door normale chat laten herkennen, op eind-B zetten en echte gezins-/groepstest uitvoeren | 10-16 uur | 6-10 uur | 45-90 min eigenaar plus accounts/testgroep |
| 3. Algemene skilluitvoering en bewijs | `TECHNISCH AF` | De providerneutrale lees→actie→teruglees→vervolgketen is lokaal 75/75 groen; Home Assistant is de eerste live skill | Onafhankelijke reviews en Telegram→planning→echte skill→readback→vervolg plus crashtest | 4-10 uur | 4-8 uur | 2-4 uur met veilige testskill/-entiteit |
| 4. Spontane huisaanwezigheid | `IN BOUW` | Oude Eva bewijst de beleving; Eva 2 heeft events, stiltekeuze en duurzame aandacht als bouwstenen | Eén speaker, deurbel, quiet hours, cooldown, budget, stop en herstel aansluiten | 12-24 uur | 6-12 uur | Gedeelde 7-daagse huisproef |
| 5. Conversationele voice | `ONTWORPEN` | Identiteit, geheugen, skills en latencydoelen zijn herbruikbaar | Audio-contract, streaming STT/TTS, client, onderbreken, reconnect, voorkeursroute, security en skillreadback | 24-48 uur | 12-24 uur | 4-8 uur gebruikers-/apparaatmeting |
| 6. Browser en zelfstandige computer | `ONTWORPEN` | Mandaat-, ledger-, runner- en readbackbouwstenen bestaan gedeeltelijk | Geïsoleerde browser, grants, meekijken, overnemen, stop, echte webtaak, resultaatcontrole en crashherstel | 24-48 uur | 12-24 uur | Testaccount en gedeelde 7-daagse autonomierun |
| 7. Mensen leren kennen en skills maken | `IN BOUW` | Persoonsfeiten, correcties, reflectie en één eigen verbetering werken | Inzien/vergeten, usage/kosten, quota, on-the-fly skillquarantaine, evaluatie, promotie en rollback | 24-48 uur | 12-24 uur | Gedeelde 7-daagse groeiproef |
| 8. Subagents, dashboard en Kanban | `IN BOUW` | Kleine status-/identiteitspagina en duurzame taakleases bestaan | Workergrants, handoff, heartbeat, crash/reclaim, Kanban, chat-/dashboardbediening en multi-workerproef | 20-40 uur | 10-20 uur | 2-4 uur gebruikerssessies |
| 9. Eigenaarmodus | `ONTWORPEN` | Mandaat-, stop- en auditbouwstenen bestaan deels | Na projecten 6-8 één ruim maar intrekbaar mandaat boven browser, skills en subagents sluiten | 6-12 uur | 6-12 uur | 1-3 uur risicoacceptatie |
| 10. Installatie, update en verplaatsen | `IN BOUW` | Images, file-secrets, back-up, schema-installatie en A/B-onderdelen bestaan | Wizard, identiteit, Telegram/HA/e-mail, veilige secretinvoer, twee fresh installs, restore, update en rollback | 24-48 uur | 12-24 uur | Twee machines; 1-3 kalenderdagen testvenster |
| 11. Snelheid, omvang en kosten | `IN BOUW` | Codegrens, eerste geheugenlimieten en twee gunstige kleine snelheidsmetingen bestaan | CI-grenzen, streaming tekst, p50/p95, tokens/kosten, sidecarbudgetten en 200-beurtentest | 8-16 uur | 6-12 uur | Metingen lopen mee in gedeelde 7 dagen |
| 12. Eerlijke openbare benchmarks | `ONTWORPEN` | LAB-36, drie ontwikkelcases en publicatieregels bestaan; prompts, tools en context waren niet gelijk | Officiële GAIA, AgentBench en OSWorld-runs plus gecontroleerde vijf-agentmatrix | 24-56 uur | 12-24 uur | Dataset/compute 1-10 dagen, parallel waar mogelijk |
| 13. Documentatie als controleerbaar product | `IN BOUW` | Leesbare overzichten, technische gates, reviews en padinventaris bestaan | Componentregister, ownershipchecker, unieke gate-/bewijskoppeling en dashboardprojectie | 8-16 uur | 4-8 uur | Geen belangrijke wachttijd |
| 14. Vrije kanalen en connectoren | `ONTWORPEN` | Connectorneutrale kern en veilige read-only MCP-basis bestaan | Vanuit chat één niet-ingebakken kanaal maken/testen, least-privilege koppelen, stoppen en terugrollen | 12-32 uur | 6-16 uur | Providergoedkeuring 0-6+ weken; blokkeert V1 niet |
| 15. Eindfreeze en veilige overgang | `IN BOUW` | A/B, back-ups, kandidaatbuild en veel losse receipts bestaan | Eén image bevriezen, alle eindproeven herhalen, gebruikersacceptatie en rollbackceremonie | 6-12 uur | 12-20 uur | Minimaal één gedeelde 7-daagse ongewijzigde run |

## Wat kan parallel en wat bepaalt de einddatum?

Na het veilig sluiten van runtime/B kunnen drie bouwstromen echt naast elkaar
lopen:

1. gezinschat, algemene skilluitvoering en huisaanwezigheid;
2. voice en reactietijd;
3. browserautonomie, leren en daarna subagents/eigenaarmodus.

Installer, benchmarkadapters en documentatiecheckers kunnen gedeeltelijk
parallel worden gebouwd, maar hun eindbewijs wacht op de bevroren versie. De
eindversie kan pas worden bevroren wanneer de blokkerende onderdelen niet meer
veranderen. Voice, browserautonomie en installer kennen de grootste technische
onzekerheid. Iedere noodzakelijke fix tijdens de officiële zevendaagse proef
start die klok opnieuw.

De resterende portfolio bevat ongeveer **342-696 actieve agenturen** voor
bouwen, integreren en reviewen. Omdat drie bouwstromen parallel lopen en veel
werk wordt hergebruikt, is dit geen optelsom in kalenderdagen. De eerste drie
projecten vertegenwoordigen samen 40-76 agenturen; parallel bouwen en reviewen
maakt daar onderstaande verstreken tijd van.

Mijn huidige raming bij tijdige toegang tot accounts en apparaten:

- **Eerste veilig beheerde B-alpha met gezinschat en één echte skillketen:**
  ongeveer 2-5 verstreken werkdagen met drie parallelle bouwstromen en review,
  plus het afgesproken omschakelvenster.
- **Veilige vervanger van de oude Eva**, inclusief presence, voice en de
  gezamenlijke zevendaagse eindproef: ongeveer 2,5-5 kalenderweken.
- **Volledige concurrerende productbelofte**, inclusief browser, leren,
  subagents/Kanban, installer, benchmarks en eindproef: ongeveer 5-9
  kalenderweken.
- **Een extra kanaal zoals WhatsApp:** de bouw past in bovenstaande uren, maar
  externe providergoedkeuring kan afzonderlijk 0-6+ weken duren en blokkeert
  de eerste release niet.

Dit zijn geen menselijke programmeerschattingen en geen beloftes. Na P1.1 en
P2.1 vervangen we deze banden door de werkelijk gemeten tijd per kleine slice.
Codegeneratie kan zeer snel gaan; onafhankelijke review, hardwaregedrag en een
duurproef worden niet kunstmatig honderd keer korter gerekend.

## De eerstvolgende twee zichtbare mijlpalen

1. **P1.1 — één versiepassende releasekandidaat.** Het eerste checkpoint is
   nu onafhankelijk groen: 31 release-inputs en 56 testbestanden zijn exact en
   reproduceerbaar gebonden. Daardoor is ook het echte resterende gat zichtbaar:
   de oude build heeft 38 dependencies en de huidige Eva-bron 39. Fermat sluit
   nu die 39-set als één bouwinput; daarna herhaalt Curie build, start, herstart,
   schemadrift- en secret-nonleakcontrole. Dit verandert A of B nog niet.
2. **P2.1 — echte Eva 2-gezinschat.** Na de veilige B-adoptie demonstreert
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
