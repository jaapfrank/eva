# Onafhankelijke review — dependency-first strategie `7d5c93f`

Datum: 25 augustus 2026

Reviewer: Curie the 2nd, onafhankelijke verificatie- en releaselead

Beoordeeld checkpoint: `7d5c93f32a8349fbbbacaf1cb554c247b31a96d3`

## Conclusie

**GO.** De dependency-first strategie is begrijpelijk, intern consistent en
feitelijk voldoende begrensd. Zij maakt generieke infrastructuur vervangbaar,
terwijl Eva zelf autoriteit houdt over identiteit, privacy, mandaat, intent,
effectbewijs, readback en eindantwoord. De nieuwe projecten zijn kandidaten
voor een afgeschermde proef; dit checkpoint claimt niet dat zij al zijn
geïnstalleerd, goedgekeurd of in productie gebruikt.

## Scope

De review omvatte uitsluitend de acht paden van commit `7d5c93f`:

- `CONCURRENTIE_EN_HERGEBRUIK.md`;
- `DOCUMENTATIE_EN_ONDERHOUD.md`;
- `OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`;
- `PROJECTEN_EN_PLANNING.md`;
- `PROJECT_STATUS.md`;
- `design/architecture/ecosystem-bridge-v1.md`;
- `design/product/conversational-voice-v1.md`;
- `design/product/delegation-and-workboard-v1.md`.

Gecontroleerd zijn de primaire-bronclaims, de grens tussen Eva-eigendom en
hergebruik, de ramingen en optellingen, de nonclaims, de nieuwe interne en
externe documentlinks en de Markdown-tabellen. Er is geen implementatie- of
liveproef van de voorgestelde dependencies uitgevoerd.

## Primaire-broncontrole

- Pydantic AI `v2.32.1` documenteert op die exacte tag realtime voice,
  web-/UI-routes, MCP en duurzame uitvoering via onder meer DBOS en Temporal.
- Pydantic AI Harness `v0.24.0` declareert
  `pydantic-ai-slim>=2.28.0`. Release `v0.25.0` declareert
  `pydantic-ai-slim>=2.33.0` en past daardoor niet bij Eva's huidige exacte
  `2.32.1`-pin. De keuze van `0.24.0` als eerste compatibele proefkandidaat is
  dus correct begrensd; compatibiliteit in de praktijk moet nog worden getest.
- Harness `v0.24.0` documenteert afzonderlijke child-runs met eigen
  berichtcontext, planning, skills, browserbouwstenen en begrensbare tool-,
  context-, token-, request-, tijd- en callbudgetten.
- DBOS documenteert PostgreSQL-gebonden workflows en queues, hervatten na
  uitval en duurzaam slapen/wakker worden. De documenten besteden de
  betekenis van een externe actie niet uit: Eva's eigen ledger blijft nodig
  voor call-start, ambigu resultaat en onafhankelijke readback.
- Microsoft Playwright MCP is de officiële Microsoft/Playwright-MCP-server
  voor browserbediening. De documenten claimen terecht niet dat MCP op zichzelf
  een extern eindresultaat bewijst.
- Hindsight `v0.9.1` bevat een lokale Control Plane/UI naast API en clients.
  Het document vereist nog een exacte pin, offline materialisatie, scan en
  rollback voordat die UI productonderdeel wordt.
- OpenClaw documenteert WhatsApp als officieel kanaalplugin. De Eva-documenten
  eisen vóór gebruik nog een exacte pluginversie, licentiecontrole,
  accountbeleid en sidecarisolatie.
- Renovate documenteert dependency-updatevoorstellen via pull requests. Trivy
  documenteert scans van dependencies/SBOM, kwetsbaarheden, secrets,
  licenties en images. Beide blijven hulpmiddelen; zij krijgen geen zelfstandig
  releasebesluit.

Geraadpleegde primaire bronnen:

- <https://github.com/pydantic/pydantic-ai/tree/v2.32.1>
- <https://github.com/pydantic/pydantic-ai-harness/tree/v0.24.0>
- <https://github.com/pydantic/pydantic-ai-harness/blob/v0.25.0/pyproject.toml>
- <https://github.com/dbos-inc/dbos-transact-py>
- <https://github.com/microsoft/playwright-mcp>
- <https://github.com/vectorize-io/hindsight/tree/v0.9.1>
- <https://docs.openclaw.ai/channels>
- <https://github.com/renovatebot/renovate>
- <https://github.com/aquasecurity/trivy>

## Eigendomsgrens

De gekozen grens is logisch en eerlijk. Eva blijft eigenaar van:

- identiteit en de binding met persoon of groep;
- privacy, consent, mandaat, budget en stop;
- de bedoelde actie en de toegestane resource;
- poging, call-start, ambigu resultaat en effectbewijs;
- onafhankelijke readback en de beslissing of iets echt gelukt is;
- persoonsgebonden geheugenregels en het uiteindelijke antwoord.

Agentloop, audio, browserbediening, generieke scheduling, UI-componenten,
updatevoorstellen en scanners mogen vervangbare dependencies of sidecars zijn.
Geen van de beschreven providers wordt een tweede Eva-brein of een tweede
autoriteit voor taken, geheugen of externe waarheid.

## Ramingen

De projecttabel telt rekenkundig correct op:

| Deel | Minimum | Maximum |
|---|---:|---:|
| Bouwen en gerichte tests | 70 agenturen | 173 agenturen |
| Integratie en onafhankelijke review | 55 agenturen | 106 agenturen |
| Totaal | **125 agenturen** | **279 agenturen** |

De eerste drie projecten tellen samen op tot **15–31 agenturen**. De
verstreken banden van 0,5–2 werkdagen voor de eerste beheerde B-alpha, 8–14
kalenderdagen voor een veilige vervanger inclusief de verplichte zevendaagse
proef en 2–4 kalenderweken voor de volledige productbelofte zijn onder drie
parallelle bouwstromen plus onafhankelijke review onderling coherent. Zij zijn
expliciet als ramingen en niet als garanties beschreven. Externe
providergoedkeuring, accounts, apparaten en niet-versnelbare duurproeven staan
afzonderlijk vermeld.

## Nonclaims

- `pydantic-ai-slim==2.32.1` en de lokale DBOS-patch bestaan al in Eva's
  huidige dependency-authority; de nieuwe strategie maakt nog geen volledige
  Pydantic-agentroute live.
- Harness, Playwright MCP, Browser Use, React-Admin, Renovate, Trivy en LiveKit
  zijn in dit checkpoint niet als nieuwe productdependency geïnstalleerd of
  goedgekeurd.
- `DF01`–`DF06` blijven open. Iedere kandidaat moet nog worden gepind,
  geïsoleerd, gemeten, gescand, geüpgraded en teruggerold voordat promotie mag.
- De Hindsight Control Plane bestaat upstream, maar is nog niet als beheerde
  Eva-productroute aangesloten.
- Dependency-first bewijst geen voice, browserautonomie, subagents, Kanban,
  nieuw chatkanaal, updatepad of productrelease.
- De kortere planning is geen live bewijs en geen garantie dat een kandidaat
  de functionele, privacy-, latency- en herstelgrenzen haalt.

## Documentcontrole en bestaande hygiëne

De door `7d5c93f` toegevoegde interne verwijzingen bestaan, de gecontroleerde
externe links wijzen naar de bedoelde primaire bronnen, de Markdown-tabellen
hebben consistente kolomaantallen en `git diff --check` is groen.

Niet-blokkerend bestaand punt: zes receiptlinks in `PROJECT_STATUS.md` wijzen
naar bewijsbestanden die in de huidige werkboom aanwezig maar op dit checkpoint
niet gevolgd zijn. `7d5c93f` introduceert deze links niet. Voor een schone,
reproduceerbare release moeten de receipts worden gevolgd of moeten de links
naar een wel commitgebonden bewijsbron worden verplaatst.

## Eindoordeel

Commit `7d5c93f` mag als documentatie- en strategiecheckpoint worden
geïntegreerd. De GO geldt uitsluitend voor de dependency-first keuze,
bronjuistheid, planning en nonclaims. Iedere werkelijke dependencyselectie en
ieder gebruikersgedrag vereist nog de afzonderlijke `DF01`–`DF06`-proeven en
een nieuw onafhankelijk stop/go-besluit.
