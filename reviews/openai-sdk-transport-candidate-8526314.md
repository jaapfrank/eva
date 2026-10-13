# Onafhankelijke review — OpenAI SDK-transport DF02a

Bijgewerkt: 26 augustus 2026.

## Uitkomst

`GO` voor exact codecheckpoint
`8526314fde754ba56cbedde4be352f2496ed4910` als afgeschermde,
optionele dependencykandidaat. Dit is geen toestemming om productie, kandidaat B
of de release-lock om te schakelen.

De officiële OpenAI Python-SDK `3.3.1` neemt in deze kandidaat één gewone
Responses-request- en streamingroute over. Eva blijft eigenaar van identiteit,
persoonscontext, de atomische token/accountlease, tooltoestemming,
tooluitvoering, retrybesluit, usagejournal en eindantwoord.

## Onafhankelijk bevestigd

- De SDK wordt bij de kandidaat-modelconstructie geladen. De eerste echte
  modelcall gebruikt daarna één absolute deadline.
- Een schone koude productiecompositie stopte bij een grens van één seconde na
  ongeveer `1,001` seconde.
- Het resterende budget loopt door clientopbouw, SDK-call, requestworker en
  antwoordstream heen.
- Zes extra trage-header- en trage-streamproeven lieten na opruiming geen extra
  `eva-openai-*`-threads en geen groei van open bestandsdescriptors achter.
- Een opzettelijk vijandige HTTP 500 lekte in de volledige traceback geen
  toegangstoken, account-ID, prompt of geheime providerbody. De fout verlaat de
  adapter zonder SDK-cause/context/request/response.
- Remote plain HTTP wordt geweigerd; alleen een letterlijke loopback-IP mag in
  lokale tests HTTP gebruiken.
- 58 relevante regressies uit een schoon archief van exact het checkpoint en
  aanvullende deadline-, redactie- en resourceproeven waren groen. Ruff was
  groen.

## Eerdere reviewbevindingen

De eerste review hield de kandidaat terecht tegen omdat de wandklokgrens te
laat begon, SDK-exceptions onderliggende geheime objecten konden vasthouden en
remote plain HTTP was toegestaan. Een volgende review vond dat een koude
SDK-import nog buiten de grens viel. Het genoemde checkpoint bevat de fixes en
regressies voor alle vier punten.

## Bewijsgrens

`reviewed` betekent hier uitsluitend dat de lokale kandidaatcode en haar
fail-closed contract onafhankelijk zijn gecontroleerd. Nog open zijn:

- een echte OpenAI/Codex-providercall en eventpariteit;
- p50/p95-latency tegenover Eva's oude transport;
- amd64- en arm64-wheelhouse, licenties, SBOM en kwetsbaarheidsscan;
- offline kandidaatimage, managed-B-adoptie en rollback;
- iedere voice- of Realtime-claim.

De bestaande `legacy_urllib`-route blijft standaard en de productie-lock bevat
de SDK niet.
