# DF02e — exacte HA-MCP-pariteitsgrens

Bijgewerkt: 26 augustus 2026.

## Uitkomst

Eva heeft niet één monolithische Home Assistant-adapter van 763 regels, maar
twee direct gekoppelde productie-adapters van samen **1.023 regels** voor
zoeken, toestand, historie, services en automatiseringsbeheer. De exact gepinde
`v8.3.0`-image heeft zeven kandidaattools voor dat technische oppervlak.

Deze gate bindt de exacte Eva-bronbaseline, vervangingsmapping en relevante
toolbeschrijvingen. Hij stopt op input-, output-, annotatie-, versie- en
baseline-drift. De upstream outputschema's zijn echter open objecten; deze gate
bewijst daarom nog geen semantische resultaatpariteit. Hij maakt geen echte
Home Assistant-call en verwijdert geen productcode.

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
174–192 regels verwijdert. Dat is minder dan de toelatingsgrens. Een
productkandidaat mag hoogstens 180 regels dunne integratie toevoegen en moet
minimaal 300 regels bestaande productiecode verwijderen. Daarvoor moet eerst
ook zoek- en historiesemantiek werkelijk gelijk blijken. Daarna volgen dezelfde
state-, historie-, service-, automation-, crash- en readbacktests met een apart
least-privilege Home Assistant-testtoken. Zonder echte nettokrimp blijft de
directe adapter staan.

Herhalen:

```bash
python3 eva2/experiments/ha-mcp-parity-v1/check_parity_contract.py
```

De checker gebruikt de al lokaal aanwezige image met `--pull=never`, zonder
netwerk, secrets, mounts of echte Home Assistant.
