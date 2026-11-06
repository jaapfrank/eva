# Hindsight 1.2.0: reproduceerbare Eva-overlay

Deze map bevat de openbare bouwinvoer van de op 6 november 2026 beoordeelde
Hindsight-upgrade. Geen geheugendata, sleutels, privécriteria of binaire wheels.
De exacte upstreamcommit, bestaande lokale basisimage, 202 wheels en vier
gewijzigde bronbestanden staan in `source-build-manifest.json`. De MIT-licentie
van upstream staat ongewijzigd in `UPSTREAM-LICENSE`.

## Bouwen zonder installatie

Gebruik Linux x86_64, Git, Docker en een afzonderlijke CPython 3.11.18-bouwomgeving.
De behouden runtimebasis gebruikt Python 3.11.16; dit recept wijzigt die interpreter
niet. De basisimage moet lokaal aanwezig zijn: hij bevat bestaande Eva-integratie,
het bestandgeheim-entrypoint en de cachehelper. Een willekeurige officiële image
is geen vervangende basis. Reproduceerbaarheid vereist de vastgelegde basisimage.

Stel `RECIPE`, `UPSTREAM`, `WHEELS` en `OUTPUT` in op afzonderlijke absolute paden.
`OUTPUT` mag nog niet bestaan. Maak een schone checkout en bouw de eigen wheel:

```sh
git clone https://github.com/vectorize-io/hindsight.git "$UPSTREAM"
git -C "$UPSTREAM" checkout --detach 34a138684a899b6ec34175f7a2b633860466d289
python3.11 -m venv /absolute/path/hindsight-build-tools
. /absolute/path/hindsight-build-tools/bin/activate
python -m pip install --require-hashes -r "$RECIPE/build-toolchain.lock"
mkdir -p "$WHEELS"
uv --directory "$UPSTREAM" build --package hindsight-api-slim --wheel \
  --build-constraint "$RECIPE/build-toolchain.lock" --out-dir "$WHEELS"
python "$RECIPE/download_wheels.py" --wheelhouse "$WHEELS"
```

De eigen wheel moet SHA256
`dfc48839b656ebb42efd9334131744654b813663d78e7a8a337d3b23b7085275`
hebben. Dit is opnieuw bewezen met de vastgelegde bouwtools. De downloadhelper
haalt uitsluitend gehashte binaire dependencies op en installeert niets; de
volledige set moet exact de 202 bestanden uit het wheelmanifest bevatten.

Haal de twee oorspronkelijke bestanden uit een **niet gestart** tijdelijk
containerobject van de exacte basisimage. Dit leest geen volumes of geheimen:

```sh
BASE=sha256:ba9f050f49eeb50604c7793630c8b653dde728486eb744a7e30f3f42450f9388
CID=$(docker create --network none "$BASE")
docker cp "$CID:/app/api/hindsight_api/engine/memory_engine.py" /absolute/path/old-memory-engine.py
docker cp "$CID:/opt/eva2/hindsight_entrypoint.py" /absolute/path/old-entrypoint.py
docker rm "$CID"
python "$RECIPE/prepare_build.py" --upstream "$UPSTREAM" \
  --wheelhouse "$WHEELS" --old-engine /absolute/path/old-memory-engine.py \
  --old-entrypoint /absolute/path/old-entrypoint.py --output "$OUTPUT" --build-images
```

Controleer het entrypointpad tegen de basisimageconfiguratie vóór extractie;
`prepare_sources.py` accepteert uitsluitend de vastgelegde oorspronkelijke hash.
De voorbereider weigert een andere upstreamcommit, gewijzigde tracked bron,
verkeerde bronhashes of afwijkende wheelset. Alle Dockerbouwstappen hebben
`--network=none` en downloaden niets. De drie gemaakte images zijn runtime,
activatieprofiel en rollbackreferentie. Geen container wordt gestart en geen
bestaande dienst wordt vervangen. Docker-tijdstempels kunnen nieuwe image-ID's
opleveren; nieuwe images vereisen eigen verificatie en installatiebinding.

## Behouden gedrag en gerichte wijzigingen

- Native 1.2.0 vervangt de oude GC-aanpassing. De beperkte rerankcache bewaart
  uitsluitend gezouten hashes en scores; timeout en annulering worden vóór en
  na cachegebruik gecontroleerd. Fouten en gedeeltelijke batches worden niet
  gecachet. Opwarming gebruikt acht synthetische paren vóór gereedmelding.
- Het activatie-entrypoint normaliseert uitsluitend bekende oude PYTHONPATH-
  waarden naar de eigen venv. Het controleert daar expliciet Hindsight 1.2.0 en
  CPU-Torch 2.13.0. `LD_LIBRARY_PATH` blijft behouden. De oorspronkelijke
  beveiligde bestandgeheim-lezer is AST-identiek.
- BGE `BAAI/bge-small-en-v1.5` en MiniLM
  `cross-encoder/ms-marco-MiniLM-L-6-v2` worden expliciet ingesteld wanneer niet
  geconfigureerd. De native 1.2-rerankerdefault is veranderd; vertrouwen op de
  oude impliciete default werkt offline niet. Legacy parallelle batches staan
  uit om native annulering en deadlines te behouden.
- Operatiebewaartermijn blijft nul: deze upgrade ruimt geen operatiereceipts op.
  De runtimebronpatches worden ook in de eigen distributie-RECORD bijgewerkt.
- De rollbackimage behoudt de oude 0.9.1-basis en weigert refresh van nieuwere of
  onbekende gestructureerde mentale modellen vóór modeldispatch of inhoudwrite.
  Alleen deze specifieke fout is terminal. Oude v1/NULL-modellen volgen de
  oorspronkelijke route; bestaande v2-inhoud blijft leesbaar en behouden.

## Gerichte bronproeven

De voorbereiding produceert volledige, hashgecontroleerde bron buiten de repo.
Voer de 8 profiel- en 8 rollbackproeven uit zonder provider of database:

```sh
python -m unittest discover -s "$RECIPE/tests" -p test_activation_profile.py
EVA_HINDSIGHT_ROLLBACK_SOURCE="$OUTPUT/sources/rollback/hindsight_api/engine/memory_engine.py" \
  python -m unittest discover -s "$RECIPE/tests" -p test_rollback_guard.py
```

De 7 cache/annulerings/opwarmproeven vereisen de geïnstalleerde, vastgelegde
native runtimeclosure en de twee gepatchte modules op zijn importpad:

```sh
EVA_HINDSIGHT_MEMORY_ENGINE_SOURCE="$OUTPUT/sources/runtime/hindsight_api/engine/memory_engine.py" \
  python -m unittest discover -s "$RECIPE/tests" -p test_local_overlay.py
```

## Installatie- en herstelgrens

Dit is bouwinvoer, geen autonoom live-installatiescript. De bestaande Eva
image-updater staat uitsluitend de expliciete Hindsight-dienst extra toe; zijn
image-only, configuratie-, opslag- en ontvangstbewijsgrenzen blijven gelden.
De beoordeelde installatie gebruikte eerst de bewaarbeveiligde 0.9.1-referentie,
daarna de 1.2-activatieimage op dezelfde database. Rollback herstelt geen oude
databasesnapshot en herhaalt geen onbekend effect.

De 20 native migraties zijn vooraf op een geïsoleerde kopie onderzocht. Sommige
oude afgeleide structured_content wordt NULL; de getrouwe Markdowninhoud blijft
behouden. Vereist vóór en na migratie: stilgelegde schrijvers, geen actieve Eva-
claims of native operaties, geen eligible achtergrondwerk, brongebonden
schema-/rijvergelijking en behoud van compaction, originele receipts en criteria.
Controleer het effectieve CLI-procespad: `docker exec` erft de oude onveranderlijke
containeromgeving en bewijst op zichzelf niet welke modules het echte proces laadt.

De geïnstalleerde uitvoer en onafhankelijke review zijn afzonderlijk vastgelegd.
De DNS-readbackcontinuatie accepteerde uitsluitend `null` versus `[]` voor de drie
aantoonbaar ongeconfigureerde DNS-velden; overige configuratie bleef exact gelijk.
De openbare bouwmap bevat geen privé-installatieplan of vrijstelling van deze gates.
