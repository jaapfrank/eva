# CP-001 lokale Bottie P-256 client-ACK — 12 september 2026

Gebouwd en offline getest; onafhankelijke review open. Geen productcodewijziging,
externe provider/modelcall, productdatabase of menselijke ontvangst/acceptatieclaim.

Code `7f5bc5fe541ade7d76f8b66e67b48f356e1b36c5` (parent `2ba7abb7`).
Alleen bestaande benchmarkworker, Bottie-contracttest en README uitgebreid.
Deze bewijsnotitie verandert de code niet. Eigen worktree:
`/home/administrator/project/.eva2-artifacts/cp001-runner-20260912`.

## Echte bevestigingsketen

De adapter verkrijgt eerst één antwoord via de echte lokale Bottie-chatroute.
De fixture gebruikt exact de ontvangen responsebytes en writeheaders en
Botties bestaande `canonicalHttpWireSha256`/`canonicalHttpClientAckPreimage`.
Node crypto ondertekent met de bij dat verzoek gebonden P-256-fixturesleutel.
De privésleutel blijft in de geïsoleerde worker. De echte
`/api/http-delivery/ack` verifieert en verwerkt de bevestiging.

De proef toont opslag versus bevestigde lokale aflevering afzonderlijk:

- Na chat: één prepared response; nul ACK-receipts, assistentberichten en chat_out.
- Verkeerde sleutel: canoniek ondertekende ACK geeft HTTP 400, acknowledged=false,
  delivered=false; dezelfde lege afleveringsreadback blijft behouden.
- Gewijzigde antwoordhash: responseRef wordt overeenkomstig gewijzigd en de
  canonical ACK correct ondertekend; de echte route weigert met HTTP 400 en
  maakt geen receipt/afleveringsregistratie.
- Geldige ACK: HTTP 200/delivered=true; één persistente receipt, één assistentbericht,
  één chat_out, nog steeds één prepared response.
- Exact dezelfde ACK opnieuw: hetzelfde HTTP-resultaat/receipt; alle onafhankelijke
  tellings- en receiptsnapshots blijven gelijk.

Er was één chatrequest en vier ACK-verzoeken. De twee modelstubcalls van de
chatbeurt nemen bij geen enkele ACK toe. De onafhankelijke SQLite-reader controleert
requestscope, receiptinhoud/-hash, responsehash, wirehash, signature en tellingsrijen.
Geen nieuwe ACK-engine, protocol of productcode gebouwd; het bestaande protocol
uit `src/web/httpack.js` en `/api/http-delivery/ack` wordt werkelijk uitgevoerd.

## Resultaat en artifacts

36 gerichte tests groen: vier echte Bottie-gevallen, twaalf native-adaptertests,
zeven fixture-runnertests en dertien EV-001-tests. De eerdere vier EVA2/PG-tests
behouden hun aparte bewijs. Ruff en ESLint op gewijzigde paden groen.

Duurzame map:
`/home/administrator/project/.eva2-artifacts/cp001-bottie-ack-20260912/`.

- `receipt.json`: exact commit/tree, veertien bronhashes, artifacthashes,
  ACK-statusreeks, één receipt/één chatrequest en expliciete beperkte betekenis.
- `evidence/*/readbacks.json` en `eva.db`: vier echte lokale HTTP-/SQLite-proeven;
  de ACK-proef bevat ook vijf onafhankelijke DB-snapshots vóór/na de ACK's.
- `tests.txt`, `junit.xml`: 36 passed, geen skip.
- `verify.py`: onafhankelijke heropening van alle vier SQLite-snapshots,
  stored replyhashes, ACK-statussen 400/400/200/200, identieke duplicate,
  nul effecten van ongeldige ACK's en precies één echte ACK-receipt.

De behouden Node22.22.3/npm-omgeving van de vorige Bottie-proef is hergebruikt;
geen download/dependencywijziging. Iedere worker draaide in een eigen user/network-
namespace met uitsluitend loopback, beperkte omgeving en tijdelijke private
SQLite-data. Alle workers eindigden exit0; snapshots zijn vóór het opruimen van
de tijdelijke directories bewaard. Geen extra product/serverproces blijft actief.

## Reviewgrens

Dit bewijst een correcte ACK van de lokale cryptografische testclient en
Botties éénmalige afleveringsregistratie. Het bewijst niet dat Jaap iets heeft
gelezen of geaccepteerd, noch live-aflevering, procescrashherstel of volledige
netwerkherstelketens. Totale usage, serverbudgetten, echte modelkosten,
productadmission en ranking blijven unknown.

Graag dezelfde onafhankelijke reviewer op wire/preimagehergebruik, verkeerd-
sleutel/gewijzigde-hashgevallen, echte ACK-route, onafhankelijke receiptreadback,
deduplicatie en één-chat/geen-extra-model-callbewijs. Geen brede benchmark-GO.
