# Restate-kandidaat

Deze kandidaat draait de verzegelde `EvaArchitectureEpisode/v1` Noor-flow als een
`EvaNoorWorkflow`, met `episode_id` als workflow-ID. Iedere model-, lees- en effectgrens is een
Restate `ctx.run_typed` en gebruikt rechtstreeks de gedeelde functies en modellen uit `common.py`.

De externe effecten hebben bewust geen frameworkclaim van "exactly once". De enige effectroute
is de localhost-fixture. `effect_intent` levert een stabiele idempotency-key;
`execute_or_reconcile` leest eerst terug, voert zo nodig uit en leest na acceptatie nogmaals terug.
Wanneer de SDK direct na externe acceptatie hard crasht, wordt de niet-bevestigde `ctx.run_typed` opnieuw
uitgevoerd en reconcilieert die tegen dezelfde fixture-key. De fixture-evidence moet voor iedere
operatie precies een opgeslagen effect en precies een POST tonen. Ook de gedeelde
`run_deterministic_model(contract, fixture)` schrijft zijn modelcall naar de fixture; replaybewijs
vereist daardoor zichtbaar exact een modelcall.

De gedeelde effectbroker verwerkt fixture-`429` binnen dezelfde durable effectstap, controleert
header en body, respecteert de bewezen retrygrens en begrenst het aantal pogingen. Een ongeldige of
uitgeputte rate-limit en een definitieve `422` worden door de Restate-lane terminal gemaakt; Restate
start daarvoor dus geen tweede blinde retrycyclus. Een verbroken verbinding na externe commit blijft
wel retrybaar, zodat dezelfde effectstap eerst via readback reconcilieert.

Een definitieve `422` zelf wordt als succesvol gejournaled domeinresultaat naar de workflow
teruggebracht. De workflow retourneert dan `EvaArchitectureFailureOutcome/v1` met uitsluitend de
reeds extern bewezen receipts en de ongewijzigde gedeelde `render_definitive_failure_reply`. Daardoor
doet een replay van zo'n deelresultaat geen connectorcall en meldt de reply nooit een niet-geaccepteerd
effect als gelukt.

## Reproduceren

Vereisten zijn Python 3.12, Docker en curl. Er wordt geen Eva-service of productdatabase geopend.

```bash
python3 -m venv /tmp/eva-restate-venv
/tmp/eva-restate-venv/bin/pip install -r requirements-restate.txt
```

### Gemeenschappelijke invocation-CLI

De invocation-ingang heeft exact de drie vereiste argumenten en schrijft precies een canonieke
`EpisodeOutcome`-JSON-regel naar stdout. Een bewezen definitieve connectorafwijzing is een canonieke
`EpisodeFailureOutcome`-JSON-regel; technische fouten geven een niet-nul exitstatus:

```bash
STATE=/tmp/eva-restate-noor
FIXTURE=http://127.0.0.1:18765

# Start of hervat de gepinde server en SDK-worker. De fixture moet al draaien.
/tmp/eva-restate-venv/bin/python restate_runtime.py start \
  --state-dir "$STATE" --fixture "$FIXTURE"

/tmp/eva-restate-venv/bin/python restate_candidate.py \
  --state-dir "$STATE" \
  --fixture "$FIXTURE" \
  --contract contract/episode-noor-v1.json

# Stopt uitsluitend de worker en unieke Restate-container; state blijft restartbaar onder /tmp.
/tmp/eva-restate-venv/bin/python restate_runtime.py stop --state-dir "$STATE"
```

`restate_runtime.py start` bindt een state-dir bij de eerste start aan vaste lokale poorten, de
fixture-URL, imagepin en stabiele Restate-node-identiteit. Een latere `stop`/`start` met dezelfde
state-dir hergebruikt het Restate-volume en de geregistreerde workflow. De invocation-CLI gebruikt
eerst Restate `send` en daarna workflow `attach`; daardoor kan dezelfde CLI na een afgebroken
clientproces opnieuw aan een al geaccepteerde workflow koppelen.

Een harde crash na externe acceptatie is met twee terminals reproduceerbaar:

```bash
# Terminal A: start een worker die eenmaal hard crasht zodra calendar.create extern is geaccepteerd.
EVA_BAKEOFF_CRASH_AT=calendar.create:after_remote_accept \
  /tmp/eva-restate-venv/bin/python restate_runtime.py start \
  --state-dir "$STATE" --fixture "$FIXTURE"

/tmp/eva-restate-venv/bin/python restate_candidate.py \
  --state-dir "$STATE" --fixture "$FIXTURE" \
  --contract contract/episode-noor-v1.json

# Terminal B, nadat de worker met 86 is gestopt: herstart zonder crashinjectie.
/tmp/eva-restate-venv/bin/python restate_runtime.py start \
  --state-dir "$STATE" --fixture "$FIXTURE"
```

De wachtende invocation in terminal A eindigt dan met exact `EpisodeOutcome` JSON. De
`calendar.create`-receipt heeft `reconciled: true`; fixture-evidence bevat nog altijd een modelcall,
drie effecten en een effect-POST per operatie.

### Zelfcontrolerende wegwerpscenario's

De volgende helper maakt voor iedere run alle scenario-opslag en logs in een unieke map onder
`/tmp`, valideert outcome en fixture-evidence, en ruimt processen, container en data weer op:

```bash
# Een regel op stdout: exact EpisodeOutcome JSON.
/tmp/eva-restate-venv/bin/python run_restate_scenarios.py happy

# Crasht de SDK met exit 86 na calendar.create-acceptatie, start dezelfde endpoint opnieuw,
# laat de lopende Restate-workflow hervatten en print exact EpisodeOutcome JSON.
/tmp/eva-restate-venv/bin/python run_restate_scenarios.py crash-restart

# Beide scenario's achter elkaar; een canonieke EpisodeOutcome JSON-regel per scenario.
/tmp/eva-restate-venv/bin/python run_restate_scenarios.py all

# Tweede identity-pack, vanuit een nieuwe state-dir/fixture en zonder bron- of configuratiepatch.
/tmp/eva-restate-venv/bin/python run_restate_scenarios.py happy \
  --contract contract/episode-noor-mira-v1.json
```

Beide runners gebruiken uitsluitend `docker.restate.dev/restatedev/restate:1.7.2`. Registratie wordt
geweigerd als de server geen versie `1.7.2` rapporteert of discovery geen SDK-versie `1.0.4`
rapporteert. Met `--keep-tmp` blijven de wegwerpbare logs en Restate-data voor inspectie onder
`/tmp` staan; zonder die optie worden ze verwijderd. De container schrijft met de uid/gid van de
aanroepende gebruiker, zodat ook de bind-mounted Restate-data zonder rootresten opruimbaar blijft.

De service kan ook los worden gestart. Omdat een Restate-container de host moet kunnen bereiken,
luistert dit voorbeeld op alle interfaces; de fixture-URL zelf wordt door de kandidaat strikt tot
een expliciete localhost-http-URL beperkt.

```bash
EVA_BAKEOFF_FIXTURE_URL=http://127.0.0.1:18765 \
  /tmp/eva-restate-venv/bin/python restate_service.py \
  --fixture http://127.0.0.1:18765 --host 0.0.0.0 --port 19080
```

Officiële referenties: [Python services](https://docs.restate.dev/develop/python/services),
[durable `run`-acties](https://docs.restate.dev/foundations/actions),
[Python serving](https://docs.restate.dev/develop/python/serving),
[deploymentregistratie](https://docs.restate.dev/admin-api/deployment/register-deployment) en
[lokale Docker-installatie](https://docs.restate.dev/installation).
