# Eva 2 — eerst hergebruiken, pas daarna zelf bouwen

Bijgewerkt: 26 augustus 2026.

## Het eerlijke antwoord

Er is **niet vroeg genoeg streng gecontroleerd wat bestaande open-sourceprojecten
al konden**. Daardoor dreigde Eva delen zelf te bouwen die een onderhouden
dependency of sidecar al beter en sneller kan leveren.

Het duidelijkste voorbeeld staat al in de repository:
`pydantic-ai-slim==2.32.1` is exact vastgezet, maar wordt in Eva's productiecode
nog niet als agentframework gebruikt. Dezelfde officiële projectfamilie levert
al realtime voice, webinterfaces, MCP, duurzame DBOS-uitvoering en een apart
Harness voor subagents, planning, browsergebruik, skills en contextbeheer.
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
| Duurzame workflows en wachtrijen | DBOS via de officiële Pydantic AI-koppeling | Eva-ledger voor intent, call-start, ambigu resultaat en readback | DBOS laat starten, wachten en hervatten; geen tweede eigen queue-engine uitbreiden |
| Korte subagents | Pydantic AI Harness `0.24.0` als eerste compatibele kandidaat | Exacte taak-, context-, tool-, tijd- en token-grant plus duurzame handoff | Niet zelf spawnen, budgetteren en foutafhandelen wanneer Harness dit al doet |
| Voice | Pydantic AI Realtime vanaf `2.32.2` als eerste proef | Persoonsbinding, privacy, barge-in naar Eva-stop, memory en toolreadback | Geen eigen WebRTC-, STT-, TTS- of jitterbufferstack bouwen |
| Browser | Microsoft Playwright MCP als eerste eenvoudige provider | URL-/account-/bestandsgrant, live stop, artifacts en eindcontrole | Geen Chromium-, profiel-, screenshot- of downloadengine bouwen |
| Open-ended webwerk | Geen extra V1-stack; Browser Use alleen als latere vergelijking | Zelfde Eva-grenzen; expliciet model-, stap- en kostenbudget | Nu niet toevoegen; alleen heropenen wanneer een benoemde taak Playwright MCP aantoonbaar niet haalt |
| Extra chatkanalen | Gepinde OpenClaw Gateway/plugin of officiële provider-SDK | Account→persoon, groepsconsent, deliveryledger en geheugenisolatie | WhatsApp niet zelf vanaf de draad bouwen |
| Geheugenbeheer | Hindsight `0.9.1` plus de bestaande lokale Control Plane UI | Eva's persoon-/groep-/projectbinding, consent en correcties | Geen tweede geheugenproduct installeren alleen voor een dashboard |
| Chat- en beheerinterface | Pydantic web/AG-UI, Hindsight Control Plane en bestaande UI-componenten | Eén dun Eva-scherm voor taken, status, mandaten en bewijs | Geen compleet dashboardframework namaken |
| Kanbanpresentatie | Een onderhouden datagrid/boardcomponent; eerste kandidaat React-Admin | PostgreSQL blijft taakautoriteit; iedere wijziging via Eva's API | Geen tweede taakdatabase of Hermes-kloon |
| Installatielauncher | PyInstaller als eerste bundelkandidaat plus Eva's bestaande FastAPI-wizard | Docker/WSL-controle, secrets, restore, A/B-update, ondertekening en uninstall | Geen eigen Python-bootstrapper bouwen; wel per besturingssysteem een exact artifact testen |
| Skillbestandsformaat | Agent Skills-specificatie plus officiële MCP SDK | Quarantaine, rechten, evaluatie, promotie, audit en rollback | Geen eigen skillformaat ontwerpen; een formaat is nog geen veilige store of sandbox |
| Metrics en kosten | OpenTelemetry uit Pydantic AI en een bestaande backend zoals Grafana | Eva-specifieke productmetrics en privacyfilter | Geen eigen observabilityplatform bouwen |
| Dependencyupdates | Renovate in de ontwikkelomgeving | Exacte pins, compatibiliteitstest, menselijke/reviewer-go en rollback | Nooit automatisch naar productie promoveren |
| SBOM, CVE's, secrets en images | Trivy; eventueel Cosign/in-toto voor ondertekening en attestatie | Eva valideert de receipt en blokkeert de release | Geen eigen scanner of cryptografisch updateprotocol bouwen |

## Belangrijke versiegrenzen

Hergebruik betekent niet blind `latest` installeren.

- Eva pinnt nu `pydantic-ai-slim==2.32.1`. De bron op exact die tag bevat
  realtime voice, DBOS/Temporal-integratie en web-UI-adapters.
- De eerste voiceproef gebruikt niet stil diezelfde pin. `2.32.2` repareert
  coöperatieve realtime run-/sessie-annulering via `RunContext.cancel()`.
  Hoorbaar stoppen binnen 250 ms blijft een aparte proef van Eva's eigen
  playback/media-bridge. Die kleine upgrade wordt afzonderlijk gesloten; de
  nieuwere `2.34.0` wordt niet automatisch meegenomen.
- Het losse Pydantic AI Harness is jonger en gebruikt `0.x`-releases. De
  actuele `0.25.0` vereist `pydantic-ai-slim>=2.33.0` en past dus **niet** bij
  Eva's huidige pin. `0.24.0` accepteert `>=2.28.0` en is de eerste
  compatibele proefkandidaat. Er wordt niets stil geüpgraded.
- Eva gebruikt nu een lokale DBOS-patch `2.30.0+eva.1` die retry na een
  onduidelijke databasecommit begrenst. Dat is onderhoudsschuld. De patch wordt
  ofwel upstream overbodig gemaakt, of tot één smalle adapter begrensd; hij mag
  niet de reden worden om de hele workflowstack zelf te onderhouden.
- Playwright MCP, Browser Use, OpenClaw en iedere andere sidecar worden op
  release, commit, image-digest en licentie vastgezet voordat zij secrets of
  echte accounts zien.

De actuele eerste proefpins zijn: OpenAI Python `3.3.1`, Pydantic AI `2.32.2`,
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
waarvan 483 `test_*.py`-modules. Het kan niet alleen
de huidige adapter, maar ook toekomstige dashboard-, trace-, backup- en
beheeradapters voorkomen. Daarom gaat het door naar een echte pariteitsproef met
een least-privilege testtoken.

Het is nog geen productieonderdeel en heeft het echte huis niet benaderd. De
harde toelatingseis is minimaal 300 regels bestaande Home Assistant-productcode
verwijderen tegen hoogstens 180 regels dunne Eva-integratie, met dezelfde
state-, historie-, service-, automation-, crash- en readbackgevallen. De exacte
bron/imagebinding en herhaalbare smoke staan in
`experiments/ha-mcp-sidecar-v1/`. Een onafhankelijke reviewer herhaalde beide
checks en gaf `GO` voor de geïsoleerde pariteitsproef op checkpoint `3896f17`;
zie `reviews/ha-mcp-sidecar-candidate-3896f17.md`.

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
| Pydantic AI `2.32.1` huidige lock / `2.32.2` proefkandidaat | `PROEF` | MIT, stabiele V2-versiepolicy, compatibility-CI, meerdere recente bijdragers en openbare advisories | Echte Eva-route met IdentityPack, persoonsisolatie, usagebudget en uitgeschakelde/gesaneerde telemetry; alleen publieke API en geen tweede Agent-loop |
| Pydantic AI Harness | `PROEF` | MIT, officieel apart Pydantic-project, recente `0.x`-releases en meerdere auteurs | API stabiliseert nog; exacte compatibele pin, geen taak-/geheugenautoriteit en directe rollback |
| DBOS Transact | `PROEF` | MIT, gepubliceerde Pythonreleases en correctnessfixes rond herstel en duplicaten | Eva's private patch/imports zijn de blocker; eerst publieke API, schema-upgrade en downgrade op kopiedata |
| Officiële MCP Python SDK `2.1.1` | `NO-GO: smalle V1-client` | MIT, Tier-1 SDK, snelle releases en expliciet versiebeleid; exacte 48-package-lock is compatibel | Volledige SDK voegt tien packages en onnodig sessiegedrag toe; types-only gaf +87 productregels en verwijderde niets |
| Home Assistant MCP Server | `PROEF: basis` | Onderdeel van Home Assistant Core; officiële Streamable HTTP-/OAuth-route, tools en live contextsnapshot | Geschikt voor blootgestelde Assist-entiteiten en context; nog geen bewijs voor Eva's historie- en configuratiegevallen |
| `homeassistant-ai/ha-mcp` `v8.3.0` | `PROEF: uitgebreide HA-sidecar` | MIT, vier maintainers, recente onveranderlijke release, 88 tools, multi-arch images en 483 `test_*.py`-modules | Exacte image-smoke en onafhankelijke review zijn groen; echte least-privilege HA-pariteit, codeverwijdering, latency, upgrade en rollback zijn open |
| Microsoft Playwright MCP | `PROEF` | Apache-2.0, eigen release- en securityroute en onderhoud binnen Microsoft | Nog `0.x`; originfilters zijn geen sandbox. Pin ook browser/container en test SIGKILL, egress, downloads en orphanprocessen |
| Browser Use | `NO-GO V1` | MIT en een eigen securityroute | Tweede modelgedreven planner, extra calls en overlap; alleen heropenen als een benoemde taak Playwright MCP niet haalt |
| Hindsight API `0.9.1` | `GO: huidige L2` | MIT, exact artifact/digest en bestaande Eva-livebewijzen | PostgreSQL blijft canoniek; iedere upgrade blijft een aparte proef met export, privacy, latency en rollback |
| Hindsight Control Plane UI | `PROEF` | Onderdeel van dezelfde releasefamilie | Auth en tenant-/ACL-grenzen passen niet direct bij Eva; begin read-only achter Eva's API |
| LiveKit Agents | `PROEF: voicefallback` | Apache-2.0 framework, releases en bijdragen van meerdere auteurs | Alleen als Pydantic Realtime latency, reconnect of transport niet haalt; geen dubbele voicekern |
| OpenClaw Gateway/plugin | `PROEF` | MIT core, signed releases, zeer recente commits en veel bijdragers | Hoge churn, eerdere gateway-scopeadvisory en volledig vertrouwde plugins; exact één geïsoleerde plugin per proef |
| React-Admin | `PROEF: UI` | MIT, doorlopende releases en openbare issue-/releaseplanning | Alleen presentatie via Eva's API; CSP/XSS-tests, geen directe database of secrets |
| PyInstaller | `PROEF: launcher` | `GPL-2.0-or-later WITH Bootloader-exception`, veel releases en ondersteuning voor gangbare desktopplatforms | Geen cross-compiler of complete installer; per OS bouwen, signen, verwijderen en rollback testen |
| Agent Skills-specificatie | `PROEF: skillformaat` | Open specificatie/code met Apache-2.0 en een exact te pinnen commit | Geen store, sandbox of rechtenmodel; Eva's quarantaine en promotie blijven leidend |
| Renovate | `GO: voorstelbot` | AGPL-3.0, immutable releases en concrete security-/permissiondocumentatie | `automerge=false`, geen base-branch push of post-upgradecode; ieder voorstel door tests en review |
| Trivy | `PROEF: scannersignaal` | Apache-2.0, signed/checksummed artifacts en actieve advisoryafhandeling | Releases/actions zijn in 2026 gecompromitteerd; nooit `latest`, nooit de enige releasewaarheid |

## Wat dit concreet aan bestaand werk verandert

- De geteste Eva-ledger en readbackketen worden niet weggegooid. Zij beschermen
  precies het deel dat generieke frameworks niet voor Eva kunnen beslissen.
- Na de huidige actieketen komen er geen nieuwe handgeschreven workerloops bij.
  DBOS moet scheduling, wake-up en hervatting leveren.
- MCP blijft de normale skill- en providerdraad, maar de volledige officiële
  clientdependency komt niet in de smalle V1-route. De huidige begrensde client
  blijft zolang een dependency aantoonbaar geen code verwijdert.
- Home Assistant gaat niet per functie verder groeien in Eva. De volgende echte
  proef zet `homeassistant-ai/ha-mcp` als geïsoleerde sidecar naast de huidige
  adapter en promoveert alleen wanneer honderden eigen regels kunnen verdwijnen.
- Voice, browser en subagents beginnen met een kleine dependencyproef, niet met
  een nieuw platformontwerp.
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
| `adapters/connectors/home_assistant.py` | 763 regels | Officiële ingebouwde server voor basis plus `homeassistant-ai/ha-mcp v8.3.0` voor historie/configuratie/beheer | Least-privilege binding, één duurzame poging en onafhankelijke readback; promotie alleen bij minimaal 300 regels weg en maximaal 180 erbij |
| Zeven application-workerbestanden | meerdere losse loops | DBOS scheduling, queues en durable execution op dezelfde crashcases | Taakbetekenis, mandaat, idempotency, externe ambiguïteit en gebruikersstatus |
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
- [ ] `DF02b` — Pydantic AI `2.32.2` krijgt daarna alleen modelmapping of een
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
  (`86c7247`, `3896f17`); echt huis, codeverwijdering, latency, crash, upgrade
  en rollback staan open.
- [ ] `DF03` — DBOS versus de handgeschreven workflowdelen is op dezelfde
  crashcases vergeleken; dubbele scheduling krijgt een verwijderbesluit.
- [ ] `DF04` — compatibel Harness, Pydantic Realtime en Playwright MCP krijgen
  ieder één kleine, afgeschermde verticale proef met latency en kosten.
- [ ] `DF05` — Hindsight Control Plane, standaard observability en een
  dashboardframework leveren samen de beheerbasis zonder tweede waarheid.
- [ ] `DF06` — Renovatevoorstel, Trivy-scan, SBOM, upgrade, rollback en
  onafhankelijke review zijn groen op exact dezelfde kandidaatimage.

Tot deze gates groen zijn, is de keuze **dependency-first** vastgesteld maar
zijn de nieuwe packages nog geen goedgekeurde productdependencies.

## Primaire bronnen

- Pydantic AI exact `2.32.1`: <https://github.com/pydantic/pydantic-ai/tree/v2.32.1>
- Pydantic AI realtime-fix `2.32.2`: <https://github.com/pydantic/pydantic-ai/releases/tag/v2.32.2>
- Pydantic AI cancellation-fix: <https://github.com/pydantic/pydantic-ai/pull/7528>
- Pydantic AI-versiebeleid: <https://github.com/pydantic/pydantic-ai/blob/main/docs/version-policy.md>
- Pydantic AI telemetry-advisory: <https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-3gh4-cghq-f8v4>
- Pydantic AI Harness: <https://github.com/pydantic/pydantic-ai-harness>
- DBOS Python: <https://github.com/dbos-inc/dbos-transact-py>
- Officiële MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- MCP Python SDK-versiebeleid: <https://github.com/modelcontextprotocol/python-sdk/blob/main/VERSIONING.md>
- Home Assistant MCP Server: <https://www.home-assistant.io/integrations/mcp_server/>
- Home Assistant MCP-bron: <https://github.com/home-assistant/core/tree/dev/homeassistant/components/mcp_server>
- Home Assistant MCP-schema-issue: <https://github.com/home-assistant/core/issues/178032>
- HA-MCP communityproject: <https://github.com/homeassistant-ai/ha-mcp>
- HA-MCP exact `v8.3.0`: <https://github.com/homeassistant-ai/ha-mcp/releases/tag/v8.3.0>
- HA-MCP securitybeleid: <https://github.com/homeassistant-ai/ha-mcp/security/policy>
- Microsoft Playwright MCP: <https://github.com/microsoft/playwright-mcp>
- Microsoft Playwright MCP-securitybeleid: <https://github.com/microsoft/playwright-mcp/security/policy>
- Browser Use: <https://github.com/browser-use/browser-use>
- Hindsight exact `0.9.1`: <https://github.com/vectorize-io/hindsight/tree/v0.9.1>
- Hindsight securitybeleid: <https://github.com/vectorize-io/hindsight/security>
- LiveKit Agents: <https://github.com/livekit/agents>
- OpenClaw-kanalen: <https://docs.openclaw.ai/channels>
- OpenClaw scope-advisory: <https://github.com/openclaw/openclaw/security/advisories/GHSA-4f8g-77mw-3rxc>
- React-Admin: <https://github.com/marmelab/react-admin>
- PyInstaller: <https://github.com/pyinstaller/pyinstaller>
- Agent Skills-specificatie: <https://github.com/agentskills/agentskills/commit/69ef37e9424c0a7ea9dd2293b559e43ec8176379>
- Renovate: <https://github.com/renovatebot/renovate>
- Renovate security en permissies: <https://docs.renovatebot.com/security-and-permissions/>
- Trivy: <https://github.com/aquasecurity/trivy>
- Trivy supply-chainadvisory: <https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23>
