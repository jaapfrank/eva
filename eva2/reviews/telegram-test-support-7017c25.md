# Review — Telegram-testondersteuning (`7017c25`)

Datum: 26 augustus 2026

Reviewer: onafhankelijk, na vier reviewrondes

Besluit: **GO voor de begrensde testomgeving; geen Telegram-liveclaim**

## Wat nu af is

De testbot krijgt straks een eigen, lege database en een eigen testnetwerk. De
oude Eva, managed B, hun volumes en hun bot-token zijn geen invoer van deze
route.

- PostgreSQL krijgt een eigen benoemd volume en een intern Docker-netwerk.
- De testcore geeft alleen een vaste diagnosezin terug. Hij is bewust geen
  taalmodel en geen vervanger van Eva.
- De launcher controleert eerst de draaiende database, het volume, netwerk,
  image en de vaste core. Pas daarna mag het databaseschema worden gemaakt.
- De database-DSN wordt alleen in het geheugen gelezen om te eisen dat hij
  precies naar `postgres-test:5432/eva2test` gaat. De inhoud of het wachtwoord
  komt nooit in output, review of receipt terecht.
- Na het starten wordt de ingress opnieuw gecontroleerd. Bij een fout wordt
  juist de Telegram-poller gestopt; database en diagnosecore blijven staan om
  veilig te kunnen onderzoeken wat misging.

## Controle op dit checkpoint

```text
ruff check src/eva/operations/telegram_test_preflight.py \
  src/eva/operations/telegram_test_launch.py \
  src/eva/operations/telegram_test_core.py \
  tests/bootstrap/test_telegram_test_ingress_deployment.py
python3 -m py_compile src/eva/operations/telegram_test_{preflight,launch,core}.py
PYTHONPATH=src:. python3 -m pytest -s -q \
  tests/bootstrap/test_telegram_test_ingress_deployment.py
# 23 passed
docker compose --env-file deploy/living-core/telegram-test.example \
  -f deploy/living-core/telegram-test-ingress.compose.yml config
docker compose --env-file deploy/living-core/telegram-test.example \
  -f deploy/living-core/telegram-test-support.compose.yml config
git diff --check
```

De tests bevatten onder meer: een echte tijdelijke Docker-netwerkbinding die
zichzelf opruimt, weigering van extra ingressnetwerken en -mounts, weigering
van een volume met bind-opties, DSN-failoverweigering, geen schema vóór de
supportcontrole en stop van de poller na een foute eindcontrole.

## Wat dit niet bewijst

- Geen Telegram-bericht, BotFather-testbot, Telegram-account of testgroep is
  werkelijk gebruikt.
- De toekomstige testimage, PostgreSQL-containerbytes en testsecrets zijn nog
  niet als één echte verticale run gestart.
- De vaste diagnosezin bewijst geen Eva-gesprek, geheugen of
  gebruikersacceptatie.
- Eva A, managed B en hun data zijn niet gestart, aangepast of gedeployed.

Sinds deze review is de eerste praktische stap uitgevoerd: de lokale,
copy-only testimage is gebouwd op commit `e11c136` en importeert de
Telegram-testmodules. Die build is zelf gecontroleerd met 26 gerichte tests
voor testimage en testomgeving; deze review blijft echter gebonden aan
`7017c25` en is geen onafhankelijke review van de imagebuild.

De volgende stap is daarom praktisch: een nieuwe BotFather-testbot en twee
testaccounts klaarzetten en de privé-, koppel- en groepsproef werkelijk
uitvoeren.
