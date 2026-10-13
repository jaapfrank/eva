# CP-001 echte Bottie-route + SQLite — 12 september 2026

Gebouwd/offline getest; onafhankelijke review open. Geen liveprovider,
productgegevens, Bottie-productiecodewijziging of vergelijking/rankingclaim.

Code `de9fef37de9172b65d61a6bf549d62c00b34659e` (parent `4cadbb75`).
Vier paden: `benchmark/inspect_ai_v1/{cp001_native.py,cp001_bottie_worker.mjs,README.md}`
en `tests/benchmark/test_cp001_native_bottie.py`. Deze notitie verandert geen code.
Eigen worktree `/home/administrator/project/.eva2-artifacts/cp001-runner-20260912`.

## Echte keten en gevonden contractgat

Python CP001-adapter → lokale echte Bottie `/api/chat` → echte agent-/model-
transportverwerking → echte SQLite HTTP-ingress/prepared-responsejournal.
De bestaande Responses-stub uit `test/web-agent-ingress.test.js` is hergebruikt;
alleen providerantwoorden zijn gecontroleerde fixtures. Agent, HTTP-response en
DB-journal worden niet door een test-double vervangen of achteraf ingezaaid.

De echte route vereist `client_ack_public_key`. De eerdere mapping zonder sleutel
wordt HTTP 400/client_ack_key_required, vóór modelstubcalls. `request_for` accepteert
nu optioneel die sleutel; de test genereert een P-256-public key via Node crypto,
zoals de bestaande Bottie-tests. Dit was een concreet ontbrekend ingresscontract,
geen productfout. Productcode is ongewijzigd.

De proef stuurt geen bevestigende client-ACK. HTTP 200 en persistente response
bewijzen hier een opgeslagen antwoord, geen geverifieerde gebruikersaflevering.
De native preflight blijft unknown zolang sleutel/ACK-route en andere admission-
garanties niet toegelaten zijn.

Een aparte Python SQLite-verbinding leest read-only de echte `public_body_json`
via channel/conversation/request en verifieert `public_body_sha256`. De adapter
controleert vervolgens de replybinding. Na afsluiten van het Node-proces is de
SQLite-database opnieuw geopend en zijn de persistente rijen gecontroleerd.

## Uitgevoerde controles

35 tests groen op codecheckpoint (3 nieuwe Bottie + 32 bestaande native/fixture/EV001),
6.00s; ruff en ESLint op gewijzigde paden groen. De eerdere vier PG-proeven zijn
niet onnodig herhaald en behouden hun afzonderlijke checkpointbewijs.

- Intact antwoord/readback en exacte replay: één persistente response, twee
  gecontroleerde providerstubcalls voor de eerste agentbeurt, geen extra bij replay.
- Ontbrekende ACK-key: HTTP 400 en nul stubcalls. Vervolgens geldige beurt;
  veranderde tekst bij dezelfde requestscope: HTTP 409/request_intent_conflict,
  zonder extra stubcalls.
- Twee echte sessierijen met hetzelfde antwoord: verkeerd opgehaalde rij wordt
  readback_failed; geen verified_end of usage. Twee persistente responses blijven
  ook na workerexit leesbaar.

Bottie hanteert channel + conversation + request als ingressscope; die is niet
stil gelijkgesteld aan EVA2's message-scope. Provider-/totaalusage, runtimebudgetten,
kosten, hulp en semantische score blijven onbekend. Stubcalltellingen zijn alleen
fixturediagnostiek. Volledige ACK/recovery/geheugenkwaliteit is niet getest.

## Isolatie, herkomst en artifacts

Elke worker draaide in een nieuwe Linux user/network namespace met alleen lo,
lege eigen `/tmp/cp001-bottie-*` SQLite-data, minimale procesomgeving en een
vast test-only API-key. Externe fetches worden geweigerd; providerrequests krijgen
lokale stubs. Alleen de eigen loopback-HTTP-route gebruikt echte fetch. Alle
workers sloten met exit0 en de tijdelijke directories zijn opgeruimd. Bewijs is
vóór die opruiming gekopieerd naar de duurzame artifactmap hieronder.

De behouden npm-dependencies zijn lokaal gekopieerd naar de eigen checkout;
geen installatie/download of gedeelde dependencywijziging. Een aanvankelijke
symlink werd door Botties bestaande dependency-herkomstcontrole terecht geweigerd;
de controle is niet omzeild. Node 22.22.3; de native dependencyTreeSha256 is
`31fe36a05acb7ada9e9b73c56bc7dd0f942e3fa4e4b701a67fafd138d9828582`.
De packageversies staan in `dependencies.json`. Geen claim over het live-Bottie-image.

Duurzaam bewijs:
`/home/administrator/project/.eva2-artifacts/cp001-bottie-contract-20260912/`.

- `receipt.json`: commit/tree + dertien bronhashes, versies, hashes van drie
  SQLite-snapshots/readbackJSONs, JUnit/testlog en dependencybewijs; usage/ranking unknown.
- `evidence/<test>/eva.db` en `readbacks.json`: daadwerkelijke persistente responses,
  lokale HTTP-resultaten, stubcalltellingen en telling na procesexit.
- `tests.txt`, `junit.xml`: 35 passed, geen skip.
- `verify.py`: onafhankelijke heropening van snapshots; scoped SQL-selectie,
  teksthash/payloadvergelijking, persistentietelling en afwezig publiek usageveld.

Review gevraagd op echte route versus providerstub, publieke ACK-keybinding,
read-only SQLite-query, replay/conflict, verkeerde-rijcontrole en de beperkte
opslagclaim zonder afleverings- of productwinstclaim. Geen extra framework of
Bottie-runtimewijziging. Rootreview gaat vóór verdere productproeven.
