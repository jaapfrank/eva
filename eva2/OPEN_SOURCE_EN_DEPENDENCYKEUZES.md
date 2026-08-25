# Eva 2 — eerst hergebruiken, pas daarna zelf bouwen

Bijgewerkt: 25 augustus 2026.

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
| Agentloop, tools en gestructureerde antwoorden | Pydantic AI | Identiteit, persoonscontext, grants en antwoordbeleid | Bestaande dependency werkelijk gebruiken; eigen modelboilerplate daarna verkleinen |
| Duurzame workflows en wachtrijen | DBOS via de officiële Pydantic AI-koppeling | Eva-ledger voor intent, call-start, ambigu resultaat en readback | DBOS laat starten, wachten en hervatten; geen tweede eigen queue-engine uitbreiden |
| Korte subagents | Pydantic AI Harness `0.24.0` als eerste compatibele kandidaat | Exacte taak-, context-, tool-, tijd- en token-grant plus duurzame handoff | Niet zelf spawnen, budgetteren en foutafhandelen wanneer Harness dit al doet |
| Voice | Pydantic AI Realtime als eerste route | Persoonsbinding, privacy, barge-in naar Eva-stop, memory en toolreadback | Geen eigen WebRTC-, STT-, TTS- of jitterbufferstack bouwen |
| Browser | Microsoft Playwright MCP als eerste eenvoudige provider | URL-/account-/bestandsgrant, live stop, artifacts en eindcontrole | Geen Chromium-, profiel-, screenshot- of downloadengine bouwen |
| Open-ended webwerk | Browser Use pas als tweede kandidaat | Zelfde Eva-grenzen; expliciet model-, stap- en kostenbudget | Alleen inzetten wanneer Playwright-MCP de taak aantoonbaar niet goed genoeg doet |
| Extra chatkanalen | Gepinde OpenClaw Gateway/plugin of officiële provider-SDK | Account→persoon, groepsconsent, deliveryledger en geheugenisolatie | WhatsApp niet zelf vanaf de draad bouwen |
| Geheugenbeheer | Hindsight `0.9.1` plus de bestaande lokale Control Plane UI | Eva's persoon-/groep-/projectbinding, consent en correcties | Geen tweede geheugenproduct installeren alleen voor een dashboard |
| Chat- en beheerinterface | Pydantic web/AG-UI, Hindsight Control Plane en bestaande UI-componenten | Eén dun Eva-scherm voor taken, status, mandaten en bewijs | Geen compleet dashboardframework namaken |
| Kanbanpresentatie | Een onderhouden datagrid/boardcomponent; eerste kandidaat React-Admin | PostgreSQL blijft taakautoriteit; iedere wijziging via Eva's API | Geen tweede taakdatabase of Hermes-kloon |
| Metrics en kosten | OpenTelemetry uit Pydantic AI en een bestaande backend zoals Grafana | Eva-specifieke productmetrics en privacyfilter | Geen eigen observabilityplatform bouwen |
| Dependencyupdates | Renovate in de ontwikkelomgeving | Exacte pins, compatibiliteitstest, menselijke/reviewer-go en rollback | Nooit automatisch naar productie promoveren |
| SBOM, CVE's, secrets en images | Trivy; eventueel Cosign/in-toto voor ondertekening en attestatie | Eva valideert de receipt en blokkeert de release | Geen eigen scanner of cryptografisch updateprotocol bouwen |

## Belangrijke versiegrenzen

Hergebruik betekent niet blind `latest` installeren.

- Eva pinnt nu `pydantic-ai-slim==2.32.1`. De bron op exact die tag bevat
  realtime voice, DBOS/Temporal-integratie en web-UI-adapters.
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

Een afwijzing krijgt een concrete mislukte proef. “Wij kunnen het zelf netter”
is geen geldige reden.

## Onderhouden projecten die nu serieus meetellen

Dit is een momentopname van 25 augustus 2026. Zij geeft kandidaten, geen
automatische productgoedkeuring.

| Project | Waarom serieus | Voorbehoud |
|---|---|---|
| Pydantic AI | MIT, grote actieve community, exacte `2.32.1`-bron en officiële voice-, UI-, MCP- en durable-executionroutes | Eva gebruikt de dependency nu nog nauwelijks; eerst een echte verticale proef |
| Pydantic AI Harness | Officieel Pydantic-project met subagents, planning, browser- en skillbouwstenen | Nog `0.x`; exact pinnen en achter Eva's eigen contract houden |
| DBOS Transact | MIT, actieve releases, PostgreSQL-workflows en queues zonder extra broker | Lokale Eva-patch en private DBOS-imports eerst wegwerken of isoleren |
| Microsoft Playwright MCP | Apache-2.0, Microsoft/Playwright-community, echte browserbediening via MCP | MCP bewijst niet vanzelf dat een aankoop of formulier werkelijk goed eindigde |
| Browser Use | MIT, zeer actieve grote community en zelfherstellende browseragent | Meer modelcalls, sneller veranderend oppervlak en risico op een tweede agentbrein |
| Hindsight | MIT, actieve community, API/SDK/CLI én lokale Control Plane UI op de al gekozen `0.9.1`-tag | Eva moet personen, groepen, consent en verwijdering blijven afdwingen |
| LiveKit Agents | Apache-2.0 en een actieve realtime voice-community | Alleen fallback wanneer Pydantic Realtime de gemeten route niet haalt; anders dubbel |
| OpenClaw | Zeer actieve channel-/gatewaycommunity en bestaande WhatsApp-plugin | Exacte pluginlicentie, versie, accountbeleid en sidecarisolatie per kanaal controleren |
| React-Admin | MIT, volwassen dashboardframework boven REST/GraphQL | Alleen presentatielaag; geen directe toegang tot Eva's tabellen of secrets |
| Renovate | Zeer actief dependency-updateproject | Alleen voorstellen maken; nooit zelf een Eva-release goedkeuren |
| Trivy | Apache-2.0, volwassen scanner voor images, dependencies, secrets, licenties en SBOM | Scannerdata kan fout-positieven bevatten; releasebeleid bepaalt de blokkade |

## Wat dit concreet aan bestaand werk verandert

- De geteste Eva-ledger en readbackketen worden niet weggegooid. Zij beschermen
  precies het deel dat generieke frameworks niet voor Eva kunnen beslissen.
- Na de huidige actieketen komen er geen nieuwe handgeschreven workerloops bij.
  DBOS moet scheduling, wake-up en hervatting leveren.
- MCP wordt de normale skill- en providerdraad. Eva's eigen connector-RPC blijft
  alleen bestaan waar de strengere externe-I/O-ambiguïteit aantoonbaar nodig is.
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

## Kleine dependency-first gates

- [ ] `DF01` — iedere hoofdcomponent heeft `reuse`, `thin Eva layer` of
  `Eva-owned` plus kandidaat, licentie, pin en onderhoudsbewijs.
- [ ] `DF02` — Pydantic AI `2.32.1` draait één echte connectorneutrale
  gespreks-/toolbeurt met Eva's bestaande identiteit en usage-receipt.
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
- Pydantic AI Harness: <https://github.com/pydantic/pydantic-ai-harness>
- DBOS Python: <https://github.com/dbos-inc/dbos-transact-py>
- Microsoft Playwright MCP: <https://github.com/microsoft/playwright-mcp>
- Browser Use: <https://github.com/browser-use/browser-use>
- Hindsight exact `0.9.1`: <https://github.com/vectorize-io/hindsight/tree/v0.9.1>
- LiveKit Agents: <https://github.com/livekit/agents>
- OpenClaw-kanalen: <https://docs.openclaw.ai/channels>
- React-Admin: <https://github.com/marmelab/react-admin>
- Renovate: <https://github.com/renovatebot/renovate>
- Trivy: <https://github.com/aquasecurity/trivy>
