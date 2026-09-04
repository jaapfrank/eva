# Inspect AI-evalomgeving

Deze map is alleen voor eerlijke productvergelijkingen. Zij wordt niet in
Eva's runtime-image geïnstalleerd en voegt geen productfunctionaliteit toe.

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
gebruikt. EV-002 mag pas daarna de vijf deelnemers met dezelfde taak, rechten
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

`ev002_baseline.py` heeft bewust nog geen uitvoermodus. Het kan alleen het plan
controleren en bestaande lokale Git-/Dockerbytes bekijken:

```bash
python3 benchmark/inspect_ai_v1/ev002_baseline.py --check-plan
python3 benchmark/inspect_ai_v1/ev002_baseline.py --preflight \
  --source-root openclaw=/pad/naar/openclaw \
  --source-root hermes=/pad/naar/hermes
```

Een ontbrekende of verkeerde bron, tag, commit of image maakt de voorcontrole
rood vóór een modelcall. Pas een afzonderlijk beoordeelde wijziging mag de
Inspect-run toevoegen. De vier originele logs, traces en gebruiksgegevens gaan
dan naar het vooraf vastgelegde private artifactpad.
