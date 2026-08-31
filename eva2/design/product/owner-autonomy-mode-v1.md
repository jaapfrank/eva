# Eva 2 — Eigenaarmodus voor vergaande autonomie

Bijgewerkt: 31 augustus 2026.

## Begripsverwarring rond Hermes Godmode

Hermes' actuele officiële **Godmode** is geen algemene autonome werkstand. Het
is een optionele securityskill voor jailbreaks, promptinjectie en het omzeilen
van veiligheidsfilters. Dat bewijst niet dat Hermes zelfstandig accounts,
computertaken, huisacties of langdurige doelen veiliger of beter uitvoert.

Eva kopieert daarom geen permanente safety-bypass in haar productiepersona. De
productmogelijkheid die de eigenaar bedoelt heet **Eigenaarmodus**: tijdens de
installatie geeft de eigenaar Eva één blijvend mandaat om binnen het huishouden,
het lokale netwerk en de gekoppelde accounts zelfstandig te leven en handelen.
Eva mag daarna alle werkelijk bereikbare capabilities zelfstandig ontdekken en
combineren. Zij informeert volgens de afgesproken cadans en vraagt niet per
apparaat, provider, tool, planstap of gewone handeling opnieuw toestemming.

## Productbelofte

Na activatie tijdens de eigenaargebonden installatie kan Eva binnen het mandaat:

- zelf onderzoeken, plannen, observeren, handelen, herplannen en wachten;
- browser, terminal/sandbox, bestanden, Home Assistant, communicatiekanalen,
  on-the-fly skills en begrensde subagents combineren;
- binnen expliciet toegelaten scope ook accountcreatie, voorwaarden, betaling,
  KYC en echte orders uitvoeren zodra hun afzonderlijke providerproef groen is;
- doorgaan na een crash zonder blind een mogelijk uitgevoerd effect te herhalen;
- de eigenaar tussentijds informeren zonder voor iedere gewone stap te pauzeren;
- zelf lokale diensten ontdekken, actuele documentatie raadplegen, MCP/API/SDK
  kiezen, een koppeling voorbereiden en beschikbare rechten gebruiken;
- nieuwe apparaten, providers en gereedschappen gebruiken zonder dat hun naam
  of soort vooraf in Eva's code stond.

Een waarschuwing is informatie, geen vermomde verplichte bevestiging per actie.
De activering van het mandaat is de toestemming voor alle handelingen binnen de
gekozen brede leefomgeving en blijft geldig tot de eigenaar het wijzigt of
intrekt. Eva vraagt alleen menselijke hulp wanneer de buitenwereld die technisch
vereist, bijvoorbeeld voor een nog ontbrekende login, 2FA, CAPTCHA, fysiek
contact of wettelijk vereiste persoonlijke verklaring. Dat is geen nieuwe
Eva-toestemming: na die menselijke stap gaat zij binnen hetzelfde mandaat door.

## Wat het mandaat minimaal bindt

- geverifieerde eigenaar, installatie en ingangsdatum; standaard geen eindtijd,
  maar geldig tot intrekking;
- één begrijpelijke brede leefscope, bijvoorbeeld alle apparaten en diensten op
  het eigen lokale netwerk plus alle bewust gekoppelde accounts;
- alle huidige en later ontdekte capabilities en providers binnen die leefscope,
  zonder losse gebruikersgoedkeuring per tool;
- budget, ordergrootte, verlieslimiet, betaalmaximum en eventuele opnamegrens;
- welke voorwaarden/KYC-flow Eva mag accepteren en welke bewijsstukken zij mag
  gebruiken;
- welke uitzonderlijke gevolgen Eva direct moet melden of voor zover mogelijk
  zelf moet terugdraaien;
- rapportagecadans, stiltebeleid en omstandigheden voor `needs_help`;
- directe pauze-/stopknop, intrekking, expiry en herstel-/rollbackroute.

Geen enkele skill, subagent of provider kan het installatiemandaat buiten de
gekozen leefomgeving verruimen. De bestaande
identity-, privacy-, consent-, secret-, policy-, ledger- en readbackcontracten
blijven intern leidend, maar veroorzaken geen bevestigingsvraag per handeling.
Een externe provider of toepasselijke wet kan een actie alsnog onmogelijk
maken; Eva meldt dat als blocker en verzint geen succes.

## Bedieningsvorm

- Het eerste brede mandaat wordt tijdens installatie zichtbaar geactiveerd.
  Aanpassen en stoppen kan via dezelfde Eva-chat en later via het dashboard;
  beide schrijven hetzelfde append-only mandatevent.
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
- [ ] OM01 — Versieerbaar `OwnerAutonomyMandate/v1` met brede blijvende
  leefscope, meldbeleid, intrekking en append-only receipt; geen verplichte
  expiry of tool-permission per handeling.
- [ ] OM02 — Eén geverifieerde privéchat- en dashboardroute voor preview,
  activatie, status, wijziging en stop; secretwaarden blijven write-only.
- [ ] OM03 — De algemene runner accepteert hetzelfde actieve mandaat bij iedere
  call zonder herbevestiging binnen scope. Alleen een technisch noodzakelijke
  menselijke providerstap mag `needs_help` veroorzaken.
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
