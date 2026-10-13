# Community-dependencyaudit — beslissingen van 26 augustus 2026

## Kort oordeel

De koers is gewijzigd van “zelf ontwerpen tenzij” naar **“bestaand project
eerst werkelijk proberen”**. Dat levert één concrete hergebruikwinst op en
voorkomt drie verkeerde productkeuzes:

| Onderdeel | Besluit | Wat hierdoor niet zelf wordt gebouwd |
|---|---|---|
| Subagents | Pydantic AI `2.34.0` + Harness `0.24.0` is `GO` als geïsoleerde enginekandidaat | Spawn/modelrun, childcontext, request-/tokenlimiet, callbudget en coöperatieve async-time-out |
| Browser | Microsoft Playwright MCP `0.0.79` krijgt als enige de eerste productproef | Chromiumbesturing, toegankelijkheidsboom, screenshots, downloads en browserprofielmotor |
| Voice | Pydantic AI Realtime `2.34.0` krijgt de eerste verticale proef | Providerneutrale realtime-agentloop, audio-/transcriptevents, interruptie en usage-afhandeling |
| Workflow | Stock DBOS `2.31.0` is `NO-GO`; een kleine `2.31.0+eva.1`-rebase is de proef | Geen tweede scheduler/queueplatform; Eva houdt haar veiligheidsadapter |
| Home Assistant | HA-MCP `8.3.0` is sterk, maar een directe swap is nu `NO-GO` | Geen extra HA-features handmatig toevoegen; alleen een semantische pariteitsproef kan bestaande code vervangen |
| Geheugenbeheer | Eerst Hindsight Control Plane | Geen tweede herinneringendashboard bouwen |
| Dashboard/Kanban | React-Admin OSS plus `dnd-kit`; PostgreSQL blijft waarheid | Geen eigen adminframework, drag-and-dropengine of tweede taakdatabase |
| Updates/scans | Renovatevoorstellen, Trivy, bestaande SBOM/signing | Geen eigen dependencybot of vulnerabilitiescanner |

## Waarom niet ieder populair project wordt geïnstalleerd

Een dependency is alleen winst als zij dezelfde Eva-uitkomst haalt met minder
eigen code en beheerlast. Daarom zijn ook afwijzingen belangrijk:

- de volledige officiële MCP Python SDK voegde tien packages en te ruim
  sessiegedrag toe; `mcp-types` alleen gaf netto 87 regels groei;
- een directe HA-MCP-transportswap verwijdert naar schatting slechts 174–192
  regels, onder de promotiegrens van 300;
- Harness Playwright draait in Eva's Pythonproces en past niet bij duurzame
  replay; de browser hoort als Playwright MCP-sidecar buiten de kern;
- Browser Use zou een tweede modelplanner en extra kostenroute toevoegen;
- stock DBOS `2.31.0` kan na onduidelijke commitbevestiging nog gedrag herhalen
  dat Eva exact één keer wil uitvoeren.

Dit zijn geen principiële bezwaren tegen open source. Het zijn gemeten redenen
om de kleinere of beter geïsoleerde communityroute te kiezen.

## Wat Eva altijd zelf houdt

Eva blijft eigenaar van identiteit, persoon/groep, privacy, toestemming,
mandaat, doel, kostenplafond, duurzame taakstatus, ambigu resultaat,
onafhankelijke eindcontrole en het antwoord aan de gebruiker. Een community-
project mag audio, browser, child-run, wachtrij of UI leveren, maar wordt geen
tweede Eva met eigen geheugen of waarheid.

## Bewijs en nonclaims

- Harness-kandidaat: `00482a3`–`55cf7cc`, exacte 42-package-lock, 22 tests en
  onafhankelijke `GO`; zie `harness-subagent-engine-55cf7cc.md`.
- HA-MCP-contract: `7c9f34e` en onafhankelijke sluiting `c03942d`; geen
  productiepromotie.
- DBOS: bronaudit van exact upstream `2.31.0`/commit
  `a51254c6c5ab2f1f217444778d306d4c967b1ba8`; rebaseproef nog niet gebouwd.
- Voice en Playwright MCP zijn bron-/releasebeslissingen, nog geen werkende
  Eva-routes.
- A, B, echte accounts, secrets en het echte huis zijn door deze audit niet
  gewijzigd.
