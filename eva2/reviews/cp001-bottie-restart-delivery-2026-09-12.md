# CP-001 Bottie: procescrash vóór ACK

Codecheckpoint: `920f1fe5007e437895c16c03d47916e55de20110`.
Private branch: `codex/cp001-runner-20260912`.
Voortbouwend op lokaal ACK-bewijs `7f5bc5fe` / `78aeda2c`.

## Resultaat en toelatingsgrens

De echte Bottie HTTP-route/agent/SQLite is getest met synthetische data en uitsluitend
gecontroleerd modeltransport. Na ontvangst van de prepared HTTP-response én een
onafhankelijke read-only SQLite-readback krijgt de Node-worker SIGKILL (exit -9).
Dit omzeilt graceful close en worker-checkpoint. De database inclusief WAL en
vooraf gefsyncte P-256-clientsleutel blijven in dezelfde private tijdelijke map.
Een nieuw proces in een nieuwe user/netwerknamespace opent die database en sleutel.
De proef controleert verschillende PID/netwerknamespace en gelijke publieke sleutel
plus sleutelbestandhash. De private PEM (0600) wordt niet in evidence opgenomen.

Replay geeft exact dezelfde responsebody, met replay-header true. Iedere poging tot
providergeneratie in het tweede proces gooit een fixturefout; alle zes HTTP-resultaten
geven de stubcallreeks 2,0,0,0,0,0. Verkeerde sleutel en geldig ondertekende gewijzigde
hash/responseRef worden na herstart geweigerd (400). De geldige ACK en exact herhaalde
ACK geven 200 en dezelfde receipt. Onafhankelijke SQLite-readbacks tonen vóór geldige
ACK geen receipt/assistant/chat_out en daarna precies één van elk, met één prepared
response. De receiptinhoud en SHA-256 komen overeen met de echte ACK-HTTP-response.

Dit laat alleen deze herstelgrens toe: procesverlies na voltooide antwoordopslag en
vóór client-ACK, met behouden database en clientsleutel. Geen bewijs voor OS-crash,
stroomverlies, een crash midden in een schrijfoperatie, verloren sleutel, willekeurige
andere crashgrenzen of algemene recovery. De ACK komt van een cryptografische lokale
testclient; menselijke ontvangst, Jaap-acceptatie en live-aflevering zijn niet gemeten.
Serverbudgetten, totale provider/toolusage en productranking blijven unknown.

## Validatie en evidence

- 37 tests geslaagd: vijf echte Bottie-contracttests plus bestaande native-, runner-
  en EV-001-tests; JUnit en uitvoer bewaard. De afzonderlijke EVA2/PG-proef is ongewijzigd.
- ESLint worker en Ruff testbestand geslaagd vóór codecheckpoint.
- `bridge_probe.verify_source_checkout` bindt 14 bronbestanden aan exact bovenstaand
  commit; geen Bottie-productcode of centrale administratie gewijzigd.
- Vijf databases na afsluiten opnieuw read-only geopend en alle responsehashes/scopes
  gecontroleerd; aanvullende ACK/restart-asserties in de bewaarde `verify.py`.
- Eerste recoveryworker eindigt bewust -9, alle eindworkers eindigen 0. Alle tijdelijke
  datamappen zijn na de test onafhankelijk op afwezigheid gecontroleerd. Geen live
  providers of productdata; alleen loopback in private netwerknamespace.

Duurzaam artifact:
`/home/administrator/project/.eva2-artifacts/cp001-bottie-restart-20260912`
met `tests.txt`, `junit.xml`, `verify.py`, `receipt.json` en vijf
`evidence/<test>/{readbacks.json,eva.db}`-paren. Receipt bevat bronbinding,
procesidentiteiten en SHA-256 van evidence. De verifier slaagde op het codecheckpoint
vóór deze evidence-only commit; voor herhaling de exacte codecheckout gebruiken.

Runtime: WSL Ubuntu, Node 22.22.3, bestaande private kopie van Bottie-dependencies
(tree SHA-256 `31fe36a05acb7ada9e9b73c56bc7dd0f942e3fa4e4b701a67fafd138d9828582`).
Geen nieuwe runtimeafhankelijkheid of productwijziging. Onafhankelijke review via
bestaande coördinator/tl003 en integratie door centrale beheerder blijven afzonderlijk.
