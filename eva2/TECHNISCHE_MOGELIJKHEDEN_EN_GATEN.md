# Eva 2 — technische mogelijkheden en open gates

Bijgewerkt: 25 augustus 2026.

Dit is het technische detailoverzicht. De korte uitleg in gewone taal staat in
`EVA2_MOGELIJKHEDEN_EN_GATEN.md`. Hier blijven precieze implementatiestatussen,
gate-ID's, checkpoints en bewijsgrenzen staan.

## Wat Eva 2 nu daadwerkelijk kan

- Een normaal gesprek voeren vanuit één configureerbare identiteit.
- Een door het model voorgestelde keten van maximaal zes lees- en actiestappen
  streng controleren en volledig in PostgreSQL bewaren zonder al iets uit te
  voeren; na een herstart wordt exact hetzelfde plan teruggelezen.
- Na een bewezen read de vrijgegeven effectstap opnieuw tegen actuele authority
  controleren en atomair als één duurzaam, idempotent effectintent voorbereiden,
  nog zonder de provider aan te roepen.
- Voor zo'n effect precies één call-start vastleggen, de algemene verzegelde
  subprocessprovider aanroepen en acceptatie, afwijzing, protocolfout of
  ambiguïteit atomair met evidence en eventueel providerreceipt bewaren. Replay,
  herstart en een achtergelaten onopgeloste call-start geven nooit een tweede
  providercall. Provideracceptatie is nog geen fysiek succes: de effectstap blijft
  `running` totdat een afzonderlijke Home Assistant-readback de doeltoestand
  bewijst. Dit is getest op disposable PostgreSQL en een lokale procesgrens, niet
  op B of een echte Home Assistant-provider.
- Voor één voorbereide ketenread precies één duurzame callclaim maken, de
  algemene verzegelde providergrens aanroepen, resultaat en evidence bewaren en
  alleen na een echte succesobservatie de uitvoerbare vervolgstap vrijgeven.
  Een protocolfout, ambiguïteit of rate-limit geeft geen vervolgautoriteit;
  replay en een nieuwe engine starten geen tweede proces. Dit is disposable
  PostgreSQL-bewijs; onafhankelijke review, B en een echte Home Assistant-
  provider blijven open.
- Recente gesprekken uit PostgreSQL meenemen zonder na een herstart opnieuw te
  beginnen.
- Persoonlijke feiten, voorkeuren en correcties via Hindsight onthouden.
- Hindsight via een kleine providerneutrale geheugenpoort gebruiken: recall,
  retain, onderhoud en health zijn niet langer als Hindsight-type in de kern
  vastgezet. Hindsight blijft wel de gekozen langetermijnprovider; dit is geen
  providerselectie of datamigratie (`79bc376`).
- Geheugen per persoon en per project gescheiden houden.
- Een Telegramvoorkeur later via een webgesprek van dezelfde persoon herinneren.
- Persoonscontext begrensd ophalen: de kandidaatconfiguratie kent 2.000–4.000
  recalltokens en 20–100 retrieval-nodes; retain en reflectie blijven buiten de
  eerste-antwoordroute.
- Hindsight snel gebruiken in gesprekken: alleen `recall` zit in de chatroute.
- Nieuwe herinneringen asynchroon via `retain` opslaan.
- Langzame reflectie buiten de chatroute uitvoeren wanneer Eva niets te doen heeft.
- Actuele Home Assistant-toestanden lezen.
- Home Assistant-geschiedenis over een begrensde periode lezen.
- Lampen en schakelaars op natuurlijke namen vinden en bedienen.
- Pas succes melden na een nieuwe, onafhankelijke statuscontrole.
- Eenvoudige en samengestelde Home Assistant-automatiseringen aanmaken.
- Een Tuya-naar-LocalTuya-migratie inventariseren en per apparaat plannen.
- De onderhouden LocalTuya-bron veilig voorbereiden met versie- en
  goedkeuringscontrole.
- Langlopende doelen bewaren, op extern bewijs wachten en later hervatten.
- Na een crash doorgaan zonder hetzelfde bewezen effect opnieuw uit te voeren.
- Een mislukte levering, annulering en echte uitvoering van elkaar onderscheiden.
- Eén opdracht naar meerdere ontvangers sturen en per ontvanger het resultaat
  bewaren.
- Actuele primaire bronnen zoeken en een heropenbaar Markdownrapport bewaren.
- Begrensde document-, bestands- en repositorytaken uitvoeren met hashes,
  validatie en rollback van alleen haar eigen wijziging.
- Zelf een kleine echte verbetering in een geïsoleerde repository kiezen en
  afronden.
- In een testomgeving een productbestand, prijsvoorstel, betaalroute en
  downloadfulfillment maken.
- Vanaf een lege PostgreSQL-installatie starten en na een harde herstart verdergaan.
- Als geïsoleerde B-versie naast productie A draaien zonder A te vervangen.
- Afzonderlijke installatiecomponenten door een operator laten renderen of op
  disposable PostgreSQL/core testen. De fresh-proef hergebruikte externe
  Hindsight/modelautoriteit en het managed manifest is nog niet geadopteerd;
  dit is dus geen volledige lege-stack- of productinstallatie.

## Wat nog niet af is

- Eva 2 draait nog niet als de definitieve productie-Eva; Eva A blijft actief.
- Er is nog geen eenvoudige installer of lokale onboardingwizard. De huidige
  setup vereist operatorwerk met Compose/configuratie en secrets. IN01-IN08
  voegen één ondertekend pakket, IdentityPack/optionele `SOUL.md`-import,
  Telegram-, Home Assistant- en e-mailkoppeling, fresh install, restore, update
  en rollback toe; zie `design/product/installer-and-onboarding-v1.md`.
- Eva's connectoren zijn nog grotendeels vooraf gebouwd. De architectuur is
  connectorneutraal, maar Eva kan vandaag nog niet vanuit een gesprek zelf een
  ontbrekend kanaal zoals WhatsApp onderzoeken, een standaardprovider kiezen of
  een kleine adapter maken, testen, koppelen en dynamisch activeren. AC01-AC06
  maken dat store- en catalogusonafhankelijke pad af; zie
  `design/architecture/ecosystem-bridge-v1.md`.
- Eva-runtime kan nog geen algemene subagents spawnen. Het ontwikkelproces kan
  dat wel, maar dat is geen productbewijs. WB01-WB07 voegen parentgestuurde,
  begrensde workers met eigen taakgrant, contextscope, budget, lease,
  heartbeat, crashherstel en gevalideerde teruglevering toe; zie
  `design/product/delegation-and-workboard-v1.md`.
- Er bestaan nog geen officiële Eva-scores voor AgentBench, GAIA of OSWorld.
  CB01-CB07 verplichten exact dezelfde upstream release, split en scorer,
  heropenbare trajecten en twee gescheiden scoreborden: native productopstelling
  en gecontroleerde frameworkvergelijking met gelijk model en budget. Zie
  `design/product/comparable-agent-benchmarks-v1.md`.
- Eva heeft nog geen Eigenaarmodus. Hermes' huidige officiële `Godmode` is een
  optionele jailbreak-/safety-bypassskill en geen bewijs van computerautonomie.
  OM01-OM06 bouwen daarom de gewenste uitkomst op Eva's eigen runner: één
  vooraf gewaarschuwd, exact en intrekbaar mandaat, daarna zelfstandig handelen
  zonder bevestiging per tussenstap, met readback en stopknop. Zie
  `design/product/owner-autonomy-mode-v1.md`.
- Het webdashboard heeft D01/D02 read-only basis, maar nog geen volledige
  chat-/sessiesurface, Hermes-achtig duurzaam Kanbanbord of workerbediening.
  D06 voegt dezelfde Eva-chat toe; D07/D08 tonen en bedienen dezelfde
  PostgreSQL-taken via dashboard én Eva-chat. Een kaart op `done` is nooit een
  vervanging voor providerreceipt of fysieke readback.
- Er is geen runtime-`MEMORY.md`: PostgreSQL en Hindsight bewaren gescopeerd
  geheugen met herkomst, consent en crashherstel. Een toekomstige `MEMORY.md`
  is alleen een persoonsgebonden import/export, nooit de geheugenautoriteit.
- Hindsight blijft Eva's langetermijngeheugen. Een snelle lokale Mnemosyne-L1
  en een Honcho-persoonsmodel zijn alleen optionele aanvullingen en vervangen
  Hindsight niet. MA02-MA06 meten iedere laag afzonderlijk op echte winst,
  privacy, correctie, uitval, tokens, latency, beheer, upgrade en rollback;
  Mem0 heeft nu geen V1-runtimefunctie. Zie
  `design/product/tiered-memory-and-person-model-v1.md`.
- Het huidige Home Assistant-token van B moet worden ingetrokken en vervangen;
  het staat nog inspecteerbaar in containerconfig in plaats van als file-secret.
- De recente Living Core-bron, architectuur en receipts hebben nog geen reeks
  kleine, onafhankelijk gecontroleerde Git-checkpoints.
- De documentatie is breed maar nog niet volledig machine-afgedwongen. De
  Living-Core-matrix en repository-inventaris beschrijven veel componenten en
  paden, maar nog niet ieder productie-/deployment-/migrationbestand heeft één
  semantische componentowner met actuele bewijs- en optimalisatiestatus.
  DG01-DG06 maken dat af; zie `DOCUMENTATIE_EN_ONDERHOUD.md`.
- Sensorbeslissingen en levering moeten uit de Hindsight-cold-path naar eigen
  duurzame workers met backlog- en livenessbewijs.
- De residentpolicy heeft nog geen atomische PostgreSQL-state, exacte urgente
  resourcebinding, TTL, activeringsgrens of hercontrole vlak vóór levering.
- Zeven oude proactieve expressies blijven daarom gesloten en mogen bij een
  latere kanaalactivatie niet alsnog worden verstuurd.
- De definitieve Telegramconnector heeft nog een eigen Eva 2-bottoken en echte
  eigenaar-gecontroleerde persoonsbindings nodig.
- De menselijke gesprekservaring is nog niet door jou geaccepteerd; vooral
  natuurlijkheid, initiatief en bondigheid moeten in echte Telegramgesprekken
  stabiel blijken.
- Eva kent nog geen volledige Hermes-achtige gesloten leercyclus. Persoonsfeiten
  en correcties werken, maar een corrigeerbaar totaalprofiel, usage-/kostenmeter,
  fysieke geheugenquota en zelf on the fly gemaakte, automatisch geëvalueerde,
  versieerbare skills met rollback ontbreken nog. Eva mag daarvoor niet van een
  skillstore of vooraf bestaande catalogus afhankelijk zijn. LG01-LG07 maken
  dit af; zie
  `design/product/learning-and-person-model-v1.md`.
- Eva A praat volgens de eigenaar nu soms spontaan via deurbel of huisspeaker.
  Eva2 heeft daarvoor nog geen toegelaten V1-route. Deze minimale presence is
  daarom een overgangsgate P01-P05. Daarnaast ontbreekt nog echte
  conversationele voice: doorlopend praten, snel antwoord horen en Eva kunnen
  onderbreken. V01-V07 maken dat een afzonderlijke V1-gate; zie
  `design/product/conversational-voice-v1.md`. De eerlijke
  oude/nieuwe vergelijking staat in `OUDE_EVA_VS_EVA2.md`.
- De opgeslagen meerstappenketen kan nu één gevalideerde read voorbereiden,
  eenmalig uitvoeren, resultaat/evidence/observatie bewaren, na succes de juiste
  afhankelijkheid vrijgeven en die effectstap duurzaam voorbereiden. Voor dat
  effect kan zij ook één providerpoging autoriseren en precies één duurzame
  call-startclaim uitgeven, de eerste geclaimde subprocesscall uitvoeren en de
  provideruitkomst duurzaam herladen; concurrency, replay en herstart krijgen
  geen tweede set callable bytes of tweede proces. Na een geaccepteerd receipt
  kan zij nu tevens exact één afzonderlijk geautoriseerde, duurzame
  `effect_verify`-read voorbereiden en die aan exact één actuele sealed
  providerrelease plus duurzame poging binden. Vlak vóór I/O kan exact één
  caller nu een duurzame call-start vastleggen en pas daarna het sealed
  `effect.readback`-pakket ontvangen; de executor voert alleen dat eerste pakket
  uit en bewaart readbackantwoord, protocolfout of ambiguïteit atomair. Herstart,
  concurrency en een onopgeloste call-start krijgen geen tweede proces.
  MC03c3d zet een reeds opgeslagen getypeerde providerwaarneming daarna zonder
  nieuwe provider-I/O om in precies één canoniek `EffectReadback/v1`, inclusief
  de hash van de werkelijk waargenomen toestand. Replay, concurrency, rollback,
  alle vier uitkomststatussen en corruptie zijn getest op `76404a4`. MC03c3e
  gebruikt vervolgens een verzegelde declaratieve projectie om de bedoelde en
  waargenomen JSON-velden zelfstandig te vergelijken. Alleen een verse exacte
  match schrijft atomair `STATE_VERIFIED`, effect- en stepsucces, continuation
  en vrijgegeven dependencies (`0acdd33`–`a99563b`); mismatch, onbekend,
  niet-waargenomen en type-lookalikes blijven gesloten. `5a1556e` bewijst die
  vrijgave daarna in een werkelijke driestappenketen, inclusief herstart,
  vervolgclaim en volledige rollback. `0ca0c4b` herhaalt de geïntegreerde
  effect→readback→advance-keten over vijf verse engines en houdt effectattempt,
  readbackattempt, beide call-starts, `STATE_VERIFIED` en dependencyvrijgave
  exact enkelvoudig. Alle 75 action-chaincases zijn groen op een verse
  PostgreSQL 16.13-database.
  Reviews van readresultaat, readvrijgave en alle effectslices blijven open.
  Nog ontbreken de onafhankelijke review en de echte Telegram-B-/Home
  Assistant-proef met fysieke readback en crashinjectie.
- Eva heeft nog geen OpenBot-achtige eigen browsercomputer, blijvend
  loginprofiel, live scherm of human-takeover. `AU01-AU08` maken het pad van
  chatmandaat naar autonome webtaak, crashherstel en praktijkacceptatie
  afzonderlijk afvinkbaar; zie `OPENBOT_VERGELIJKING.md`.
- Eva 2 is nu een Python 3.12-product; er staat geen Rust-crate in deze bronboom.
  Python blijft de Living Core. Rust is alleen kandidaat voor een vervangbare
  lage-latency audioworker of sandbox wanneer de V02-metingen aantonen dat de
  gekozen standaardcomponenten de p95-budgetten niet halen.
- Git-baseline `35e2c0d…` bevat 51.928 productregels; een latere dirty
  werkboomobservatie bevatte 53.428 niet-gegenereerde regels. Dat is niet
  “klein”, maar ook geen pakket van honderdduizenden regels. PC00 zet daarom V1
  hard op maximaal 75.000 handgeschreven productregels, maximaal twaalf directe
  coredependencies en gemeten p95-reactiviteit. De commitgebonden checker en
  eindmeting PC01-PC06 zijn nog open; zie
  `design/product/performance-and-complexity-v1.md` en de exacte baseline in
  `evidence/product/performance-complexity-pc00-2026-08-25.json`.
- De eerste residentperiode is na onafhankelijke review een shakedown geworden,
  geen officiële zevendaagse praktijktest. Na het bevriezen van de echte
  kandidaat begint een nieuwe klok van zeven dagen.
- Temperatuurhysterese, camerameldingen samenvoegen en nachtstilte zijn als
  afzonderlijke aandachtsmodule in aanbouw en nog niet live gekoppeld.
- Proactieve Telegrammeldingen hebben nog geen goedgekeurd doelkanaal voor Eva 2.
- Echte LocalTuya-installatie en apparaatmigratie zijn nog niet uitgevoerd;
  bronregistratie, pakketinstallatie en eerste apparaat blijven drie aparte
  mutatiestappen.
- Echte Stripe-testmodus ontbreekt nog. Eva verdient dus nog geen echt geld.
- AgentMail voor Eva 2 wacht op een nieuw eigen read-only secret.
- De volledige productiepromotiegate, ondertekende release en terugrolceremonie
  zijn nog niet met de uiteindelijke kandidaat afgerond.
- Langdurige betrouwbaarheid, stilte, herstel en geheugengedrag moeten nog zeven
  dagen met echte gebeurtenissen worden bewezen.

## Stand tegenover OpenClaw, Hermes, Agent Zero en OpenBot

- Correctie op de eerdere vergelijking: die was technisch echt en gebruikte
  actuele, gepinde builds, maar dekte op alle vier slechts C01, T02 en C04.
  Skills, plugins, gateways, nodes, kanalen, browser/desktop, MCP, schedulers en
  multi-agentwerk waren niet volledig geconfigureerd of vergeleken.
- Eerlijke hoofdconclusie: Eva 2 is nog niet algemeen aantoonbaar beter. Daarvoor
  ontbreken dezelfde volledige verborgen en zevendaagse tests voor alle vier.
- In één geblindeerde gesprekscase had Eva 2 wel de hoogste gemiddelde score:
  `22,6667`. Hermes had `22,3333`; OpenClaw en Agent Zero ieder `21,3333`.
- Dit waren drie afzonderlijke GPT-5.4-mini-judgecalls met verborgen
  frameworknamen en geroteerde volgorde, geen menselijke jury. Eva kreeg twee
  beste-stemmen en Hermes één.
- In twee zichtbare native ontwikkelcaptures was Eva 2 sneller dan alle drie:
  - C01: Eva `7,512 s`; OpenClaw `26,148 s`; Hermes `16,691 s`; Agent Zero `46,272 s`.
  - T02: Eva `6,375 s`; OpenClaw `16,883 s`; Hermes `19,248 s`; Agent Zero `19,468 s`.
- Alleen Eva had bij T02 een echte Home Assistant-readback. De andere drie waren
  daar niet met Home Assistant verbonden, dus dit is geen gelijkwaardige
  toolvergelijking.
- De frameworks gebruikten dezelfde modelnaam en reasoningstand, maar hun eigen
  systemprompts, tools en contextgroottes verschilden. De latency is dus een echte
  native meting, geen zuivere causaliteitsmeting van alleen het framework.
- Eva 2 heeft vijftien core-live- en negen native-receipts, maar die native
  receipts zijn nog over meerdere images verdeeld. Eén eindkandidaat moet ze
  samen met core 15/15 opnieuw doorlopen. De drie concurrenten hebben diezelfde
  volledige reeksen niet doorlopen.
- De huidige voorsprong is daarom: betere eigen evidence, crashherstel,
  Home Assistant-integratie en een kleine winst in één menselijke case.
- Hermes documenteert daarnaast een gesloten loop waarin geheugen en autonome
  skillcreatie/-verbetering tijdens gebruik groeien. Eva heeft al
  persoonsgeheugen en één begrensde I02-verbetering, maar loopt op die volledige
  procedurele cyclus nu achter. LG01-LG07 zijn de expliciete concurrentiegate;
  groei telt alleen als herhaaltaken verbeteren zonder persoonslek, drift of
  onbegrensde geheugen-, token-, kosten-, latency- en codegroei.
- De nog onbewezen claim is: structureel menselijker, autonomer en betrouwbaarder
  dan alle drie over alle 36 cases en zeven dagen echt gebruik.
- AgentBench, GAIA en OSWorld worden toegevoegd als de extern verifieerbare
  scorelaag. Scores blijven per officiële benchmark en release zichtbaar; er
  komt geen eigen gewogen totaalcijfer. Eva, OpenClaw, Hermes, Agent Zero en
  OpenBot krijgen daarnaast een gecontroleerde run met gelijk model, budget en
  omgeving voordat een frameworkclaim wordt gedaan.
- De volledige technische capabilitymatrix, ontbrekende tests en hergebruikroute
  via ClawHub/OpenClaw staan in `TECHNISCHE_PRODUCTGATES.md`. De leesbare
  conclusie staat in `CONCURRENTIE_EN_HERGEBRUIK.md`.
- `CopilotKit/OpenBot` is apart uit officiële, gepinde bronnen vergeleken. Het
  is nog niet in dezelfde live benchmark uitgevoerd. OpenBots documentatie
  omvat een veel breder computer- en beheeroppervlak: eigen browsercomputer per
  Bot, shell/workspace, live takeover en dashboard voor skills, MCP,
  credentials, policies en audit.
- OpenBot is expliciet alpha en brengt het eigenlijke brein via AG-UI mee. De
  documentatie bewijst geen langlopende doelen, self-improvement,
  Telegramgezin, Home Assistant, fysieke readback of spontane huispresence.
- Eva moet voor een eerlijke concurrentie `AU01-AU08` sluiten: één duurzaam
  chatmandaat, algemene autonome runner, geïsoleerde browsercomputer,
  Eva-ledger/readback, live stop/overname, echte webtaak, crashherstel en een
  zevendaagse praktijktest. Zie `OPENBOT_VERGELIJKING.md`.

## Waar je de bewijzen vindt

- Actuele voortgang: `PROJECT_STATUS.md`
- Technische architectuur en bestandseigenaarschap:
  `design/architecture/living-core-v1.md`
- Live receipts: `evidence/living-core/`
- Concurrentenrun: `evidence/living-core/lab36-eva2-openclaw-hermes-agent-zero-live-2026-08-23.json`
- Leesbare productvergelijking: `CONCURRENTIE_EN_HERGEBRUIK.md`
- Volledige technische product-, hergebruik-, OpenBot- en autonomiegates:
  `TECHNISCHE_PRODUCTGATES.md`
- Leesbare OpenBot-vergelijking: `OPENBOT_VERGELIJKING.md`
- Full-duplex voice, Telegram/HA-route en latencygates:
  `design/product/conversational-voice-v1.md`
- Code-, dependency-, image- en reactietijdbudgetten:
  `design/product/performance-and-complexity-v1.md`
- Subagents, duurzame taakdelegatie en Kanban-dashboard:
  `design/product/delegation-and-workboard-v1.md`
- Openbaar vergelijkbare AgentBench-, GAIA- en OSWorldscorecontracten:
  `design/product/comparable-agent-benchmarks-v1.md`
- Vergaande autonomie zonder bevestiging per tussenstap binnen één mandaat:
  `design/product/owner-autonomy-mode-v1.md`
- Installer, onboarding, koppelingen, `SOUL.md`/`MEMORY.md` en verplaatsen:
  `design/product/installer-and-onboarding-v1.md`
- Mogelijkhedenindex, bestandsdoel, status, bewijs en optimalisatie:
  `DOCUMENTATIE_EN_ONDERHOUD.md`
- Geblindeerde gesprekscase: `evidence/living-core/lab36-c04-blind-2026-08-24.json`
- Resident-shakedown, niet de officiële zevendaagse run:
  `evidence/living-core/resident-long-run-v1-2026-08-24.json`
