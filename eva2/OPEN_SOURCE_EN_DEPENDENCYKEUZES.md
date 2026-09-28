# Eva 2 — eerst hergebruiken, pas daarna zelf bouwen

> Actualisatie28 september 2026: de eigenaar heeft op 27 september vastgelegd dat
> bestaande code/platformkeuzes geen verplichte selectievoorwaarde meer zijn.
> Het [nieuwe platformvoorstel](design/blueprint-2026-09-28/platform-decision.md)
> beveelt Hermes aan met één begrensde compatibiliteitscontrole en Pydantic-native
> als vaste terugval. Oudere voorkeuren/tabellen hieronder blijven historische
> bronnen; zij zijn geen opdracht om al die pakketten te installeren. Behoud van
> gegevens, exacte pins, licentie-/onderhoudscontrole en een werkende terugweg blijven
> gelden. Dit ontwerp is geen dependency-upgrade of productie-installatie.

> Eigenaarbesluit 22 september 2026: bestaande bouwstenen blijven in gebruik
> zolang zij aan de eigenaarseisen voldoen. Onderstaande alternatieven zijn
> geen vervangingsopdracht; selecteer alleen opnieuw bij een concreet tekort.

> Actuele herbeoordeling: [basiskeuze van 21 september 2026](HERBEOORDELING_2026-09-21.md)
> en [upstreammomentopname](evidence/reassessment/upstream-2026-09-21.json).
> De huidige live kern bevat Pydantic AI 2.37.0; oudere liveversies en de
> prioriteit voor een andere stemroute hieronder zijn historische waarnemingen.
> LiveKit blijft de aanbevolen stemtransportlaag. Nieuwe upstreamreleases zijn
> onderzocht maar niet geïnstalleerd, gescand of als compatibel bewezen.
> De dependency-, licentie-, versiepin-, scan- en terugwegregels blijven gelden.
> Een aanvullende geïsoleerde proef is alleen nodig als de uitslag de keuze kan
> veranderen. De oudere algemene verplichting hieronder om iedere kandidaat
> eerst te installeren en te beproeven is daarmee vervangen; een bronvergelijking
> kan voldoende zijn om een niet-passende of overbodige toevoeging af te wijzen.

Bijgewerkt: 1 september 2026.

## Het eerlijke antwoord

Er is **niet vroeg genoeg streng gecontroleerd wat bestaande open-sourceprojecten
al konden**. Daardoor dreigde Eva delen zelf te bouwen die een onderhouden
dependency of sidecar al beter en sneller kan leveren.

Het duidelijkste voorbeeld staat al in de repository. De draaiende `/eva2`-
image bevat nog `pydantic-ai-slim==2.32.1`. De actuele offline gebouwde en
scan-schone kandidaat gebruikt `2.37.0`; de direct voorafgaande `2.36.0` blijft
alleen historisch vergelijkingsbewijs. De oude modeltransportlaag is in de
actuele kandidaat verwijderd. Het aparte
Harness `0.25.0` levert subagents, planning, browsergebruik, skills en
contextbeheer.
Tegelijk telt een kleine selectie van Eva's eigen action-chain-, API- en
conversatiebestanden al meer dan 7.000 regels.

De nieuwe hoofdregel is daarom:

> Eva bouwt alleen zelf wat haar Eva maakt. Voor algemene infrastructuur wordt
> eerst een onderhouden open-sourceproject geïnstalleerd en in een kleine proef
> getest. Eigen bouw is pas toegestaan wanneer die proef een concreet gat
> aantoont.

## Wat Eva zelf moet houden

Deze onderdelen mogen niet naar een algemene dependency verhuizen:

- wie Eva is en hoe zij praat;
- welke persoon of groep met haar spreekt;
- wat privé, gedeeld of verboden is;
- toestemming, mandaat, budget en stopknop;
- welke echte actie bedoeld was;
- poging, ontvangst, fout, bewijs en onafhankelijke controle achteraf;
- de beslissing of iets werkelijk gelukt is;
- Eva's uiteindelijke antwoord en haar persoonsgebonden geheugenregels.

Dat is de dunne maar waardevolle Eva-kern. Browser, audio, wachtrijen,
chattransport, dashboards en update-scanners zijn hulpmiddelen rond die kern.

## Wat we voortaan niet zelf bouwen

| Onderdeel | Eerste hergebruikkeuze | Wat Eva nog toevoegt | Besluit |
|---|---|---|---|
| Modeltransport en gestructureerde antwoorden | Officiële OpenAI-SDK onder Pydantic AI Slim | Identiteit, persoonscontext, grants, duurzaam bewijs en antwoordbeleid | OpenAI SDK `3.6.0` draait live. De eerste `2.37.0`-image streamde via echte Codex, koos een tool en herstelde zonder dubbele call, maar een bredere fysieke subgate vond een ontbrekend runtime-librarypad. Die image is `NO-GO`. De minimale broncorrectie doorliep diagnostisch 4/4 browsergevallen; een nieuw bronimage, volledige wegwerpstack en A/B-livegang ontbreken nog |
| MCP-koppeling | Pydantic AI's native `MCPToolset`; ContextForge alleen als optionele gateway | Toegelaten persoon/scope, byte- en tijdslimieten, duurzame poging en readback | Een afzonderlijke providercontainer werd door de minimale client ontdekt en aangeroepen. `process_tool_call` onderschepte de call vóór provider-I/O; promotie wacht op verwijdering van de oude client/loop |
| Home Assistant-skill | Eerst Home Assistants ingebouwde MCP Server voor basiscontext; voor historie/configuratie de community-sidecar `homeassistant-ai/ha-mcp` | Persoonsmandaat, toolallowlist, één duurzame poging en onafhankelijke controle achteraf | `ha-mcp` `v8.3.0` is exact gepind en lokaal read-only gestart; promotie vereist semantische pariteit, verwijdering van de oude uitvoerroute en precies één runtime-eigenaar |
| Duurzame workflows en wachtrijen | DBOS blijft de bestaande workflowdependency; `2.31.0+eva.1` wordt de volgende proef | Eva-ledger voor intent, call-start, ambigu resultaat en readback | Stock `2.31.0` kan de kleine Eva-patch en workerledger niet veilig vervangen; rebase wel, blind upgraden niet |
| Subagents | Harness `0.25.0` voor korte parentgestuurde fork/join; publieke Pydantic AI `Agent.run()` voor één reeds duurzaam gekozen child | Exacte taak-, context-, tool-, tijd- en token-grant plus duurzame handoff | Harness retourneert publiek alleen tekst en verliest voor een databaseworker getypeerde output/usage/cancellation; geen eigen spawn-, model-, budget-, time-out- of child-contextengine bouwen |
| Voice | Pydantic AI Realtime `2.34.0` met upstream-extra `openai-realtime` | Persoonsbinding, privacy, barge-in naar Eva-stop, memory en toolreadback | De aparte 47-package kandidaat is hashgeïnstalleerd en exposeert `interrupt(played_ms)` met `websockets==17.0.1`; geen eigen WebRTC-, STT-, TTS- of jitterbufferstack bouwen. Echte audio/providercall en promotie blijven open |
| Browser | Microsoft Playwright MCP `0.0.79` heeft de eerste lokale read- en actieproef doorlopen; Harness `PlaywrightBrowser` blijft alleen een wegwerpvergelijking | URL-/account-/bestandsgrant, live stop, artifacts en eindcontrole | Geen Chromium-, profiel-, screenshot- of downloadengine bouwen; de browser blijft buiten Eva's kernproces. Lightpanda `0.3.7` bouwt en start als compacte kandidaat, maar is afgewezen: de actuele strikte scan vond 4 kritisch en 26 hoog, terwijl ingebedde native componenten nog geen volledige SBOM-dekking hebben |
| Open-ended webwerk | Geen extra V1-stack; Browser Use alleen als latere vergelijking | Zelfde Eva-grenzen; expliciet model-, stap- en kostenbudget | Nu niet toevoegen; alleen heropenen wanneer een benoemde taak Playwright MCP aantoonbaar niet haalt |
| Extra chatkanalen | Gepinde OpenClaw Gateway/plugin of officiële provider-SDK | Account→persoon, groepsconsent, deliveryledger en geheugenisolatie | WhatsApp niet zelf vanaf de draad bouwen |
| Geheugenbeheer | Hindsight `0.9.1` | Eva's persoon-/groep-/projectbinding, consent en correcties | Geen tweede geheugenproduct installeren alleen voor een dashboard |
| Chat- en beheerinterface | Hindsight Control Plane als toekomstige eigenaar-weergave, React-Admin `5.15.1` voor Eva's beheerpagina's; Pydantic `to_web()` alleen voor lokale ontwikkeling | Eén dun Eva-scherm voor taken, status, mandaten en bewijs | Geen compleet dashboardframework namaken; Hindsight Control Plane mag alleen achter Eva's eigenaar-/privacygrens en directe UI-writes zijn geen geheugenwaarheid |
| Kanbanpresentatie | React-Admin OSS plus de onderhouden drag-and-dropbouwstenen van `dnd-kit` | PostgreSQL blijft taakautoriteit; iedere wijziging via Eva's API | Geen tweede taakdatabase of Hermes-kloon; React-Admins Enterprise-Kanban wordt niet stil als gratis OSS-dependency aangenomen |
| Installatielauncher | PyInstaller als eerste bundelkandidaat plus Eva's bestaande FastAPI-wizard | Docker/WSL-controle, secrets, restore, A/B-update, ondertekening en uninstall | Geen eigen Python-bootstrapper bouwen; wel per besturingssysteem een exact artifact testen |
| Skillbestandsformaat | Agent Skills-specificatie plus officiële MCP SDK | Quarantaine, rechten, evaluatie, promotie, audit en rollback | Geen eigen skillformaat ontwerpen; een formaat is nog geen veilige store of sandbox |
| Metrics en kosten | OpenTelemetry uit Pydantic AI en een bestaande backend zoals Grafana | Eva-specifieke productmetrics en privacyfilter | Geen eigen observabilityplatform bouwen |
| Dependencyupdates | Renovate in de ontwikkelomgeving | Exacte pins, compatibiliteitstest, menselijke/reviewer-go en rollback | Nooit automatisch naar productie promoveren |
| SBOM, CVE's, secrets en images | Trivy; eventueel Cosign/in-toto voor ondertekening en attestatie | Eva valideert de receipt en blokkeert de release | Geen eigen scanner of cryptografisch updateprotocol bouwen |

## Belangrijke versiegrenzen

Hergebruik betekent niet blind `latest` installeren.

- De draaiende `/eva2`-image gebruikt nog `pydantic-ai-slim==2.32.1`. De
  actuele hoofd-runtimebron, dependencylock en PlatformLock v3 kiezen
  `2.37.0`. Exact die 38-distributie-kandidaat is zonder bouwnetwerk gebouwd,
  streamde via echte Codex, koos een uitgestelde tool, hervatte uit een receipt,
  doorliep TOOL01 met een fysieke `SIGKILL` en scant 0 hoog/0 kritisch. De
  direct voorafgaande `2.36.0`-receipts blijven historisch en worden niet als
  bewijs voor `2.37.0` hernoemd. De volledige beheerde wegwerpstack,
  onafhankelijke herbouw en A/B-livegang blijven open. De
  brede 69-distributiekandidaat met MCP-extra en een tweede HTTP-stack is
  afgewezen. De
  voicekandidaat
  `experiments/pydantic-realtime-v1/` installeerde 47 hashgepinde packages en
  bevestigde de publieke sessie-/onderbrekingsinterface. De extra vereist
  `websockets>=14`; de kandidaat behoudt daarom compatibel
  `websockets==17.0.1`. De eerdere tussenkandidaat `2.32.2` vervalt daarmee.
- Voor de lokale voicefallback is geen eigen audio- of spraakprotocol gemaakt:
  checkpoint `73561ce` beproeft de officiële `wyoming==1.10.0`-client met
  gepinde `OHF-Voice/wyoming-piper v2.4.2`- en
  `OHF-Voice/wyoming-faster-whisper v3.6.0`-sidecars. De kleine synthetische
  STT→Eva→TTS-keten is groen. Checkpoint `8af6a15` herhaalt de exact gepinde
  route ook vanuit voorverwarmde caches op een alleen-lezen intern netwerk
  zonder gateway, hostpoort of hostmount. Checkpoint `d28ee3a` relayt nu de
  Piper-PCM direct chunk voor chunk naar Faster-Whisper zonder een compleet
  invoerbestand te bewaren. Beide sidecars blijven `PROEF`: hardware, twee
  richtingen tegelijk, interrupt, latency en review zijn nog niet bewezen. Er
  is niets aan de productlock of B toegevoegd.
- Hoorbaar stoppen binnen 250 ms blijft een aparte proef van Eva's eigen
  playback/media-bridge. Een upstream cancellation-API is nog geen gemeten
  barge-in.
- Het losse Pydantic AI Harness is jonger en gebruikt `0.x`-releases. De
  beproefde actuele kandidaat is `0.25.0`. Zijn
  subagentengine is lokaal echt beproefd. Zijn `PlaywrightBrowser` is handig,
  maar draait in Eva's Pythonproces en kan niet samen met Pydantic durable
  execution. Daarom heeft Microsoft Playwright MCP de eerste productgerichte
  lokale read- en actieproef doorlopen en houden we niet beide browserlagen.
- Eva gebruikt nu een lokale DBOS-patch `2.30.0+eva.1` die retry na een
  onduidelijke databasecommit begrenst. De bronaudit van stock `2.31.0` vond
  wel een belangrijke upstreamfix voor dubbele workers, maar nog steeds geen
  begrensde retry/no-DDL/commit-ambiguïteitsgarantie. De volgende proef is
  daarom een kleine `2.31.0+eva.1`-rebase; een blinde stockupgrade en een eigen
  workflowplatform zijn beide afgewezen.
- Playwright MCP, Browser Use, OpenClaw en iedere andere sidecar worden op
  release, commit, image-digest en licentie vastgezet voordat zij secrets of
  echte accounts zien.

De actuele hoofdproefpins zijn: OpenAI Python `3.6.0`, Pydantic AI `2.37.0`,
MCP Python SDK `2.1.1`, HA-MCP `v8.3.0` op OCI-index
`sha256:38b3a0b6b6b69d3fd074f958fbf9653ae2d50c8727b4c0dcde640cfa3a638176`,
Playwright MCP `0.0.79`, Harness `0.25.0`, React Admin
`5.15.1` en PyInstaller `6.20.0`. Agent Skills wordt op commit `69ef37e…`
beproefd. Dit zijn wegwerp- of toekomstige proefinputs, geen goedgekeurde
productdependencies en geen toestemming om transitieve packages stil mee te
veranderen. De aparte delegated-work-worker houdt voorlopig zijn eigen
geïsoleerde `2.32.1`-lock; dat is niet de hoofd-runtime en wordt afzonderlijk
opgewaardeerd. HA-MCP is een losse Python-3.13-sidecar en verandert de
hoofd-runtime-lock niet.

## Wat nu echt is geprobeerd

### Eerste echte vervangkandidaat: officiële OpenAI-SDK

Checkpoint `8526314` gebruikt de officiële OpenAI Python-SDK `3.3.1`
daadwerkelijk voor één gewone gespreksroute. Dit is dus meer dan een GitHub-link
of installatiedemo: 18 SDK-tests en in totaal 63 gerichte regressies zijn groen,
de volledige dependencydelta is hashgebonden en een onafhankelijke reviewer gaf
`GO` voor de kandidaatcode.

Wat hierdoor niet meer door Eva zelf hoeft te worden onderhouden, is het gewone
versturen en decoderen van die ene Responses-stream. Wat wel Eva-eigen blijft,
is wie spreekt, wat privé is, welk token/accountpaar actueel is, of een tool mag
worden uitgevoerd, wanneer opnieuw geprobeerd mag worden en wat Eva uiteindelijk
zegt.

Dit was de eerste kandidaat. De latere actuele SDK `3.6.0` draait inmiddels
werkelijk op `/eva2`: een gehashte wheelhouse, netwerkloze minimale image,
SBOM/0-0-scan, echte providercall, datakopie en volledige nieuw→oud→nieuw-
terugrolcyclus zijn groen. Een multi-architectuurrelease is daarmee nog niet
bewezen. Zie `experiments/openai-sdk-v1/`,
`reviews/openai-sdk-transport-candidate-8526314.md` en
`evidence/product/openai-sdk-3.6.0-live-ab-cycle-2026-08-30.json`.

### Officiële MCP-SDK: gemeten `NO-GO` voor de smalle client

De officiële MCP Python SDK `2.1.1` is inmiddels wel commitgebonden beproefd.
De volledige dependency voegt tien packages toe aan Eva's runtimeclosure. Zijn
standaardclient kan bovendien GET/DELETE, reconnects, callbacks en ruimere
sessies gebruiken, terwijl Eva voor deze ene read-route exact vier begrensde
POSTs nodig heeft.

Daarna is alleen `mcp-types==2.1.1` in een tijdelijke productdiff geprobeerd.
Die proef was 22/22 groen, maar voegde 87 productregels toe en verwijderde geen
transportcode. De kandidaat is daarom teruggedraaid. De huidige client blijft:
niet omdat alles zelf bouwen stoerder is, maar omdat beide dependencies hier
meetbaar groter werden. De exacte locks, replaychecker en onafhankelijke review
staan in `experiments/mcp-sdk-v1/`, `experiments/mcp-types-v1/` en
`reviews/mcp-dependency-bakeoff-c5b7d88.md`.

### Communitykandidaat HA-MCP: lokaal werkelijk gestart

`homeassistant-ai/ha-mcp` `v8.3.0` is een veel grotere hergebruikkans. De exact
gepinde amd64-image is lokaal gestart met `--network none`, read-only
filesystem, zonder capabilities, zonder secrets en zonder echt Home Assistant.
De MCP-handshake werkte en gaf in read-only modus 39 tools, waaronder zoeken,
actuele toestand, historie en automation-lezen. Een automation-write en een
backup-write werden vóór Home Assistant met `READ_ONLY_MODE` geweigerd.

Dit project heeft 88 gedocumenteerde tools, vier vaste maintainers, een
securitybeleid, multi-architectuurimages en 621 Pythonbestanden onder `tests/`,
waarvan 483 `test_*.py`-modules. Het kan vooral toekomstige dashboard-, trace-,
backup- en beheeradapters voorkomen. Daarom blijft het een serieuze optionele
capabilityprovider.

Het is nog geen productieonderdeel en heeft het echte huis niet benaderd. De
harde toelatingseis is dat de dependency de verantwoordelijkheid werkelijk
overneemt, de oude uitvoerroute verdwijnt en precies één runtime-eigenaar
overblijft, met dezelfde state-, historie-, service-, automation-, crash- en
readbackgevallen. De verdiepende audit mat dat een veilige transportswap circa
174–192 regels zou verwijderen; dat is context en geen promotiegrens.
Bovendien zijn alle zeven outputschema's open
objecten; zoek-, historie- en resultaatsbetekenis moeten dus nog door Eva
worden gecontroleerd. Een simpele productieomschakeling is daarom nu `NO-GO`.

De exacte bron/imagebinding en herhaalbare smoke staan in
`experiments/ha-mcp-sidecar-v1/`. De gesloten bron-, mapping- en
tooldescriptorgrens staat in `experiments/ha-mcp-parity-v1/`. Het vervolg mag
alleen een kleine state→service→aparte-stateproef zijn; pas bij echte
semantische pariteit, expliciete verantwoordelijkheidsoverdracht en verwijdering
van de overlappende oude route mag productcode verdwijnen. De onafhankelijke
herreview staat in `reviews/ha-mcp-parity-contract-c03942d.md`.

### Eerste echte subagentkandidaat: Harness neemt de generieke motor over

Pydantic AI `2.34.0` en Harness `0.25.0` zijn samen in een afzonderlijke,
volledig hashgebonden 42-package-omgeving geïnstalleerd. Harness voert daar de
echte child-run uit; Eva heeft dus geen eigen spawn-, modelrun-, childcontext-,
callbudget- of coöperatieve async-time-outengine nodig.

De dunne proeflaag accepteert alleen een gesloten naam, model en tekstinstructie
per helper. Automatisch gevonden agentbestanden, parenttools, gedeelde
capabilities en recursieve delegatie staan uit. Alle **22 tests**, Ruff en
`pip check` zijn groen. De onafhankelijke reviewer gaf `GO` voor exact
`55cf7cc`, uitsluitend als toolvrije en effectvrije enginekandidaat.

De eerste verticale productkoppeling haalt de toegelaten taak nu wél atomair
uit PostgreSQL, bewaart usage en resultaat en voorkomt na `call_started` een
blinde tweede modelrun. Harness draait bovendien in hetzelfde proces: een
coöperatieve time-out stopt async werk, maar is geen harde kill van blokkerende
code of een extern effect. De koppeling blijft daarom providerloos, toolvrij en
effectvrij. Zie `experiments/harness-subagents-v1/` en
`reviews/harness-subagent-engine-55cf7cc.md`.

De daaropvolgende bronaudit vond een belangrijke API-grens: Harness'
`delegate_task` retourneert in `0.25.0` alleen `str(result.output)`. Daarom is
Harness wel de gekozen interactieve fork/join-engine, maar niet de interface
tussen PostgreSQL en een reeds gekozen worker. De tweede proef gebruikt daar
rechtstreeks Pydantic AI's publieke `Agent.run()`, `PromptedOutput`,
`RunUsage`, `UsageLimits` en `CancellationToken`. Eva voegt alleen het gesloten
taakcontract, verzegeld model-/worker-/prijsprofiel, canonieke opslag en
foutredactie toe. De eerste onafhankelijke review vond vier echte fouten in de
proef: kosten waren niet hard, een absolute deadline kon worden overschreden,
onbekende usage leek nul en het contract stond in de verkeerde pakketlaag. De
lokale correctie voegt bovendien contracthashes, provider-outputlimiet en
uitgeschakelde telemetry toe. 23 nieuwe tests houden de child privé,
context-/tool-/workspacevrij en zonder child-netwerktools; samen met Harness is
de map 45/45 groen. De vaste modelproviderverbinding is geen childtool.
De directe runner loopt inmiddels via een eigen PostgreSQL-call-startfence,
duurzame resultaatopslag en read-only Kanbanprojectie. De officiële-SDK-worker
draait in een aparte kandidaatimage en Eva's commandolaag kan hem starten,
volgen en actief stoppen. Open blijven een eigenaar-goedgekeurde externe
providerproef via Telegram/B, harde processkill, portable-v2-release,
eigenaar-authenticatie en productpromotie. De eerdere API-kandidaatherreview op
`b7e9af7` en de volledige verticale herreview zijn groen.

## Hoe een GitHub-project wordt toegelaten

Veel sterren zijn prettig, maar geen bewijs van kwaliteit. Een kandidaat gaat
pas door wanneer alle onderstaande vragen groen zijn:

1. Is de repository niet gearchiveerd en zijn er recente commits en releases?
2. Werken meerdere onderhouders of terugkerende bijdragers eraan?
3. Zijn licentie, securitybeleid, changelog en upgradepad duidelijk?
4. Kan de gekozen versie exact worden gepind en zonder verborgen `latest`
   worden gebouwd?
5. Kan zij lokaal of als geïsoleerde sidecar draaien zonder Eva's database,
   hele geheugen of alle secrets te krijgen?
6. Levert zij dezelfde gebruikersuitkomst binnen latency-, token-, geheugen-
   en kostenbudget?
7. Blijven Eva's identiteit, privacy, mandaat, ledger en waarheid leidend?
8. Bestaan back-up, rollback, SBOM en vulnerabilityscan?
9. Slaagt een upgrade én een downgrade op gekopieerde testdata?
10. Kan de dependency later worden vervangen zonder Eva's geheugen of
    taakgeschiedenis te verliezen?
11. Welke versies ontvangen securityfixes, welke advisories zijn open en is het
    exacte oude rollbackartifact lokaal beschikbaar?
12. Wanneer is de volgende onderhoudscontrole en wie bezit de dunne
    Eva-adapter?

Een afwijzing krijgt een concrete mislukte proef. “Wij kunnen het zelf netter”
is geen geldige reden.

## Onderhouden projecten die nu serieus meetellen

Dit is een actuele kandidaatmomentopname. Zij geeft kandidaten, geen
automatische productgoedkeuring.

`GO` geldt alleen voor de genoemde smalle rol. `PROEF` betekent nog geen
productdependency. `NO-GO V1` voorkomt overlap totdat een smallere kandidaat
een concreet gat laat zien.

| Project | Oordeel | Feitelijke onderhoudssignalen | Voorbehoud |
|---|---|---|---|
| Pydantic AI Slim `2.37.0` + OpenAI SDK `3.6.0` | `PROEF: gecorrigeerde bron, nog geen nieuwe kandidaat` | De bestaande 38 distributies blijven 38; exacte lock/wheels, echte streaming/usage/toolkeuze en TOOL01-crashherstel zijn groen. De eerste 0/0-image is afgekeurd; de loadercorrectie is fysiek diagnostisch bewezen | Nieuw offline bronimage, SBOM/scan, volledige release-testset, beheerde wegwerpstack, onafhankelijke herbouw, arm64-keuze en A/B-livegang ontbreken nog |
| Pydantic AI Slim `2.32.1` + OpenAI SDK `3.6.0` | `GO: geïsoleerde bestaande delegated-work-worker` | De worker gebruikt PostgreSQL-handoff, lease, eenmalige call-start, resultaat, actieve stop, aparte kandidaatimage en read-only Kanban | Dit is een afzonderlijke legacy pin, niet de hoofd-runtime. Upgrade en productpromotie blijven apart |
| Pydantic AI `2.34.0` + Harness `0.25.0` | `GO: geïsoleerde fork/join-kandidaat` | Harness voert de interactieve toolvrije en effectvrije fork/join-engine uit met gesloten context, calls, tokens en time-out | Nog niet de duurzame worker of productroute; koppeling aan Eva's identiteit, live provider, Telegram/B en productpromotie blijven afzonderlijk open |
| DBOS Transact `2.31.0` | `NO-GO stock / GO rebaseproef` | MIT, actuele officiële release met de belangrijke duplicate-loserfix #823 en nul nieuwe basispackages | Stock behoudt onbegrensde transactieretry en kan na verloren commitbevestiging de body herhalen; Eva's smalle patch moet op 2.31 worden gerebased |
| Officiële MCP Python SDK `2.1.1` | `NO-GO: smalle V1-client` | MIT, Tier-1 SDK, snelle releases en expliciet versiebeleid; exacte 48-package-lock is compatibel | Volledige SDK voegt tien packages en onnodig sessiegedrag toe; types-only gaf +87 productregels en verwijderde niets |
| Home Assistant MCP Server | `PROEF: basis` | Onderdeel van Home Assistant Core; officiële Streamable HTTP-/OAuth-route, tools en live contextsnapshot | Geschikt voor blootgestelde Assist-entiteiten en context; nog geen bewijs voor Eva's historie- en configuratiegevallen |
| `homeassistant-ai/ha-mcp` `v8.3.0` | `PROEF: optionele HA-sidecar` | MIT, vier maintainers, recente onveranderlijke release, 88 tools, multi-arch images en 483 `test_*.py`-modules | Een directe transportswap nam de bestaande semantische verantwoordelijkheid niet volledig over. Alleen een state/service/readbackproef zonder dubbele route gaat door |
| Microsoft Playwright MCP `0.0.79` | `PROEF: lokale read + vaste actie groen` | Apache-2.0, signed release, 36.500 sterren, 573 commits en onderhoud binnen Microsoft; checkpoint `72625e0` is onafhankelijk herhaald | Nog `0.x`; alleen een credentialvrije lokale fixture is getest. Publieke sites, accounts en productisolatie blijven open; upstream zegt zelf dat originfilters geen securitygrens zijn |
| Vercel `agent-browser` `0.35.1` | `PROEF: functioneel groen, niet toegelaten` | Apache-2.0, compacte native CLI/MCP-provider met actuele release; dezelfde gesloten read→één klik→onafhankelijke readbackproef slaagde als niet-root zonder host-mount, poort of internetroute | De standaard MCP-core bevat ook JavaScript-evaluatie, plugins en statefuncties; een toekomstige Eva-adapter moet daarom veel smaller zijn. De verse gelijke scan vond 8 CRITICAL + 55 HIGH, dus deze kandidaat is niet geïnstalleerd of gepromoveerd. Receipt: `evidence/living-core/agent-browser-sidecar-evaluation-2026-08-26.json` |
| Browser Use | `NO-GO V1` | MIT en een eigen securityroute | Tweede modelgedreven planner, extra calls en overlap; alleen heropenen als een benoemde taak Playwright MCP niet haalt |
| Hindsight API `0.9.1` | `GO: huidige L2` | MIT, exact artifact/digest en bestaande Eva-livebewijzen | PostgreSQL blijft canoniek; iedere upgrade blijft een aparte proef met export, privacy, latency en rollback |
| Hindsight Control Plane UI | `PROEF: eigenaar-weergave` | Tijdelijk leeg gestart uit de gepinde Hindsight `0.9.1`-image; health en UI kwamen op, zonder Eva-data of secrets | De UI biedt bankaanmaak, geheugenwissen, configuratie en bankverwijdering. Daarom is zij geen directe schrijvende autoriteit, maar wel een kandidaat achter Eva's toekomstige eigenaargrens. Receipt: `evidence/living-core/hindsight-control-plane-isolation-2026-08-26.json` |
| LiveKit Agents | `PROEF: voicefallback` | Apache-2.0 framework, releases en bijdragen van meerdere auteurs | Alleen als Pydantic Realtime latency, reconnect of transport niet haalt; geen dubbele voicekern |
| OpenClaw Gateway/plugin | `PROEF` | MIT core, signed releases, zeer recente commits en veel bijdragers | Hoge churn, eerdere gateway-scopeadvisory en volledig vertrouwde plugins; exact één geïsoleerde plugin per proef |
| React-Admin | `PROEF: UI` | MIT, doorlopende releases en openbare issue-/releaseplanning | Alleen presentatie via Eva's API; CSP/XSS-tests, geen directe database of secrets |
| `dnd-kit` | `PROEF: Kanbanbediening` | MIT, 17.000+ sterren, honderden releases en actieve bijdragen in 2026 | Alleen drag-and-droppresentatie; Eva's API valideert iedere verplaatsing en blijft de enige taakwaarheid |
| PyInstaller | `PROEF: launcher` | `GPL-2.0-or-later WITH Bootloader-exception`, veel releases en ondersteuning voor gangbare desktopplatforms | Geen cross-compiler of complete installer; per OS bouwen, signen, verwijderen en rollback testen |
| Agent Skills-specificatie | `PROEF: skillformaat` | Open specificatie/code met Apache-2.0 en een exact te pinnen commit | Geen store, sandbox of rechtenmodel; Eva's quarantaine en promotie blijven leidend |
| Renovate | `GO: voorstelbot` | AGPL-3.0, immutable releases en concrete security-/permissiondocumentatie | `automerge=false`, geen base-branch push of post-upgradecode; ieder voorstel door tests en review |
| Trivy | `PROEF: scannersignaal` | Apache-2.0, signed/checksummed artifacts en actieve advisoryafhandeling | Releases/actions zijn in 2026 gecompromitteerd; nooit `latest`, nooit de enige releasewaarheid |

## Wat dit concreet aan bestaand werk verandert

- De geteste Eva-ledger en readbackketen worden niet weggegooid. Zij beschermen
  precies het deel dat generieke frameworks niet voor Eva kunnen beslissen.
- Na de huidige actieketen komen er geen nieuwe handgeschreven generieke
  workerloops bij. DBOS mag scheduling en wake-up leveren, maar vervangt Eva's
  grants, externe-actiestatus, ambiguïteit en readback niet.
- MCP blijft de normale skill- en providerdraad. Een volledige clientdependency
  komt alleen in de route wanneer zij een eigen benoemde verantwoordelijkheid
  heeft of de overlappende huidige client werkelijk vervangt.
- Home Assistant gaat niet per functie verder groeien in Eva. HA-MCP wordt niet
  blind ingevoerd: één state→service→aparte-stateproef moet aantonen welke
  verantwoordelijkheid upstream overneemt en welke providerneutrale Eva-grens
  noodzakelijk blijft.
- Voice en subagents beginnen op de actuele Pydantic AI/Harness-basis. De
  subagentengine werkt al in een onafhankelijk gereviewde providerloze proef;
  een eigen generieke runner vervalt. Voor de browser heeft Microsoft
  Playwright MCP inmiddels één vaste lokale pagina gelezen en één vaste klik
  via Eva's duurzame logboek uitgevoerd. Harness blijft alleen de goedkope
  vergelijkingsroute. Eva houdt niet twee browserplatforms.
- Hindsight's bestaande UI blijft kandidaat voor een eigenaar-weergave. De
  tijdelijke proef bewees wel dat directe UI-writes geen Eva-persoon-, groep-
  of consentgrens kennen. Hindsight blijft het bestaande langetermijngeheugen.
- Een toekomstige eigenaarroute begint read-only. Correcties of verwijderen
  lopen via Eva's eigen afgeschermde API en audit, niet als stil doorgeefluik
  naar de algemene Hindsight-UI.
- Nieuwe chatkanalen beginnen bij OpenClaw of de officiële SDK. Zelf adaptercode
  maken is de laatste uitweg.
- De lokale DBOS-fork, private imports en dubbele workflowcode worden vóór de
  eindfreeze expliciet verminderd of als noodzakelijk bewezen.

De read-only code-audit schat dat hiermee tienduizenden toekomstige regels voor
browser, voice, Kanban, queues, updater en extra kanalen kunnen worden vermeden.
Ook kan na pariteitsproeven enkele duizenden bestaande productregels verdwijnen.
Verwijderen gebeurt pas na dezelfde tests op beide routes en een eenvoudige
rollback; een grote herschrijving zonder vangnet zou opnieuw tijd verspillen.

## Grootste concrete vervangkansen in de huidige bron

Dit zijn geen beloofde verwijdercijfers. Het zijn de eerste plekken waar een
upstream proef moet aantonen hoeveel eigen code werkelijk overbodig wordt.

| Huidige Eva-bron | Omvang op 25 augustus 2026 | Upstream proef | Wat altijd Eva-eigen blijft |
|---|---:|---|---|
| `adapters/model/conversation.py` | 2.378 regels | OpenAI-SDK voor providertransport en typed streaming; pas later lagere Pydantic-mapping | IdentityPack, audience, agentloop, budgetbesluit, tooluitvoering, actiebewijs en eindantwoord |
| `tests/fixtures/mcp.py` | 415 bewijsregels, niet in het productbudget | Volledige SDK en `mcp-types` zijn beproefd en afgewezen voor de oude smalle route | Alleen de testfixture blijft; opnieuw een productadapter kiezen als een echte provider MCP vereist |
| `adapters/connectors/home_assistant.py` plus automationtransport | 1.023 regels | `homeassistant-ai/ha-mcp v8.3.0`; eerste audit vindt circa 174–192 transportregels direct vervangbaar | Least-privilege binding, één duurzame poging en onafhankelijke readback; alleen verder bij echte semantische pariteit, verwijdering van de oude route en één runtime-eigenaar |
| Zeven application-workerbestanden | meerdere losse loops | Alleen gedeelde polling/scheduling tegen DBOS `2.31.0+eva.1` meten | De 2.31-audit vond nul veilig verwijderbare workerregels; taakbetekenis, mandaat, idempotency, externe ambiguïteit en gebruikersstatus blijven Eva-eigen |
| HTML/JavaScript in `adapters/api/app.py` | onderdeel van 1.536 regels | React-Admin of Pydantic web/AG-UI als presentatielaag | Gesaneerde Eva-API, authenticatie, writes via commands en audit |

De volgorde is bewust: eerst de officiële OpenAI- en MCP-SDK's in twee kleine
echte routes, daarna Home Assistant via zijn eigen MCP-server en pas daarna de
grotere worker- en UI-vereenvoudiging. Lagere Pydantic-primitives komen alleen
tussen Eva en de OpenAI-SDK als een diff bewijst dat zij een duidelijke
verantwoordelijkheid beter overnemen en de overlappende oude route verdwijnt.
Zo wordt "dependency-first" zichtbaar als echte verantwoordelijkheidsoverdracht
met minder dubbel onderhoud, niet alleen als een lijst GitHub-links. Totale LOC
mag daarbij groeien voor aantoonbaar afzonderlijk nut.

## Kleine dependency-first gates

- [ ] `DF01` — iedere hoofdcomponent heeft `reuse`, `thin Eva layer` of
  `Eva-owned` plus kandidaat, licentie, pin en onderhoudsbewijs.
- [x] `DF02a` — OpenAI Python SDK `3.6.0` verving de live Responses-transport-
  route met Eva's identiteit, access-only lease en usage. De exacte offline
  image, SBOM/scan, echte providerbeurt, data-upgrade en volledige
  nieuw→oud→nieuw A/B-cyclus zijn groen.
- [ ] `DF02b` — Pydantic AI `2.37.0` mag de model- en MCP-toolloop alleen in één
  aantoonbaar verwijderende diff overnemen. De 5/5 modelproef, minimale image,
  SBOM, verse 0/0-scan, externe providercall en callback vóór provider-I/O zijn
  groen. Er zijn 3.342 overlappende productregels gemeten, maar nog nul regels
  veilig verwijderd; een tweede loop naast Eva blijft verboden.
- [x] `DF02c` — de officiële MCP SDK `2.1.1` en de kleinere
  `mcp-types==2.1.1`-route zijn met exacte hashlocks en productprototype
  vergeleken. Beide zijn voor Eva's smalle V1-client `NO-GO`: de volledige SDK
  voegt tien packages en te ruim gedrag toe; types-only gaf netto 87 regels
  groei. Replay, bestaande regressies en onafhankelijke review zijn groen
  (`c5b7d88`, `c83d395`).
- [ ] `DF02d` — `homeassistant-ai/ha-mcp v8.3.0` draait exact gepind als
  geïsoleerde sidecar en vervangt na echte least-privilege HA-pariteit de oude
  uitvoerroute zonder een tweede runtime-eigenaar achter te laten. De
  netwerkloze read-only image-smoke en onafhankelijke review zijn groen
  (`86c7247`, `3896f17`). De gesloten bron-/toolgrens en twaalf mutatietests
  zijn onafhankelijk groen (`c03942d`). De huidige directe vervangingsraming
  van 174–192 regels is alleen een omvangsmeting; echt huis, semantische
  pariteit, latency, crash, upgrade en rollback staan open.
- [ ] `DF03` — stock DBOS `2.31.0` is brongebonden vergeleken en afgewezen als
  drop-in: het bevat de gewenste upstream duplicate-loserfix, maar niet Eva's
  begrensde retry/no-DDL/commit-ambiguïteitsgaranties. De exacte
  `2.31.0+eva.1`-wheel is lokaal hashgebonden herbouwd en doorliep 15
  PostgreSQL-crash-/herstelscenario's plus 15 catalogusattestaties. Over blijft
  schema 107→108, upgrade, downgrade, candidate-image, onafhankelijke review
  en rollback; er worden nu geen workerregels verwijderd.
- [ ] `DF04` — Harness `0.25.0` voert in de actuele kandidaat
  echte providerloze child-runs uit met een aparte context, nul childtools,
  vooraf getelde inputtokens, begrensde calls en een coöperatieve async-time-out.
  De gesloten 42-package-lock, 22 tests, Ruff, `pip check` en onafhankelijke
  review zijn groen. De publieke Pydantic AI-worker voegt 23 groene checks
  voor gesloten output, eigen usage, cancellation en foutredactie toe; de
  onafhankelijke review op `b7e9af7` is groen. PostgreSQL-handoff, lease,
  eenmalige call-start, resultaatopslag, aparte officiële-SDK-workerimage,
  commandostart/-status/-stop, crashgrens en read-only Kanban zijn daarna als
  één verticale kandidaat getest. Productpromotie blijft open voor portable-v2,
  harde processkill, eigenaar-authenticatie en een eigenaar-goedgekeurde externe
  providerproef via Telegram/B; Pydantic Realtime en de publieke browserroute
  zijn aparte proeven.
- [ ] `DF05` — Hindsight Control Plane als eigenaar-weergave, standaard
  observability en een dashboardframework leveren de beheerbasis zonder tweede
  waarheid; een direct schrijvende Control Plane blijft uitgesloten.
- [ ] `DF06` — Renovatevoorstel, Trivy-scan, SBOM, upgrade, rollback en
  onafhankelijke review zijn groen op exact dezelfde kandidaatimage.

Tot deze gates groen zijn, is de keuze **dependency-first** vastgesteld maar
zijn de nieuwe packages nog geen goedgekeurde productdependencies.

## Primaire bronnen

- Pydantic AI actuele hoofdkandidaat `2.37.0`: <https://github.com/pydantic/pydantic-ai/tree/v2.37.0>
- Pydantic AI laatste volledig gebouwde kandidaat `2.36.0`: <https://github.com/pydantic/pydantic-ai/tree/v2.36.0>
- Pydantic AI bestaande delegated-worker `2.32.1`: <https://github.com/pydantic/pydantic-ai/tree/v2.32.1>
- Pydantic AI actuele proefrelease `2.34.0`: <https://github.com/pydantic/pydantic-ai/releases/tag/v2.34.0>
- Pydantic AI realtime-fix `2.32.2`: <https://github.com/pydantic/pydantic-ai/releases/tag/v2.32.2>
- Pydantic AI cancellation-fix: <https://github.com/pydantic/pydantic-ai/pull/7528>
- Pydantic AI-versiebeleid: <https://github.com/pydantic/pydantic-ai/blob/main/docs/version-policy.md>
- Pydantic AI telemetry-advisory: <https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-3gh4-cghq-f8v4>
- Pydantic AI Harness: <https://github.com/pydantic/pydantic-ai-harness>
- Pydantic AI Harness `0.25.0`: <https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.25.0>
- DBOS Python: <https://github.com/dbos-inc/dbos-transact-py>
- DBOS Python `2.31.0`: <https://github.com/dbos-inc/dbos-transact-py/releases/tag/2.31.0>
- Officiële MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- MCP Python SDK-versiebeleid: <https://github.com/modelcontextprotocol/python-sdk/blob/main/VERSIONING.md>
- Home Assistant MCP Server: <https://www.home-assistant.io/integrations/mcp_server/>
- Home Assistant MCP-bron: <https://github.com/home-assistant/core/tree/dev/homeassistant/components/mcp_server>
- Home Assistant MCP-schema-issue: <https://github.com/home-assistant/core/issues/178032>
- HA-MCP communityproject: <https://github.com/homeassistant-ai/ha-mcp>
- HA-MCP exact `v8.3.0`: <https://github.com/homeassistant-ai/ha-mcp/releases/tag/v8.3.0>
- HA-MCP securitybeleid: <https://github.com/homeassistant-ai/ha-mcp/security/policy>
- Microsoft Playwright MCP: <https://github.com/microsoft/playwright-mcp>
- Microsoft Playwright MCP `0.0.79`: <https://github.com/microsoft/playwright-mcp/releases/tag/v0.0.79>
- Microsoft Playwright MCP-securitybeleid: <https://github.com/microsoft/playwright-mcp/security/policy>
- Browser Use: <https://github.com/browser-use/browser-use>
- Hindsight exact `0.9.1`: <https://github.com/vectorize-io/hindsight/tree/v0.9.1>
- Hindsight securitybeleid: <https://github.com/vectorize-io/hindsight/security>
- LiveKit Agents: <https://github.com/livekit/agents>
- OpenClaw-kanalen: <https://docs.openclaw.ai/channels>
- OpenClaw scope-advisory: <https://github.com/openclaw/openclaw/security/advisories/GHSA-4f8g-77mw-3rxc>
- React-Admin: <https://github.com/marmelab/react-admin>
- `dnd-kit`: <https://github.com/clauderic/dnd-kit>
- PyInstaller: <https://github.com/pyinstaller/pyinstaller>
- Agent Skills-specificatie: <https://github.com/agentskills/agentskills/commit/69ef37e9424c0a7ea9dd2293b559e43ec8176379>
- Renovate: <https://github.com/renovatebot/renovate>
- Renovate security en permissies: <https://docs.renovatebot.com/security-and-permissions/>
- Trivy: <https://github.com/aquasecurity/trivy>
- Trivy supply-chainadvisory: <https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23>
