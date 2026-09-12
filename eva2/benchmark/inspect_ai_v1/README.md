# Inspect AI-evalomgeving

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
