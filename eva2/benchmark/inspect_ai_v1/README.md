# Inspect AI-evalomgeving

Deze map is alleen voor eerlijke productvergelijkingen. Zij wordt niet in
Eva's runtime-image geïnstalleerd en voegt geen productfunctionaliteit toe.

`pylock.toml` legt voor Linux/amd64 en Python 3.12 exact één wheel en SHA-256
per evaluatiedependency vast. De directe keuzes zijn Inspect AI `0.3.263` en
de officiële OpenAI-SDK `3.6.0`. Een volgende release wordt eerst opnieuw
gelockt en door dezelfde probe gehaald.

`bridge_probe.py` bewijst met Inspect's lokale `mockllm/model` twee routes:

- een Python-agent via de echte `agent_bridge()`;
- een stdlib CLI-agent in een netwerkloze, vooraf gebouwde container via de
  echte `sandbox_agent_bridge()`.

Daarna leest de probe het oorspronkelijke `.eval`-log, scoort een kopie opnieuw
en controleert dat de SHA-256 van het origineel niet veranderde. Er worden geen
betaalde modelcalls uitgevoerd.

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
