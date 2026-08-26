# Eva 2 — actuele voortgang

Laatst bijgewerkt: 2026-08-26

Dit is het korte meekijkdocument. Ik werk het bij na een afgeronde verticale
stap en uiterlijk bij de volgende uurupdate. De technische waarheid blijft in
[`design/architecture/living-core-v1.md`](design/architecture/living-core-v1.md);
het begrijpelijke hoofdprojectbord met wat af is, wat resteert, vaste
teamrollen en ramingen staat in
[`PROJECTEN_EN_PLANNING.md`](PROJECTEN_EN_PLANNING.md);
de harde keuze om eerst onderhouden open source te beproeven en alleen Eva's
eigen kern zelf te houden staat in
[`OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`](OPEN_SOURCE_EN_DEPENDENCYKEUZES.md);
de leesbare scheiding tussen wat Eva nu kan, wat nodig is voor de overgang en
het volledige productdoel staat in
[`EVA2_MOGELIJKHEDEN_EN_GATEN.md`](EVA2_MOGELIJKHEDEN_EN_GATEN.md); de
vergelijking in gewone taal en de conclusie wie vandaag waar voorstaat staan in
[`CONCURRENTIE_EN_HERGEBRUIK.md`](CONCURRENTIE_EN_HERGEBRUIK.md); alle precieze
gatecodes, checkpoints en technische nonclaims staan apart in
[`TECHNISCHE_PRODUCTGATES.md`](TECHNISCHE_PRODUCTGATES.md) en
[`TECHNISCHE_MOGELIJKHEDEN_EN_GATEN.md`](TECHNISCHE_MOGELIJKHEDEN_EN_GATEN.md);
de oude-naar-nieuwe vergelijking voor spontane huisaanwezigheid staat in
[`OUDE_EVA_VS_EVA2.md`](OUDE_EVA_VS_EVA2.md); de verplichte
leesbare OpenBot-vergelijking staat in
[`OPENBOT_VERGELIJKING.md`](OPENBOT_VERGELIJKING.md); de verplichte
full-duplex voice-mode, kanaalkeuze en latencygates staan in
[`design/product/conversational-voice-v1.md`](design/product/conversational-voice-v1.md);
de harde code-, dependency-, image- en reactietijdbudgetten staan in
[`design/product/performance-and-complexity-v1.md`](design/product/performance-and-complexity-v1.md);
het corrigeerbare persoonsmodel, de groeicyclus en de geheugen-, token- en
kostenbudgetten staan in
[`design/product/learning-and-person-model-v1.md`](design/product/learning-and-person-model-v1.md);
de eenvoudige installatie, lokale koppelwizard, `SOUL.md`/`MEMORY.md`-grens en
verplaats-/updateroute staan in
[`design/product/installer-and-onboarding-v1.md`](design/product/installer-and-onboarding-v1.md);
de documentatiekaart, componentregistratie, bewijsstatus en optimalisatiegrens
staan in [`DOCUMENTATIE_EN_ONDERHOUD.md`](DOCUMENTATIE_EN_ONDERHOUD.md);
het subagentcontract, duurzame werkbord en Kanban-dashboard staan in
[`design/product/delegation-and-workboard-v1.md`](design/product/delegation-and-workboard-v1.md);
het contract voor openbaar vergelijkbare AgentBench-, GAIA- en OSWorldscores
staat in
[`design/product/comparable-agent-benchmarks-v1.md`](design/product/comparable-agent-benchmarks-v1.md);
Eva's vergaande maar begrensde Eigenaarmodus staat in
[`design/product/owner-autonomy-mode-v1.md`](design/product/owner-autonomy-mode-v1.md);
het gelaagde geheugen met vaste Hindsight-L2 en optionele Mnemosyne-/Honcho-
aanvullingen staat in
[`design/product/tiered-memory-and-person-model-v1.md`](design/product/tiered-memory-and-person-model-v1.md);
de verplichte
teamvolgorde staat in [`TEAM_WERKWIJZE.md`](TEAM_WERKWIJZE.md);
de controleerbare live receipts staan in
[`evidence/living-core/`](evidence/living-core/).

## Projectoverzicht in gewone taal

| Hoofduitkomst | Wat aantoonbaar staat | Status van het complete project |
|---|---|---|
| Betrouwbare kern | Core-live 15/15, native historisch 9/9, echte HA-basis, duurzaam geheugen en 75/75 algemene action-chaintests | Sterke kern, maar nog niet gezamenlijk live op één eindimage |
| Menselijke gezinsassistent | Tekst, persoonsgeheugen, accountbinding en groepsprivacy hebben stevige bouwstenen; de eerste veilige voicegrens laat alleen een definitieve private transcriptie in dezelfde gesprekcore toe | Echte gezinschat, spontane presence en bellen met streaming audio/onderbreking nog open |
| Zelfstandig handelen | Doelen, bewijs en crashherstel bestaan. Een subagent kan begrensd vanuit Eva worden gestart en gestopt. Een afgeschermde browser las één vaste pagina en voerde via Eva's duurzame logboek één vaste klik uit met onafhankelijke controle achteraf | Publieke websites, echte accounts, meekijken/overname, volledige skillcyclus en Eigenaarmodus nog open |
| Beheerbaar product | A/B, back-ups, offline image, secrets en één geïntegreerde 3D-cockpit met status, identiteit, Mijn kring en read-only Kanban bestaan | Eigenaar-authenticatie, bordbediening, installer, update/restore en releasefreeze nog open |
| Eerlijk vergelijkbaar worden | Drie met dezelfde vragen uitgevoerde ontwikkelcases en LAB-36-bewijs bestaan; Eva had een kleine gesprekswinst en sterk bewijs voor generieke resultaatcontrole via haar eerste HA-skill | Prompts, tools en context verschilden; officiële AgentBench/GAIA/OSWorld- en volledige gecontroleerde vijf-agentvergelijking zijn nog open |

## Sinds 03:00 daadwerkelijk opgeleverd

Tussen 03:00 en statuscheckpoint `8d93338` bevat Git 41 kleine checkpoints.
Dat zijn niet 41 producten, maar vier gesloten, begrijpelijke uitkomsten:

| Uitkomst | Implementatie en zichtbare closure | Wat jij ervan terugziet |
|---|---|---|
| Kring en geheugen in de cockpit | implementatie `1c62778`; bewijsbinding `c500f74` | De centrale pagina toont privacyveilige actuele persoons-, deel- en geheugenstatus |
| Subagent van opdracht tot werkbord | implementatie `f9373f1`; cockpit-/bewijsbinding `db4cedd` | Starten, volgen, actief stoppen, aparte workerimage en duurzame Kanbankaart zijn technisch groen |
| Afgeschermde browser-read | `27965d8` | Een echte Chromium las één vaste lokale pagina zonder host- of internetroute |
| Duurzame browseractie | `72625e0` | Eén vaste klik liep via Eva's logboek en aparte servercontrole; twee providerfouten gaven geen blinde tweede klik |

De subagentuitkomst was te breed als één werkblok: motor, opslag, chatbediening,
stop, workerimage, Kanban en bewijs liepen achter elkaar voordat de zichtbare
projectstatus werd bijgewerkt. Vanaf `72625e0` blijft één capabilityproject
actief totdat code, test, onafhankelijke review én cockpitstatus samen gesloten
zijn. Het actieve project blijft daarom **browser en zelfstandige computer**;
de volgende uitkomst is één afgeschermde publieke testsite met grant en live
stop. Voice of een ander groot capabilityproject start niet tussendoor.

## Nieuwste afgeronde stap

Checkpoint `d8b8d2f` sluit een belangrijke **stopknop-kloof vóór een externe
actie**. Eerder kon stoppen alleen vóór autorisatie; nu kan de eigenaar ook
stoppen nadat Eva de actie al technisch heeft toegestaan, maar voordat een
browserproces of andere provider daadwerkelijk is gestart. De dispatcher en
stoproute delen hiervoor dezelfde database-lock. De uitkomst is dan eerlijk
`geannuleerd`: één eventueel aangemaakte autorisatiepoging blijft als auditspoor
bestaan, maar er is nul provider-call-start, nul browserproces, nul klik en nul
externe receipt. Acht echte PostgreSQL-gevallen zijn groen, inclusief beide
races (stop eerst of autorisatie eerst), replay, manipulatie en rollback. De
onafhankelijke review is nog open; daarom is dit **getest**, niet als live
browser- of Telegramproef geclaimd.

Dezelfde stopgrens is daarna in checkpoint `6d32d96` door de bestaande,
volledig geïsoleerde Chromiumproef gelopen. Vier scenario's zijn groen: één
vaste klik met onafhankelijke server-readback, crash vóór klik, crash ná klik
en de nieuwe stop ná autorisatie. In dat laatste geval telde de onafhankelijke
testserver nul POSTs en nul toestandsovergangen; de browserworker kreeg geen
callmateriaal.
De browser had één intern netwerk, nul gepubliceerde poorten en geen internet-
egress. Receipt:
[`browser-a02-stop-before-call-2026-08-26.json`](evidence/living-core/browser-a02-stop-before-call-2026-08-26.json).
Dit is nog steeds geen publieke site, account, download, overname, Telegram of
B-deploy; onafhankelijke review blijft open.

De Telegramproef heeft nu ook een **één-opdracht voorbereiding**: de operator
geeft account A op en kan account B als een afzonderlijke testpersoon
toevoegen. `tools/prepare_telegram_test_config.py` vraagt de aparte
BotFather-testtoken onzichtbaar op, maakt de private testbestanden met de
juiste containereigenaren en schrijft een geheimevrije launcherconfiguratie.
`tools/admit_telegram_test_group.py` leest daarna één unieke marker uit de
lege testgroep, haalt alleen de exacte negatieve groeps-ID op en voegt die
atomair alleen voor A en B toe. Zo test `/link` eerlijk een tweede account van
dezelfde persoon, terwijl de groepsproef werkelijk twee personen test. Geen
van beide opdrachten start een container, poller, migratie of gesprek. De
roottool opent private token- en bindingsbestanden eerst en controleert daarna
diezelfde inode; een symlink, hardlink, verkeerde eigenaar/mode of een te groot
bestand wordt vóór de Telegram-call geweigerd. De
exacte afvinkstappen staan in `deploy/living-core/TELEGRAM_TEST.md`. De exact
gepinde PostgreSQL-testinput moet nog wel lokaal bij Docker beschikbaar zijn;
de launcher vervangt die nooit stil door `latest` of een andere tag. Dit is
testvoorbereiding, geen Telegram-liveclaim of B-wijziging.

Checkpoint `e11c136` maakt de **Telegram-testimage daadwerkelijk lokaal
bruikbaar**. Hij bouwt uit een schone, vaste Git-commit en een bestaande,
gepinde Eva-parent met exact dezelfde dependency-lock. De build heeft geen
netwerk of package-installatie gebruikt; de gemaakte image importeert de
Telegram-testmodules echt. Samen met checkpoint `7017c25` is de omgeving dus
niet meer alleen een Compose-ontwerp: zij heeft nu een bruikbare testimage,
een eigen lege PostgreSQL-database, volume en intern netwerk. De private
BotFather-token, twee testaccounts en de lege testgroep ontbreken nog; zonder
die drie doen we nadrukkelijk geen Telegram-liveclaim. Zie
[`deploy/living-core/TELEGRAM_TEST.md`](deploy/living-core/TELEGRAM_TEST.md).

Checkpoint `7017c25` maakt de **volledige aparte Telegram-testomgeving**
beheersbaar klaar. Zij heeft een eigen lege PostgreSQL-database, volume en
intern netwerk, plus een vaste diagnosecore die nadrukkelijk géén Eva-gesprek
speelt. De startvolgorde is nu: eigen testondersteuning starten, die werkelijk
controleren, pas dan het schema maken, opnieuw controleren en pas als laatste
de Telegram-poller starten. Een fout vóór het schema doet geen migratie; een
fout ná het starten stopt de poller weer en laat de supportomgeving staan om
te onderzoeken. De DSN mag alleen naar
`postgres-test:5432/eva2test` wijzen en het volume mag geen verborgen
productiebind-mount zijn. **23 gerichte controles**, linting, compile en beide
Compose-renders zijn groen; de onafhankelijke herreview is groen. Dit is nog
steeds voorbereiding, niet de Telegramproef zelf: er is geen echte BotFather-
bot, account, groep, bericht, PostgreSQL-run, Eva-gesprek of B-deploy gestart.
Zie [`reviews/telegram-test-support-7017c25.md`](reviews/telegram-test-support-7017c25.md)
en de korte operatoruitleg in
[`deploy/living-core/TELEGRAM_TEST.md`](deploy/living-core/TELEGRAM_TEST.md).

Checkpoint `57f36f0` maakt voice nu **een echte kernvoorbereiding**, geen
losse toekomsttekening. Een servergebonden `VoiceSession` brengt dezelfde
persoon, gesprek, identiteit, policy, privacy en deadline mee. Alleen een
definitieve transcriptie kan daaruit naar Eva's bestaande private gesprekcore;
ruwe audio en tussentekst komen niet in die grens. Een onderbreking stopt
uitsluitend hoorbare output en een onaf antwoord, nooit stil een huisactie.
Zeven directe grensgevallen plus de bestaande gesprek- en schemacontroles
zijn samen **21/21 groen**. Dit is nog geen microfoon, voiceprovider,
Telegram-call, Home Assistant-route, latencyresultaat of live voice; de
onafhankelijke review is eveneens nog open.

Checkpoint `8438fea` sluit de volgende, eerlijke veiligheidsrem: na een al
gestarte providercall kan de geverifieerde eigenaar één duurzaam **stopverzoek**
opslaan. Eva start die call niet opnieuw en wacht op het echte resultaat. Acht
tijdelijke PostgreSQL-proeven zijn groen, inclusief de race met een
providerresultaat, historische replay na bindingintrekking en zeventien
vervalsingspogingen. De onafhankelijke review is groen. Dit is nadrukkelijk
geen Telegramknop, proceskill, rollback, B-deploy of bewijs dat een externe
klik is gestopt; Eva moet nog teruglezen wat er werkelijk gebeurde. Zie
[`reviews/action-chain-effect-post-call-stop-request-8438fea.md`](reviews/action-chain-effect-post-call-stop-request-8438fea.md).

De vorige rem, checkpoint `fedfb15`, blijft daarnaast geldig vóór call-start:
een voorbereide actie kan dan volledig worden geannuleerd met nul provider-I/O.
Zie
[`reviews/action-chain-effect-preauthorization-cancellation-fedfb15.md`](reviews/action-chain-effect-preauthorization-cancellation-fedfb15.md).

De planning telt geen seriële menselijke werkdagen meer op. De huidige raming
is 0,5-2 verstreken werkdagen bij drie parallelle bouwstromen plus review voor
een veilig beheerde B-alpha met gezinschat en één echte skillketen, 8-14
kalenderdagen voor een veilige vervanger met presence, voice en de verplichte
zevendaagse eindproef, en 2-4 kalenderweken voor de volledige productbelofte.
Bouwuren, reviewuren en echte wachttijd staan afzonderlijk in
`PROJECTEN_EN_PLANNING.md`; Home Assistant is één verwisselbare skill en niet
Eva's unieke hoofdfeature.

## Nu

- Dependency-first audit DF00: `pydantic-ai-slim==2.32.1` staat al exact in
  Eva's lock maar wordt nog niet als agentframework gebruikt. De actuele
  Pydantic AI `2.34.0` en Harness `0.25.0` leveren al voice, subagents,
  browserkoppelingen, MCP, web-UI en duurzame DBOS-routes; Hindsight heeft al
  een lokale beheer-UI. Eva bouwt
  daarom geen eigen voice-, browser-, worker-, Kanban-, scanner- of
  updateplatform voordat een gepinde dependencyproef een concreet gat bewijst.
  Ook Eva's 404 regels handgeschreven MCP-client en 1.023 regels directe
  Home Assistant-/automationroute zijn expliciet onderzocht. De MCP-client
  blijft na
  twee gemeten `NO-GO`-proeven voorlopig staan; voor de veel grotere
  Home Assistant-route is `homeassistant-ai/ha-mcp v8.3.0` nu de eerste serieuze
  community-sidecar. De onafhankelijke
  onderhoudsaudit geeft alleen Hindsight in zijn huidige smalle L2-rol en
  Renovate als niet-automerge voorstelbot een begrensde `GO`; onder meer
  Pydantic AI, DBOS, Home Assistant MCP Server, HA-MCP, Playwright MCP,
  Hindsight UI, LiveKit, OpenClaw, React-Admin, PyInstaller, Agent Skills en
  Trivy blijven `PROEF`. Harness begon daar ook, maar checkpoint `55cf7cc`
  promoveert uitsluitend zijn geïsoleerde toolvrije/effectvrije subagentengine
  naar `GO`; runtime-integratie en productpromotie blijven open. De officiële
  MCP SDK is voor de smalle V1-client juist `NO-GO`; Browser Use en een tweede
  workflowplatform komen nu niet in V1.
  `DF01`-`DF06` en de eerste kandidaten staan in
  `OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`; dit is een selectiecontract, nog geen
  goedkeuring of installatie van nieuwe productdependencies.

- Subagentdependency DF04: Pydantic AI `2.34.0` en Harness `0.25.0` voeren in
  een aparte exacte 42-package-omgeving echte toolvrije child-runs uit. Eva
  bouwt daarom geen eigen spawn-, modelrun-, childcontext-, callbudget- of
  coöperatieve async-time-outengine. Naam, tekstinstructie, workerlimiet,
  calls, tokens en time-out worden fail-closed begrensd; diskagents,
  parenttools, gedeelde capabilities en recursie staan uit. **22/22 tests**,
  Ruff, `pip check` en de onafhankelijke herreview zijn groen op `55cf7cc`.
  Productie is niet gewijzigd. Duurzame PostgreSQL-handoff, eigen
  child-usage-receipt, harde processkill, crashherstel en Kanban blijven open;
  zie `experiments/harness-subagents-v1/` en
  `reviews/harness-subagent-engine-55cf7cc.md`.
- Duurzame-subagentlijn: Harness blijft de korte parentgestuurde fork/join-
  motor, maar wordt niet geforceerd als databaseworker omdat zijn publieke
  route alleen tekst teruggeeft. Een dunne adapter rond de publieke Pydantic AI
  `Agent.run()` levert nu gesloten `DelegatedWork/v1`-output, usage,
  cancellation en gesaneerde foutcategorieën. De eerste onafhankelijke review
  gaf terecht `NO-GO` op schijnkosten, deadlineoverschrijding, onbekende usage
  als nul en verkeerde pakketplaatsing. De lokale correctie bindt nu een
  verzegeld model-/worker-/prijsprofiel, absolute deadline, provider-outputlimiet,
  contracthash en uitgezette telemetry. 23 nieuwe tests plus de bestaande
  22 Harnesschecks zijn **45/45 groen**. De onafhankelijke herreview van
  `b7e9af7` is groen. WB01a en WB01b hebben daarna het contract geregistreerd
  en `delegated_work` als zesde soort toegelaten in Eva's bestaande taakrij.
  De lijn is nu verder dan het losse contract: een echte tijdelijke PostgreSQL
  neemt de handoff atomair aan, leaset en reclaimt, zet vóór uitvoering een
  eenmalige call-startfence, bewaart resultaat of ambiguïteit en projecteert de
  taak als veldarme kaart in de vijfkoloms cockpit. De eerste providerloze
  Pydantic-run ging werkelijk door die hele keten; een herstart na
  `call_started` veroorzaakte geen tweede run. Latere checkpoints hebben dit
  inmiddels uitgebreid met de officiële-SDK-worker in een aparte
  kandidaatimage en commandolaag voor starten, volgen en actief stoppen. Open
  blijven een eigenaar-goedgekeurde externe providerproef via Telegram/B,
  eigenaar-authenticatie, portable-v2-schemarelease en productpromotie. B is
  niet gewijzigd. Zie
  `reviews/pydantic-tool-free-child-candidate-b7e9af7.md` en
  `reviews/delegated-work-database-admission-d510366.md`.

- Browseractie A02: checkpoint `72625e0` laat de gepinde Microsoft Playwright
  MCP-/Chromium-sidecar via Eva's bestaande PostgreSQL-logboek precies één
  vooraf toegestane lokale knop bedienen. Eva leest de servertoestand apart
  terug en zet pas daarna `STATE_VERIFIED`. In twee echte niet-nul eindigende
  providerprocessen — vóór en ná effect-I/O — bewaart zij de uitkomst eerlijk
  als onduidelijk en klikt zij na hervatten niet blind opnieuw. De schone
  Git-archive-review herhaalde 37/37 connectorgevallen, Ruff, 8/8 bronhashes en
  3/3 disposable PostgreSQL-/Chromiumscenario's groen. Dit is technisch lokaal
  bewijs, geen publieke website, download, account, Telegram/B, browser-SIGKILL
  of algemene computerbediening; zie
  `reviews/browser-action-a02-72625e0.md`.
- Browser-/workflowbesluit DF03/DF04: Harness PlaywrightBrowser blijft alleen
  een wegwerpvergelijking omdat hij in Eva's proces zit en niet met duurzame
  Pydantic-replay samengaat. Stock DBOS `2.31.0` is na bronaudit
  `NO-GO`: upstream lost een belangrijke dubbele-workerfout op, maar mist nog
  Eva's begrensde retry/no-DDL/commit-ambiguïteitsgrens. Alleen een smalle
  `2.31.0+eva.1`-rebase wordt beproefd; er worden nu nul workerregels op basis
  van DBOS verwijderd.

- MCP-dependencybesluit DF02c: de officiële MCP Python SDK `2.1.1` is met een
  exacte 48-package-hashlock geïnstalleerd; hij voegt tien packages en voor
  Eva's vaste read-route onnodige GET/DELETE-, reconnect-, callback- en
  sessiemogelijkheden toe. Alleen `mcp-types==2.1.1` is daarna werkelijk in een
  tijdelijke productdiff gebruikt: 22 tests groen, maar **87 productregels
  erbij en niets verwijderd**. Beide routes zijn daarom voor deze smalle client
  `NO-GO`; de kandidaatcode is teruggedraaid en productie bleef gelijk.
  Checkpoints `c5b7d88` en `c83d395`, exacte locks, replaychecker en
  onafhankelijke review zijn groen; zie `experiments/mcp-sdk-v1/` en
  `reviews/mcp-dependency-bakeoff-c5b7d88.md`.

- Home Assistant-communityproef DF02d: `homeassistant-ai/ha-mcp v8.3.0` is op
  exacte bron en OCI-digest gebonden en lokaal zonder netwerk, secrets of echt
  Home Assistant gestart. De handshake werkte; read-only waren 39 tools
  zichtbaar, waaronder toestand, historie en automation-lezen. Een automation-
  en backupwrite werden met `READ_ONLY_MODE` geblokkeerd. Dit is kandidaatbewijs,
  geen B- of live-huisbewijs. De bron-, mapping- en zeven toolbeschrijvingen
  zijn nu exact gebonden; twaalf mutatietests en onafhankelijke herreview zijn
  groen (`c03942d`). De verdiepende audit vindt echter slechts circa 174–192
  direct vervangbare transportregels. Dat is minder dan de vereiste 300 tegen
  hoogstens 180 integratieregels. Een simpele productieomschakeling is daarom
  `NO-GO`; alleen een kleine state→service→aparte-stateproef mag nog bewijzen
  of ook semantische code veilig kan verdwijnen. Echt huis, latency, crash,
  upgrade en rollback staan open. Zie `experiments/ha-mcp-sidecar-v1/` en
  `experiments/ha-mcp-parity-v1/`.

- Dependencykandidaat DF02a: checkpoint `8526314` laat de officiële OpenAI
  Python-SDK `3.3.1` nu werkelijk één gewone Responses-stream vervoeren en
  decoderen. Eva houdt identiteit, leasepaar, tools, retries, usage en
  eindantwoord. De volledige 38→41-packagekandidaat is hashgebonden; 18
  SDK-tests, 63 gerichte regressies en onafhankelijke review zijn groen. Een
  koude eerste modelcall hield de totale tijdsgrens en herhaalde time-outs
  lieten geen thread- of bestandsdescriptorgroei achter. Productie gebruikt nog
  steeds `legacy_urllib`: echte providerpariteit, latency, wheelhouses,
  licenties/SBOM/scan, offline image, B en rollback zijn open. Zie
  `experiments/openai-sdk-v1/` en
  `reviews/openai-sdk-transport-candidate-8526314.md`.

- Pydantic-voorwaarde DF02a0: checkpoint `4e5d5af` kan gerapporteerd
  modelgebruik nu providerneutraal over één of twee modelcalls optellen, met
  het antwoord in PostgreSQL bewaren en na herstart exact teruglezen. Oude en
  huidige niet-aangesloten providers blijven eerlijk `unmeasured` met lege
  waarden, nooit alsof nul tokens zijn gebruikt. Er is nog geen
  runtimebudgetclaim. Onafhankelijke review `ae088c9`, 69 conversationtests
  en een echte wegwerp-PostgreSQL-16.13-proef zijn groen. De officiële
  Pydantic-provideradapter en echte usage-/budgetwiring blijven DF02-vervolgwerk.

- Releaseproject P1.1: checkpoint `0bbfe3d` bindt de versiepassende statische
  39-package-input nu onafhankelijk groen aan de huidige 4D-bron. Een fysieke
  wheelhouse, imagebuild, start, B-adoptie of release is hiermee niet bewezen.

- Gezinschat P2.1a: checkpoint `187f797` laat de normale Telegrambron een
  dynamisch gekoppeld account veilig herkennen, ook na procesherstart, zonder
  een conflict stil te overschrijven. De echte B-, netwerk-, multi-account- en
  groepstest blijven open.

- Geheugenlagen MA00/MA01: PostgreSQL levert huidige beurt plus maximaal twaalf
  recente berichten; Hindsight blijft L2-langetermijngeheugen. Mnemosyne
  3.15.1 is alleen een optionele lokale L1-kandidaat en Honcho 3.0.12 alleen een
  optioneel achtergrond-persoonsmodel; geen van beide vervangt Hindsight en
  Mem0 heeft geen V1-rol. Acht fail-closed checkertests zijn groen; de echte
  gate blokkeert terecht op beide niet-gematerialiseerde sidecars en een open
  beslissing. Dit is `tested`, nog niet onafhankelijk `reviewed`; A/B en
  bestaande geheugendata zijn niet gewijzigd.
- Vergelijkbare benchmarks CB00: AgentBench, GAIA en de actuele OSWorld 2.0-
  release `v2026.08.08` zijn als drie verschillende openbare scorelagen
  vastgelegd. Iedere toekomstige score moet de upstream release, split,
  officiële scorer, agent/model/config, budgetten en ruwe trajecten publiceren.
  Native en gecontroleerde frameworkruns krijgen aparte tabellen; LAB-36 wordt
  niet als openbare leaderboardscore hernoemd. CB01-CB07 en alle echte runs
  staan nog open.
- Eigenaarmodus OM00: Hermes' actuele officiële `Godmode` blijkt een optionele
  jailbreak-/safety-bypassskill, geen autonome superstand. Eva krijgt in plaats
  daarvan één expliciete Eigenaarmodus boven haar bestaande mandaat/ledger:
  vooraf gevolgen en grenzen vastleggen, daarna zelfstandig handelen zonder
  bevestiging per tussenstap, met dashboardstatus en directe stop. OM01-OM06
  staan nog open; er is geen productie-safety-bypass toegevoegd.
- Subagents/werkbord WB00: Hermes' korte delegatie en duurzame multi-profile
  Kanban zijn uit de officiële documentatie van elkaar onderscheiden. Harness
  neemt inmiddels interactieve fork/join over en Pydantic AI de reeds gekozen
  child-run. De providerloze proef slaat een taak duurzaam op, pakt haar één
  keer op, bewaart het resultaat en toont haar op het vijfkolomsbord in de
  cockpit. De actuele kandidaat voegt daar de officiële-SDK-worker in een
  aparte image en de commandolaag voor starten, volgen en actief stoppen aan
  toe; een herstart na het begin veroorzaakt geen tweede modelrun.
  WB01-WB07 voegen nog de eigenaar-goedgekeurde externe providerproef via
  Telegram/B, eigenaar-authenticatie en bordbediening toe;
  React-Admin OSS plus `dnd-kit` kunnen die bediening presenteren.
  Er komt geen eigen subagentmotor, tweede taakdatabase of tweede Eva-brein.

- Bezig: MC03c0 bereidt na bewezen read precies één duurzaam effectintent voor;
  MC03c1a autoriseert precies één poging zonder callable bytes en MC03c1b
  hercontroleert vlak vóór I/O alle authority plus deadline. Zij commit exact
  één `ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED` en geeft alleen aan de eerste
  caller het private proces- en RPC-materiaal. Dezelfde duurzame idempotency key
  bereikt de provider als RPC- en connector-request-ID. MC03c2 start daarna
  uitsluitend die eerste sealed subprocesscall en bewaart provideracceptatie,
  afwijzing of exacte fout plus evidence en optioneel receipt atomair. Een
  onopgeloste call-start wordt nooit blind herhaald. MC03c3a zet uitsluitend
  na een duurzaam geaccepteerd effect één afzonderlijk geautoriseerde,
  onveranderlijke `effect_verify`-read klaar. De read gebruikt een eigen
  `verification_read`-manifest, scope en tijdlijn en maakt de effectstap nog
  niet af. MC03c3b1 autoriseert daarop exact één actuele sealed providerpoging
  en bewaart die atomair zonder callable bytes. MC03c3b2 commit vlak vóór I/O
  exact één call-start en geeft alleen aan die eerste caller het sealed proces-
  en `effect.readback`-RPC-pakket. MC03c3c voert dat pakket nu eenmaal uit en
  bewaart antwoord, protocolfout of ambiguïteit atomair als providerbewijs en
  waarneming. MC03c3d maakt uit zo'n reeds opgeslagen waarneming precies één
  onveranderlijk `EffectReadback/v1`; zij roept de provider daarvoor niet
  opnieuw aan en bewaart ook mismatch, onbekend of niet-waargenomen eerlijk.
  MC03c3e vergelijkt daarna de door Eva bedoelde waarde onafhankelijk met de
  werkelijke providerstaat. Alleen een verse exacte match schrijft in één
  transactie `STATE_VERIFIED`, zet effect en planstap op succes en maakt
  uitvoerbare vervolgstappen vrij. Niet-waargenomen, tegenstrijdige,
  onbesliste of inhoudelijk verkeerde toestand blijft gesloten. De commits
  `0acdd33`, `f43b73f`, `a99563b`, `5a1556e` en `0ca0c4b`, 45
  applicationtests, negen gerichte PostgreSQL 16.13-cases en de volledige
  75-case action-chainreeks zijn groen. De gerichte proef bevat nu werkelijk
  een derde stap: die blijft
  dicht tot de effectreadback klopt, wordt daarna eenmaal vrijgegeven, kan na
  herstart worden opgepakt en rolt bij een geforceerde fout mee terug. De
  geïntegreerde herstelproef heropent de database na iedere procesgrens en
  bewijst exact één effectcall, één readbackcall, één `STATE_VERIFIED` en één
  dependencyvrijgave zonder dubbele replay. Dit is `tested`, nog niet
  onafhankelijk `reviewed`; er is nog geen echte Home
  Assistant-call of fysieke readback via B uitgevoerd en A en B zijn niet
  gewijzigd.
- Nieuw vastgelegd productdoel: ChatGPT-achtige full-duplex voice met streaming
  STT/TTS, onderbreken en dezelfde Eva-context. De voorkeur is `Bel Eva` in een
  Telegram Mini App; Home Assistant Assist/Wyoming is de lokale fallback. De
  huidige kern is Python 3.12. Rust is alleen een mogelijke geïsoleerde
  audio-dataplane wanneer metingen bewijzen dat dit nodig is; er loopt geen
  Rust-herschrijving.
- Omvang/reactiviteit PC00: Git-checkpoint `35e2c0d…` bindt 51.928 regels in
  133 productbestanden aan een exact bronmanifest; de dirty werkboomobservatie
  lag tijdens ontwerp op 53.428 niet-gegenereerde regels. V1 heeft een harde grens van 75.000
  handgeschreven productregels, maximaal twaalf directe coredependencies en
  p95-doelen voor acknowledgement, eerste inhoud en voice-barge-in. PC01-PC06
  maken teller, hot-path, sidecars, eindmeting en degradatiebewijs af.
- Leren/persoonsmodel LG00: Eva's bestaande persoonsgebonden Hindsight-geheugen,
  correcties, M01-continuiteit en I02-verbetering zijn van een volledige
  Hermes-achtige leercyclus onderscheiden. LG01-LG07 maken een inzichtelijk,
  corrigeerbaar persoonsmodel, token-/kostenreceipts, fysieke geheugenquota,
  store-onafhankelijke on-the-fly skillcreatie, skillquarantaine, evaluatie,
  promotie en rollback af. Tot die tijd is Eva niet
  aantoonbaar een agent die procedureel steeds beter wordt.
- Installer/onboarding IN00: technisch bestaan schema-installatie,
  managed-B-manifest, A/B-beheer, offline imagebouw en portable export, maar er
  is nog geen eenvoudige setupwizard of bewezen restore op een andere computer.
  IN01-IN08 maken één lokale wizard, IdentityPack/`SOUL.md`, Telegram, Home
  Assistant, e-mail, secretinvoer, fresh install, restore en update af.
- Adaptieve connectoren AC01-AC06: Telegram blijft de eerste V1-bewijsroute,
  maar niet de productgrens. Eva moet vanuit de chat een ontbrekend legitiem
  kanaal zoals WhatsApp kunnen contracteren, via actuele officiële documentatie
  een provider kiezen, zo nodig een kleine adapter in quarantaine maken en die
  na tests en least-privilege koppeling zonder core-rebuild activeren. Dit werkt
  nu nog niet.
- Documentatiebeheer DG01-DG06: mogelijkheden, gaten, architectuur, reviews en
  het mechanische padinventaris bestaan al, maar semantisch componentownership
  en de labels `complete`/`optimized_for_<metric>` worden nog niet repositorybreed
  machine-afgedwongen. Het nieuwe onderhoudscontract maakt die open grens en de
  benodigde checker/eindsnapshot expliciet.
- Historisch productinventarischeckpoint: OpenBot `v0.0.4` en de actuele gepinde
  documentatie zijn onafhankelijk geïnventariseerd. OpenBot documenteert een
  veel breder computer-, browser-, takeover- en beheeroppervlak, maar levert via
  AG-UI een agent aan en bewijst geen Eva-achtige gezinsidentiteit, fysieke
  readback of huispresence. `OB00` is klaar; `AU01-AU08` maken de ontbrekende
  echte computerautonomie nu zichtbaar en afvinkbaar.
- Historisch action-chaincheckpoint: `0ca0c4b` trekt de driestappenketen van `5a1556e`
  door vijf verse databaseprocessen. Het bewijst dat
  `a99563b` een effect alleen afrondt na een verse,
  onafhankelijke en inhoudelijk exacte doelmatch. Effect, stap,
  `STATE_VERIFIED`, continuation en vrijgegeven vervolgstappen veranderen
  atomair; herstart/replay schrijven niets dubbel, effect- en readbackprovider
  worden ieder exact eenmaal aangeroepen, de vervolgworker kan de vrijgegeven
  stap echt claimen en een geforceerde tijdlijnfout rolt alles terug. Zie
  `reviews/action-chain-effect-advance-mc03c3e.md`.
- De eerdere run `resident-long-2026-08-24-a` is na review teruggezet naar
  shakedown. Hij verzamelt echte HA-events, maar telt niet als acceptatierun.
- Eerstvolgend: laat MC03c3e onafhankelijk reviewen en voer daarna dezelfde
  algemene keten via echt Telegram-B en Home Assistant uit. Een fysieke
  readback moet de echte doeltoestand bewijzen; een crash tussen effect en
  readback mag geen tweede provideractie veroorzaken. Onafhankelijke reviews
  van MC03b2b/MC03b3 en MC03c0–MC03c3e blijven eveneens open.
- Productie A: actief en niet gewijzigd.
- Actuele B: `eva2-living-core:cross-channel-v53-20260824`, core ready,
  duurzame-doelenworker running met nul herstarts.
- Eindacceptatie is uitgebreid maar nog niet gestart: naast core 15/15 en
  native 9/9 moeten echte multi-persoon-, multi-beurt-, multi-tool-,
  crashherstel-, self-improvement-, presence- en autonome computerproeven groen
  zijn, plus de echte voice-mode V01-V07. Openbare vergelijkingsclaims wachten
  daarnaast op CB01-CB07; de Eigenaarmodus op OM01-OM06. De norm staat in
  [`design/validation/lab-36-final-acceptance-addendum-v1.md`](design/validation/lab-36-final-acceptance-addendum-v1.md).

## Afgerond en live bewezen

- De eerdere drie-case concurrentenmeting is eerlijk begrensd en uitgebreid met
  een 22-categorie productmatrix en hergebruikarchitectuur voor ClawHub,
  OpenClaw Gateway/nodes, MCP, Hermes en Agent Zero. De onafhankelijke reviewer
  gaf PASS. De fail-closed gate blijft rood zolang niet iedere categorie tegen
  iedere concurrent met een gehashte volledige `won`-receipt is bewezen.
- LAB-36 core-live: 15/15.
- De draaiende v53-B-bron is onafhankelijk vastgelegd zonder secrets of
  containerwijzigingen: 377 bestanden, 363 gelijk, 8 afwijkend en 6 alleen in
  de werkboom. Dit is herkomstbewijs, geen release-freeze of live-acceptatie.
- Historisch native bewezen: P03, X01, A01, A02, A03, I02, F03, F04 en M01.
  Deze receipts zijn verdeeld over v43, v49/v50, v52 en v53; één bevroren
  eindkandidaat moet de volledige 9/9-regressie nog doorlopen.
- A01/A02: zelfstandig wachten, tussenvraag verwerken en daarna precies één
  effect plus levering.
- A03: extern bewijs opnieuw lezen en inhoudelijk pivotten.
- F03: externe blocker begrensd naar `needs_help`, zonder verkeerd effect.
- F04: cancellation en delivery failure technisch en menselijk onderscheiden.
- I02: zonder voorgeschreven fix drie echte fixtureproblemen gewogen, zelf de
  uitgeschakelde transient retry gekozen, één patch behouden en gerichte
  readback plus behoud van ongerelateerd werk bewezen.
- M01: een voorkeur kwam via de definitieve Telegramconnectorcode tegen een
  lokale Bot API-contractprovider binnen en werd
  later via een nieuwe webconversatie voor dezelfde vertrouwde persoon
  herinnerd; een tegengestelde voorkeur van een tweede persoon lekte niet mee.
- Regressie uit de echte F04-proef: terminale `delivery_failed`-staat veroorzaakte
  een worker-herstartlus; hersteld, getest en als v50 live gezet.

## Nog open in deze acceptatiefase

- Core-live: 15/15 opnieuw op dezelfde bevroren eindimage.
- Native: 9/9 opnieuw op één bevroren eindimage.
- Multi-persoonidentiteit: broncode ondersteunt meerdere accounts per persoon,
  tweezijdig koppelen en replayveilige groepsconsent. De beheerde B-deploy en
  echte multi-persoon/groepstest ontbreken nog; WhatsApp is niet v1.
- Eindaddendum: MX01-MX06 moet na de 15/15 en 9/9 op dezelfde image slagen:
  parallelle personen, groepsprivacy, meerbeurts redeneren, toolketen met
  readback, crash midden in een keten en begrensde self-improvement.
- Resident-track: een nieuwe officiële zevendaagse run op die eindimage; de
  huidige v53-shakedown heeft nul geslaagde residentcases.
- Presence-behoud: de bestaande spontane deurbel-, intercom- en speakerbeleving
  van Eva A moet via P01-P05 op Eva2 terugkomen. Brede multimedia is niet V1,
  maar deze minimale bestaande huisaanwezigheid blokkeert de overgang wel.
- Conversationele voice: V01-V07 staan open voor full-duplex audio, barge-in,
  lage p95-latency, Home Assistant-fallback en de voorkeurroute `Bel Eva` via
  een Telegram Mini App. Voice notes tellen niet als deze gate; native bellen
  door een Bot API-bot is niet beschikbaar.
- Omvang en snelheid: PC00 is vastgelegd; PC01-PC06 staan open voor een
  commitgebonden budgetchecker, begrenzing van grote hot-pathbestanden,
  streaming tekstrespons, aparte sidecarbudgetten en een eindmeting met minstens
  200 tekstbeurten. Boven 75.000 eigen productregels is de V1-gate rood.
- Leren en persoonsmodel: LG01-LG07 staan open. Eva moet per geverifieerde
  persoon kunnen tonen, corrigeren en vergeten wat zij gebruikt; iedere
  modelcall krijgt token-, latency- en kostenusage, iedere geheugenscope een
  fysiek quotum en iedere geleerde procedure baseline/held-out-evaluatie,
  versie en rollback. De zevendaagse gate weigert profiel-, context-, token-,
  kosten- of latencygroei buiten de vastgelegde grenzen.
- Installatie/onboarding: IN01-IN08 staan open. De huidige operatoronderdelen
  zijn geen consumenteninstaller. V1 vereist een ondertekend pakket met lokale
  wizard, zonder handmatige YAML/JSON/env-edit, plus least-privilege koppeling
  van Telegram, Home Assistant en één e-mailprovider, restore op een andere
  computer en A/B-update/rollback.
- OpenBot/autonomie: `OB00` is klaar. `AU01-AU08` staan open voor een duurzaam
  chatmandaat, algemene plan-act-observeer-herplan-runner, één geïsoleerde
  browsercomputer, Eva's eigen policy/ledger/readback, live pauze/stop/overname,
  een echte afgeschermde webtaak, crashherstel en zeven dagen praktijkbewijs.
- Security: het HA-token van B staat nog als inspecteerbare container-env. Het
  moet worden ingetrokken, opnieuw uitgegeven en uitsluitend als file-secret
  worden gemount.
- Proactieve uitgang: zeven oude `expression_pending`-items blijven fail-closed;
  geen kanaal wordt geopend vóór TTL, policyversie en activation cutoff bestaan.
- Git: de worktree is echt Git, maar 27 gewijzigde en 103 untracked Eva2-paden
  hebben nog geen opgesplitste, onafhankelijk gecontroleerde checkpoints.
- Externe gates die op aparte configuratie of toestemming wachten: echt Eva 2
  Telegram-token/bindings, Stripe-testaccount, nieuw AgentMail-secret en de
  afzonderlijk goed te keuren LocalTuya-mutatiestappen.

## Laatste receipts en reviews

- [`live-b-native-a01-a02-durable-goals-v50-2026-08-24.json`](evidence/living-core/live-b-native-a01-a02-durable-goals-v50-2026-08-24.json)
- [`live-b-native-a03-f03-goal-pivot-blocker-v50-2026-08-24.json`](evidence/living-core/live-b-native-a03-f03-goal-pivot-blocker-v50-2026-08-24.json)
- [`live-b-native-f04-terminal-goals-v50-2026-08-24.json`](evidence/living-core/live-b-native-f04-terminal-goals-v50-2026-08-24.json)
- [`live-b-native-i02-self-chosen-repository-improvement-v52-2026-08-24.json`](evidence/living-core/live-b-native-i02-self-chosen-repository-improvement-v52-2026-08-24.json)
- [`live-b-native-m01-cross-channel-continuity-v53-2026-08-24.json`](evidence/living-core/live-b-native-m01-cross-channel-continuity-v53-2026-08-24.json)
- [`resident-long-run-v1-2026-08-24.json`](evidence/living-core/resident-long-run-v1-2026-08-24.json)
- [`live-b-v53-source-provenance-2026-08-24.json`](evidence/living-core/live-b-v53-source-provenance-2026-08-24.json)
- [`openbot-competitive-inventory-ob00.md`](reviews/openbot-competitive-inventory-ob00.md)
- [`voice-performance-product-gates-v1.md`](reviews/voice-performance-product-gates-v1.md)
- [`performance-complexity-pc00-2026-08-25.json`](evidence/product/performance-complexity-pc00-2026-08-25.json)

## Betekenis van status

- `bezig`: code en echte B-proef zijn nog niet allebei groen.
- `live bewezen`: ontwerp, code, gerichte tests, echte B-serviceproef en receipt
  zijn aanwezig.
- `wacht extern`: technisch pad blijft fail-closed tot de genoemde toestemming,
  credential of providerconfiguratie bestaat.
