# CP-001: directe journalexports gekoppeld aan admissionpreflight

Code `cd3eed28e759e899962e090a871d18833e5cf62c` op private
`codex/cp001-runner-20260912`. Ownership beperkt tot CPbenchmark/test/docs.
Geen runtime-, schema-, quota-, helper-, budgetpolicy- of frameworkwijziging.

## Uitvoerbaar resultaat

De bestaande `cp001_accounting.py --exports /private/native-exports.json` leest nu
ook expliciete directe journalexports. Nieuwe kleine adapter
`cp001_journal_admission.py` bindt bestaande eventvelden aan drie gehashte product-
bronnen en de gedeclareerde tenant/install/principal/conversation/message-scope.
Admission/request-UUID, terminal usage/outcome en stopcheckpoint worden gecontroleerd,
met bestaande checkpointfase/status/revision. Dubbele, afwijkende of malformed
exports krijgen rejected zonder een schijnbaar geldige limiet.

Rapport bevat maximum_requests, admission/terminal/onopgeloste aantallen, stopmarker
en bekende directe usage. Unmeasured blijft null; gedeeltelijke som wordt partial.
Het admissionaantal is uitdrukkelijk niet hetzelfde als bewezen providerdispatches.
Bestaande SDK UsageLimits-expressies worden met Python AST uit dezelfde bron gelezen,
geen gekopieerde beleidswaarden. De huidige request_limit-expressie is 1, tools en
tokens blijven hun werkelijke conditionele bronexpressies. Runtimeconfiguratie en
providerhandhaving zijn daarmee niet geattesteerd en blijven afzonderlijk unknown.

Lees README voor het exacte optionele direct_journal JSON-formaat en de test voor
de onafhankelijke SQL-exportquery. Geen discovery van services/DB/credentials.
CLI blijft exitcode 2, live_enabled false; de native Inspectpreflight erft dit rapport.
Bron-/rowbinding is geen authenticiteit of volledigheidsattestatie van externe JSON.

## Eén geïntegreerde echte PG-proef

`tests/benchmark/test_cp001_journal_admission.py` hergebruikt de bestaande PG16.13-
fixture en ScriptedPydanticConversation. Een afzonderlijke engine/pool leest per
snapshot scoped SQL-rijen. De proef controleert:

- Voor de eerste admission is nog geen duurzame cap aanwezig; tijdens de echte
  Pydantic-fixtureaanroep, vóór provideroutput, staat cap2/admission1/onopgelost1.
- Eén providerloze modelphase wordt terminal met gemeten fixtureusage.
- Tweede admission zonder beweerde dispatch maakt onopgelost1; bij reopen met
  maximum99 weigert de echte journal de derde admission op behouden maximum2.
- Stopcheckpoint blijft naast de onopgeloste admission zichtbaar en verhindert
  nieuwe admission. Een terminal unmeasured record sluit de tweede af zonder
  providertokens te verzinnen; bekend direct subtotal blijft partial.
- Stop- en completionreplay wijzigen counts/usage/events niet. Slechts één echte
  scripted providerphase. Scope/source/duplicate/checkpointafwijkingen geweigerd.

85 gerichte tests op codecheckpoint: bovenstaande geïntegreerde PG-proef plus
bestaande accounting/native/runner/EV-001-tests; Ruff/diffcheck slagen.
Eerdere directe runtime- en budgetproeven zijn hergebruikt als fixtures/bron en
blijven hun eigen reviewed scope, zonder claim dat de releasepoort is herhaald.

Artifact `/home/administrator/project/.eva2-artifacts/cp001-journal-admission-20260913`
bevat 8 lifecycle-snapshots, native-exports, echte CLI-preflightuitvoer, JUnit/tests,
verifier en receipt met acht bronbindingshashes. PG network none, tmpfs, private
Unixsocket, geen gepubliceerde poorten. Na fixture nul eva-schema's, container
verwijderd en afwezigheid gecontroleerd. Geen liveprovider of productdata.

## Exact ontbrekende zekerheid / voorbereid vervolg

De proef demonstreert een concrete ontbrekende zekerheid: stop=true met een admitted
poging zonder terminal is geen quiescence. Zelfs terminal unmeasured kan niet zeggen
of een provider nooit gestart is, gestopt is of buiten het proces nog werkt. Daarom
blijft server_quiescent null in alle snapshots, ook na replay. Runtime token-/tool-/
deadlinelimieten staan niet in deze duurzame events; bronexpressies zijn geen
requestgebonden actieve-limit-receipt. Helper/geheugendekking blijft buiten scope.

Een eventueel volgend runtimewerk vereist eerst exacte ownership en semantiek:
hoe kan de bestaande transportlifecycle een requestgebonden afgeronde/cancelled-
transportobservatie leveren zonder remote providerstilstand te verzinnen, en welke
bestaande actieve instellingen kunnen bij admission worden vastgelegd zonder nieuwe
policy? De huidige onopgeloste-stop-snapshot is de reproduceerbare negatieve case
voor die afstemming. Er is hier geen nieuwe serverpolicy of quiescenceclaim ingevoerd.
Onafhankelijke review vóór centrale integratie; werkbord blijft bij beheerder.
