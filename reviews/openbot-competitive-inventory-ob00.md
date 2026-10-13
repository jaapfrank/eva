# Onafhankelijke review — OpenBot-concurrentie-inventaris OB00

Datum: 25 augustus 2026.

## Afbakening

- Alleen-lezen product- en architectuurvergelijking met
  `CopilotKit/OpenBot`; niet met de gelijknamige robot- of agentharnessprojecten.
- OpenBot-release `v0.0.4`, releasecommit
  `6826e11afd52f03c30af2d873203792acad95f63`, en documentatiecommit
  `d293f2331bd5ff9ba4ad17af6ac94570a157d26d` zijn afzonderlijk vastgezet.
- De OpenBot-kolom berust op officiële documentatie en broninspectie. OpenBot
  is niet door ons uitgevoerd en is niet aan de bestaande vier-frameworkscore
  toegevoegd.
- Geen runtime-, provider-, credential-, browser-, A- of B-wijziging.

## Gecontroleerde primaire bronnen

- README, quickstart en alphastatus:
  <https://github.com/CopilotKit/OpenBot/tree/d293f2331bd5ff9ba4ad17af6ac94570a157d26d>
- Architectuur, action gateway, policy en audit:
  <https://github.com/CopilotKit/OpenBot/blob/d293f2331bd5ff9ba4ad17af6ac94570a157d26d/docs/architecture.md>
- Coworkers, AG-UI en human takeover:
  <https://github.com/CopilotKit/OpenBot/blob/d293f2331bd5ff9ba4ad17af6ac94570a157d26d/docs/coworkers.md>
- Openbare release `v0.0.4`:
  <https://github.com/CopilotKit/OpenBot/releases/tag/v0.0.4>
- Exacte releasecommit:
  <https://github.com/CopilotKit/OpenBot/commit/6826e11afd52f03c30af2d873203792acad95f63>

## Bevindingen

- OpenBot documenteert per Bot een eigen container, Chromiumprofiel,
  workspace en shell, plus live meekijken, hulp, menselijke overname, skills,
  MCP, credentials, policy en audit. gVisor is een optionele isolatielaag.
- Het eigenlijke agentbrein wordt via AG-UI aangesloten. Een eventuele
  OpenBot-sidecar mag daarom alleen Eva's browser-/computerprovider zijn; hij
  wordt geen tweede eigenaar van identiteit, geheugen, mandaat, policy,
  threads, orchestratie of eindantwoord.
- De gedocumenteerde meegeleverde policy staat standaard breed open met
  `allow: ["true"]`, en een verse ontwikkelconfiguratie gebruikt
  `OPENBOT_SINGLE_USER=true`. Eva's eerste proef vereist daarom vooraf een
  gepinde provider, restrictive policy, credentialvrije read-only taak en geen
  single-admin productieroute.
- De officiële bronnen leveren geen gelijkwaardig bewijs voor Eva's
  Telegramgezin, Home Assistant-readback, deurbel-/speakerpresence,
  no-duplicate-effectherstel of begrensde self-improvement. Dit is een
  nonclaim over gevonden documentatie, geen bewijs dat OpenBot deze uitkomsten
  principieel niet kan leveren.
- `AU01-AU08` vertalen de aantrekkelijke productuitkomst — één chatopdracht
  en daarna zelfstandig doorwerken binnen een intrekbaar mandaat — naar kleine
  Eva-gates. De eerste computerroute blijft browser plus één afgeschermd
  downloadbestand; shell, MCP, brede files, accounts en financiële effecten
  volgen als aparte capabilitygates.
- Voor eindacceptatie moeten de technisch gesloten `AU01-AU07` allemaal
  opnieuw op dezelfde bevroren image worden gereceipt. Daarna start `AU08` de
  verzegelde zevendaagse praktijktest op diezelfde image.

## Reviewverloop

- De architectuurreview wees aanvankelijk op onduidelijke providerordering,
  een te brede eerste computerroute, samengevoegde AU-gates, losse B01/E01-
  claims en onvoldoende scherpe AG-UI/nonclaimgrenzen. Deze punten zijn
  afzonderlijk hersteld; de herreview gaf **GO**.
- De onafhankelijke bron- en bewijsreview bevestigde de pins en productfeiten,
  maar blokkeerde eerst op dit ontbrekende receipt en een tegenstrijdige
  eindimageregel. Het receipt bestaat nu en de eindgate vereist expliciet een
  nieuwe `AU01-AU07`-receipt op dezelfde bevroren image. De herreview gaf
  daarna **GO**.

## Besluit

**GO.** Twee onafhankelijke reviewers keuren de productafbakening,
bronverankering, sidecargrens, kleine AU-gates, nonclaims en eindbewijsvolgorde
goed. OB00 bewijst uitsluitend een eerlijke documentatie-inventaris; het is
geen live OpenBot-proef en geen claim dat Eva al gelijkwaardig autonoom is.
