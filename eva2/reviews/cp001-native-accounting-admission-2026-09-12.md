# CP-001 native usage en serverbudgettoelating

Code: `0f16e7face43b152adaa2c8c3fdb97657942661b`, private branch
`codex/cp001-runner-20260912`. Alleen benchmark-, test- en documentatiepaden.

## Uitvoerbaar resultaat

`python3 eva2/benchmark/inspect_ai_v1/cp001_accounting.py` produceert een JSON-
preflight en exitcode 2 (niet toegelaten). Optioneel `--exports /private/exports.json`
leest expliciete lokale native exports. Geen netwerk, betaalde calls, credentials,
productstart, budgetverhoging of productwijziging. De bestaande native Inspect-
preflight bevat hetzelfde accountingrapport. README specificeert de exportvelden.

De adapter leest echte bestaande veldnamen en bewaart hun beperkte betekenis:

| Interface | Beschikbaar | Ontbrekend voor volledige sample |
| --- | --- | --- |
| EVA2 ConversationModelUsage / REPLY_COMMITTED | input/output, model_attempts, tool_calls, measured/partial; bestaande binding/hashcontrole hergebruikt | helpers/geheugen en complete requestgrens zijn niet door dit antwoord geattesteerd |
| EVA2 journal model_usage | admitted/recorded reconstructie; ontbrekende resultaten blijven onzeker | admission is geen dispatchbewijs of complete providerretrytelling |
| Beide raw response.completed usage | input_tokens, output_tokens, total_tokens; coherentietest zonder nuldefaults | één response bewijst geen volledige sample of onbekende retries |
| Bottie llm_usage timeline meta | inputTokens/outputTokens/requestAttempts diagnostiek | producer zet ontbrekende waarden soms op nul, logfouten worden opgevangen; geen requestgebonden complete receipt |
| Beide provider-wirebody | model, reasoning.effort, max_output_tokens indien aanwezig | limiet van één call is geen samplebrede servergrens |
| EVA2 RuntimeResourceBudget | max_input_tokens/max_output_tokens als configuratie | export valideert geen installerautoriteit, actieve toepassing of totale samplegrens |

Bronnen zijn de 9 productbestanden in `cp001_accounting.SOURCES`; de preflight
hasht deze. Pydantic UsageLimits bevat lokale request/tool/tokenlimieten; Bottie
chatComplete heeft outputlimiet, retryknoppen en optionele timeout. Deze bestaande
voorzieningen zijn niet ontkend, maar bewijzen op zichzelf geen gelijke grenzen
voor alle gespreks-, helper-, geheugen- en achtergrondcalls samen.

## Gate en open voorwaarde

Ieder ontbrekend/ongeldig raw usageveld krijgt een afzonderlijke blocker.
Bekende providergrenzen worden per veld equal/mismatch/unknown; twee ontbrekende
waarden zijn nooit equal. Measured gespreksusage wordt niet gepromoveerd tot
sampletotaal. Partial blijft partial. Timelinegegevens vervangen geen raw tokens.
`total_usage` en `server_sample_limits` blijven per veld null. Buiten de bestaande
exports zijn geen complete attesten uitgevonden; aangeleverde complete/live-enabled-
booleans en gefingeerde totalen kunnen de gate niet openen.

Voor een afzonderlijk toegelaten echte productenproef ontbreekt dus nog aantoonbaar:
request-/samplegebonden provenance voor alle model/helper/memory/retrycalls,
complete usage inclusief onbekende dispatch/fouten, gelijke actieve servergrenzen
voor input/output/modelattempts/tools/deadline en cancellation/quiescence van de
volledige keten. Tot die contracten aantoonbaar beschikbaar zijn, blijft de gate
unknown en live_enabled false. Deze oplevering levert de concrete offline diagnose;
zij implementeert geen nieuw serverbudgetcontract buiten de geautoriseerde scope.

## Bewijs

84 gerichte tests geslaagd op het codecheckpoint, plus Ruff. Tests omvatten ontbrekend,
negatief, boolean, float en string per raw usageveld en beide producten; ontbrekende
gespreksvelden, verkeerde binding/hash, partial, incoherente totalen, echte nul,
losse timeline, gelijke/gewijzigde/ontbrekende waargenomen grenzen, alle onbekende
servergrenzen, config/booleans die geen toelating geven, CLI en ontbrekende bron.

Artifact: `/home/administrator/project/.eva2-artifacts/cp001-accounting-20260912`.
Bevat JUnit/tests.txt, verify.py, receipt.json, lege en native preflightuitvoer en
native-exports.json. De verifier bindt 13 bestanden aan het codecheckpoint.

De native-exportproef hergebruikt eerdere echte PG-journal- en SQLite-snapshots met
synthetische modelstubdata. PG-gespreksusage 23 input / 7 output wordt exact uitgelezen;
Bottie heeft in deze snapshot nul llm_usage-rijen, wat unknown blijft. Dit zijn geen
nieuwe providerkostenmetingen. Beide CLI-paden exit2. Geen services gestart of
gewijzigd. ACK-/herstelbewijs blijft de eerder gereviewde scope, zonder uitbreiding.

Onafhankelijke review via bestaande coördinator/reviewer; centrale beheerder verwerkt
na review het werkbord en de integratie. Geen ranking of menselijke acceptatieclaim.
