# DF02e — exacte HA-MCP-pariteitsgrens

Bijgewerkt: 26 augustus 2026.

## Uitkomst

Eva heeft niet één monolithische Home Assistant-adapter van 763 regels, maar
twee direct gekoppelde productie-adapters van samen **1.023 regels** voor
zoeken, toestand, historie, services en automatiseringsbeheer. De exact gepinde
`v8.3.0`-image heeft zeven kandidaattools voor dat technische oppervlak.

Deze historische gate bindt de toenmalige Eva-bronbaseline,
vervangingsmapping en relevante toolbeschrijvingen. Hij stopt op input-,
output-, annotatie-, versie- en baselinedrift. De actuele algemene
providerroute heeft deze directe productieopzet inmiddels opgevolgd; dit
experiment is dus geen promotie-authority meer. De upstream outputschema's zijn
bovendien open objecten. De gate bewijst geen semantische resultaatpariteit,
maakt geen echte Home Assistant-call en verwijdert geen productcode.

## Wat mag worden vervangen

- natuurlijke entityzoeking: `ha_search`;
- actuele toestand en onafhankelijke readback: `ha_get_state`;
- één of meer recorderhistorieën: `ha_get_history`;
- generieke bediening: `ha_call_service`;
- automation create/readback/delete:
  `ha_config_get_automation`, `ha_config_set_automation` en
  `ha_config_remove_automation`.

Eva houdt doelbinding, persoonsmandaat, veilige automationbuilders, één
duurzame poging, idempotency, selectie van minimaal bewijs en het eindantwoord.
De bestaande `health()`-uitkomst heeft nog geen toegelaten toolmapping en blijft
expliciet buiten deze vervangingsproef.

## Wat niet wordt meegerekend

Events, admininventaris, HACS, LocalTuya en de veilige automationbuilders zijn
andere rollen. Zij worden niet als nepbesparing opgeteld. Sommige kunnen later
ook door HA-MCP worden gedragen, maar alleen na een eigen pariteitsproef.

## Harde vervolggrens

De eerste audit schat dat alleen het vervangen van HTTP-transport ongeveer
174–192 regels zou verwijderen. Dat getal is uitsluitend een meting en geen
toelatingsgrens. Een productkandidaat moet de oude verantwoordelijkheid echt
overnemen, de oude uitvoerroute in dezelfde vervangingslijn verwijderen en één
runtime-eigenaar overhouden. Daarvoor moet ook zoek- en historiesemantiek
werkelijk gelijk blijken. Daarna volgen dezelfde state-, historie-, service-,
automation-, crash- en readbacktests met een apart least-privilege Home
Assistant-testtoken. Omvang alleen beslist nooit over promotie.

Herhalen:

```bash
python3 eva2/experiments/ha-mcp-parity-v1/check_parity_contract.py
```

De checker gebruikt de al lokaal aanwezige image met `--pull=never`, zonder
netwerk, secrets, mounts of echte Home Assistant.
