# HA-MCP v8.3.0 als vervanger van eigen Home Assistant-code

Bijgewerkt: 26 augustus 2026.

## Kort antwoord

Dit is een **serieuze hergebruikkandidaat**. Het project
`homeassistant-ai/ha-mcp` levert al toestand, zoeken, historie,
automatiseringen, services, dashboards, backups en beheer. Eva moet die
algemene Home Assistant-code niet opnieuw maken.

De exacte `v8.3.0`-image is lokaal zonder Home Assistant-token en zonder
netwerk gestart. De MCP-handshake werkte. In read-only modus waren 39 tools
zichtbaar, waaronder toestand, historie en het lezen van automatiseringen.
Een automation-write en een backup-write werden met `READ_ONLY_MODE`
geweigerd voordat Home Assistant kon worden bereikt.

Dit is nog geen productieaansluiting. De volgende proef gebruikt een apart
least-privilege testaccount en vergelijkt dezelfde echte taken met Eva's
huidige directe adapter. Pas daarna mag code verdwijnen.

## Waarom deze kandidaat serieus is

- Niet gearchiveerd; laatste waargenomen push: 25 augustus 2026.
- Release `v8.3.0` is onveranderlijk gepubliceerd op 19 augustus 2026.
- 4.511 sterren, 196 forks en 69 door GitHub vermelde bijdragers op het
  meetmoment.
- Vier vaste maintainers in de repositorydocumentatie.
- MIT-licentie, securitybeleid, changelog, Renovate/Dependabot en
  multi-architecture images.
- 88 gedocumenteerde tools en 621 Pythonbestanden onder `tests/`, waarvan 483
  `test_*.py`-modules.
- Het project gebruikt Python 3.13 en blijft daarom een losse sidecar; het
  verandert Eva's Python 3.12-lock niet.

Sterren zijn niet de doorslag. Doorslaggevend zijn de exacte release, de
brede tests, meerdere onderhouders, de lokale werking en het feit dat deze
sidecar bestaande én toekomstige Eva-code kan vervangen.

## Exacte bron en image

| Onderdeel | Binding |
|---|---|
| Release | `v8.3.0` |
| Releasebron | `7f9227272df387df40ac2db844e2edeeee9ebe49` |
| Bronboom onder `src/` | `b6f80bafd162c4e9ce99d66a25f3ea043a85894d` |
| OCI-index | `sha256:38b3a0b6b6b69d3fd074f958fbf9653ae2d50c8727b4c0dcde640cfa3a638176` |
| `linux/amd64` | `sha256:2d06fa32c06a935700c44fbce3998a2741edf20e698cc8a0231743f595bd653e` |
| `linux/arm64` | `sha256:6e60aea9bb01f7e4b0cc20dc874d3043599dc3df77892b87e56ab9cbf18258c3` |

De image noemt commit `8ed7f47105e7902ded6fa61d30f2a7f926e68f2a`.
Die ligt twee commits na de releasecommit; de totale diff wijzigt alleen twee
add-onbestanden.
De `src/`-boom is op beide commits exact `b6f80b…`. Dit verschil wordt dus
niet verstopt, maar bindt wel aan dezelfde runtimebron.

## Wat hiermee waarschijnlijk kan verdwijnen

De pariteitsproef richt zich eerst op:

- natuurlijke entityzoeking en actuele toestand via `ha_search` en
  `ha_get_state`;
- recorderhistorie via `ha_get_history`;
- automation lezen, schrijven en verwijderen via de drie
  `ha_config_*_automation`-tools;
- generieke services via `ha_call_service` of `ha_bulk_control`;
- later dashboards, traces, backups en HA-beheer zonder nieuwe Eva-adapters.

Eva houdt alleen haar eigen grens: welke persoon wat mag, de exacte bedoeling,
één duurzame poging, idempotency, een onafhankelijke state-readback en het
eindantwoord. De sidecar wordt geen tweede persona, planner, geheugen of
taakdatabase.

## Harde promotie-eis

De kandidaat wordt alleen productonderdeel wanneer één gecontroleerde diff:

1. minimaal 300 handgeschreven productregels uit de Home Assistant-route
   verwijdert;
2. hoogstens 180 nieuwe regels dunne Eva-integratie toevoegt;
3. geen nieuwe Pythonpackage aan Eva's core-lock toevoegt;
4. dezelfde huidige-state-, historie-, service-, automation- en
   readbackgevallen haalt;
5. met een least-privilege testtoken draait en alleen de benodigde tools aan
   Eva blootstelt;
6. na crash of time-out geen blinde tweede actie uitvoert;
7. p95-latency, responsebytes en tokens meet en geen onbegrensde groei toont;
8. exact dezelfde image-digest kan terugrollen.

Worden er geen honderden regels verwijderd, dan blijft de huidige kleine
directe adapter beter dan een extra platform.

## Belangrijke grenzen

- De upstream threat model vertrouwt een geauthenticeerde MCP-client. Eva doet
  dat niet en blijft daarom zelf toolnamen, argumenten, resultaat en readback
  controleren.
- HTTP bindt upstream standaard ruim en Host/Origin-bescherming staat standaard
  uit. Eva gebruikt uitsluitend een privé Docker-netwerk of loopback, nooit een
  publiek gepubliceerde poort.
- Alleen de nieuwste upstreamversie krijgt securityfixes. Eva gebruikt toch een
  exacte digest en promoveert upgrades pas na dezelfde tests.
- Read-only modus laat zes gemengde read/write-tools zichtbaar, waaronder
  backupbeheer. Hun writevarianten werden in deze proef wel correct geblokkeerd;
  Eva gebruikt daarnaast altijd een expliciete toolallowlist.
- `ha-mcp --version` laadt de configuratie en vereist daardoor al een geldige
  URL en token. Voor beheer gebruikt Eva image-labels en de MCP-serverversie,
  niet die CLI-route.

## Herhalen

Na het eenmalig ophalen van de exact gepinde image:

```bash
python3 eva2/experiments/ha-mcp-sidecar-v1/check_candidate.py
python3 eva2/experiments/ha-mcp-sidecar-v1/smoke_candidate.py
```

De smoke gebruikt `--pull=never`, `--network=none`, een read-only filesystem,
geen Linux-capabilities, `no-new-privileges`, een tijdelijk `/tmp` en alleen
duidelijk ongeldige testwaarden. A, B en het echte huis worden niet benaderd.
