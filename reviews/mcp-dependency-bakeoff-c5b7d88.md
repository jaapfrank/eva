# Onafhankelijke review — MCP dependency bake-off

Datum: 26 augustus 2026

Beoordeeld checkpoint: `c5b7d883c173d1d74a7be5e2d46a4e3b5eabc5ae`

Oordeel: **GO voor het vastgelegde NO-GO-besluit**

## Wat onafhankelijk is herhaald

- Beide kandidaatlocks zijn opnieuw gemaakt met CPython 3.12, `pip==24.0` en
  `pip-tools==7.6.1`; de bytes en hashes waren gelijk.
- De volledige lock installeerde met `--require-hashes` en `pip check`: 48
  packages, exact tien nieuw en geen gewijzigde of verwijderde productiepin.
- De types-only lock deed hetzelfde met 39 packages: uitsluitend
  `mcp-types==2.1.1`; het volledige `mcp`-pakket was afwezig.
- `check_bakeoff.py` controleerde de locks, aantallen, deltas en nonclaims.
- De bestaande MCP-regressies bleven groen: tien tests.
- Het checkpoint voegt alleen acht experimentbestanden toe. Productiecode en
  producttests zijn bytegelijk aan de parent.

## Waarom de afwijzing klopt

De officiële `mcp==2.1.1`-client kan na initialisatie een GET-luisterstroom
starten, automatisch herverbinden, servercallbacks afhandelen en bij afsluiten
met een sessie-ID DELETE sturen. De publieke clientlaag levert geen equivalent
voor Eva's afzonderlijke request-, response- en totale sessiebyteplafonds.

Een eigen HTTP-client en uitgeschakelde afsluit-DELETE kunnen delen beperken,
maar vervangen Eva's bestaande smalle transport niet. De volledige SDK is
daarom terecht alleen voor deze vaste V1-readclient afgewezen; dit is geen
algemene afwijzing van MCP of van de SDK als server-/brede-clientbibliotheek.

De types-only proef is eerlijk als tijdelijke observatie beschreven. De 22
tests en `+87` regels zijn niet zelfstandig replaybaar omdat de afgewezen diff
bewust is verwijderd. `persisted=false` en de nonclaims voorkomen dat dit later
als duurzaam productbewijs wordt voorgesteld.

## Niet-blokkerende grens

De checker bindt de tijdelijke `22/+87`-observatie niet aan een bewaarde diff.
Die getallen blijven daarom alleen lokale bake-offobservatie. Voor een latere
claim zijn een bewaarde kandidaatdiff en herhaalbare tests vereist.
