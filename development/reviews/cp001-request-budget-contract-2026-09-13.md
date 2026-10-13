# CP-001: bestaande requestgrenzen en één admissiongat

Onderzocht op private HEAD `ca6cf4a12056d8dfc976036f6a18672865cd2d32`,
met gereviewde directe pogingfix `084cdc822cd9f7b5e88ea99678379949897e46a5`.
Bronhashes en rode proef in
`/home/administrator/project/.eva2-artifacts/cp001-request-budget-20260913`.
Geen livecalls, databasewijziging of extra eigenaarlimiet.

| Bestaande grens | Werkelijke scope / afdwinging | Duurzaam per gebruikersrequest? |
| --- | --- | --- |
| conversation maximum_requests | Journal begin_model_request, scope tenant/install/principal/conversation/message; advisory lock; telt admissions incl. onbekende usage; min huidige en opgeslagen cap | Ja, bestaand ADMITTED maximum_requests, ook reconnect met ruimere config. Bestaande test_operational_timeline.py test_model_request_limit_is_durable_across_restart_and_unknown_usage dekt dit |
| Pydantic request_limit=1 | Eén Agent.run; directe buitenloop kan eenmaal retryen; journal begrenst ieder admitted attempt apart | De lokale 1 niet; journal totaal wel |
| RuntimeResourceBudget max_input_tokens | Envelopinschatting vóór fase; UsageLimits binnen fase; gemeten aggregate binnen één generate ook achteraf gecontroleerd | Geen vastgelegde tokenreserve/cap voor gehele gebruikersrequest; geen reden deze automatisch in te voeren |
| max_output_tokens | UsageLimits binnen fase en achteraf generate-aggregaat | Geen duurzame hele-requesttokenreserve |
| tool_calls_limit=1 | Eén Agent.run met tools; geen complete toolketenbegrenzing door deze instelling | Nee, geen bewijs van totale requesttoolcap |
| timeout_seconds | asyncio.timeout rondom één fase incl. interne retryloop; taalherstel opent een nieuwe fase | Nee, geen duurzame requestdeadline/quiescence-attestatie |

Bronnen: pydantic_conversation.py UsageLimits (839), bounded_execute (1063),
generate aggregate enforcement (1248), _admit_request/_enforce_reported_usage;
conversation_journal.py begin_model_request (209); runtime.py settings/max requests;
contracts/runtime_budget.py max input/outputvelden. Regels gelden voor bovengenoemd
checkpoint, niet als ongecontroleerde claim over latere canonical bronwijzigingen.

## Reproduceerbaar gat zonder nieuwe budgetsemantiek

`test_language_repair_does_not_dispatch_after_existing_input_cap_exhausted`
gebruikt bestaande ScriptedPydanticConversation en _ExactStreamUsageModel:
inputcap blijft 16.000, eerste phase rapporteert precies 16.000 en een antwoord
met vreemde schrifttaal, waarop generate taalherstel aanvraagt. De tweede phase
start toch met opnieuw dezelfde inputcap. Pas de bestaande aggregatecontrole
weigert het resultaat bij 32.000 inputtokens. De rode test verwacht één dispatch
zodra de eerste de bestaande cap volledig heeft uitgeput; huidig gedrag geeft twee.
Dit is een vermijdbare extra poging binnen één bestaande generatecap, geen bewijs
voor een nieuw samplebreed budget. Native modelrequestaantallen zijn niet de oorzaak.

Reproductie vanuit eva2, bestaande offline venv met `PYTHONPATH=src`:
`python -m pytest -q tests/model/test_direct_request_budget.py`.
`regression-before.txt` bevat de werkelijke FAIL 2 != 1 en de 32.000/16.000-logregel.

Voorgestelde fix, pas na ownershipafstemming: een smalle guard vóór taalherstel
wanneer gemeten input de bestaande generatecap al uitgeput heeft; behoud bekende
usage in de bestaande modelfout. Geen budget verhogen/verlagen, geen extra
ownerrequestmandaat, geen whole-turn reserve, geen verandering aan toegelaten
normale taalherstelpogingen met ruimte. Fix en groene evidence volgen afzonderlijk.


## Gerichte correctie na ownershipakkoord

Rode contract/testcommit `82a351633024c5a090f8c73cd05cc9015729abe0` gevolgd door
fix `1f1e064e4ab144ceae4464943a36a64698064b0d`. Het modelbestand was vóór de fix
exact gelijk aan centrale `bb021d501034e711af18463f7155adcb0073aba8`.
Alleen generate krijgt zeven guardregels vóór taalherstel: als bekende inputusage
de reeds bestaande aggregatecap bereikt, wordt de bestaande ConversationModelError
met die usage geworpen vóór de volgende phase/admission. Geen nieuwe requestcap,
geen andere retry/tool/deadline/ownerlimiet, geen wijziging aan de directe execute-loop.
De coördinator is geïnformeerd over bestandoverlap met de afzonderlijke bundelhelper.

83 gerichte tests geslaagd op fixcheckpoint, plus Ruff/diffcheck. Nieuwe tests:
exact 16.000 resulteert in één echte scripted dispatch én één journaladmission,
errorusage identiek aan eerste phase; 15.999+1 blijft toegestaan en eindigt exact
op dezelfde cap; geheel onbekende usage blijft unknown en krijgt de bestaande
unmeasured-fout; een oorspronkelijke phasefout behoudt exceptionidentiteit/usage.
De overige model-, usage-, journal- en researchfailuretests blijven groen.

Artifact bevat tests.txt/JUnit, verify.py/receipt, acht bronbindingshashes,
regression-before.txt en repair-guard.patch (uitsluitend modelhunk+test tegen centrale
bb021d50). Deze fase start geen database of service en gebruikt geen liveprovider.
De bestaande duurzame requestaantallimiet is contractonderzoek met eerder bestaande
PG-testbron, geen nieuw uitgevoerde PG-proef in deze fase. Volledige requestbrede
token/tool/deadlineadmission blijft afzonderlijk; deze fix claimt dat niet.

Voor integratie na onafhankelijke review zijn alleen bovenstaande twee kleine
commits en deze bijgewerkte notitie nodig, of de artifactpatch plus notitie.
Niet stil opnemen in de al gereviewde eerdere directe-pogingbundel.
