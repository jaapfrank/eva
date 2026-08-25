# Onafhankelijke review — HA-MCP-pariteitscontract

Beoordeeld: 26 augustus 2026.

Checkpoint: `c03942df87b2e02ea5baa10908e31af66c79af4a`.

Oordeel: **GO voor een dunne, geïsoleerde pariteitskandidaat; geen GO voor
Home Assistant-, B- of productiepromotie.**

## Wat onafhankelijk is herhaald

- de exacte amd64-image en zeven toolbeschrijvingen;
- de bronbaseline van 1.023 regels op commit `1e780ca…`, inclusief beide
  bronhashes;
- exacte inclusions, exclusions, Eva→MCP-mapping en de uitgesloten
  `health()`-uitkomst;
- alle nonclaims, de grens van minimaal 300 regels weg tegen maximaal 180
  regels erbij en de drie verplichte vervolgvoorwaarden;
- twaalf fail-closed mutaties van metadata, bronbinding, mapping, bewijsvelden
  en toolhashes;
- Ruff, de sidecarchecker en de pariteitschecker;
- afwezigheid van een achtergebleven testcontainer.

## Resultaat

- pariteitschecker: groen;
- mutatietests: `12 passed`;
- Ruff: groen;
- de eerdere reviewblokkers op checkpoint `7c9f34e` zijn gesloten.

Alle zeven HA-MCP-outputs blijven open JSON-objecten. Dit contract bindt dus de
bron en relevante tooldescriptors, maar bewijst nog niet dat zoeken, historie,
service-uitvoering of automatiseringen dezelfde betekenis en foutafhandeling
hebben als Eva's huidige adapter.

De verdiepende audit schat bovendien slechts circa 174–192 direct vervangbare
transportregels. Dat is minder dan de promotie-eis van 300. Productcode mag pas
verdwijnen na een aparte state→service→stateproef, semantische pariteit, echte
least-privilege Home Assistant-toegang, crashbewijs, latencymeting en rollback.
