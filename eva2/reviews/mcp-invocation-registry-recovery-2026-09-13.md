# PG registry + invocation recovery — 13 september 2026

Test-only kandidaat; geen nieuw productdefect of runtimewijziging. Gebaseerd op f7781170.

`tests/postgres/test_mcp_invocation_registry_recovery.py`: **1 passed in 2.44s** op bestaande geïsoleerde PostgreSQL 16.13 met alle timeline-migraties. De test hergebruikt catalogus/transport/command-fixtures uit de bestaande invocation-tests en de bestaande registry- en PG-fixtures.

De keten registreert en ontdekt een immutable invoke-binding in echte PostgreSQL, laat één gesimuleerde gateway-dispatch via de echte discoveryadapter uitvoeren en onderbreekt vóór observatie. TOOL_EXECUTED is dan duurzaam. Na sluiten van de oorspronkelijke pool worden engine, registry, timeline en discoveryadapter opnieuw gemaakt. De connector wordt ingetrokken en het testmandaat gepauzeerd. Iedere nieuwe GET/POST faalt direct in de transportdouble.

Herstel leest het opgeslagen dispatchantwoord, schrijft precies één STATE_VERIFIED en behoudt external_state_verified=false. Exact dezelfde eindproef wordt opnieuw gelezen zonder eventgroei. Totaal één catalogus-GET en één invocation-POST, beide vóór herstel. Nieuwe execute, resolve_for_turn en read-observatie worden geweigerd, evenals ontdekking namens een andere persoon.

Het testmandaat staat uitsluitend observe/invoke na pauzeren toe, conform het bestaande registrycontract voor het al toegelaten antwoord. Geen nieuwe privacyrechten: er wordt geen actuele externe toestand opgehaald. Dit is geen bewijs dat een volledige applicatie-autorisatieketen of een productmandaat op dezelfde wijze is aangesloten. De eigenaarcontrole is een expliciete fixturecallback; de registry valideert daarnaast zijn eigen scope. Runtime-adapters en PG-opslag zijn echt; gatewaytransport en resultaat zijn synthetisch. Geen HTTP-server, model, externe dienst, persoonlijke data, OS-crash of databaseherstart gebruikt.

Reproductie vanuit eva2 met bestaande testvenv `/home/administrator/project/.eva2-artifacts/native-voice-offline-env-w8rt00c4/venv`:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src EVA2_TEST_POSTGRES_DSN=<disposable PG16.13 DSN> \
  <venv>/bin/python -m pytest -q -p no:cacheprovider --tb=short \
  tests/postgres/test_mcp_invocation_registry_recovery.py
```

Fixture vereist databaseprefix `eva2_living_disposable_`, exacte PG16.13 en leeg Eva-schema. Proef gebruikte afzonderlijke container `eva2-review-invoke-pg`, tmpfs en willekeurige loopbackpoort, zonder productiemounts; na afloop verwijderd. Bestaande image en dependencies gebruikt. De eerste testversie maakte de andere-persoonbeurt met een inconsistent audience; die fixtureconstructie is vervangen door ConversationTurn.private, waarna de daadwerkelijke registry-afwijzing wordt getoetst.

Onafhankelijke review nog vereist. Geen centrale werkbordmutatie of volledige integratie-/productacceptatie.