# RB-004 — onafhankelijke review builder-B-admission

Besluit: **NO-GO**

Beoordeelde keten:

- basis: `26753817bacef13cdbd52eb5169417f4d83df8a9`
- implementatie: `f89d3d72c01fa8d7917d44a5a79395f0301393a9`
- evidence: `cc81cef637a62c3636f2bb317f1d657d5670e84d`
- productbron: `45d2ff6d61793809a514c01b3a3c000ce1632da6`
- release-inputauthority: `20323cc166d5b7055c43dc94a0d5e09c47ab729036e479c166768a2a6e841020`

De commitketen klopt. De implementatie wijzigt exact de twee bordpaden en de
evidencecommit uitsluitend het ene evidencebestand.

## Wat groen is

- Alle publieke hashes van B-preflight, build, postflight, authority,
  kandidaat en admission komen exact overeen met de private bestanden.
- De B-keten bindt exact bron `45d2ff6d`, authority `20323cc…`, afzonderlijke
  observation-, build- en admissionchallenges en één stabiele builderidentiteit.
- De preflight is leeg en routeloos. De postflight bevat uitsluitend de
  ongetagde kandidaat; de drie verzegelde basisimages zijn niet in Docker
  geladen.
- Kandidaatarchive `10840641…`, image-ID `3a4f6554…`, BuildKit-resultaat,
  Docker-manifest, configblob, lagen en `diff_ids` zijn onderling gebonden.
- De admission van A en de afzonderlijk gehashte A-preflight zijn verplicht.
  B verschilt op alle zeven vereiste identiteiten: daemon, Docker-socket,
  state-root, BuildKit-node, containerd-socket en beide namespaces.
- Hergebruik of splice van A-admission/A-preflight, een van de zeven
  identiteiten, challenges, receipts, bron of authority wordt fail-closed
  geweigerd.
- De eerdere RB-003-admission blijft via de generieke interne verifier werken.
- De netto `+490/-44` bestaat uit generieke bewijsbinding en gerichte
  aanvalstests boven hergebruikte parsers; er is geen tweede build-, runtime-
  of deployroute toegevoegd.
- Het ontbreken van een single-builder-CLI is op zichzelf geen
  `doneWhen`-blokkade. De publieke functies zijn testbaar; een operator-CLI is
  latere ergonomie.

## Enige blokkade

Het publieke bewijs claimt `physicalBuildCalls: 1` en dat de eerdere mislukte
losse-daemon-readinesspoging nul buildcalls deed. Dat is niet onafhankelijk
herleidbaar uit de gehashte private bewijsset:

- preflight, build, postflight en admission bewijzen één toegelaten
  kandidaatreeks, maar tellen geen aanroepen van `docker buildx build`;
- een tweede identieke buildcall kan dezelfde ene uiteindelijke image en
  receiptuitkomst achterlaten;
- de bewaarde Docker- en containerd-unitlogs bevatten alleen daemon/runtime-
  observaties en geen sluitende CLI-calljournal;
- `failedPreflightScratchRemoved: true` staat uitsluitend in de publieke
  samenvatting; er is geen gehasht failure-/cleanupreceipt dat de mislukte
  readinesspoging, nul buildcalls en verwijdering van haar oude state,
  namespace en processen samen bindt.

Daarom kan deze review de expliciet vereiste eigenschap “exact één fysieke
buildcall, en nul tijdens de mislukte voorbereiding” niet onafhankelijk
bevestigen. De succesvolle B-build zelf hoeft niet inhoudelijk opnieuw te
worden ontworpen, maar het fysieke bewijs moet deze teller en de eerdere
failure-cleanup duurzaam vastleggen. Als oorspronkelijke, betrouwbare ruwe
call- en cleanupregistratie niet meer bestaat, is daarvoor een nieuwe B-run
nodig.

## Herhaalde controles

```text
TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/operations/test_two_builder_reproducibility.py
34 passed in 0.20s

ruff check tools/check_two_builder_reproducibility.py tests/operations/test_two_builder_reproducibility.py
All checks passed!
```

## Vereiste correctie

Bewaar vóór de readinesspoging een private run/challenge en een append-only of
atomair gefinaliseerde commandjournal. Bind daarin iedere buildstart, de
readinessuitkomst en cleanup-identiteiten. Laat preflight, postflight en het
publieke bewijs de hash daarvan opnemen. De geaccepteerde keten moet exact één
buildstart tonen; de mislukte voorbereiding exact nul en volledige verwijdering
van haar eigen state, namespace en processen.

Tot dat bewijs bestaat blijft RB-004 **NO-GO**. Dit oordeel maakt geen claim
over scan, signing, deployment of twee-builder-reproduceerbaarheid.
