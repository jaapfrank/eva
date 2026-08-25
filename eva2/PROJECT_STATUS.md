# Eva 2 — actuele voortgang

Laatst bijgewerkt: 2026-08-25

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
| Menselijke gezinsassistent | Tekst, persoonsgeheugen, accountbinding en groepsprivacy hebben stevige bouwstenen | Echte gezinschat, spontane presence en conversationele voice nog open |
| Zelfstandig handelen | Doelen, leases, bewijs, crashherstel en één zelfverbetering bestaan | Browsercomputer, volledige skillcyclus, subagents en Eigenaarmodus nog open |
| Beheerbaar product | A/B, back-ups, offline image, secrets en kleine status-/identiteitspagina bestaan | Installer, volledig dashboard/Kanban, update/restore en releasefreeze nog open |
| Eerlijk vergelijkbaar worden | Drie met dezelfde vragen uitgevoerde ontwikkelcases en LAB-36-bewijs bestaan; Eva had een kleine gesprekswinst en sterk bewijs voor generieke resultaatcontrole via haar eerste HA-skill | Prompts, tools en context verschilden; officiële AgentBench/GAIA/OSWorld- en volledige gecontroleerde vijf-agentvergelijking zijn nog open |

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
  Eva's lock maar wordt nog niet als agentframework gebruikt. Pydantic AI en
  zijn Harness leveren al voice, subagents, browserkoppelingen, MCP, web-UI en
  duurzame DBOS-routes; Hindsight heeft al een lokale beheer-UI. Eva bouwt
  daarom geen eigen voice-, browser-, worker-, Kanban-, scanner- of
  updateplatform voordat een gepinde dependencyproef een concreet gat bewijst.
  Ook Eva's 404 regels handgeschreven MCP-client en 763 regels directe
  Home Assistant-adapter zijn nu expliciete vervangkandidaten: eerst de
  officiële MCP Python SDK en Home Assistants ingebouwde MCP Server testen,
  daarna alleen aantoonbaar ontbrekende Eva-grenzen behouden. De onafhankelijke
  onderhoudsaudit geeft alleen Hindsight in zijn huidige smalle L2-rol en
  Renovate als niet-automerge voorstelbot een begrensde `GO`; onder meer
  Pydantic AI, Harness, DBOS, de officiële MCP SDK, Home Assistant MCP Server,
  Playwright MCP, Hindsight UI, LiveKit, OpenClaw, React-Admin, PyInstaller,
  Agent Skills en Trivy blijven `PROEF`. Browser Use en een tweede
  workflowplatform komen nu niet in V1.
  `DF01`-`DF06` en de eerste kandidaten staan in
  `OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`; dit is een selectiecontract, nog geen
  goedkeuring of installatie van nieuwe productdependencies.

- Dependencytransportwaarneming DF02-voorwerk: in een afgeschermde tijdelijke
  Pythonomgeving zijn `pydantic-ai-slim[openai]==2.32.2`, `openai==3.3.1` en
  `mcp==2.1.1` eenmalig geïnstalleerd. Een lokale proefserver zag een
  Eva-achtig verzoek en gaf synthetische usagevelden terug die Pydantic/OpenAI
  behield; dit was geen token- of providerkostenmeting. De gebruikte Pydantic
  `Agent`-loop is geen productiekandidaat. De MCP-SDK doorliep lokaal de
  initialize/list/call-route. Er is nog geen commitgebonden proefscript,
  artifacthash of onafhankelijke replay. De onafhankelijke audit houdt beide
  daarom op `PROEF`: exacte locks/wheels, PyPI/Git-provenance, nul SDK- én
  frameworkretries, atomair leasepaar, byte-/deadlinegrenzen,
  kwaadaardige-servertests en een echte providerproef staan open. De
  wegwerpinstallatie week bovendien af van Eva's huidige Pydanticpins en trok
  meerdere nieuwe transitieve packages binnen. A en B zijn niet benaderd of
  gewijzigd; zie
  `reviews/dependency-transport-trials-2026-08-25.md`.
  De kleinste vervolgstap gebruikt eerst alleen de synchrone OpenAI-SDK
  `3.3.1`; Pydantic AI wordt niet als tweede agentloop in dezelfde slice gezet.

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
  Kanban zijn uit de officiële documentatie van elkaar onderscheiden. Eva's
  ontwikkelproces gebruikt agents, maar de runtime spawnt ze nog niet en het
  dashboard heeft nog geen Kanban. WB01-WB07 bouwen parentgestuurde begrensde
  workers, duurzame handoffs, crash/reclaim, dezelfde chat-/dashboardservice
  en een live multi-workerproef boven Eva's bestaande PostgreSQL-ledger; er
  komt geen tweede taakdatabase of tweede Eva-brein.

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
- Laatste afgeronde productcheckpoint: OpenBot `v0.0.4` en de actuele gepinde
  documentatie zijn onafhankelijk geïnventariseerd. OpenBot documenteert een
  veel breder computer-, browser-, takeover- en beheeroppervlak, maar levert via
  AG-UI een agent aan en bewijst geen Eva-achtige gezinsidentiteit, fysieke
  readback of huispresence. `OB00` is klaar; `AU01-AU08` maken de ontbrekende
  echte computerautonomie nu zichtbaar en afvinkbaar.
- Laatste checkpoint: `0ca0c4b` trekt de driestappenketen van `5a1556e`
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
