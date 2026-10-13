# Onafhankelijke review — action-chain readautorisatie MC03b1

Bijgewerkt: 25 augustus 2026.

## Uitkomst

`GO` voor MC03b1 als bron- en PostgreSQL-checkpoint. Er is bewust nog geen
provider aangeroepen en er is geen B-deploy- of live providerclaim.

## Kleine Git-checkpoints

- `7c7544b` — release-owned providerregistry, exacte procesbinding en sealed
  commandtargets. Netto 568 productregels; onder het normale PC00-slicebudget
  van 600.
- `df2e794` — corruptiegevoelige readvalidatie uit het bestaande grote ledger
  gehaald. Netto 296 productregels en het aangeraakte ledger werd 278 regels
  kleiner.
- `227ef2c` — precies één duurzame dispatchfence en PostgreSQL-gate. 591
  productregels; onder het normale slicebudget.

Samen voegen de drie checkpoints netto 1.455 productregels toe. Dat is geen
vrijbrief voor pakketgroei: de opsplitsing houdt ieder nieuw productbestand
onder 800 regels en iedere gedragsslice onder 600 productregels. De formele
repositorybrede PC01-teller blijft open, dus dit is geen nieuwe totale
LOC-baseline of optimalisatieclaim.

## Bewezen

- Alleen een intern geverifieerde Ed25519-release kan de canonieke provenance
  en daarmee de exacte providerregistry selecteren.
- Directe programma's binden index 0 aan de gehashte programmabytes. Python
  inlinecode staat exact direct na één `-c`, met alleen een gesloten flagset;
  `-m`, een ander codeargument en de `-c <andere code> <programma als argv0>`-
  bypass worden geweigerd.
- Gepinde reguliere bestanden worden met `O_NOFOLLOW` geopend, gehashd, naar
  een sealed memfd gekopieerd en via dezelfde filedescriptorcontext uitgevoerd.
  Een wijziging van het oorspronkelijke pad na de snapshot verandert de uit te
  voeren bytes niet.
- De autorisatie hercontroleert het gevalideerde plan, de volledige actuele
  catalogus en gelockte resource-, activation-, binding-, policy- en
  manifestauthority. Zij schrijft atomair maximaal één
  `dispatch_authorized`-attempt en hetzelfde release-/providerbewijs in de
  append-only tijdlijn.
- Geforceerde rollback laat nul resten achter; concurrency levert één attempt;
  herstart en exacte replay maken geen tweede attempt. Intrekking, scopefout en
  payload-/grant-/tijdlijncorruptie falen gesloten.
- MC03b1 exposeert geen RPC-exchange en start geen proces. Provider-I/O en
  waarnemingsopslag zijn MC03b2.

## Uitgevoerde controles

- 51 gerichte planning-, connector-, release-, ledger- en PostgreSQL-tests.
- 24 afzonderlijke operationele-tijdlijntests tegen PostgreSQL 16.13.
- 9 releaseverificatie-, A/B- en operatorregressies.
- Ruff, `py_compile` en `git diff --check` groen op alle gewijzigde bestanden.
- De architectuurchecker is repositorybreed nog rood door 11 reeds bestaande
  of ongerelateerde overtredingen. De HEAD-baseline had er 15; MC03b1 voegt er
  geen toe en verwijdert de eerdere directe Pydantic-import uit
  `action_chain_ledger.py`. Dit is uitdrukkelijk geen groene architectuurgate.

## Reviewverloop

De onafhankelijke reviewer gaf eerst `NO-GO` op drie punten: een door de caller
gekozen registryhash, zoeken naar programmabytes op een willekeurige argv-plek
en een check-path/execute-path-race. Release-provenance, expliciete binding en
sealed memfd-snapshots sloten die punten. De herreview vond vervolgens nog een
Python `-c`/`argv0`-bypass. Na de gesloten launchersemantiek en negatieve test
gaf de reviewer `GO`, met één expliciete vervolgvoorwaarde: MC03b2 controleert
authority en deadline opnieuw vlak vóór echte provider-I/O.
