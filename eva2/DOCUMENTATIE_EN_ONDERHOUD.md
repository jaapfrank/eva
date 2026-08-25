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
| Wat kan Eva nu echt en wat nog niet? | `EVA2_MOGELIJKHEDEN_EN_GATEN.md` | Leesbare capability- en gaplijst; alleen bewezen gedrag staat onder “kan” |
| Waar werken we nu aan? | `PROJECT_STATUS.md` | Korte actuele werkstatus en laatste checkpoint |
| Hoe hangt de runtime technisch samen? | `design/architecture/living-core-v1.md` | Componenten, primaire bestanden, verantwoordelijkheid, status en bewijsgrenzen |
| Waarom bestaat een productgate? | `design/product/*.md`, `OPENBOT_VERGELIJKING.md`, `OUDE_EVA_VS_EVA2.md` | Productbelofte, huidige stand, acceptatiecriteria en nonclaims |
| Hoe vergelijken en hergebruiken we? | `CONCURRENTIE_EN_HERGEBRUIK.md` | Capabilitymatrix, concurrentie, hergebruik en volledige afvinkvolgorde |
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
  bewijsstatus, Git-checkpoints, nonclaims en meetstatus. MC03c2 is hiervan een
  concreet voorbeeld in `design/architecture/living-core-v1.md` en
  `reviews/action-chain-effect-execution-mc03c2.md`; runtime-optimalisatie
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
- Er bestaat nog geen volledig, commitgebonden semantisch componentregister dat
  ieder productie-, deployment-, migration- en testpad dekt. Ook is
  optimalisatiestatus nog niet voor iedere component gemeten.

## Kleine documentatiegates

- [ ] DG01 — Eén gesloten `EvaComponentCatalog/v1`-schema en catalogus met
  stabiele component-ID's, doel, eigenaar, paden, contracten en verboden scope.
- [ ] DG02 — Checker bewijst dat ieder gevolgd productie-, deployment- en
  migrationpad precies één componentowner heeft; test- en bewijsbestanden mogen
  expliciet meerdere componenten verifiëren maar hebben één primaire rol.
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
