# CP-001 native ingress adapters — 12 september 2026

Gebouwd en offline getest; onafhankelijke review open. Geen native productcall,
herstart, privédataread/reset, nieuwe provider, budgetverhoging of winstclaim.
Deze stap volgt op de onafhankelijk beoordeelde offline fixture-runner.

Codecheckpoint `17a7bd67a7e8922a44e501334ece0e74f5dfd90f`, parent `8e0a62ba`.
Wijzigingen uitsluitend `benchmark/inspect_ai_v1/{cp001.py,cp001_native.py,README.md}`
en `tests/benchmark/test_cp001_native.py`. Deze bewijsnotitie verandert geen code.
Worktree `/home/administrator/project/.eva2-artifacts/cp001-runner-20260912`.

## Concreet uitvoerbaar

De nieuwe adapter gebruikt de bestaande EVA2 `/internal/v1/turns/respond`- en
Bottie `/api/chat`-wirecontracten. De `exchange`-poort voert precies één
geïnjecteerde transportcall uit en vraagt daarna apart durable readback op.
Tests voeren dit pad werkelijk uit met lokale callbacks; de CLI heeft geen
product-/netwerktransport. Er zijn geen vervangende agents of modelproviders.
De bestaande managed-B-probe is de bron voor synthetische identiteit,
requestvorm en durable usage. Botties bestaande `access.js`-normalizer is in
een gerichte Node-controle daadwerkelijk op het geconstrueerde sessie-ID getest.

Request-ID's blijven bij replay gelijk. De derde geheugenbeurt krijgt een nieuw
conversation-ID, dezelfde synthetische persoon en geen ingevoegde historie.
Hervatten houdt dezelfde sessie. Deze ID's vervangen geen schoon productprofiel.

Antwoord-ID's en EVA2-contenthash worden gevalideerd. Apart gelezen durable
inhoud moet overeenkomen. Anders dan de fixture-oracle wordt geen vast antwoord
verlangd: vrije tekst mag dezelfde integriteitscontrole doorlopen. Dit is geen
semantische score; rubric/menselijk oordeel blijft unknown. Gemeten gedeeltelijk
EVA2-conversationgebruik wordt als gedeeltelijk bewaard; volledige helper-/memory-/
retry-/achtergrondusage en geld blijven unknown. Botties publieke antwoordusage
wordt niet als gebonden native meting geaccepteerd.

## Aantoonbare ontbrekende toelating

De readonly native preflight blijft voor beide deelnemers unknown. De CLI bevat
geen liveflag; een zelfverklaarde configuratie kan dit niet groen maken.

- Runtime-image/configbinding, schoon profiel en gelijke model/reasoninginstelling
  moeten vooraf aantoonbaar zijn. In deze stap zijn alleen bronbytes gebonden.
- Gelijke byte/clientdeadlinegrenzen worden doorgegeven en overschrijdingen
  geregistreerd. De concrete transportinjectie moet I/O zelf begrenzen; het
  bestaande managed-B `_http_json` heeft 120s vast en voldoet niet ongemerkt.
- Gelijke model-/tool-/call-/retrygrenzen zijn op beide ingangen niet bewezen.
  Er worden dus geen positieve modelbudgetten of nulverbruik voor echte producten
  verzonnen. Alle native budgetvelden in het manifest zijn null.
- Een HTTP-timeout bewijst geen serverstop. `timeout_execution_unknown` doet
  geen retry en claimt geen quiescence. De native lifecycle/cancelgarantie blijft open.
- Taak B's gelijke save-onderbreking en actie-/bestandreadbacks moeten nog native
  aangesloten worden. Deze adapter start of reset geen productdienst.

## Bewijs op exact codecheckpoint

31 gerichte tests groen: 11 nieuw, 7 fixture-runner, 13 bestaande EV-001.
Ruff op gewijzigde Pythonpaden groen. De tests bevatten vrije antwoorden,
identiteits-/hashfouten, pending/handoff, gewijzigde/ontbrekende readback,
onbekend/partial gebruik, input/output/deadline, timeout/failure zonder retry,
new-conversation/resume en ontbrekende bronadmission.

Dezelfde exact gelockte Inspect-omgeving als de vorige levering is hergebruikt:
`/home/administrator/project/.eva2-artifacts/cp001-eval-env-20260912`.

- Native CLI: `/home/administrator/project/.eva2-artifacts/cp001-native-preflight-20260912/receipt.json`.
  Native `.eval` SHA-256 `d1fe66e45bf12149ad2ddd92f52e8efd9bed30f4f2389882be72e7d6e4c5bc2e`.
  Acht unknown-samples, 20 echte requestmappings, nul modelgebruik, geen aggregatie.
  14 bronbestanden zijn vooraf via Git/SHA-256 gebonden. Fixturetargets ontbreken
  uit native samples; tekst blijft gelijk aan de oorspronkelijke taakbeurten.
- Fixture-regressie-CLI: `/home/administrator/project/.eva2-artifacts/cp001-native-regression-dry-run-20260912/receipt.json`.
  Native `.eval` SHA-256 `541b62b85244435c6709172af186821aa8b452affcc806cfc20ebb8684c7ae79`.
  Acht samples succesvol; product_result unknown, ranking null, geen modelcalls.
- Beide logs zijn teruggelezen. `verify_native.py` in de evaluatieomgeving
  controleert bronbindingtelling, null-budgetten, verwijderde targets, native
  requestteksten, unknown-scores en leeg modelgebruik. Geen nieuwe score-engine.

Reviewbehoefte: wirecompatibiliteit, onafhankelijke readerbinding, onbekend
verbruik, gedeelde cliëntgrenzen versus nog onbewezen servergrenzen, sessies en
expliciete no-live-toelating. Geen upstreamupgrade of externe documentatiekeuze
in deze stap; lokale productimplementaties zijn de gecontroleerde bron.
Rootreview gaat vóór een echte productproef. De centrale admin beheert de status;
de echte CP-001-productvergelijking blijft open.

## Reviewcorrectie — 12 september, kandidaat 8f446c85

De onafhankelijke review van `17a7bd67` gaf NO-GO: EVA2-readback vergeleek alleen
tekst en principal. Een receipt van een andere beurt met hetzelfde antwoord
kon daardoor ten onrechte `readback_verified` worden. De eerdere codekandidaat
is dus niet gereviewd groen; onderstaand checkpoint vervangt die kandidaat.

Correctiecode: `8f446c856e6d7c49fc01696226aec0b3a0ba7d9a` (parent `62ad6a4e`).
De native adapter eist nu óók message_id, conversation_id en text_sha256.
Alle vijf velden (inclusief tekst en principal_id) zijn verplicht en exact
gebonden aan het request en het reeds gevalideerde response. De hash wordt
opnieuw uit de response-tekst berekend. Ontbrekende/afwijkende waarden geven
readback_failed, zonder verified_end of overgenomen usage. Malformed payloads
worden evenmin geverifieerd. De bestaande journal `_reply_data` is read-only
gecontroleerd en toegevoegd aan de bronseal.

32 gerichte tests groen, ruff groen. De nieuwe regressie varieert elk van de
vijf velden met zowel ontbrekend als afwijkend, bewaart tekst/persoon bij de
verkeerde-beurtgevallen en controleert daarnaast intact bewijs. Bestaande
partial/unknown/timeout/fixture- en EV-001-tests blijven groen.

Native voorcontrole op exact correctiecheckpoint:
`/home/administrator/project/.eva2-artifacts/cp001-native-binding-fix-20260912/receipt.json`.
Native `.eval` SHA-256:
`c72bf64e5f8e0d49961f773941eee1e8cb7bd05978493e9cb36ac27fd4b62742`.
Acht unknown-samples, 15 verzegelde bronbestanden, modelgebruik leeg,
budgetvelden null, geen aggregatie. Het log is afzonderlijk teruggelezen.
Geen runtimewijziging of product/modelcall uitgevoerd.

De authenticiteit en queryscope van de geïnjecteerde reader blijven afzonderlijke
admissionvoorwaarden. Deze correctie voorkomt dat tegenstrijdige payloads toch
als bewijs worden geaccepteerd; zij bewijst geen native live-readertoegang.
Herreview door dezelfde onafhankelijke reviewer is gevraagd; alle eerdere
live-/budget-/profiel-/herstelblockers blijven ongewijzigd.
