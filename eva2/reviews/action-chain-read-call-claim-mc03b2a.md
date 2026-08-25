# Onafhankelijke review — action-chain readcallclaim MC03b2a

Bijgewerkt: 25 augustus 2026.

## Uitkomst

`GO` voor MC03b2a als bron- en PostgreSQL-checkpoint `376112f`. Deze slice
claimt alleen duurzaam welke caller de providercall mag beginnen. Er is geen
providerproces gestart, geen extern I/O-resultaat ontvangen, geen waarneming
opgeslagen en niets aan B gewijzigd.

## Waarom dit onderdeel bestaat

MC03b1 bewaart dat één read is geautoriseerd, maar tussen autorisatie en echte
provider-I/O kunnen twee workers of een procesherstart ontstaan. MC03b2a maakt
die overgang eenrichtingsverkeer: precies één caller commit vóór mogelijke I/O
`ACTION_CHAIN_READ_PROVIDER_CALL_STARTED`. Alleen die caller ontvangt het
exacte initialize-/invokepakket. Een concurrent of herstart krijgt geen
callable bytes meer en kan de providerpoging dus niet stil herhalen.

## Bestandseigenaarschap

- `src/eva/operations/action_chain_read_dispatcher.py` bezit uitsluitend de
  eenmalige call-startclaim, de laatste authority-/deadlinecontrole en de
  private reconstructie van het exacte RPC-pakket. Het voert het proces niet
  uit en schrijft geen provideruitkomst.
- `src/eva/operations/action_chain_read_authorizer.py` bezit de eerdere
  `dispatch_authorized`-fence en bouwt alleen attempt, grants en requesthash.
  Het exposeert geen initialize-/invokeobjecten.
- `src/eva/adapters/postgres/action_chain_read_validation.py` valideert dat het
  nieuwe call-start-event exact voortbouwt op de autorisatie, een geldige tijd
  heeft en geen waarneming of terminale attempt verzint.
- `src/eva/adapters/postgres/action_chain_ledger.py` laat het nieuwe childevent
  toe binnen dezelfde corruptiegevoelige action-chainprojectie.
- `tests/postgres/test_action_chain_read_dispatcher.py` bezit de
  concurrency-, herstart-, deadline-, no-I/O- en publiek-API-regressie.

## Bewezen

- Twee gelijktijdige workers leveren exact één `claimed`, één
  `already_started`, één readattempt en één duurzaam call-start-event.
- De eerste claim krijgt een manifestgebonden processpec, initialize en invoke;
  de requesthash is bytegelijk aan MC03b1.
- Een nieuwe SQLAlchemy-engine simuleert procesherstart en krijgt na het event
  geen processpec, manifest, initialize of invoke terug.
- De deadline wordt vlak vóór de call-startclaim opnieuw gecontroleerd. Op de
  deadline ontstaat geen event.
- Het procespad is in de test verboden via een falende `subprocess.run`-patch;
  hij wordt niet geraakt.
- Het codecheckpoint voegt netto 446 productregels toe. De nieuwe dispatcher
  telt 479 regels en blijft onder het maximum van 800 per nieuw productbestand
  en 600 netto productregels per normale slice. Latency, geheugen en kosten
  zijn voor deze no-I/O-slice `unmeasured`, dus er is geen optimalisatieclaim.

## Uitgevoerde controles

- 32 gecombineerde application-, ledger-, authorizer- en dispatcher-tests op
  een echte disposable PostgreSQL 16.13.
- 24 afzonderlijke operationele-tijdlijntests op PostgreSQL 16.13.
- Ruff, compileall en `git diff --check` groen op alle vijf code-/testpaden.
- De runtime-contractkaart was repositorybreed groen voor 76 bestanden. De
  repository-inventarisgate stopte op reeds aanwezige
  `GITIGNORE_CONTRACT_DRIFT`; deze slice wijzigt geen `.gitignore` of
  inventarisbron en claimt daarom geen groene volledige architectuurgate.

## Reviewverloop

De eerste onafhankelijke review gaf `NO-GO`: een publiek geëxporteerde helper
kon na een call-start het concrete RPC-pakket opnieuw opbouwen. De helper is
verwijderd. De authorizer exposeert nu alleen niet-callable authoritymateriaal;
de concrete exchange ontstaat uitsluitend in een private dispatcherfunctie en
pas nadat de actuele tijdlijn nog `DISPATCH_AUTHORIZED` is. Een API-oppervlaktest
borgt die grens. De onafhankelijke herreview voerde de twee PostgreSQL-tests,
Ruff en compile opnieuw uit en gaf daarna `GO`.

## Open vervolg

MC03b2b moet de eenmalig geclaimde processpec en RPC buiten de
databasetransactie uitvoeren en succes, exacte fout of ambigue crashgrens
duurzaam opslaan. Een call-start zonder terminale uitkomst mag na herstart niet
blind opnieuw worden uitgevoerd. MC03c voegt daarna pas effectuitvoering,
fysieke readback en dependency-advance toe.
