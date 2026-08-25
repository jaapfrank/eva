# Eva Performance and Complexity v1

Bijgewerkt: 25 augustus 2026.

## Doel

Eva moet als één samenhangend product snel en reactief voelen zonder uit te
groeien tot een verzameling frameworks, tweede orchestrators en honderdduizenden
regels eigen code. Lang werk mag tijd kosten; erkennen, eerste inhoud en
voortgang mogen niet levenloos stilvallen.

## Commitgebonden baseline en actuele observatie

PC00 bindt commit `35e2c0d…` aan 133 productbestanden, 51.928 fysieke regels en
2.018.005 bytes onder `src/eva`. De gesorteerde pad/byte/regel/contenthash-
manifesthash is `58627232…`; teldefinitie en gesloten uitzonderingen staan in
`evidence/product/performance-complexity-pc00-2026-08-25.json`.

Ter vergelijking, een niet-bindende observatie van de dirty werkboom tijdens
het ontwerp telde zonder de gegenereerde catalogus 53.428 regels in 133
bestanden. Inclusief die negen gegenereerde catalogusregels waren dat 134
bestanden en 53.437 regels. Dirty observaties zijn nooit een releasebaseline;
iedere bronwijziging telt pas formeel mee op haar eigen Git-checkpoint.

De productstack is nu Python 3.12 met negen directe runtime-dependencies in
`pyproject.toml`. Er is geen Rust-crate. Een nieuwe taal of framework telt dus
als kostenpost die een gemeten productuitkomst moet rechtvaardigen.

## Omvang- en afhankelijkheidsbudget

- De V1-release faalt boven **75.000 handgeschreven productregels** samen voor
  Python, TypeScript en Rust. `physical LOC` telt iedere blanke, comment- en
  coderegel als één record, ook zonder laatste newline. PC01 bevat een gesloten
  manifest van productroots en exacte generated/vendor-uitzonderingen. Een
  nieuwe bronroot of uitzondering vereist eerst een gereviewde manifestrevision.
  Tests, fixtures, migraties, generated en vendor tellen daarnaast afzonderlijk
  in bestanden/bytes en mogen deze grens niet verbergen.
- De Python Living Core blijft bij voorkeur onder 60.000 regels. Browser- en
  media-dataplanes leven in afzonderlijke processen en mogen geen tweede
  identiteit, planner, geheugen- of policylaag meebrengen.
- Nieuwe handgeschreven bestanden blijven maximaal 800 regels. Bestaande
  grotere bestanden mogen niet netto groeien; een wijziging eraan bevat een
  extractie of een expliciet, tijdelijk budgetrecord.
- Een kleine verticale slice voegt normaal maximaal 600 productregels toe.
  Grotere slices worden gesplitst of motiveren aantoonbaar waarom hergebruik
  meer complexiteit of risico oplevert.
- De Living Core houdt maximaal twaalf directe runtime-dependencies. Zware
  browser-, WebRTC-, STT- en TTS-dependencies horen in vervangbare sidecars en
  worden afzonderlijk op imagebytes, CVE's, startup en resident geheugen
  gemeten.
- Iedere capability heeft één eigenaar en één contract. Geen provider mag een
  tweede plan-act-loop, persona, Hindsight-bank of antwoordroute toevoegen.
- De bevroren core-image mag maximaal 15% groter zijn dan de nog te verzegelen
  pre-voice/pre-browser OCI-baseline. PC01 bindt daarvoor broncommit,
  `linux/amd64`, OCI-manifestdigest, configdigest en de som van de gecomprimeerde
  layerdescriptorbytes. Zonder die receipt bestaat geen percentageclaim.
  Iedere sidecar krijgt dezelfde eigen meetmethode en bytebudget; samenvoegen
  om de meting te omzeilen is verboden.

## Reactiviteitsbudget

Deze waarden zijn doelen tot de bevroren kandidaat ze echt bewijst.

| Gebruikersmoment | V1-doel p95 |
| --- | ---: |
| Ingress geaccepteerd → typing/hoorbare acknowledgement aangevraagd | 250 ms intern |
| Einde korte tekstvraag → eerste betekenisvolle zichtbare inhoud | 2,0 s |
| Einde korte tekstvraag zonder tools → compleet antwoord | 5,0 s |
| Tooltaak aangenomen → eerste concrete status | 500 ms intern |
| Barge-in → audio stil | 250 ms |

Een extern Telegram- of audioproviderreceipt wordt apart gemeten; externe
netwerktijd mag de interne meting niet verbergen en ook niet ten onrechte aan
Eva worden toegeschreven. Bij werk langer dan drie seconden geeft Eva één korte,
concrete voortgangsstatus en daarna alleen bij echte toestandverandering of
maximaal eens per vijf seconden.

De hot path bevat vóór de eerste inhoud geen reflectie, retain, evaluatie,
self-improvement, package-scan of seriële providerinventaris. Noodzakelijke
recall en onafhankelijke reads lopen parallel waar hun contracts dat toelaten.
Er is maximaal één antwoordgeneratie-eigenaar. Streaming mag de waarheid niet
vooruitlopen: een toolresultaat wordt pas genoemd na receipt/readback.

## Meetregel

De gate gebruikt monotonic timestamps aan ingress, admission, recall, model
first-token, first-visible/first-playout, final, toolprepare, provider en
readback. Minimaal 200 gewone tekstbeurten en de V07-voicesamples draaien op de
bevroren image. Time-outs, reconnects en fouten blijven in de noemer;
onvoldoende samples is rood. P50, p95, maximum, resident RSS, cold/warm startup,
imagebytes, dependencyaantal en handgeschreven LOC komen in één receipt.

Het corpusmanifest bindt vóór de proef vraagbytes/categorieën, route, model en
reasoningstand, IdentityPack, Hindsight/contextbudget, toolfixtures, device,
netwerkklasse en cold/warm-verdeling. `Eerste betekenisvolle inhoud` is het
eerste user-visible antwoordchunk met een compleet lexicaal token dat niet in
de gesloten typing/status-placeholderlijst staat. Een timeout krijgt de
deadline als latency én telt als failure; hij verdwijnt nooit uit de reeks.

Voor PC06 mag de mediaan van de dagelijkse p95 in dag 5-7 tegenover dag 1-3
maximaal 20% stijgen, met daarnaast maximaal 250 ms absolute stijging voor
acknowledgement/first-content en 500 ms voor final. Warm resident RSS groeit
maximaal 2 MiB per dag en maximaal 15% boven de dag-1-baseline. De vaste
probe-input groeit na dag 1 maximaal 5% in inputtokens, conversational queue-age
blijft onder 60 seconden, verlopen werk eindigt begrensd, en dubbel antwoord of
effect blijft exact nul.

## Kleine opleverslices

- [x] PC00 — Productdoel, exacte Git-baseline, manifesthash en harde
  V1-bovengrens vastgelegd; dit is nog geen image-/latency-/releaseacceptatie.
- [ ] PC01 — Commitgebonden teller en fail-closed CI-gate voor LOC, bestanden,
  directe dependencies en imagebytes; generated/vendor-uitzonderingen staan in
  een gesloten allowlist.
- [ ] PC02 — De grootste hot-pathbestanden begrenzen: niet verder laten groeien
  en bij aanraking gericht per eigenaar/contract splitsen, zonder massarewrite.
- [ ] PC03 — Teksthot-path streamen en instrumenteren; cold recall, retain,
  reflectie en evaluatie aantoonbaar buiten tijd tot eerste inhoud houden.
- [ ] PC04 — Browser- en voice-sidecars afzonderlijk budgetteren op code,
  dependencies, image, startup, RSS en IPC-latency.
- [ ] PC05 — Op de bevroren image minimaal 200 tekstbeurten plus V07 draaien;
  alle p95-, foutnoemer- en omvanggrenzen zijn groen.
- [ ] PC06 — De zevendaagse residentrun toont geen latencydegradatie,
  geheugenlek, queuegroei of steeds tragere context; eigenaar accepteert de
  snelheid en natuurlijkheid expliciet.

PC01-PC04 sluiten vóór freeze. PC05 en PC06 zijn release- en praktijkbewijs.
