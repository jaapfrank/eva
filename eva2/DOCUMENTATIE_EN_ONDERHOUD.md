# Eva 2 — documentatie- en onderhoudscontract

Bijgewerkt: 25 augustus 2026.

## Doel

Iedere productmogelijkheid, bewuste non-goal, open blokkade en belangrijke
broncomponent moet vindbaar blijven zonder de hele repository te lezen. Een
wijziging is daarom niet administratief afgerond wanneer alleen de code werkt:
de capability, bestandsrol, actuele status, bewijsgrens en gemeten
optimalisatiestatus moeten op dezelfde Git-baseline terug te vinden zijn.

Dit contract voorkomt ook documentatie-inflatie. Niet ieder bestand krijgt een
los handgeschreven document. Ieder repositorypad komt in een mechanische
inventaris; semantisch verwante bestanden horen daarnaast bij precies één
hoofdcomponent met doel, eigenaar en lifecycle. Alleen publieke entrypoints,
effectvolle adapters, migrations, deployment en bijzondere trustgrenzen hebben
ook een eigen README, moduletekst of ontwerpbesluit nodig.

## Waar welke waarheid staat

| Vraag | Primaire bron | Betekenis |
|---|---|---|
| Wat kan Eva nu, wat is nodig voor de overgang en wat is het einddoel? | `EVA2_MOGELIJKHEDEN_EN_GATEN.md` | Leesbaar overzicht in drie duidelijk gescheiden momenten |
| Welke hoofdprojecten zijn af, wat resteert en hoe lang duurt dat? | `PROJECTEN_EN_PLANNING.md` | Primair projectbord met gebruikersuitkomst, status, raming, praktijktijd en eindproef |
| Waar werken we technisch nu aan? | `PROJECT_STATUS.md` | Korte actuele werkstatus en laatste checkpoint |
| Welke kleine taak is nu actief en wat komt exact daarna? | `WERKBORD.md` plus `design/product/execution-workboard-v1.json` | Menselijk bord en machineleesbare vaste volgorde met één actieve implementatietaak, eigenaar, bestanden, tests, bewijs en definitie van klaar |
| Hoe blijft het team van begin tot eind betrokken? | `TEAM_WERKWIJZE.md` | Vaste projectrollen, vroege verificatie, gescheiden werkbomen, integratie en acceptatie |
| Waarom bouwen we iets zelf in plaats van een dependency te gebruiken? | `OPEN_SOURCE_EN_DEPENDENCYKEUZES.md` | Dependency-first selectie, exacte pins, onderhoud/licentie, proef, update, rollback en expliciete afwijzingsreden |
| Hoe hangt de runtime technisch samen? | `design/architecture/living-core-v1.md` | Componenten, primaire bestanden, verantwoordelijkheid, status en bewijsgrenzen |
| Wie staat vandaag voor en waarom zou Eva straks beter zijn? | `CONCURRENTIE_EN_HERGEBRUIK.md`, `OPENBOT_VERGELIJKING.md`, `OUDE_EVA_VS_EVA2.md` | Vergelijkingen in gewone taal; huidige winnaar en toekomstdoel blijven gescheiden |
| Waar staan alle precieze gatecodes en technische claims? | `TECHNISCHE_PRODUCTGATES.md`, `TECHNISCHE_MOGELIJKHEDEN_EN_GATEN.md`, `design/product/*.md` | Technisch bewijsbord met acceptatiecriteria, checkpoints en nonclaims |
| Welk pad bestaat en welke technische rol heeft het? | `../docs/eva-repository-inventory.generated.{json,md}` | Mechanische repository-inventaris; aanwezigheid of rol is geen bewijs dat gedrag af is |
| Welke importgrens geldt? | `design/architecture/package-boundaries-v1.json` | Machineleesbare package-eigenaarschap en toegestane imports |
| Waarom mag een checkpoint groen heten? | `reviews/` plus `evidence/` | Onafhankelijke review, tests en waar vereist live receipts |
| Is iets snel, klein of geoptimaliseerd? | `design/product/performance-and-complexity-v1.md` plus commitgebonden meting | Alleen een genoemde metric en baseline rechtvaardigt zo'n claim |
| Is een agentscore extern vergelijkbaar? | `design/product/comparable-agent-benchmarks-v1.md` | Alleen officiële benchmarkmetrics op exact dezelfde release/split/scorer; native en gecontroleerd blijven aparte scoreborden |
| Wat betekent vergaande autonomie? | `design/product/owner-autonomy-mode-v1.md` | Eigenaarmodus is een ruim maar begrensd mandaat, niet Hermes' jailbreakskill of een tweede orchestrator |
| Hoe werken korte context, Hindsight, Mnemosyne en Honcho samen? | `design/product/tiered-memory-and-person-model-v1.md` plus `../benchmark/memory-augmentation-gate-v1.json` | Hindsight blijft L2; optionele L1-/persoonsmodellen tellen alleen bij hashgebonden winst zonder authority-, privacy- of latencyregressie |

## Statuswoorden

Deze woorden mogen niet door elkaar worden gebruikt:

| Status | Vereiste |
|---|---|
| `planned` | Doel en acceptatiegrens beschreven; nog geen implementatieclaim |
| `implemented` | Code bestaat en importeert/compileert; nog geen test- of liveclaim |
| `tested` | Gerichte positieve en fail-closed regressies op exact Git-checkpoint |
| `reviewed` | Onafhankelijke reviewer controleerde scope, gedrag en nonclaims |
| `live_proven` | Werkelijke bedoelde provider/hardware/netwerkroute heeft een receipt |
| `user_accepted` | De eigenaar accepteerde het waarneembare gedrag expliciet |
| `optimized_for_<metric>` | Voor/na-meting op dezelfde taak bewijst winst zonder budgetregressie |
| `complete` | Alle voor die gate vereiste voorgaande statussen zijn groen; open vervolgwerk staat afzonderlijk vermeld |

`Geoptimaliseerd` zonder metric is ongeldig. Een onderdeel kan bijvoorbeeld
`optimized_for_first_token_latency` zijn en tegelijk nog onbewezen op geheugen,
kosten of residentstabiliteit. Onbekende metingen worden `unmeasured`, nooit
stil `goed`.

## Verplichte componentregistratie

Voor iedere productcomponent moet uiteindelijk één machineleesbare entry bestaan
met minimaal:

- stabiele component-ID en korte Nederlandse productnaam;
- doel en waarom de component bestaat;
- eigenaarspakket en alle productie-, deployment-, migration- en testpaden;
- inkomende en uitgaande contracten en verboden verantwoordelijkheden;
- capability- en gate-ID's die de component bedient;
- lifecycle-status uit de tabel hierboven;
- laatste gecontroleerde Git-commit, test- en reviewreferenties;
- live receipt wanneer de claim extern of fysiek gedrag betreft;
- relevante latency-, token-, geheugen-, codeomvang- en kostenmetingen, anders
  expliciet `unmeasured`;
- open blockers, vervanging/rollback en datum van laatste controle.

Paden mogen niet ongemerkt tussen componenten zweven. Een nieuw productiepad
moet in dezelfde kleine commit aan een component worden gekoppeld. Verwijdering
of vervanging laat een disposition achter, zodat oude documentatie niet naar
verdwenen autoriteit blijft wijzen.

## Huidige eerlijke grens

- De Living-Core-matrix documenteert al veel primaire bestanden, doelen en
  bewijsstatussen; de productdocumenten bewaren mogelijkheden en open gates.
- De gegenereerde repository-inventaris classificeert ieder zichtbaar pad, maar
  de huidige brede werkboom bevat nog policyfouten en ongetrackte recovery- en
  evidencepaden. Die inventaris is daarom diagnostiek, geen schone releasegate.
- Packagegrenzen en performancebudgetten zijn machineleesbaar of meetbaar.
- Nieuwe verticale slices registreren nu expliciet bestandsrol, bestaansreden,
  bewijsstatus, Git-checkpoints, nonclaims en meetstatus. MC03c3a is hiervan een
  concreet voorbeeld in `design/architecture/living-core-v1.md` en
  `reviews/action-chain-effect-readback-preparation-mc03c3a.md`;
  runtime-optimalisatie
  blijft daar eerlijk `unmeasured` terwijl codeomvang wel exact is gecontroleerd.
- Externe agentscores hebben nu een afzonderlijk publicatiecontract. AgentBench,
  GAIA en OSWorld-resultaten tellen alleen met upstream scorer, vastgezette
  release/split/configuratie en heropenbare trajecten; LAB-36 blijft een eigen
  productsuite en wordt niet tot een openbare leaderboardscore omgedoopt.
- De semantische geheugenprovider is nu een expliciete, kleine runtimepoort en
  Hindsight blijft de L2. De machineleesbare augmentatiegate voorkomt dat een
  Mnemosyne-L1 of Honcho-persoonsmodel Hindsight/SQL vervangt of zonder
  privacy-, correctie-, latency-, token-, beheer-, upgrade- en rollbackbewijs
  in Eva's antwoordroute komt. Ook een geheugendashboard blijft een projectie:
  het mag geen providerkeuze afdwingen of Eva's correctie- en
  verwijderautoriteit omzeilen.
- `design/architecture/component-catalog-v1.json` koppelt nu ieder door de
  actuele inventaris gevolgd productie-, deployment-, migration- en testpad
  aan exact één semantische component. Per component staan doel, noodzaak,
  eigenaar, verboden scope, upstreamkeuze, vervangingsstatus en verificatie.
  `tools/check_component_catalog.py` weigert ontbrekend, dubbel of dood
  eigenaarschap. Dit is lokaal getest, maar nog niet commitgebonden of
  onafhankelijk gereviewd. Optimalisatiestatus is bovendien nog niet voor
  iedere component gemeten.
- Vier historische bewijs- en preflighttools staan daarnaast gesloten in
  `design/architecture/evidence-tool-registry-v1.json`. Voor ieder bestand zijn
  eigenaar, doel, side effects, risicoklasse, uitvoerbeleid, bronhash en tests
  verplicht. De LAB-36-checker en B-preflight zijn alleen-lezen. De
  betaalproef mag uitsluitend een wegwerpdatabase en lokale contractprovider
  gebruiken. `migrate_hindsight_provenance.py` blijft wegens zijn externe
  geheugenwrites expliciet `quarantined`; de cataloguschecker weigert die tool
  als een gewone beheerde of automatisch uitvoerbare handeling te registreren.

## Kleine documentatiegates

- [x] DG01 — Eén gesloten `EvaComponentCatalog/v1`-contract en catalogus met
  stabiele component-ID's, doel, noodzaak, eigenaar, paden, verboden scope,
  upstream- en vervangingsstatus. De actuele kandidaat is lokaal getest; haar
  checkpoint en onafhankelijke review vallen onder DG04.
- [x] DG02 — De checker bewijst voor de actuele inventaris dat ieder gevolgd
  productie-, deployment-, migration- en testpad precies één componentowner
  heeft en weigert ontbrekende, overlappende of dode ownerpatronen.
- [ ] DG03 — Iedere capability/non-goal en iedere open checkbox heeft één unieke
  gate-ID, primaire productspecificatie, status en geldige bewijssoort.
- [ ] DG04 — Iedere afgeronde gate bindt exact Git-checkpoint, gerichte tests,
  onafhankelijke review en waar nodig live receipt; verouderde links maken de
  documentatiecheck rood.
- [ ] DG05 — Optimalisatievelden gebruiken alleen PC-/LG-/V-metingen met
  baseline, p50/p95 of passend exact budget; `optimized` zonder metric faalt.
- [ ] DG06 — De eindimage bevat een gesaneerde, alleen-lezen documentatiesnapshot
  en dashboardprojectie van versie, capabilities, componenten, gates, receipts,
  blockers en meetstatus.

Tot DG01-DG06 groen zijn, is de documentatie uitgebreid maar niet volledig
machine-afgedwongen. Dat onderscheid blijft zichtbaar; een lange lijst is op
zichzelf geen bewijs dat hij actueel of compleet is.
