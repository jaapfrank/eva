# AU-007 — aanvullende onafhankelijke technische afsluiting

Datum: 2026-09-10.
Reviewer: `/root/au002_release_review` (werkbordrol `memorydeliveryreviewer`).
Besluit: **GO voor de technische afsluiting van de begrensde AU-007-oplevering**.

Deze aanvullende review bindt het eerder gegeven onafhankelijke technische
product-GO aan de definitieve implementatie en de vastgelegde bewijscommit.
Zij opent geen codeonderzoek, tests of praktijkproef opnieuw.

## Exacte bewijsbinding

- Kaart: `AU-007` — zelf een relevant privaat voorstel uitwerken.
- Implementatiebasis: `ff8071c08a41c5e73036acc43a1d642f99a08f1b`.
- Volledige implementatiecommit: `34ed766a1947e78a268b800728d227218aaa8f14`.
- Bewijscommit: `f99b5a2e9561ce96d4b77668b3caf9b813339239`.
- Directe ouder van de bewijscommit: `0b852229cf8667c6c92a98440c3d703bf9fe1b63`.

De bewijscommit bevat uitsluitend de volgende twee paden. De SHA-256-waarden
zijn berekend over hun exacte Git-inhoud in die commit:

| Bewijsbestand | SHA-256 |
|---|---|
| `eva2/evidence/product/AU-007-private-initiative-2026-09-10.json` | `0e370cf585809cf3fab134684c0fedc576ccbc0d7839a950abf1110e5cd92516` |
| `eva2/reviews/AU-007-private-initiative-34ed766.md` | `8397297295e90162c44035cbc7b62bfa3e5996fc00ecba96521a6f698352fa80` |

De eerder gecontroleerde oplevering betreft liveimage
`sha256:35fdc82c8d041a58d9da8be1fdf59d60844e8499a37e7fbd1ad6a261491b515e`.
Vijf geladen modulehashes waren gelijk aan de implementatiebron. Veertien
gecontroleerde payloadhashes verbinden de native waarneming en eigen keuze
met de duurzame oplevering en `done` / `release_ready` op de eigenaarroute.

Het resultaat staat in
`eva2/evidence/product/AU-007-beoordelingskaart-huisautomatisering-2026-09-10.md`,
vastgelegd in `0b852229cf8667c6c92a98440c3d703bf9fe1b63`, met SHA-256
`9f351ab6f928fbf0a7010e44d95e8ce520374b7a34b65990f8abbf312d520522`.
Dit is dezelfde inhoud als de eerder gecontroleerde eigenaar-download,
opgeslagen prototype en private bundel.

## Afsluiting en eigenaarsignaal

De oorspronkelijke technische GO blijft geldig: Eva koos en leverde één
bruikbare private beoordelingskaart met beslisregels en drie duidelijk
fictieve voorbeelden. Er is geen claim van actuele huisanalyse of externe
uitvoering, en geen concrete technische blocker voor deze ene oplevering.

De coordinator gaf als aanvullend eigenaarsignaal door dat Jaap de kaart
"op zich wel bruikbaar" vindt. Dat oordeel is uitsluitend op deze kaart
betrokken. Het is geen algemene acceptatie van Eva's initiatiefgedrag,
zelfstandigheid of voorsprong op andere systemen.

De eerdere bewijsstukken houden hun historische vermelding dat menselijke
beoordeling toen nog openstond. Met dit begrensde eigenaarsignaal en de reeds
voltooide technische GO kan AU-007 administratief als `done` worden afgesloten.
Het ruimere initiatiefmandaat en de uitvoering daarvan behoren tot AU-008;
deze review verleent daarvoor geen implementatie- of product-GO.

## Gebruik voor het werkbord

Gebruik de twee paden uit de bewijscommit als `testReceiptPaths`,
`f99b5a2e9561ce96d4b77668b3caf9b813339239` als `evidenceCommit`, en uitsluitend
dit aanvullende reviewbestand als `evidencePaths` van de aparte `reviewCommit`.
De beslissing blijft `GO`, gebonden aan implementatie
`34ed766a1947e78a268b800728d227218aaa8f14` en de onafhankelijke reviewer hierboven.
