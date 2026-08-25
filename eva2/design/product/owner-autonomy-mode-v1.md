# Eva 2 — Eigenaarmodus voor vergaande autonomie

Bijgewerkt: 25 augustus 2026.

## Begripsverwarring rond Hermes Godmode

Hermes' actuele officiële **Godmode** is geen algemene autonome werkstand. Het
is een optionele securityskill voor jailbreaks, promptinjectie en het omzeilen
van veiligheidsfilters. Dat bewijst niet dat Hermes zelfstandig accounts,
computertaken, huisacties of langdurige doelen veiliger of beter uitvoert.

Eva kopieert daarom geen permanente safety-bypass in haar productiepersona. De
productmogelijkheid die de eigenaar bedoelt heet **Eigenaarmodus**: Eva mag
binnen één vooraf besproken mandaat alle werkelijk gekoppelde capabilities
zelfstandig combineren, waarschuwt vooraf voor de gevolgen en vraagt niet voor
iedere tussenstap opnieuw toestemming.

## Productbelofte

Na activatie in een geverifieerd privégesprek kan Eva binnen het mandaat:

- zelf onderzoeken, plannen, observeren, handelen, herplannen en wachten;
- browser, terminal/sandbox, bestanden, Home Assistant, communicatiekanalen,
  on-the-fly skills en begrensde subagents combineren;
- binnen expliciet toegelaten scope ook accountcreatie, voorwaarden, betaling,
  KYC en echte orders uitvoeren zodra hun afzonderlijke providerproef groen is;
- doorgaan na een crash zonder blind een mogelijk uitgevoerd effect te herhalen;
- de eigenaar tussentijds informeren zonder voor iedere gewone stap te pauzeren.

Een waarschuwing is informatie, geen vermomde verplichte bevestiging per actie.
De activering van het mandaat is de toestemming voor de daarin beschreven
handelingen. Buiten de exacte scope, looptijd, middelen of limieten stopt Eva en
vraagt zij om een nieuw of gewijzigd mandaat.

## Wat het mandaat minimaal bindt

- geverifieerde eigenaar, doel en begin-/eindtijd;
- accounts, personen, groepen, apparaten, markten en data die in scope zijn;
- toegelaten capabilities, providers, netwerkdoelen en subagentparalleliteit;
- budget, ordergrootte, verlieslimiet, betaalmaximum en eventuele opnamegrens;
- welke voorwaarden/KYC-flow Eva mag accepteren en welke bewijsstukken zij mag
  gebruiken;
- waarschuwing en samenvatting van verwachte onomkeerbare gevolgen;
- rapportagecadans, stiltebeleid en omstandigheden voor `needs_help`;
- directe pauze-/stopknop, intrekking, expiry en herstel-/rollbackroute.

Geen enkele skill, subagent of provider kan dit mandaat verruimen. De bestaande
identity-, privacy-, consent-, secret-, policy-, ledger- en readbackcontracten
blijven leidend. Een externe provider of toepasselijke wet kan een actie alsnog
onmogelijk maken; Eva meldt dat als blocker en verzint geen succes.

## Bedieningsvorm

- Activeren, aanpassen en stoppen kan via dezelfde Eva-chat en later via het
  dashboard; beide schrijven hetzelfde append-only mandatevent.
- Het dashboard toont scope, resterende tijd/budgetten, actief plan, workers,
  waarschuwingen, providerattempts, receipts en een prominente stopknop.
- De eigenaar kan live meekijken of tijdelijk overnemen zonder dat een tweede
  agent de taak of identiteit overneemt.
- Na stop/expiry worden nieuwe calls geweigerd; reeds begonnen externe effecten
  worden eerst teruggelezen en eerlijk als gelukt, mislukt of ambigu gemeld.

## Geen tweede orchestrator

Eigenaarmodus is een ruimer, expliciet mandaat boven dezelfde Eva. Zij hergebruikt
AU01-AU08 voor computerautonomie, MC03 voor toolketens, WB01-WB07 voor workers,
LG04-LG06 voor zelfgemaakte skills en B01/E01 voor onomkeerbare/economische
providers. Er komt geen aparte “godmode-agent”, ongecontroleerde shell in de
core of parallelle geheugenautoriteit.

## Kleine opleverslices

- [x] OM00 — Hermes' actuele Godmode op officiële bron afgebakend als optionele
  jailbreakskill; Eva's gewenste autonome uitkomst en nonclaim zijn beschreven.
- [ ] OM01 — Versieerbaar `OwnerAutonomyMandate/v1` met alle bovengenoemde
  grenzen, waarschuwing, expiry, intrekking en append-only receipt.
- [ ] OM02 — Eén geverifieerde privéchat- en dashboardroute voor preview,
  activatie, status, wijziging en stop; secretwaarden blijven write-only.
- [ ] OM03 — De algemene runner accepteert hetzelfde actieve mandaat bij iedere
  call zonder herbevestiging binnen scope en stopt fail-closed erbuiten.
- [ ] OM04 — Skills en subagents krijgen alleen afgeleide grants; poging tot
  privilege-, budget-, context- of recursion-escalatie wordt duurzaam geweigerd.
- [ ] OM05 — Crash, intrekking en stop midden in een actie leveren readback en
  geen blinde duplicatie; dashboard en chat tonen dezelfde toestand.
- [ ] OM06 — Echte afgeschermde end-to-endproef, daarna afzonderlijke
  account-/voorwaarden-/KYC-/betaal-/orderproeven volgens B01/E01.

## Primaire Hermes-bron

- Actuele officiële Godmode-documentatie, gecontroleerd op commit
  `1bbb6e5bce56e721ab685af4cd87df21bbff4d35`:
  <https://github.com/NousResearch/hermes-agent/blob/1bbb6e5bce56e721ab685af4cd87df21bbff4d35/website/docs/user-guide/skills/optional/security/security-godmode.md>
