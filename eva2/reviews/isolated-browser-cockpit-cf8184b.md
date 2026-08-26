# Onafhankelijke herreview geïsoleerde browser en cockpit — `cf8184b`

Datum: 26 augustus 2026.

## Oordeel

**GO** voor checkpoint
`cf8184b48936762e68bd76e00a3ba890c9cec3d3` als een geteste, lokale en
credentialvrije browser-readvertical met cockpitprojectie.

De eerdere blocker is gesloten. De gewone interne Docker-bridge liet vanuit
de browser nog verkeer naar de hostgateway toe. De kandidaat maakt nu een
interne bridge met
`com.docker.network.bridge.inhibit_ipv4=true`, controleert een lege IPv4-
gateway en laat de browser alleen de vaste fixture bereiken. Ik heb die grens
zelf opnieuw aangevallen: de fixture antwoordde met HTTP 200, terwijl dezelfde
gepinde browserimage een tijdelijke HTTP-canary op de host niet kon bereiken.
De host-canary ontving nul verzoeken.

Er zijn geen resterende functionele, privacy- of claimblockers gevonden binnen
de hieronder begrensde slice.

## Beoordeelde bron

- exact Git-checkpoint:
  `cf8184b48936762e68bd76e00a3ba890c9cec3d3`;
- nieuwe schone `git archive`, uitgepakt onder een nieuwe tijdelijke root;
- SHA-256 van die reviewarchive:
  `5712d82de24d7ed717345c5c6d6c4ec2aa86ef966834ec7c4a8fc04dbeeec3e9`;
- voorgangers voor de verticale lijn: `6b4cc87`, `75e581d`, `84b1937`,
  `5ceeae2`, `a7913a8`, `23ed141` en `6e7b305`.

De vuile hoofdwerkmap is niet als bewijsbron gebruikt. Alleen dit reviewbestand
is daar toegevoegd.

## Zelf uitgevoerde controles

### Herhaling van de eerdere hosttoegangsbevinding

Ik heb een nieuwe tijdelijke Docker-bridge gemaakt met exact:

```text
--internal
--driver bridge
--opt com.docker.network.bridge.inhibit_ipv4=true
```

`docker network inspect` gaf voor die bridge:

```json
{
  "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
  "IPAM": {"Config": [{"Subnet": "172.21.0.0/16", "Gateway": ""}]}
}
```

Daarna draaiden de vaste fixture en de exact gepinde Playwright MCP-image in
die bridge. Vanuit de browsercontainer gaf
`http://fixture.eva.test:8080/index.html` status 200. Een tijdelijke host-
HTTP-canary, aangeboden via een `host-gateway`-hostnaam, bleef onbereikbaar.
De canarylog bleef leeg. De tijdelijke container, bridge en hostserver zijn na
de proef verwijderd.

Dit is het relevante verschil met de eerdere NO-GO: een normale interne bridge
had nog een bruikbare IPv4-hostgateway; deze gatewayloze bridge niet.

### Werkelijke Eva-adapter in hetzelfde netwerkvak

De kandidaat start in dezelfde gatewayloze bridge een begrensde
controllercontainer uit
`eva2-living-core@sha256:d1a861106ecaaa2230e5bcac63232808eced907bf2b1febeff4db2ab7cc161ae`.
Die controller importeert en gebruikt de echte
`PlaywrightMcpReadAdapter` en `PlaywrightMcpReadProfile` uit de beoordeelde
Git-archive. Dit is dus geen los curl- of mockbewijs naast Eva's adapter.

De werkelijke smoke doorliep uitsluitend:

1. `browser_navigate` naar de toegelaten fixture-origin;
2. `browser_snapshot` voor de eindcontrole.

De uitkomst was onder meer:

```json
{
  "externalEgressBlocked": true,
  "finalUrl": "http://fixture.eva.test:8080/index.html",
  "fixtureGets": 1,
  "gatewaylessNetwork": true,
  "hostNetworkBlocked": true,
  "internalNetwork": true,
  "matchedVisibleTexts": 2,
  "publishedPorts": 0,
  "sandboxRequested": true,
  "stopMilliseconds": 253,
  "toolSequence": ["browser_navigate", "browser_snapshot"]
}
```

De onafhankelijk gemeten stoptijd van 253 ms wijkt normaal af van de 219 ms in
de vastgelegde productreceipt; beide liggen binnen de grens en de cockpit
presenteert die 219 ms terecht als de meting uit haar gebonden receipt.

### Pins en isolatie

- browserimage exact:
  `mcr.microsoft.com/playwright/mcp@sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9`;
- imageplatform `linux/amd64`, imageuser `node`;
- `@playwright/mcp` `0.0.79`, Playwright en Playwright Core
  `1.63.0-alpha-2026-08-05`, Chromium
  `152.0.7977.8`;
- browsercontainer: read-only root, non-root, alle capabilities verwijderd,
  `no-new-privileges`, Chromium-sandbox aangevraagd, begrensde CPU, memory en
  PID-aantallen, geen mounts, geen secrets en geen gepubliceerde poort;
- controller: non-root, read-only en capabilityvrij; uitsluitend de
  beoordeelde bron, connector-SDK, profiel en smokeclient zijn read-only
  gekoppeld. Er worden geen secrets of hostomgevingsvariabelen doorgegeven; de
  enige expliciet toegevoegde run-env is `PYTHONDONTWRITEBYTECODE=1`, naast de
  vaste image-defaults;
- fixture: uitsluitend de vaste read-only fixturebytes;
- netwerk: alleen fixture, browser en tijdens de call de Eva-controller; geen
  default gateway en geen internetroute.

De adapter weigert buiten het vaste origin, controleert de uiteindelijke URL en
titel, accepteert alleen de twee verwachte calls met gesloten schema's,
begrensde response- en snapshotbytes en één einddeadline. Onbekende tools,
schema-afwijking, redirects, proxy-omgeving, callback-/DNS-afwijking, te grote
output en deadlineoverschrijding sluiten de route.

### Tests, stijl en releasebinding

Vanuit de schone archive zijn uitgevoerd:

```text
python3 experiments/playwright-mcp-sidecar-v1/check_candidate.py
python3 experiments/playwright-mcp-sidecar-v1/smoke_candidate.py
PYTHONPATH=src:. python3 -m pytest -s -q \
  tests/connectors/test_playwright_mcp.py \
  tests/connectors/test_mcp.py \
  tests/api/test_dashboard_status.py \
  tests/operations/test_managed_b_release_inputs.py
python3 -m ruff check <browser-adapter, probe, tests en releasepaden>
PYTHONPATH=src:. python3 -m eva.operations.managed_b_release_inputs \
  cf8184b48936762e68bd76e00a3ba890c9cec3d3
```

Resultaat:

- kandidaatchecker: groen, 29 browserchecks;
- echte container-/MCP-smoke: groen;
- gerichte regressies: **69 passed**;
- Ruff: groen;
- exacte Git-releasecheck: groen met 93 broninputs, 71 testinputs,
  15 cockpitclaims, 15/15 core-livecases, huidige registry 71, historische en
  portable-v1-registry 64 en schemahead `4e_0004`.

De releasechecker heropent de gebonden browserreceipt, het profiel, de sandbox,
de controllerclient, de Eva-adapter, de MCP-transportgrens en de tests op hash.
De cockpitindex is eveneens hashgebonden. De zichtbare status `tested` en de
teksten over één fixturepagina, twee readcalls en de nog open vervolgstappen
komen overeen met die bronnen.

## Bevindingen

Geen blockers.

Eén niet-blokkerende onderhoudsverbetering: de onafhankelijke review heeft de
uitgaande hostgrens met een echte tijdelijke host-canary beproefd. De
vastgelegde smoke controleert zelf de lege gateway, de bridgeoptie,
internetblokkade en de omgekeerde host-naar-browserroute. Dezelfde uitgaande
host-canary kan later in de herhaalbare smoke worden opgenomen om verschillen
tussen toekomstige Docker-versies nog directer zichtbaar te maken. Voor de
exact beoordeelde Dockeromgeving is de relevante grens nu daadwerkelijk
gereproduceerd en groen.

## Exacte bewijsgrens en nonclaims

Dit checkpoint bewijst één lokale, credentialvrije readtaak tegen één vaste
fixture-origin, met precies `navigate` en `snapshot`, uitgevoerd door Eva's
echte begrensde adapter in een geïsoleerde browsercontainer. Het bewijst ook dat
de beoordeelde netwerkconfiguratie de fixture bereikbaar houdt en host- en
internettoegang blokkeert.

Het bewijst uitdrukkelijk nog niet:

- een openbare website of onbetrouwbare externe DNS-/TLS-route;
- klikken, formulieren, downloads, uploads, accounts, credentials of betaling;
- een duurzaam Eva-ledger, mandaat, effectreadback of crashherstel midden in
  browserwerk;
- live meekijken, overname of een blijvend browserprofiel;
- Telegram-, Managed-B-, huis- of productiegebruik;
- een live AU06/AU07-eindproef;
- volledige supply-chainprovenance, SBOM of kwetsbaarhedenbesluit voor de reeds
  lokaal aanwezige OCI-images.

Playwright MCP is hier alleen een verwisselbare browserhand. Planning, mandaat,
identiteit, geheugen, policy, bewijs en het eindantwoord blijven bij Eva; deze
slice introduceert geen tweede agent of tweede orchestrator.
