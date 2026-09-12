# Vergelijkingsgereedheid — 12 september 2026

Audit van bestaand bewijs, geen nieuw werkbord of ranglijst. Geen webonderzoek,
modelcalls, betaalde benchmarks of productwijzigingen uitgevoerd.

## Wat werkelijk bewezen is

Er is nog geen bewijs dat de huidige Eva2 algemeen beter is dan Eva1/Bottie,
OpenClaw, Hermes of Agent Zero. Productproeven bewijzen afzonderlijke functies.
Een groene CP-/EV-kaart betekent voltooiing van die kaart, niet concurrentiewinst.

| Referentie | Bestaand bewijs | Onbekend |
|---|---|---|
| Eva1/Bottie | Apart bewijs voor bereikbaarheid, databehoud, event-loopgedrag en Telegramroutering; Jaaps ervaringsfeedback. | Geen gepaarde huidige takenmatrix met gelijke invoer, middelen en onafhankelijke uitslagen aangetroffen in de geraadpleegde set. Relatieve kwaliteit, tijd en kosten onbekend. |
| Hermes | EV-002: één C04-antwoord voldoet aan drie eenvoudige criteria; native receipt met één call, 807 input-, 139 output-, 946 totaaltokens. | Herhalingen, dagelijks geheugen, acties, initiatief, herstel, stem en huidige-versievergelijking ontbreken. |
| OpenClaw | EV-002: toelating faalde vóór modelcall doordat de Codex-harness niet kon registreren. | Geen inhoudelijk antwoord. Adapter/toelatingsfalen is geen productnederlaag. |
| Agent Zero | EV-002: vooraf vereiste nul-tools/nul-retrygrens kon niet op verzoekniveau afgedwongen worden; geen modelcall. | Geen inhoudelijke vergelijking. Onvergelijkbare middelen betekenen onbekend, geen verlies. |
| Eva2 | EV-002: één C04-antwoord voldoet aan dezelfde drie criteria, maar oorspronkelijke stackreceipt kon door directoryrechten niet bewaard worden. | completed_receipt_degraded: geen betrouwbare relatieve wandkloktijd/providerfactuur of even sterk gebruiksbewijs als Hermes. De huidige kern is bovendien een andere kandidaat. |

## Middelen en concrete gaten

- CP-000 selecteerde een evaluatiemotor, geen winnaar. Inspect 0.3.261 uit de
  selectie van 2 september werd opgevolgd door de EV-001-lock 0.3.263.
- EV-001/v2 bewijst echte Inspect agent_bridge en sandbox_agent_bridge, native
  .eval-logs, apart herscoren met intact origineel en Docker-opruiming. Beide
  gebruikten mockllm/model: accuracy=1 is fixturegedrag, geen agentprestatie.
- EV-002 heeft twee echte calls en twee admission failures. Het latere
  EV-003-document repareerde OpenClaw-/AgentZero-toelating of Eva-receipts niet.
- EV-003 is een beoordeeld protocol zonder vergelijkingsrun. Concrete ongeziene
  dataset, curator, vastgezette instellingen en menselijke beoordelaars ontbreken.
  Zes families maal twaalf cases maal drie blokken is de volledige eindmatrix,
  niet de verplichte herhaaltest voor iedere kleine productwijziging.
- benchmark/inspect_ai_v1/bridge_probe.py is de bewezen herbruikbare Inspectbrug.
  ev002_baseline.py heeft nadrukkelijk GEEN uitvoermodus: alleen planvalidatie,
  lokale preflight en resultaatvalidatie. De map bevat nog geen complete actuele
  vier-/vijfdeelnemersrunner. Hergebruik Inspect native bridges/logs/scorers en
  experiments/managed-b-clean-graph-v1/probe.py voor Eva; geen tweede runtime of
  zelfbedacht scoreformaat toevoegen.
- De historische EV-002-snapshots: Eva881be2c, OpenClaw v2026.9.1/ad6fe23,
  Hermes v2026.8.31/29112be en Agent Zero v2.11/6a6cecf, gpt-5.4-mini medium.
  Dit zijn vastleggingen van 4 september, NIET gecontroleerd actueel op
  12 september. Nieuwe run vereist nieuwe bron/image-/configbinding. Huidige
  runtime niet als de oude baseline presenteren.
- De historische tekst “Inspect nog niet definitief gekozen” onderaan het
  vergelijkingsdocument is opgevolgd door EV-001, geen nieuwe selectieblokkade.
- Componentbakeoffs voor dashboard, MCP-gateway, VAD of dependencies bewijzen
  uitsluitend die componentkeuze. situational-humour-live-ab toont onder meer
  release/rollback en HUMORLIVE-replay, geen volledige vergelijking met Eva1.
- AgentBench/GAIA/OSWorld-builds, registraties en smokeproeven zijn geen officiële
  scores. De beschreven DBBench-smoke gebruikte een nepagent met bewust fout
  antwoord. Het oude ComparableAgentRun/v1 is afgekeurd voor nieuwe resultaten.

## Kleinste eerlijke volgende meting

Hergebruik EV-003's ontwikkelcadans: TWEE kleine cases, elk TWEEMAAL, met vooraf
vastgezette versies. Begin met Eva2 versus Eva1/Bottie voor dagelijks gebruik;
voeg ieder van de drie concurrenten toe zodra diens native route vergelijkbaar
is toegelaten. Dit is ontwikkeling, geen algemene winnaar of vervanging van de
EV-003-eindmatrix.

1. Netwerkloze voorbereiding: check lock, lokale bron/image, native launchroute,
   scopes, schoon profiel, afdwingbare budgetten en schrijfbare private logs.
   Lees een proeflog terug vóór echte calls. OpenClaw-harness en AgentZero-grenzen
   hier oplossen via ondersteunde routes; geen speciale benchmarkhulp voor één
   deelnemer. Bij ontbrekende route is dat paar onbekend, geen nulscore.
2. Case A: dezelfde synthetische voorkeur vertellen, corrigeren en in een nieuw
   gesprek gebruiken. Rubric vooraf: juiste actuele voorkeur, geen oude voorkeur
   als feit, geen verzonnen persoonlijke context, bruikbaar Nederlands antwoord.
   Identieke beurten en lege onafhankelijke startkopieën; geen gezinsgegevens.
3. Case B: dezelfde twee lokale notities vergelijken, korte brongebonden
   aanbeveling opslaan en teruglezen. Injecteer dezelfde onderbreking op een
   observeerbaar gelijk punt, hervat. Rubric: juiste relevante verschillen,
   bestand/hash, geen dubbele mutatie of verlies, correcte status, beperkte hulp.
   Geen vergelijkbare tool-/herstelroute betekent vooraf onvergelijkbaar.
4. Leg vóór resultaten dezelfde modelrevision waar ondersteund, reasoning,
   input/output-/tijd-/stapbudgetten, tools, data, rechten en hardwareklasse vast.
   Kies de kleinste ondersteunde gemeenschappelijke grenzen; hier geen ruimere
   limieten gokken. Verschillende ingebouwde modellen alleen apart benoemen als
   configuratievergelijking, niet ongemerkt als gelijk-modelmeting.
5. Vier samples per deelnemer, schone start en vooraf afgewisselde volgorde.
   Geen mislukte sample overdoen voor betere cijfers. Herstel binnen de taak telt
   inclusief tijd, calls en hulp mee.
6. Native Inspect-logs plus onafhankelijke bronreadback blijven bewijs. Voor
   menselijkheid/nut: geanonimiseerde antwoorden in afgewisselde volgorde aan
   mensen. Jaaps ervaring nooit vervangen door een betaalde of onbetaalde modeljury.

| Dimensie | Verplicht vastleggen |
|---|---|
| Kwaliteit | Alle vooraf bepaalde rubricvoorwaarden en echte effecten/bestanden; deels is geen volledig succes. |
| Zelfstandigheid | Hints, verduidelijkingen, reparaties, overnames en menselijke minuten; verplichte login apart en in totale inspanning. |
| Latency | Ontvangst tot ontvangstreactie, eerste inhoud en geverifieerd eindresultaat afzonderlijk. “Ik zoek het uit” is geen inhoudelijk antwoord. |
| Totaalverbruik | Native input/output/cache, helper-, geheugen-, retry- en beoordelingscalls. Ontbrekend is onbekend; geld alleen bij echte meting. |
| Herstel | Zelfde foutpunt, stop/herstart/hervatting, geen dubbele effecten, behoud van scopes en resultaat; fouten blijven zichtbaar. |
| Eigenaarervaring | Individueel oordeel over nut, duidelijkheid, warmte en inspanning; niet door ontwikkelaar ingevuld. |

Vier samples dragen geen robuuste p95 of brede voordeelpercentages. Toon ruwe
individuele uitslagen en alleen beperkte observaties. “Algemeen beter” blijft
gebonden aan de bestaande strengere EV-003-criteria en onafhankelijke ongeziene
set. De kleine proef voorkomt dat productontwikkeling op die volledige matrix
wacht, maar rechtvaardigt geen algemeen groen concurrentievinkje.

## Uitgevoerde auditcontroles

Lokaal beide exit0, zonder modelcalls:

```
python3 benchmark/inspect_ai_v1/ev002_baseline.py --check-plan
python3 benchmark/inspect_ai_v1/ev002_baseline.py --check-result \
  --result evidence/product/EV-002-controlled-baseline-result-2026-09-04.json
```

Uitkomst: plan valid, historische registratie valid, modelCalls=2, ranking=null.
Dit is geen nieuwe prestatiemeting.

Geraadpleegd: design/product/comparable-agent-benchmarks-v1.md,
benchmark/inspect_ai_v1/README.md en ev002_baseline_plan.json; CP-000-, EV-001-v2-,
EV-002- en EV-003-bewijs onder evidence/product; reviews/EV-002-controlled-baseline-
proof-review-62507a7.md; situational-humour-live-ab-2026-08-31.json. Eva1- en
AgentBenchbeperkingen volgen uit deze lokale documentatie; geen volledige
inventaris van alle ooit gemaakte private metingen geclaimd.