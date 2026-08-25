# Onafhankelijke review — HA-MCP-sidecarkandidaat `3896f17`

Datum: 26 augustus 2026  
Oordeel: **GO voor een geïsoleerde live pariteitsproef; geen productie-GO.**

De onafhankelijke reviewer controleerde exact checkpoint
`3896f17666d53fe63f6d8ca5f4158838469286c1` en herhaalde beide kandidaatchecks.

## Herhaalde uitkomst

- `check_candidate.py`: groen;
- `smoke_candidate.py`: groen tegen de lokaal aanwezige exact gepinde
  `linux/amd64`-image;
- 39 zichtbare tools, waarvan zes niet zuiver read-only geannoteerd;
- alle vier vereiste leestools aanwezig;
- automation-write en backup-write beide met `READ_ONLY_MODE` geblokkeerd;
- geen achtergebleven container, mount, secret of productieaanpassing.

De reviewer bevestigde daarnaast de onveranderlijke `v8.3.0`-release, bron- en
imagebinding, gelijke runtime-`src/`-boom, MIT-licentie, vier genoemde
maintainers, securitybeleid, 88 gedocumenteerde tools en de exacte testtelling:
621 Pythonbestanden onder `tests/`, waarvan 483 `test_*.py`-modules.

## Bewijsgrens

Dit bewijst alleen dat de onderhouden community-sidecar exact, afgeschermd en
read-only kan starten en de relevante toolvormen aanbiedt. Het bewijst geen
echte Home Assistant-verbinding, toolpariteit, state-readback, latencywinst,
codeverwijdering, upgrade/rollback, B-adoptie of productiegeschiktheid.

Promotie blijft geblokkeerd totdat een least-privilege live proef dezelfde
state-, historie-, service-, automation-, crash- en readbackgevallen haalt én
minimaal 300 regels bestaande productcode verwijdert tegen hoogstens 180 regels
dunne Eva-integratie.
