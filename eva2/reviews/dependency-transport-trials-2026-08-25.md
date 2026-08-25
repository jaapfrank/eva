# Dependencytransportproeven — 25 augustus 2026

Dit is het technische verslag van de bouwer, niet een onafhankelijke
reviewreceipt. De proeven waren eenmalig en zijn nog niet als commitgebonden
script met artifacthashes en herhaalreceipt vastgelegd.

## Oordeel

- `pydantic-ai-slim[openai]==2.32.2` met `openai==3.3.1`: **PROEF
  DOORZETTEN**, nog niet adopteren.
- `mcp==2.1.1`: **ALLEEN EEN BEGRENSDE PARITEITSPROEF**, geen drop-in
  vervanging.
- Geen repositorydependency, release-lock, image, A-service of B-service is
  door deze proeven gewijzigd.

## Geïnspecteerde upstreamtags

| Input | Exacte bron |
|---|---|
| Pydantic AI | tag `v2.32.2`, commit `5e59fef14bd5abc86c4cfab1480c4f380542acfe` |
| OpenAI Python | tag `v3.3.1`, commit `753ab5c1a81cd85e8bf0aef4c04c51a2e8dae6cd` |
| MCP Python SDK | tag `v2.1.1`, commit `0921d94a74db900dccd2d534842aa7b6160542d2` |

De uitvoering gebruikte PyPI-artifacts. Hun hashes zijn niet vastgelegd en zij
zijn dus niet aan deze geïnspecteerde Git-commits gebonden. De
wegwerpinstallatie resolveerde onder meer `mcp-types==2.1.1`,
`pydantic-graph==2.32.2`, `tiktoken==0.14.0` en `httpx2==2.12.0`. Zij trok ook
Pydantic `2.13.4`/`pydantic-core 2.46.4`, Starlette `1.6.0`, Uvicorn `0.52.4`,
`sse-starlette==3.4.8`, Cryptography `50.0.1` en PyJWT `2.13.0` binnen. Eva's
huidige Pydanticpins zijn `2.12.5`/`2.41.5`. Dit is dependencydrift en een open
blocker, geen Eva-lock of toestemming om transitieve packages te laten zweven.

## Uitgevoerde modelproef

Een echte lokale HTTP-server ving het verzoek op. `AsyncOpenAI` kreeg een
dummyversie van één atomair token/accountpaar, een vaste Eva-achtige base-URL,
`max_retries=0` en de vereiste account-, beta-, origin- en originatorheaders.
`OpenAIResponsesModel` en Pydantic `Agent` kregen `store=false`, lage reasoning,
een vaste prompt-cachekey en een expliciete timeout.

Eenmalig waargenomen uitkomst:

- de request ging exact naar `/backend-api/codex/responses`;
- bearer-token en account-ID kwamen als twee headers uit hetzelfde
  proefsnapshot;
- `store` bleef `false` en de cachekey bleef behouden;
- Pydantic gaf de antwoordtekst terug;
- de synthetische usagevelden van de proefserver kwamen terug als 11 input-, 7
  output- en 18 totaal; dit was geen tokenmeting;
- Pydantic berekende uit die kunstmatige usage en zijn prijstabel ook een
  kostprojectie; dit is geen werkelijk gefactureerd bedrag.

Niet bewezen:

- Eva's echte private ChatGPT/Codex-provider;
- streaming en alle bestaande functiecalls/researchbronnen;
- een nieuwe lease na 401/403 zonder gemengd token/accountpaar;
- Eva's huidige 8-MiB-streamgrens;
- exacte hash-lock, fysieke CPython-3.12/amd64-wheels, SBOM, scan, offline image
  en rollback.

Pydantic `Agent` was hier alleen een wegwerpende compatibiliteitscaller. De
proef bewees niet dat zijn eigen request-, output- of toolretrylagen uitstaan en
bewees evenmin een dunne route zonder tweede runloop. Het is geen
productiekandidaat naast Eva's eigen agentloop. De kleinste echte slice gebruikt
eerst alleen de synchrone OpenAI-SDK `3.3.1`: per poging leest de bestaande
fileloader één actueel leasepaar, maakt daarmee een tijdelijke client, zet
SDK-retries op nul en geeft alleen getypeerde modeluitvoer en usage terug aan
Eva. Pydantic-modelmapping volgt alleen via een lagere primitive die nul eigen
retries en begrensde aanvragen bewijst; geen toolcallback mag vóór Eva's grant
en ledger draaien.

## Uitgevoerde MCP-proef

De officiële SDK is via een echte lokale Streamable-HTTP-server door
`initialize`, `notifications/initialized`, `tools/list` en `tools/call` gegaan.
De call bevatte geen Authorizationheader en leverde getypeerde
`structuredContent` terug.

De proef vond tegelijk relevante verschillen met Eva's bestaande 404-regelige
client:

- de standaardhandshake start op een nieuwere protocolversie dan Eva's vaste
  `2025-06-18`;
- de standaardtransportlaag kan GET voor serverberichten en DELETE bij afsluiten
  gebruiken;
- standaard redirects, proxyomgeving en timeouts zijn ruimer;
- er is geen publieke clientinstelling voor Eva's response- en
  sessiebyteplafond;
- callbacks, meer SSE-events en reconnects zijn ruimer dan Eva's huidige ene
  read-call.

De SDK kan daardoor wel JSON-RPC, sessiecorrelatie, MCP-types en JSON/SSE
overnemen, maar alleen achter een kleine Eva-transportgrens. De proef stopt als
die wrapper meer dan ongeveer 180 regels nodig heeft of een huidige negatieve
test versoepelt. Bruto kan volgens de broninventaris circa 200 regels eigen
protocolcode verdwijnen; 40–120 regels nettowinst is slechts een ruwe,
ongeteste hypothese totdat een echte diff bestaat.

## Onafhankelijke auditvoorwaarden

De reviewer vereist voor beide kandidaten:

1. exacte roots, transitieve pins, hashes, licenties, wheels, SBOM, scan en
   offline import;
2. geen redirects, proxyomgeving, verborgen retries of onbegrensde buffering;
3. echte HTTP/SSE-proeven met timeouts, dripresponses, te grote antwoorden,
   schema-afwijkingen, callbacks en fouten;
4. Eva blijft eigenaar van identiteit, audience, grants, catalogus, budget,
   ledger, readback en eindantwoord;
5. de huidige adapter en image blijven de direct beschikbare rollback totdat
   dezelfde tests en een echte providerproef groen zijn.

De onafhankelijke auditor heeft de inhoud en nonclaims beoordeeld, maar de
eenmalige uitvoering niet kunnen herhalen vanuit een repositoryscript. Status:
**lokale happy paths eenmalig waargenomen; adoptie niet tested, niet reviewed en
niet live bewezen**.
