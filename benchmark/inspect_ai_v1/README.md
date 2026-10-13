# Inspect AI-evalomgeving

## Independently sealed final cases — 4 October 2026

[`ev003_candidate_freeze_20261004.json`](ev003_candidate_freeze_20261004.json)
binds the installed candidate and original protocol.
[`ev003_dataset_commitment_20261004.json`](ev003_dataset_commitment_20261004.json)
binds 72 independently curated cases: 12 per original family, with the same cases
in three epochs and fresh state per epoch. Public metadata contains schemas,
resource types and hashes; private input, fixtures and independent oracles are
separate evaluator-owned files inaccessible to the ordinary builder account.
Do not open private payloads while developing Eva or tune against the final set.

Structural validation is complete; every result remains unscored. This is not
execution admission or a new product feature. Before final execution, bind the
existing Inspect loader/scorers, native fixture and independent effect readers,
family and aggregate budgets, run order and controlled evidence retention.
Use known development evidence for calibration, never hidden answers. Audio cases
require actual microphone/playback assets and physical listening, not text
substitutes. The two human raters and family acceptance remain human judgments.
Missing participant access stays unknown; it cannot become a competitor loss.

The first native Agent Zero subscription attempt is retained in
[`p1-final-dataset-agentzero-attempt-20261004.json`](../../evidence/product/p1-final-dataset-agentzero-attempt-20261004.json).
It produced no valid answer and has incomplete dispatch/usage coverage. A narrow
zero counter is not zero cost. The corrected adapter candidate and mocked checks
do not admit another inference run or turn that failure into a competitor loss.

## Native scenario calendar — 4 October 2026

P2 uses the existing native workers with an optional evaluator-only calendar.
`calendar-requirements.txt` pins `time-machine`3.5.1 with the official CPython3.11
(Hindsight) and3.12 (core) wheel hashes. Install it only into a private dependency
directory for the matching interpreter and mount that directory read-only in the
worker; do not install it into Eva's production image or the global interpreter.
The standard Inspect lock and the product candidate remain unchanged.

`EvaScenarioCalendar/v1` contains only `generation` and `offsetMilliseconds` in
addition to its contract field. The trusted controller publishes it by atomic
rename. It must independently verify reply, native memory and provider settlement
before advancing, then await every worker's generation receipt before dispatching
the next input. Both workers receive the same ticking calendar; parent budgets,
monotonic duration, native TLS and credential-expiry checks retain actual time.
Node accepts `calendar_refresh`; Python uses `SIGUSR1` and a private receipt file.
These optional flags provide no execution permission and do not read hidden oracles.

Normal retain/recall receives explicit event/query dates; native SQL operation
bookkeeping remains actual time. Optional ingest-age filters and mental-model
refresh are outside this proven clock scope. See `finalDataset.nativeCalendarBinding`
in the participant manifest for exact evidence, failures and limitations. Remove
the optional calendar flags to retain the workers' original real-time behavior.

## Current participant inputs — 29 September 2026

`p1_participants_20260929.json` pins Eva2, Eva1/Bottie, OpenClaw, Hermes
and Agent Zero for the existing EV-003 protocol. Official release tags were
resolved to commit objects. Source pins do not establish runtime provenance
or native admission. No model comparison may start from this file alone.
P1.2 must bind equal access and per-task and aggregate limits before execution.
Historical EV-002 inputs and results remain unchanged. Missing access is
unknown, never a competitor loss.

The full existing acceptance matrix contains 864 samples for Eva2 and three
competitors, or 1,080 if Eva1 runs every case too. These are sample counts, not
model calls or prices. Do not launch the full matrix during participant setup.
Use existing small development cases for admission; final unseen cases remain
independently curated after candidate freeze.

Deze map is alleen voor eerlijke productvergelijkingen. Zij wordt niet in
Eva's runtime-image geïnstalleerd en voegt geen productfunctionaliteit toe.

Voor nieuwe vergelijkingen geldt het compacte
[EV-003-meetprotocol](../../design/product/comparable-agent-benchmarks-v1.md#ev-003--wanneer-telt-verbetering).
Het legt vooraf taakfamilies, herhalingen, menselijke beoordeling en winstgrenzen
vast. Het maakt geen nieuwe runner of resultaatformaat: Inspect blijft eigenaar
van logs en scores. De concrete ongeziene dataset en gelijke runconfiguratie
moeten vóór uitvoering nog onafhankelijk worden vastgezet. EV-002 hieronder
blijft ongewijzigde historische nulmeting, geen vierwegwinst.

`pylock.toml` legt met PEP 751-velden voor Linux/amd64 en exact Python 3.12
één wheel en SHA-256 per evaluatiedependency vast. Inspect AI `0.3.263` blijft uitsluitend een
evaluatiedependency. De officiële OpenAI-SDK `3.6.0` zit al in Eva's runtime;
de eval-lock pint hem opnieuw om de echte adaptercompatibiliteit te bewijzen.
Een volgende release wordt eerst opnieuw gelockt en door dezelfde probe gehaald.

`bridge_probe.py` bewijst met Inspect's lokale `mockllm/model` twee routes:

- een Python-agent via de echte `agent_bridge()`;
- een stdlib CLI-agent in een netwerkloze, vooraf gebouwde container via de
  echte `sandbox_agent_bridge()`.

Daarna leest de probe het oorspronkelijke `.eval`-log, scoort een kopie opnieuw
en controleert dat de SHA-256 van het origineel niet veranderde. Er worden geen
betaalde modelcalls uitgevoerd. De opgegeven `--source-commit` moet bovendien
gelijk zijn aan `git rev-parse HEAD`. De werkelijk uitgevoerde probe- en
lockbytes moeten bytegelijk zijn aan hun Git-blobs; commit, tree en beide
bestandshashes komen in het receipt. Een willekeurige hash of een gewijzigde
tracked bron wordt geweigerd voordat de evaluatie start.

De probe inventariseert containers, netwerken en volumes direct vóór en na de
sandboxrun. Alleen als er geen nieuw Dockerobject achterblijft ontstaat een
passing receipt; beide inventarissen, hun hashes en de delta staan in datzelfde
private bewijs. `sandbox_cleanup=True` is daarmee niet de enige opruimclaim.

De dependencycontrole kan in de gewone ontwikkelomgeving draaien:

```bash
python3 benchmark/inspect_ai_v1/bridge_probe.py --check-lock
pytest -q tests/benchmark/test_inspect_ai_ev001.py
ruff check benchmark/inspect_ai_v1/bridge_probe.py \
  tests/benchmark/test_inspect_ai_ev001.py
```

De fysieke probe draait bewust in een tijdelijke, geïsoleerde omgeving waarin
de gelockte wheels zijn geïnstalleerd. Resultaten gaan naar een nieuwe map
buiten Git:

```bash
EVAL_ROOT=$(mktemp -d /tmp/eva2-inspect.XXXXXX)
python3 -m venv "$EVAL_ROOT/venv"
"$EVAL_ROOT/venv/bin/pip" install --only-binary=:all: \
  'inspect-ai==0.3.263' 'openai==3.6.0'
"$EVAL_ROOT/venv/bin/python" benchmark/inspect_ai_v1/bridge_probe.py \
  --output "$EVAL_ROOT/result" \
  --source-commit "$(git rev-parse HEAD)"
```

De lockchecker en het receipt tonen welke exacte dependencyset werkelijk is
gebruikt. EV-002 mag pas daarna de vier deelnemers met dezelfde taak, rechten
en budgetten vergelijken.

## EV-002: eerst een kredietloze voorcontrole

`ev002_baseline_plan.json` beperkt de eerste actuele nulmeting tot Eva en de
drie concurrenten die de eigenaar expliciet heeft genoemd: OpenClaw, Hermes en
Agent Zero. OpenBot blijft een nuttige browser- en dashboardreferentie, maar
krijgt geen verzonnen vijfde resultaat zolang er geen reproduceerbare lokale
startroute is.

De eerste nulmeting herhaalt uitsluitend gesprekssituatie C04. Zij is de
baseline voor de eerstvolgende gesprekkaarten, niet het bewijs van brede
cross-domainwinst. Iedere deelnemer krijgt één verse conversatie, hetzelfde
model en reasoningniveau, geen tools, geen retry en maximaal twee modelcalls.
Daarmee zijn hoogstens acht deelnemercalls mogelijk; er is geen betaalde
modeljury.

`ev002_baseline.py` heeft bewust geen uitvoermodus. Het kan alleen het plan
controleren, bestaande lokale Git-/Dockerbytes bekijken en achteraf één
begrensd resultaat controleren:

```bash
python3 benchmark/inspect_ai_v1/ev002_baseline.py --check-plan
python3 benchmark/inspect_ai_v1/ev002_baseline.py --preflight \
  --source-root openclaw=/pad/naar/openclaw \
  --source-root hermes=/pad/naar/hermes
python3 benchmark/inspect_ai_v1/ev002_baseline.py --check-result \
  --result /pad/naar/ev002-result.json
```

Een ontbrekende of verkeerde bron, tag, commit of image maakt de voorcontrole
rood vóór een modelcall. Pas een afzonderlijk beoordeelde wijziging mag de
Inspect-run toevoegen. De vier originele logs, traces en gebruiksgegevens gaan
dan naar het vooraf vastgelegde private artifactpad.

Eva krijgt daarvoor geen tweede benchmarkruntime. De bestaande schone
wegwerpstack accepteert nu optioneel één regulier UTF-8-promptbestand. Zij start
met verse PostgreSQL- en Hindsight-data, laat Home Assistant-effecten uit,
controleert het duurzame antwoord en weigert tools, meer dan twee
modelpogingen of een overschreden tokenbudget. De stack wordt daarna volledig
opgeruimd. Zonder promptbestand blijft de oorspronkelijke vaste releaseprobe
ongewijzigd werken.

Een deelnemer die vóór de modelcall de gelijke nul-tools-, nul-retry- of
isolatiegrens niet kan afdwingen, krijgt geen versoepelde proef en geen extra
adapter. Het resultaat registreert dan een `admission_failed` met nul calls en
nul tokens. Een geslaagd antwoord moet zijn native gebruik, antwoordhash en de
drie losse semantische criteria bewaren. De validator accepteert geen ranglijst
of algemene winnaar; twee bruikbare antwoorden en twee technische uitvallen
blijven dus precies dat, geen verzonnen vierwegwinst.

## CP-001: uitvoerbare offline gepaarde proef

`cp001.py` voert twee taken elk tweemaal uit voor de labels Eva2 en Eva1/Bottie,
in vooraf afgewisselde volgorde. Dit is **alleen een runner-dry-run**: beide
labels gebruiken dezelfde expliciete `cp001_fixture_worker.py` testdouble.
Er bestaat geen model-, product-, Docker- of netwerkstartoptie. De antwoorden
van de testdouble zijn bewust vast. Een groene fixturecheck bewijst de
uitvoerketen, geen kwaliteit van Eva of Bottie en geen concurrentiewinst.

De runner hergebruikt Inspect 0.3.263, de bestaande EV-001-lock en
bronverificatie uit `bridge_probe.py`. Inspect bezit samples, scoring en
native `.eval`-logs; er is geen tweede benchmarkformaat of productruntime.
De taakinput, rubric, volgorde en gemeenschappelijke offlinegrenzen staan in
`cp001_fixtures.json`. Git commit/tree plus SHA-256 van runner, worker, fixtures,
EV-001-bron en lock worden vóór uitvoering vastgelegd; gewijzigde bronbytes
worden geweigerd. De outputmap moet nieuw zijn en wordt privé aangemaakt.

Gebruik een omgeving met alle wheels uit `pylock.toml` (bestaande EV-001-omgeving
of een afzonderlijke hashgecontroleerde installatie); geen runtime-dependencywijziging.
Vanuit `eva2`, in de exact gecommitteerde checkout:

```bash
/path/to/locked-venv/bin/python benchmark/inspect_ai_v1/cp001.py \
  --dry-run --source-commit "$(git rev-parse HEAD)" \
  --output /duurzaam/prive/cp001-eerste-run
python3 -m unittest discover -s tests/benchmark -p test_cp001_runner.py -v
```

`--fault unavailable`, `--fault failure` en `--fault timeout` voeren dezelfde
matrix uit met zichtbare toelatings-/uitvoerings-/tijdsfouten. Gebruik per run
een nieuwe map. Er is geen automatisch opnieuw proberen. De `unknown`-score
bij uitval heeft geen aggregatiemetric en wordt geen verlies of nulscore.
Ook alle echte productuitslagen blijven `unknown`. `receipt.json` verwijst met
hash naar het oorspronkelijke native log en naar het vooraf geschreven manifest;
alle samplemetingen en criteria staan in dat Inspect-log, niet in een extra
scorebord. De afzonderlijke samplemappen bewaren bronbestanden, workeruitvoer,
geheugenfixture en het teruggelezen advies.

Taak A start per beurt een nieuw fixtureproces; de laatste beurt krijgt alleen
de nieuwe vraag en gebruikt het bewaarde correctieresultaat. Taak B beëindigt
de worker na opslaan maar vóór bevestiging (exit 75), start een nieuw proces en
controleert adviesinhoud, hash vóór/na hervatten, één mutatie en bronbehoud.
Dit is een procesonderbrekingsfixture, geen bewijs van product-/machinecrashherstel.
De runner leest bestanden onafhankelijk van het antwoord terug. De exacte
fixture-oracle vervangt geen semantische of menselijke beoordeling van echte
antwoorden; menselijke rubricvelden blijven leeg.

Registratie omvat afzonderlijke ontvangstreactie, eerste inhoud en geverifieerd
einde (milliseconden vanaf samplestart), plus verstreken tijd bij fout.
Een ontbrekende ontvangstreactie is `null`. Verbruik van de fixture is bewezen
nul model-/token-/helper-/retry-/jurycalls; geld en logininspanning blijven
onbekend. Nul tokens is uitsluitend de opgelegde offlinegrens, geen geschat
productverbruik. Alle processen krijgen dezelfde byte-/fase-/tijdgrenzen en
schone samplemappen. De testdouble is vertrouwde lokale code, geen sandbox voor
willekeurige third-party agentcode. De vaste bestandswrites zijn beperkt door de
fixture; deze runner levert geen generieke filesystem- of OS-resourcebeveiliging.

Echte vergelijking blijft vervolgwerk: bind beide native bron/image/configversies,
laat gelijke model- en toolbudgetten aantoonbaar toe en verbind bestaande native
routes aan Inspect. De huidige `experiments/managed-b-clean-graph-v1/probe.py`
heeft een single-promptprobe; die bewijst geen meergesprek-/hervatroute voor deze
twee taken. Een native Bottie-route is hier niet toegelaten. Daarom start deze
levering geen van beide en maakt zij CP-001's volledige productmatrix niet groen.

Officiële documentatie gecontroleerd op 12 september 2026:
[Agent Bridge](https://inspect.aisi.org.uk/agent-bridge.html) en
[Custom Scorers](https://inspect.aisi.org.uk/custom-scorers.html). De lokale
exact gelockte API blijft de uitgevoerde compatibiliteitscontrole; deze raadpleging
claimt geen upgrade of nieuwste-versiestatus. Onafhankelijke technische review
moet deze begrensde runner beoordelen vóór integratie; model-/liveproeven vallen
buiten deze levering.

## CP-001 native adapters: alleen offline contract en voorcontrole

`cp001_native.py` vertaalt dezelfde taaktekst naar bestaande productingangen:
EVA2 `/internal/v1/turns/respond` en Bottie `/api/chat`. Er is geen nieuwe
modelprovider, server, resetfunctie, retrylus of netwerktransport. De lage
`exchange(..., post=..., readback=...)`-poort is uitvoerbaar met de bestaande
HTTP-/durable-readbackfuncties als toekomstige, beoordeelde injecties. De tests
leveren uitsluitend lokale testfuncties; geen productserver wordt aangeroepen.

Native broncontracten worden read-only gehasht en bij de CLI aan Git gebonden:

```bash
/path/to/locked-venv/bin/python benchmark/inspect_ai_v1/cp001.py \
  --native-preflight --source-commit "$(git rev-parse HEAD)" \
  --output /duurzaam/prive/cp001-native-voorcontrole
pytest -q tests/benchmark/test_cp001_native.py \
  tests/benchmark/test_cp001_runner.py tests/benchmark/test_inspect_ai_ev001.py
```

Deze modus schrijft acht native Inspect-samples met de echte requestmapping,
bronhashes en admissionblockers. Alle scores blijven unknown, budgetvelden en
productgebruik null. Fixturetargets worden verwijderd; geen fixture-oracle
beoordeelt native antwoorden. Er is bewust geen `--live`-schakelaar. Een
configuratieboolean kan de ontbrekende toelating niet groen maken.

Het adapterpad verifieert de antwoordidentiteit en EVA2-antwoordhash. Een
apart ingelezen durable antwoord moet voor EVA2 verplicht dezelfde message_id,
conversation_id, principal_id, tekst en teksthash bevatten. Ontbrekende of
afwijkende velden falen; dit bewijst integriteit,
geen semantische kwaliteit. Vrij geformuleerde antwoorden zijn toegestaan;
mensen beoordelen de vooraf beschreven rubric. De EVA2-conversationreceipt
registreert gemeten of gedeeltelijk gemeten tokens/calls afzonderlijk van
**onbekend totaalverbruik**. Botties publieke antwoord heeft geen gekoppelde
usage-receipt; een toegevoegd `usage`-veld wordt niet als native bewijs gebruikt.
Geen onbekende kosten worden nul.

Gelijke byte- en clientdeadlinegrenzen gaan naar de transportinjectie. Een
overschrijding wordt geregistreerd; de injectie moet de deadline en maximale
responsebytes tijdens I/O daadwerkelijk afdwingen. De bestaande managed-B
`_http_json` heeft een vaste 120s-timeout en mag niet ongemerkt als deze injectie
worden gebruikt. Geen van deze clientchecks stopt aantoonbaar achtergrondwerk
op de productserver. Daarom zijn model-/tool-/retrylimieten, volledige usage,
serverquiescence en disposable-profielbinding nog harde admissionblockers.
Een time-out krijgt `timeout_execution_unknown`, geen herkansing of verlies.

Taak A gebruikt bij de derde beurt een nieuw conversation-ID bij dezelfde
synthetische identiteit. Dit reset geen geheugen. Iedere echte sample vereist
nog een afzonderlijk schoon productprofiel; een nieuw ID bewijst dat niet.
Taak B behoudt de sessie bij hervatting, maar identieke onderbreking na save,
actiereceipts en onafhankelijk bestandreadback zijn nog niet native toegelaten.
De adapter start of herstart geen dienst en leest/wist geen privégegevens.

Bronnen voor de wiremapping: de bestaande managed-B-probe, EVA2 API en
`grounded_reply_wire`, plus Botties `server.js`, `access.js`, `requestidentity.js`,
`httpingress.js` en `llm.js`. Geen nieuwe externe dependency of upstreamversie
gekozen. Rootreview en bewijs van de genoemde native garanties gaan vóór een
latere echte productproef.


## Native EVA-ingress + echte PostgreSQL-journal (offline contractproef)

`tests/benchmark/test_cp001_native_postgres.py` verbindt `exchange` met de echte
FastAPI `create_app`, `PostgresHindsightMemoryRuntime.respond`, `LivingConversation`
en `PostgresConversationJournal`. Alleen model en semantische geheugenprovider
zijn stubs. De HTTP-response wordt niet geconstrueerd door de test. Een tweede
SQLAlchemy-engine leest `REPLY_COMMITTED` rechtstreeks uit PostgreSQL, in een
read-only transactie met statement-timeout en exacte tenant/installatie/message-
querybinding. De adapter controleert daarna de payloadbinding en hash.

De bestaande `people_memory_engine`-fixture verzorgt lege-DB-controle,
PostgreSQL 16.13-versiecheck, migraties en teardown. Gebruik uitsluitend een
nieuwe geïsoleerde database met prefix `eva2_4b_disposable_`; een productdatabase
is niet toegestaan. De uitgevoerde proef gebruikte de behouden lokale PG16.13-
image, `--network none`, tmpfs-data en een private Unix-socket. TestClient gebruikt
geen HTTP-listener. De volledige runtimebootstrap en externe geheugendienst
worden niet gestart. Het geheugen-submitpunt is een noop; daadwerkelijke
semantische opslag/recall is geen claim van deze proef.

Vanuit `eva2`, met de bestaande runtime-testomgeving en disposable DSN:

```bash
PYTHONPATH=eva2/src:. EVA2_TEST_POSTGRES_DSN='postgresql+psycopg://postgres@/eva2_4b_disposable_cp001?host=/private/socket' \
  CP001_PG_EVIDENCE_DIR=/private/new-evidence \
  /path/to/runtime-test-python -m pytest -q tests/benchmark/test_cp001_native_postgres.py
```

De optionele evidence-map moet vooraf bestaan; per test wordt een nieuw bestand
exclusief aangemaakt met echte lokale HTTP-antwoorden en uit de DB gelezen
payloads. Het testresultaat blijft pytest-evidence, geen nieuw benchmarkscorebord.

Vier controles: intacte reply/usage en replay zonder tweede modelstubcall;
weigering van hetzelfde message-ID in een andere conversatie; echte journal-
historie in dezelfde sessie maar niet in een nieuwe sessie; en afwijzing van een
verkeerde echte journalrij met dezelfde eigenaar én antwoordtekst. De getallen
23/7 tokens komen uit de gecontroleerde modelstub en bewijzen alleen dat de echte
journal die velden bewaart. Zij meten geen productmodelkosten. Bottie, model-
kwaliteitswinst, volledige usage en native liveadmission blijven open.

## Native Bottie-route + echte SQLite-readback (offline contractproef)

`test_cp001_native_bottie.py` verbindt dezelfde Python-adapter met de echte
Bottie `/api/chat`, agentverwerking, modeltransportlaag en SQLite HTTP-journal.
De kleine `cp001_bottie_worker.mjs` hergebruikt het gecontroleerde Responses-
modelpatroon uit `test/web-agent-ingress.test.js`. Alleen provider-Responses zijn
stubs; HTTP-antwoorden of journalrijen worden niet door de test geconstrueerd.
Alle externe fetches zijn afgesloten. Iedere worker krijgt een aparte Linux
user/network namespace met uitsluitend loopback, lege private tijdelijke
SQLite-data en een beperkte omgeving zonder productiecredentials.

De echte route maakte een ontbrekend contractdetail zichtbaar: een
`client_ack_public_key` is verplicht. `request_for` accepteert die nu optioneel
voor Bottie. De fixture genereert hiervoor een P-256-publieke sleutel via Node
crypto, zoals de bestaande HTTP-tests. Zonder sleutel geeft de echte route
HTTP 400 vóór een modelstubcall. De native voorcontrole blijft zonder toegelaten
sleutel/ACK-route unknown. Een opgeslagen antwoord is hier **geen bevestigde
gebruikersaflevering**: de proef voert geen client-ACK uit.

Een onafhankelijke Python `sqlite3`-verbinding opent de database read-only,
selecteert de persistente response via channel/conversation/request, en
controleert de opgeslagen public_body_sha256. Daarna controleert de adapter
de antwoordbinding. De SQLite-data wordt na afsluiten van de Node-worker opnieuw
geopend; met optionele evidence-map blijven snapshots en echte readbacks behouden.

Vanuit `eva2`, met Node >=22.22 en de bestaande lokaal beschikbare Bottie npm-
dependencies in de checkout (geen symlink-root; runtime-herkomstcontrole weigert
symbolische omleiding):

```bash
CP001_BOTTIE_EVIDENCE_DIR=/private/new-evidence \
  python3 -m pytest -q tests/benchmark/test_cp001_native_bottie.py
```

De evidence-map moet vooraf bestaan. Tests dekken intacte reply/persistente
replay zonder extra stubcalls, verplichte ACK-key, conflict bij veranderde
tekst met dezelfde requestscope en weigering van een verkeerde echte rij uit
een andere sessie met hetzelfde antwoord. De originele SQLite-scope bestaat uit
channel + conversation + request; deze wordt niet tot EVA2's andere
message-scope omgedefinieerd.

Providerverbruik, totale model/toolcalls en runtimebudgetten zijn niet beschikbaar
in deze publieke reply. Ze blijven null/unknown; fixture-stubcalltellingen zijn
diagnostiek, geen gemeten productmodelkosten. Geen Bottie-productiecode is gewijzigd.
Dit contractbewijs en het eerdere EVA2/PG-bewijs vormen nog geen gelijkwaardige
productvergelijking, geheugenkwaliteit, volledige ACK/recovery-proef of winstclaim.

### Afzonderlijke lokale P-256 client-ACK-proef

De eerdere opslagproeven versturen geen ACK. De aanvullende test
`test_bottie_actual_p256_ack_deduplicates_and_rejects_wrong_key_or_hash` gebruikt
nu de echte lokale `/api/http-delivery/ack` met het bestaande Bottie-protocol.
De worker bewaart de echte responsebytes/writeheaders, gebruikt
`canonicalHttpWireSha256` en `canonicalHttpClientAckPreimage` uit `src/web/httpack.js`
en ondertekent met de P-256-fixturesleutel. De privésleutel staat alleen in de private tijdelijke fixturemap (0600);
zij wordt na de test verwijderd en komt niet in evidence.
Er is geen nieuwe ACK- of cryptografische implementatie in productcode.

Vóór de geldige ACK worden een ACK met andere sleutel en een geldig ondertekende
ACK met gewijzigde responsehash/responseRef geweigerd. De onafhankelijke SQLite-
readback moet dan nog nul ACK-receipts en nul chat_out/assistant-registraties tonen.
Een geldige ACK geeft één persistente receipt, één assistentbericht en één chat_out.
Herhalen van exact die ACK geeft dezelfde receipt zonder dubbele registratie.
De proef heeft één chatrequest; ACK-verzoeken veroorzaken geen extra modelstubcalls.

Evidence bevat de echte ACK-HTTP-resultaten, scoped SQLite-snapshots, receipt-
inhoud/hash en tellingssnapshots. De database wordt ook na workerexit bewaard.
Dit bewijst bevestiging door de lokale cryptografische testclient, **geen menselijke
ontvangst, Jaap-acceptatie of live-aflevering**. Volledige usage, serverbudgetten,
herstel en productranking blijven unknown. Dezelfde testopdracht/evidence-optie
hierboven voert ook deze vierde Bottie-contracttest uit.


### Procescrash tussen prepared response en ACK

`test_bottie_process_crash_before_ack_replays_and_delivers_once` stopt de echte
Node-worker met SIGKILL na de HTTP-response en onafhankelijke SQLite-readback,
maar vóór een ACK. Er vindt geen graceful close of checkpoint door die worker
plaats. Dezelfde tijdelijke database inclusief WAL en de vooraf gefsyncte
P-256-clientsleutel blijven beschikbaar voor een nieuw proces in een nieuwe
private netwerknamespace. De test controleert verschillende proces- en namespace-
identiteiten en gelijke publieke sleutel en sleutelbestandhash.

Het nieuwe proces replayt hetzelfde opgeslagen antwoord; iedere poging tot
providergeneratie is daar een harde fixturefout. De teller blijft nul bij replay
en alle ACKs. Verkeerde sleutel en gewijzigde hash krijgen HTTP 400, zonder
afleverregistratie. Geldige en herhaalde ACK geven dezelfde receipt en precies
één assistentbericht en chat_out. Een onafhankelijke SQLite-readback controleert
de inhoud, hash en aantallen. Evidence bewaart beide procesidentiteiten, de
SIGKILL-status, HTTP-resultaten en database na normaal afsluiten van proces twee.

Deze vijfde Bottie-test bewijst uitsluitend herstel op de genoemde procescrashgrens
met behouden database en clientsleutel. Zij bewijst geen stroomuitval, OS-crash,
crash midden in een schrijfoperatie, herstel zonder sleutel of algemene recovery.
Serverbudgetten, volledige usage en productvergelijking blijven afzonderlijk unknown.


### Offline usage- en serverbudgetpreflight

Voer vanuit de repository uit:

```bash
python3 benchmark/inspect_ai_v1/cp001_accounting.py
python3 benchmark/inspect_ai_v1/cp001_accounting.py --exports /private/exports.json
```

De JSON-uitvoer bevat per ontbrekend/ongeldig usage- en limietveld een blocker,
bronbestandhashes en vergelijking van expliciete providergrenzen. Exitcode 2
betekent niet toegelaten (ook bij ongeldige invoer, dan alleen stderr). De bestaande
native Inspect-preflight bevat dezelfde accountingrapportage. Er is geen live-optie.

De optionele lokale JSON heeft uitsluitend deelnemers `eva2` en `eva1-bottie`.
Per deelnemer zijn dit de optionele exports, met ongewijzigde native veldnamen:

- `provider_request`: provider-wirebody (`model`, `reasoning.effort`,
  `max_output_tokens`). Dit zijn waargenomen grenzen van één call.
- `provider_response`: response-object uit `response.completed`, met
  `usage.input_tokens`, `output_tokens`, `total_tokens`. Ontbrekend blijft null;
  total_tokens moet exact input+output zijn. Dit bewijst geen andere calls/retries.
- Alleen EVA2: `request` en `journal_reply` uit de onafhankelijke readback. De
  bestaande native binding/hashcontrole leest `model_usage` en `provider`.
  `runtime_resource_budget` leest alleen de bestaande `max_input_tokens` en
  `max_output_tokens` als configuratie, zonder enforcementclaim.
- Alleen Bottie: `timeline_meta` uit één `llm_usage`-rij, uitsluitend diagnostiek.
  De producer normaliseert sommige ontbrekende providervelden naar nul en logfouten
  worden opgevangen. Daarom wordt deze rij nooit volledige gemeten usage.

Gebruik alleen expliciete, geschoonde lokale exports; geen credentials. De CLI
ontdekt geen hosts, databases of bestanden buiten de opgegeven export en vaste bronpaden.
Zij echoot geen prompts/antwoordteksten. Een export is geen geauthenticeerd bewijs:
herkomst, complete requestbinding en volledige dekking blijven aparte blockers.

Gelijke bekende model/reasoning/outputwaarden geven `equal`; afwijkingen `mismatch`;
ontbrekende waarden (ook null tegenover null) `unknown`. Provideroutputlimiet is
geen samplebudget. Input-, call-, tool- en deadlinegrenzen voor de hele sample,
servercancellation/quiescence, helper-/memory-/retrydekking en totaalusage zijn
niet door deze native exports geattesteerd. `total_usage` en `server_sample_limits`
blijven null en `live_enabled` false. Een aangeleverde `complete: true` of
`live_enabled: true` kan dat niet veranderen. Bestaande ACK-/herstelbewijzen krijgen
hierdoor geen nieuwe status en worden niet opnieuw getest als aparte randvarianten.


### Direct journal-admissionexport bij dezelfde preflight

De optionele EVA2-export kan nu `direct_journal` bevatten: `scope` met interne
PG tenant_id/installation_id, `source_files` met de vier exacte bronhashes uit
`cp001_journal_admission.SOURCES`, en `events` met onafhankelijke SQL-rijen
(`tenant_id`, `installation_id`, `event_type`, `payload`). `request` levert de
principal/conversation/message-binding. Alleen bestaande MODEL_ADMITTED,
MODEL_RECORDED en STOP_REQUESTED-events zijn toegestaan, inclusief hun bestaande
checkpointfase/status/revision. De test `tests/benchmark/test_cp001_journal_admission.py`
toont de exacte exportquery en draait één geïntegreerde PG-lifecycle.

`direct_journal_admission` meldt opgeslagen maximum_requests, admission-/terminal-/
onopgeloste aantallen, stopmarker en bekende directe usage. Bron-/scope-/identity-
mismatch, dubbele events of malformed checkpoints worden geweigerd. Onbekende
usage wordt nooit nul. `provider_attempt_contract` leest via Python AST de bestaande
UsageLimits-expressies uit de gehashte bron; de daadwerkelijke per-attempt runtime-
limieten blijven unknown wanneer de events die niet bevatten. Een preflight vóór
de eerste admission heeft nog geen duurzaam maximum_requests om uit te lezen.

Een export is geen geauthenticeerde of aantoonbaar volledige snapshot. Zelfs
stop plus alle terminale events bewijst geen externe providerquiescence; een
onopgeloste admission toont juist concreet de ontbrekende zekerheid. Deze beperkte
bron-/rowbinding opent geen livevergelijking en claimt geen helper/geheugentotaal.
De bestaande CLI `cp001_accounting.py --exports /private/native-exports.json`
blijft exitcode 2 geven. Er is geen nieuwe runtimepolicy of databasemigratie.


Dezelfde journalpreflight leest nu het optionele native terminalveld
`local_stream_closed: true`. `observed_local_settlement` geldt uitsluitend voor de
geëxporteerde directe pogingen: `settled` wanneer alle admissions terminal zijn en
hun lokale sluitobservatie True is, `unfinished` bij nog open admissions, anders
`unknown` (ook bij geen pogingen of een ontbrekende closeobservatie). Niet-booleaanse
of False-claims in het optionele native veld zijn ongeldig. `local_stream_closed_requests`
telt de geldige observaties. De providerbron maakt nu deel uit van de vereiste vier
bronhashes; oudere driehash-manifests moeten opnieuw uit de juiste bron worden geëxporteerd.

`remote_provider_quiescent` en `server_quiescent` blijven null, ook bij lokaal
settled. Snapshotauthenticiteit, helper/memorydekking en gelijke actieve constraints
blijven voorwaarden; de CLI blijft exit2/live_enabled false. De geïntegreerde
`test_actual_stream_events_drive_cli_local_settlement` gebruikt echte provider-/SDK-
closepaden met offline SSE, een echte private PG-journal en afzonderlijke CLI-processen
voor success, failed close, onvoltooide admission, stop en stopreplay.


### Isolated owner-reader receipt

De optionele readerroute is alleen een lokale benchmarkseam, geen geïnstalleerd
API-endpoint. `OwnerSnapshotReader` hergebruikt verplichte DashboardAccess-authenticatie,
de bestaande actuele private ownerbindingcontrole en PostgreSQL REPEATABLE READ /
READ ONLY. Scope en requestidentiteit zijn readerconfiguratie, geen browserfilters.
De reader selecteert alle rijen met die requestbinding en bindt ze aan het PG-
snapshotcursor, per-thread sequences, bronhashes, configuratiehash en optionele
imagemetadata. Private rijen blijven in de expliciete lokale bundle; receiptmetadata
bevat alleen hashes, aantallen en cursor. Schrijf geen echte private bundles naar Git.

Verifiëren in de bestaande projectvenv, met `PYTHONPATH=src` vanuit repositoryroot:

```bash
python benchmark/inspect_ai_v1/cp001_accounting.py \
  --reader-bundle /private/bundle.json --reader-trust /private/pinned-reader-trust.json
```

De trustfile wordt afzonderlijk door de operator gepind en bevat public_key,
challenge, scope_sha256 en de verwachte context. Een sleutel/boolean in de bundle
wordt niet als trustroot gebruikt. De implementatie hergebruikt EvaCanonicalJSON
en de bestaande Ed25519-verifier; er is geen eigen cryptografisch algoritme.
De ondertekening bindt de readerkey aan exacte snapshotinhoud en metadata, niet aan
een automatisch betrouwbare productie-installatie. De lokale proef heeft image_digest
null: geen live imageclaim. Een imagewaarde zou slechts aan de gepinde metadata
zijn gebonden, geen zelfstandig imageattestaat. Geldigheid van een bestaande receipt
betekent niet dat er intussen geen nieuw journalwerk bestaat; challenge en snapshot
moeten door de afnemer voor het bedoelde leesmoment gepind zijn. De bestaande live-
poort blijft false/exit2; volledige provider/helperdekking blijft unknown.

## Sealed EV-003 evaluation binding

`ev003_final_dataset.py` connects the existing final dataset to Inspect's public
`Sample` and `Score` contracts. It does not start a run or grant model access.
Load it in the trusted evaluator process with `load_final_dataset(commitment_path,
commitment_sha256=..., repo_root=...)`, using the pinned hash in
`p1_participants_20260929.json.finalDataset`. The loader checks the commitment,
candidate, protocol, private payload permissions and every case hash.

- `samples(epoch)` exposes the authorized initial input and public case/hash
  metadata only. Fixtures, expected answers and oracles are never sample targets.
- `with_fixture` and `with_oracle` supply private copies to trusted provisioning
  and evaluation callbacks. Keep these callbacks outside the participant process.
- `make_scorer(evidence_for, readback_hooks)` returns an Inspect scorer. Receipts
  must bind the actual run, case, epoch and retained evidence bytes. Hooks read
  independent native results and recorded human judgments; a participant's own
  success claim is insufficient.
- Task completion, individual human preference, initiative usefulness and usage
  remain separate. Missing adapter evidence is unknown. A verified admitted
  product failure is a failure even when its cost is unknown. Human preference
  can record a tie; initiative disagreement is conservatively not useful.

Reuse `cp001_native.exchange` for the existing Eva1/Eva2 transport and native
readback callbacks. `eva2_request_for_turn` serializes an already resolved native
`ConversationTurn`; it does not choose an actor, audience, session or timestamp.
`read_eva2_committed_reply` uses a separate read-only database connection and binds
the stored reply to tenant, installation, principal, conversation and message.
Pass this reader as the existing `exchange` callback. The legacy `request_for`
mapping only supports its original A/B development fixtures and is not the
final-dataset request builder.

The focused `test_resolved_multi_person_turns_use_native_scope_and_independent_readback`
case reuses the actual API/PostgreSQL fixture with two configured synthetic
`TelegramPrincipalBindings`, later-session history, cross-person scoping and replay.
Its model and semantic memory are stubs; it proves API/journal binding, not physical
identity verification or semantic recall. Native pairing aliases an additional
Telegram account to the confirming person; it does not create another person.
`read_eva2_memory_settlement` now reads the exact turn's outbox and retention
receipt, independently checks the stored Hindsight source document and waits for
its source facts to be consolidated plus all operations in the disposable scenario
bank to finish. A previous turn's completed operation is insufficient. Empty fact
extraction is allowed; no observation is fabricated or required. Use it only with
serial turns in the explicitly provisioned scenario databases. This observer does
not poll, retry, stop workers, grade memory quality or prove provider quiescence;
reuse the existing native worker and shared usage ledger for those responsibilities.

A two-turn offline check used the pinned native core and Hindsight images with
six synthetic provider responses: both source documents and consolidation completed,
while missing/wrong-person bindings remained incomplete. Zero real model calls.
Historical two-call development failures and their budgets are unchanged. See
`p1_participants_20260929.json#finalDataset.nativeMemoryBinding` for source/evidence
hashes and limitations. These are synthetic wiring results, not final scores.

Real shared/unknown-speaker authority, event clocks and allowed action resources
remain evaluator provisioning work before final execution.

The focused public tests use synthetic cases, not the sealed final questions:
`PYTHONPATH=eva2/src:. python -m pytest -q tests/benchmark/test_ev003_final_dataset.py`.
Use the existing pinned Inspect environment; no new dependency is introduced.

P1.2's original preparation condition is now completed by the independently
reviewed `ev003_execution_plan_20261004.json`: fixed tasks/scorers, finite equal
sample/family/aggregate ceilings and deterministic order. These are development-
grounded engineering ceilings, not observed complete consumption or expected
costs. The original two-attempt development results remain unchanged. The plan
hash in the participant manifest is frozen before final inference.

Actual native resource/provisioning/accounting callbacks remain P2.1 admission
requirements per participant/family. The plan does not admit a provider call or
automatically start the full matrix. Reuse the existing trusted Telegram binding
and household voice authority; no separate evaluation identity policy.
Audio requires actual audio resources and listening evidence; human preferences
require recorded independent judgments. Structural loading proves neither a
successful task nor competitive superiority. The sealed payloads remain unchanged.

## XD action and local service binding

The conversation worker still refuses native actions by default. Action cases
opt in with --action-control-file; the unchanged native core and optional
delegated service entrypoint retain task planning and execution. The parent
ParentProcessAccounting receives the identical Ev003ActionControl/v1 object
and action_deadline_monotonic bound to the original sample's wall deadline.
Its existing socket owns one counter for foreground actions, delegated actions
and protocol:structured_response consumption. A returned invocation is not an
external effect, a completed task or an independently verified result. Provider
attempts and token usage continue through the existing provider ledger.

The ev003_collection_fixture.py script serves one evaluator-owned, sealed generic
HTTP service per process. Use two separate configurations and state directories
for a two-service case. The fixture presents only schema discovery and collection
operations; it contains no task solution. The original fixture data, scopes and
capability token stay private. Durable effect readback is local to the evaluator,
never a participant HTTP endpoint. Source and configuration hashes, before/after
state and the append-only journal must accompany a native outcome.

These bindings alone do not admit an XD sample. Admission still requires the
pinned native tool route, an isolated reachable service/browser environment,
actual background-worker shutdown, complete shared accounting and independent
readback. An ordinary HTTP service is not an MCP endpoint. The existing browser
may be offered as its normal generic client, without instructions selecting a
solution. No native HTTP capability is added to a frozen participant to make it
pass. An absent execution adapter remains unknown, not a scored quality loss.

Focused checks: test_ev003_action_accounting.py and
test_ev003_collection_fixture.py. Relevant retained checks:
test_cp001_process_accounting.py and test_p1_subscription_binding.py.
