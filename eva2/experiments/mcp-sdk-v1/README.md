# DF02c — officiële MCP-dependencies tegen Eva's smalle client

Status op 26 augustus 2026: dependencyproef uitgevoerd; beide vervangroutes
`NO-GO` voor deze smalle V1-client. Productie, A en B zijn niet gewijzigd.

## Het korte antwoord

We hebben de onderhouden officiële MCP Python SDK `2.1.1` niet alleen bekeken,
maar exact naast Eva's huidige dependencies gezet en de bruikbare delen in code
beproefd.

- De **volledige SDK** voegt tien packages toe. Hij kan het protocol goed, maar
  start ook luisterverkeer, kan bij afsluiten DELETE sturen en ondersteunt
  reconnects, callbacks en ruimere sessies. Eva zou alsnog vrijwel haar hele
  begrensde transportwrapper moeten houden.
- Alleen **`mcp-types`** voegt één package toe en heeft geen nieuwe transitieve
  dependencies. Een tijdelijke integratie doorstond 22 gerichte tests, maar
  voegde 87 productregels toe en verwijderde nog geen bestaand transport.

Daarom installeren we geen dependency alleen omdat hij officieel en populair
is. Voor deze ene vaste read-tool blijft Eva's bestaande beperkte client nu
kleiner. De grotere kans is Home Assistants officiële MCP-server als
verwisselbare skillprovider; die proef kan mogelijk een adapter van honderden
regels vervangen zonder dat Eva het MCP-clienttransport verruimt.

## Volledige SDK: exact gemeten

`requirements.in` bevat Eva's tien bestaande directe roots plus `mcp==2.1.1`.
De volledige hashlock heeft 48 packages tegenover 38 in productie. Geen enkele
bestaande versie veranderde. Nieuw zijn:

- `mcp==2.1.1` en `mcp-types==2.1.1`;
- `attrs==26.1.0`, `jsonschema==4.26.0` en
  `jsonschema-specifications==2025.9.1`;
- `pyjwt==2.13.0`, `python-multipart==0.0.32`;
- `referencing==0.37.0`, `rpds-py==2026.6.3` en
  `sse-starlette==3.4.8`.

Een lege CPython-3.12-venv accepteerde deze volledige lock met
`pip install --require-hashes`. De packages zijn dus compatibel; het NO-GO komt
door onnodig gedrag en beheeroppervlak, niet door een resolverfout.

## Waarom de standaardclient niet past

Eva's huidige MCP-import doet precies vier POSTs: initialiseren, bevestigen,
één toolinventaris ophalen en één vooraf toegelaten read-tool aanroepen. Hij
heeft één totale deadline, request-/response-/sessiebytegrenzen, geen proxy,
geen redirect, geen credentials, precies één SSE-resultaat en geen callbacks.

De officiële standaardtransportlaag kan daarnaast GET/DELETE, herverbinden,
servercallbacks en ruimere SSE-sessies gebruiken. Zij heeft geen publieke
instelling voor Eva's drie byteplafonds. Een eigen transport eronder bouwen zou
de bestaande code grotendeels opnieuw invoeren. Dat is geen vervanging.

## Types-only proef

De kleinere sibling in `../mcp-types-v1/` voegt uitsluitend
`mcp-types==2.1.1` toe: 39 in plaats van 38 packages. Ook die volledige lock is
in een lege venv hashgecontroleerd geïnstalleerd; het volledige `mcp`-pakket was
daar aantoonbaar afwezig.

Een tijdelijke, daarna verwijderde kandidaat gebruikte de publieke
`jsonrpc_message_adapter`, `validate_client_request` en `parse_server_result`
voor versie `2025-06-18`. JSON en SSE, exacte vier-POST-route, verkeerd ID,
callback en paginering waren samen met de bestaande regressies 22/22 groen.
De productdiff was echter `+87` regels en verwijderde geen transportcode. Dit
was volgens het toenmalige netto-eenvoudcriterium een afwijzing; dat criterium
is inmiddels ingetrokken. De actuele afwijzingsgrond is de onverklaarde dubbele
verantwoordelijkheid: er kwam een laag bij zonder dat zij de transporttaak
overnam of de oude route verwijderde. Het aantal regels is alleen observatie.

## Reproduceerbaarheid en bewijsgrens

- generator: CPython 3.12, `pip==24.0`, `pip-tools==7.6.1`;
- productie-lock SHA-256:
  `89555d14acd8bceb8786dfa8539af29672899e8259e6a2b5352e27ef9219e8dc`;
- volledige SDK-roots/lock:
  `59ae90f862b7fd584262c0cd7c540ecb035973d744b8cb686088b104f8afa3c8` /
  `034ce635e0697c3708df07fbbd341f3c393c1b26e2d4b3fd8c9af5530f432e05`;
- types-only roots/lock:
  `d4fb4c5b71f48a2aed53152523343847b1e5d70eec63548786cbec383a30de9f` /
  `0a3454d1d9b9f0df8068071a125817dbd7a6a0d9d472c8fceae4ff54103b3861`.

De proef bewijst geen live MCP-sidecar, Home Assistant-pariteit, nieuwe
productielock, wheelhouse, image, B-adoptie of release. De bestaande client is
niet ineens optimaal; ontbrekende negatieve regressies worden afzonderlijk
aangevuld wanneer zijn productroute wordt gepromoveerd.

`python3 eva2/experiments/mcp-sdk-v1/check_bakeoff.py` controleert beide locks,
de exacte deltas en alle nonclaims fail-closed.
