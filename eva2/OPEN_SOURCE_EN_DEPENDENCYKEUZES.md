# Eva 2 — eerst hergebruiken, pas daarna zelf bouwen

Bijgewerkt: 26 augustus 2026.

## Het eerlijke antwoord

Er is **niet vroeg genoeg streng gecontroleerd wat bestaande open-sourceprojecten
al konden**. Daardoor dreigde Eva delen zelf te bouwen die een onderhouden
dependency of sidecar al beter en sneller kan leveren.

Het duidelijkste voorbeeld staat al in de repository:
`pydantic-ai-slim==2.32.1` is exact vastgezet, maar wordt in Eva's productiecode
nog niet als agentframework gebruikt. De actuele officiële release `2.34.0`
levert al realtime voice, webinterfaces, MCP en duurzame uitvoering. Het aparte
Harness `0.24.0` levert subagents, planning, browsergebruik, skills en
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
| Modeltransport en gestructureerde antwoorden | Eerst de officiële OpenAI-SDK; daarna alleen lagere Pydantic AI-primitives die extra code verwijderen | Identiteit, persoonscontext, agentloop, grants, tooluitvoering en antwoordbeleid | De OpenAI-SDK-kandidaat is gereviewd en behoudt Eva's grenzen; echte provider- en releasepromotie staan nog open |
| Smalle MCP-client | Eva's huidige begrensde client | Toegelaten tool/resource, byte- en tijdslimieten, schemahash en receipt | De volledige officiële MCP-SDK en alleen `mcp-types` zijn echt beproefd maar verwijderen voor deze route geen code; daarom nu niet installeren |
| Home Assistant-skill | Eerst Home Assistants ingebouwde MCP Server voor basiscontext; voor historie/configuratie de community-sidecar `homeassistant-ai/ha-mcp` | Persoonsmandaat, toolallowlist, één duurzame poging en onafhankelijke controle achteraf | `ha-mcp` `v8.3.0` is exact gepind en lokaal read-only gestart; promotie vereist minimaal 300 regels eigen HA-code weg tegen hoogstens 180 regels integratie |
| Duurzame workflows en wachtrijen | DBOS blijft de bestaande workflowdependency; `2.31.0+eva.1` wordt de volgende proef | Eva-ledger voor intent, call-start, ambigu resultaat en readback | Stock `2.31.0` kan de kleine Eva-patch en workerledger niet veilig vervangen; rebase wel, blind upgraden niet |
| Subagents | Harness `0.24.0` voor korte parentgestuurde fork/join; publieke Pydantic AI `Agent.run()` voor één reeds duurzaam gekozen child | Exacte taak-, context-, tool-, tijd- en token-grant plus duurzame handoff | Harness retourneert publiek alleen tekst en verliest voor een databaseworker getypeerde output/usage/cancellation; geen eigen spawn-, model-, budget-, time-out- of child-contextengine bouwen |
| Voice | Pydantic AI Realtime `2.34.0` als actuele proef | Persoonsbinding, privacy, barge-in naar Eva-stop, memory en toolreadback | Geen eigen WebRTC-, STT-, TTS- of jitterbufferstack bouwen |
| Browser | Microsoft Playwright MCP `0.0.79` krijgt de eerste productgerichte sidecarproef; Harness `PlaywrightBrowser` alleen een wegwerpvergelijking | URL-/account-/bestandsgrant, live stop, artifacts en eindcontrole | Geen Chromium-, profiel-, screenshot- of downloadengine bouwen; de browser blijft buiten Eva's kernproces |
| Open-ended webwerk | Geen extra V1-stack; Browser Use alleen als latere vergelijking | Zelfde Eva-grenzen; expliciet model-, stap- en kostenbudget | Nu niet toevoegen; alleen heropenen wanneer een benoemde taak Playwright MCP aantoonbaar niet haalt |
| Extra chatkanalen | Gepinde OpenClaw Gateway/plugin of officiële provider-SDK | Account→persoon, groepsconsent, deliveryledger en geheugenisolatie | WhatsApp niet zelf vanaf de draad bouwen |
| Geheugenbeheer | Hindsight `0.9.1` plus de bestaande lokale Control Plane UI | Eva's persoon-/groep-/projectbinding, consent en correcties | Geen tweede geheugenproduct installeren alleen voor een dashboard |
| Chat- en beheerinterface | Hindsight Control Plane voor geheugen en React-Admin `5.15.1` voor Eva's beheerpagina's; Pydantic `to_web()` alleen voor lokale ontwikkeling | Eén dun Eva-scherm voor taken, status, mandaten en bewijs | Geen compleet dashboardframework namaken; Pydantic waarschuwt zelf dat zijn ingebouwde webchat geen onbeveiligde productie-UI is |
| Kanbanpresentatie | React-Admin OSS plus de onderhouden drag-and-dropbouwstenen van `dnd-kit` | PostgreSQL blijft taakautoriteit; iedere wijziging via Eva's API | Geen tweede taakdatabase of Hermes-kloon; React-Admins Enterprise-Kanban wordt niet stil als gratis OSS-dependency aangenomen |
| Installatielauncher | PyInstaller als eerste bundelkandidaat plus Eva's bestaande FastAPI-wizard | Docker/WSL-controle, secrets, restore, A/B-update, ondertekening en uninstall | Geen eigen Python-bootstrapper bouwen; wel per besturingssysteem een exact artifact testen |
| Skillbestandsformaat | Agent Skills-specificatie plus officiële MCP SDK | Quarantaine, rechten, evaluatie, promotie, audit en rollback | Geen eigen skillformaat ontwerpen; een formaat is nog geen veilige store of sandbox |
| Metrics en kosten | OpenTelemetry uit Pydantic AI en een bestaande backend zoals Grafana | Eva-specifieke productmetrics en privacyfilter | Geen eigen observabilityplatform bouwen |
| Dependencyupdates | Renovate in de ontwikkelomgeving | Exacte pins, compatibiliteitstest, menselijke/reviewer-go en rollback | Nooit automatisch naar productie promoveren |
| SBOM, CVE's, secrets en images | Trivy; eventueel Cosign/in-toto voor ondertekening en attestatie | Eva valideert de receipt en blokkeert de release | Geen eigen scanner of cryptografisch updateprotocol bouwen |

## Belangrijke versiegrenzen

Hergebruik betekent niet blind `latest` installeren.

- Eva pinnt nu `pydantic-ai-slim==2.32.1`. De actuele officiële release is
  `2.34.0` van 24 augustus 2026. Die wordt niet stil geïnstalleerd, maar is nu
  de gezamenlijke proefbasis voor realtime voice en de officiële
  Harness-koppeling. De eerdere tussenkandidaat `2.32.2` vervalt daarmee.
- Hoorbaar stoppen binnen 250 ms blijft een aparte proef van Eva's eigen
  playback/media-bridge. Een upstream cancellation-API is nog geen gemeten
  barge-in.
- Het losse Pydantic AI Harness is jonger en gebruikt `0.x`-releases. De
  actuele officiële release is `0.24.0` van 19 augustus 2026. Zijn
  subagentengine is lokaal echt beproefd. Zijn `PlaywrightBrowser` is handig,
  maar draait in Eva's Pythonproces en kan niet samen met Pydantic durable
  execution. Daarom krijgt Microsoft Playwright MCP de eerste productgerichte
  sidecarproef en houden we niet beide browserlagen.
- Eva gebruikt nu een lokale DBOS-patch `2.30.0+eva.1` die retry na een
  onduidelijke databasecommit begrenst. De bronaudit van stock `2.31.0` vond
  wel een belangrijke upstreamfix voor dubbele workers, maar nog steeds geen
  begrensde retry/no-DDL/commit-ambiguïteitsgarantie. De volgende proef is
  daarom een kleine `2.31.0+eva.1`-rebase; een blinde stockupgrade en een eigen
  workflowplatform zijn beide afgewezen.
- Playwright MCP, Browser Use, OpenClaw en iedere andere sidecar worden op
  release, commit, image-digest en licentie vastgezet voordat zij secrets of
  echte accounts zien.

De actuele eerste proefpins zijn: OpenAI Python `3.3.1`, Pydantic AI `2.34.0`,
MCP Python SDK `2.1.1`, HA-MCP `v8.3.0` op OCI-index
`sha256:38b3a0b6b6b69d3fd074f958fbf9653ae2d50c8727b4c0dcde640cfa3a638176`,
Playwright MCP `0.0.79`, Harness `0.24.0`, React Admin
`5.15.1` en PyInstaller `6.20.0`. Agent Skills wordt op commit `69ef37e…`
beproefd. Dit zijn wegwerp- of toekomstige proefinputs, geen goedgekeurde
productdependencies en geen toestemming om transitieve packages stil mee te
veranderen. Eva's huidige productlock houdt Pydantic AI `2.32.1`; HA-MCP is
een losse Python-3.13-sidecar en verandert die lock niet.

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

De SDK staat nog niet in productie. Een echte providercall, snelheidsvergelijking,
beide processorarchitecturen, licentie/SBOM/scan, offline image en rollback zijn
nog open. De productie blijft daarom standaard de oude route gebruiken. Zie
`experiments/openai-sdk-v1/` en
`reviews/openai-sdk-transport-candidate-8526314.md`.

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
harde toelatingseis is minimaal 300 regels bestaande Home Assistant-productcode
verwijderen tegen hoogstens 180 regels dunne Eva-integratie, met dezelfde
state-, historie-, service-, automation-, crash- en readbackgevallen. De
verdiepende audit laat zien dat een veilige transportswap nu slechts circa
174–192 regels verwijdert. Bovendien zijn alle zeven outputschema's open
objecten; zoek-, historie- en resultaatsbetekenis moeten dus nog door Eva
worden gecontroleerd. Een simpele productieomschakeling is daarom nu `NO-GO`.

De exacte bron/imagebinding en herhaalbare smoke staan in
`experiments/ha-mcp-sidecar-v1/`. De gesloten bron-, mapping- en
tooldescriptorgrens staat in `experiments/ha-mcp-parity-v1/`. Het vervolg mag
alleen een kleine state→service→aparte-stateproef zijn; pas bij echte
semantische pariteit en nettokrimp mag productcode verdwijnen. De onafhankelijke
herreview staat in `reviews/ha-mcp-parity-contract-c03942d.md`.

### Eerste echte subagentkandidaat: Harness neemt de generieke motor over

Pydantic AI `2.34.0` en Harness `0.24.0` zijn samen in een afzonderlijke,
volledig hashgebonden 42-package-omgeving geïnstalleerd. Harness voert daar de
echte child-run uit; Eva heeft dus geen eigen spawn-, modelrun-, childcontext-,
callbudget- of coöperatieve async-time-outengine nodig.

De dunne proeflaag accepteert alleen een gesloten naam, model en tekstinstructie
per helper. Automatisch gevonden agentbestanden, parenttools, gedeelde
capabilities en recursieve delegatie staan uit. Alle **22 tests**, Ruff en
`pip check` zijn groen. De onafhankelijke reviewer gaf `GO` voor exact
`55cf7cc`, uitsluitend als toolvrije en effectvrije enginekandidaat.

Dit maakt runtime-subagents nog niet af. Eva moet nog zelf de toestemming en
taak uit PostgreSQL halen, childgebruik als eigen receipt bewaren en na een
procescrash veilig hervatten. Harness draait bovendien in hetzelfde proces: een
coöperatieve time-out stopt async werk, maar is geen harde kill van blokkerende
code of een extern effect. De eerste productkoppeling blijft daarom toolvrij en
effectvrij. Zie `experiments/harness-subagents-v1/` en
`reviews/harness-subagent-engine-55cf7cc.md`.

De daaropvolgende bronaudit vond een belangrijke API-grens: Harness'
`delegate_task` retourneert in `0.24.0` alleen `str(result.output)`. Daarom is
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
PostgreSQL, transportattemptreceipts, een echte provider en onafhankelijke
herreview zijn nog niet bewezen.

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

Dit is een momentopname van 26 augustus 2026. Zij geeft kandidaten, geen
automatische productgoedkeuring.

`GO` geldt alleen voor de genoemde smalle rol. `PROEF` betekent nog geen
productdependency. `NO-GO V1` voorkomt overlap totdat een smallere kandidaat
een concreet gat laat zien.

| Project | Oordeel | Feitelijke onderhoudssignalen | Voorbehoud |
|---|---|---|---|
| Pydantic AI `2.32.1` huidige lock / `2.34.0` proefkandidaat | `PROEF` | MIT, 19.500 sterren, 2.810 commits, actuele signed release en officiële realtime-, UI-, MCP- en durable-executionroutes | Echte Eva-route met IdentityPack, persoonsisolatie, usagebudget en uitgeschakelde/gesaneerde telemetry; alleen publieke API en geen tweede Agent-loop |
| Pydantic AI `2.34.0` + Harness `0.24.0` | `GO Harness-spike / HERREVIEW Pydantic-worker` | Harness voor interactieve fork/join is onafhankelijk groen. De publieke Pydantic AI-run voor reeds gekozen duurzaam werk is na een terechte `NO-GO` lokaal hersteld; de exacte 42-package-kandidaat staat providerloos op 45/45 tests | Directe worker wacht op herreview. PostgreSQL-handoff, schemarelease, transportattemptfence, processandbox, harde kill, echte model-/prijsrelease, provider, crashresume en Kanban staan open |
| DBOS Transact `2.31.0` | `NO-GO stock / GO rebaseproef` | MIT, actuele officiële release met de belangrijke duplicate-loserfix #823 en nul nieuwe basispackages | Stock behoudt onbegrensde transactieretry en kan na verloren commitbevestiging de body herhalen; Eva's smalle patch moet op 2.31 worden gerebased |
| Officiële MCP Python SDK `2.1.1` | `NO-GO: smalle V1-client` | MIT, Tier-1 SDK, snelle releases en expliciet versiebeleid; exacte 48-package-lock is compatibel | Volledige SDK voegt tien packages en onnodig sessiegedrag toe; types-only gaf +87 productregels en verwijderde niets |
| Home Assistant MCP Server | `PROEF: basis` | Onderdeel van Home Assistant Core; officiële Streamable HTTP-/OAuth-route, tools en live contextsnapshot | Geschikt voor blootgestelde Assist-entiteiten en context; nog geen bewijs voor Eva's historie- en configuratiegevallen |
| `homeassistant-ai/ha-mcp` `v8.3.0` | `PROEF: optionele HA-sidecar` | MIT, vier maintainers, recente onveranderlijke release, 88 tools, multi-arch images en 483 `test_*.py`-modules | Directe transportswap haalt met circa 174–192 verwijderbare regels de 300-regelgrens niet; alleen een semantische state/service-proef gaat door |
| Microsoft Playwright MCP `0.0.79` | `PROEF: eerste browsersidecar` | Apache-2.0, signed release, 36.500 sterren, 573 commits en onderhoud binnen Microsoft | Nog `0.x`; vereist Node 20+, een gepinde browser en externe netwerkisolatie. Upstream zegt zelf dat originfilters geen securitygrens zijn |
| Browser Use | `NO-GO V1` | MIT en een eigen securityroute | Tweede modelgedreven planner, extra calls en overlap; alleen heropenen als een benoemde taak Playwright MCP niet haalt |
| Hindsight API `0.9.1` | `GO: huidige L2` | MIT, exact artifact/digest en bestaande Eva-livebewijzen | PostgreSQL blijft canoniek; iedere upgrade blijft een aparte proef met export, privacy, latency en rollback |
| Hindsight Control Plane UI | `PROEF` | Onderdeel van dezelfde releasefamilie | Auth en tenant-/ACL-grenzen passen niet direct bij Eva; begin read-only achter Eva's API |
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
- MCP blijft de normale skill- en providerdraad, maar de volledige officiële
  clientdependency komt niet in de smalle V1-route. De huidige begrensde client
  blijft zolang een dependency aantoonbaar geen code verwijdert.
- Home Assistant gaat niet per functie verder groeien in Eva. HA-MCP wordt niet
  blind ingevoerd: de eerste diepere audit komt onder de 300-regelgrens. Alleen
  één kleine state→service→aparte-stateproef mag nog aantonen of de semantische
  laag werkelijk kan krimpen.
- Voice en subagents beginnen op de actuele Pydantic AI/Harness-basis. De
  subagentengine werkt al in een onafhankelijk gereviewde providerloze proef;
  een eigen generieke runner vervalt. Voor de browser is
  Microsoft Playwright MCP de eerste productgerichte sidecar; Harness blijft
  alleen de goedkope vergelijkingsroute. Eva houdt niet twee
  browserplatforms.
- Hindsight's bestaande UI wordt eerst aangesloten voordat een eigen
  geheugenbeheerpagina wordt gebouwd.
- De Hindsight Control Plane mag in productie niet stil via `npx` de nieuwste
  code ophalen. Ook die UI krijgt een exacte package- of imagepin, offline
  materialisatie, scan en rollback.
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
| `adapters/connectors/mcp.py` | 404 regels | Volledige SDK en `mcp-types` zijn beproefd en afgewezen voor deze smalle route | Huidige begrensde client blijft; opnieuw openen als een provider de standaardclient vereist én de totale code aantoonbaar krimpt |
| `adapters/connectors/home_assistant.py` plus automationtransport | 1.023 regels | `homeassistant-ai/ha-mcp v8.3.0`; eerste audit vindt slechts circa 174–192 transportregels direct vervangbaar | Least-privilege binding, één duurzame poging en onafhankelijke readback; alleen verder bij echte semantische pariteit en minimaal 300 regels weg |
| Zeven application-workerbestanden | meerdere losse loops | Alleen gedeelde polling/scheduling tegen DBOS `2.31.0+eva.1` meten | De 2.31-audit vond nul veilig verwijderbare workerregels; taakbetekenis, mandaat, idempotency, externe ambiguïteit en gebruikersstatus blijven Eva-eigen |
| HTML/JavaScript in `adapters/api/app.py` | onderdeel van 1.536 regels | React-Admin of Pydantic web/AG-UI als presentatielaag | Gesaneerde Eva-API, authenticatie, writes via commands en audit |

De volgorde is bewust: eerst de officiële OpenAI- en MCP-SDK's in twee kleine
echte routes, daarna Home Assistant via zijn eigen MCP-server en pas daarna de
grotere worker- en UI-vereenvoudiging. Lagere Pydantic-primitives komen alleen
tussen Eva en de OpenAI-SDK als een diff bewijst dat zij meer code verwijderen.
Zo wordt "dependency-first" zichtbaar als verwijderbare code, niet alleen als
een lijst GitHub-links.

## Kleine dependency-first gates

- [ ] `DF01` — iedere hoofdcomponent heeft `reuse`, `thin Eva layer` of
  `Eva-owned` plus kandidaat, licentie, pin en onderhoudsbewijs.
- [ ] `DF02a` — de synchrone OpenAI Python SDK `3.3.1` vervangt eerst één
  gewone handgeschreven request-/SSE-route met Eva's bestaande identiteit,
  actuele lease en usage-receipt. De hashgebonden kandidaat, streaming- en
  foutproeven en onafhankelijke review zijn groen (`8526314`). Echte
  providerpariteit, latency, beide wheelhouses, SBOM/scan, offline image en
  rollback staan nog open; daarom blijft de productiegate onafgevinkt.
- [ ] `DF02b` — Pydantic AI `2.34.0` krijgt daarna alleen modelmapping of een
  andere aantoonbaar verwijderende lagere primitive. Een Pydantic `Agent` wordt
  geen tweede tool-, geheugen- of antwoordloop naast Eva. Een eventuele lagere
  primitive moet nul eigen retries, begrensde aanvragen/tooluitvoer en geen
  toolcallback vóór Eva's grant en ledger bewijzen.
- [x] `DF02c` — de officiële MCP SDK `2.1.1` en de kleinere
  `mcp-types==2.1.1`-route zijn met exacte hashlocks en productprototype
  vergeleken. Beide zijn voor Eva's smalle V1-client `NO-GO`: de volledige SDK
  voegt tien packages en te ruim gedrag toe; types-only gaf netto 87 regels
  groei. Replay, bestaande regressies en onafhankelijke review zijn groen
  (`c5b7d88`, `c83d395`).
- [ ] `DF02d` — `homeassistant-ai/ha-mcp v8.3.0` draait exact gepind als
  geïsoleerde sidecar en vervangt na echte least-privilege HA-pariteit minimaal
  300 regels bestaande productcode met hoogstens 180 integratieregels. De
  netwerkloze read-only image-smoke en onafhankelijke review zijn groen
  (`86c7247`, `3896f17`). De gesloten bron-/toolgrens en twaalf mutatietests
  zijn onafhankelijk groen (`c03942d`). De huidige directe vervangingsraming
  van 174–192 regels haalt de promotiegrens niet; echt huis, semantische
  pariteit, latency, crash, upgrade en rollback staan open.
- [ ] `DF03` — stock DBOS `2.31.0` is brongebonden vergeleken en afgewezen als
  drop-in: het bevat de gewenste upstream duplicate-loserfix, maar niet Eva's
  begrensde retry/no-DDL/commit-ambiguïteitsgaranties. Volgende proef is exact
  `2.31.0+eva.1`, inclusief schema 107→108, upgrade, downgrade en foutinjectie;
  er worden nu geen workerregels verwijderd.
- [ ] `DF04` — Harness `0.24.0` voert in checkpoints `00482a3`–`55cf7cc`
  echte providerloze child-runs uit met een aparte context, nul childtools,
  vooraf getelde inputtokens, begrensde calls en een coöperatieve async-time-out.
  De gesloten 42-package-lock, 22 tests, Ruff, `pip check` en onafhankelijke
  review zijn groen. De publieke Pydantic AI-worker voegt 23 groene checks
  voor gesloten output, eigen usage, cancellation en foutredactie toe, maar
  wacht nog op onafhankelijke review. Productpromotie blijft open totdat Eva
  een version-matched schema, duurzame PostgreSQL-handoff en
  procescrashherstel heeft; harde processkill, Pydantic Realtime en Playwright
  MCP zijn aparte proeven.
- [ ] `DF05` — Hindsight Control Plane, standaard observability en een
  dashboardframework leveren samen de beheerbasis zonder tweede waarheid.
- [ ] `DF06` — Renovatevoorstel, Trivy-scan, SBOM, upgrade, rollback en
  onafhankelijke review zijn groen op exact dezelfde kandidaatimage.

Tot deze gates groen zijn, is de keuze **dependency-first** vastgesteld maar
zijn de nieuwe packages nog geen goedgekeurde productdependencies.

## Primaire bronnen

- Pydantic AI exact `2.32.1`: <https://github.com/pydantic/pydantic-ai/tree/v2.32.1>
- Pydantic AI actuele proefrelease `2.34.0`: <https://github.com/pydantic/pydantic-ai/releases/tag/v2.34.0>
- Pydantic AI realtime-fix `2.32.2`: <https://github.com/pydantic/pydantic-ai/releases/tag/v2.32.2>
- Pydantic AI cancellation-fix: <https://github.com/pydantic/pydantic-ai/pull/7528>
- Pydantic AI-versiebeleid: <https://github.com/pydantic/pydantic-ai/blob/main/docs/version-policy.md>
- Pydantic AI telemetry-advisory: <https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-3gh4-cghq-f8v4>
- Pydantic AI Harness: <https://github.com/pydantic/pydantic-ai-harness>
- Pydantic AI Harness `0.24.0`: <https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.24.0>
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
