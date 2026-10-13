# Onafhankelijke review — duurzame subagent in apart proces

Datum: 26 augustus 2026.

Beoordeelde implementation commit:
`9898926c3e70a80bedd0b31bdacf01e76cbc5bca`.

## Besluit

De technische, product- en release-reviewers geven **GO voor de smalle status
`tested`**. Eva kan een al toegelaten, toolvrije proeftaak vanuit haar duurzame
PostgreSQL-wachtrij in een echt apart OS-proces laten uitvoeren. Twee workers
starten samen niet twee modelruns. Stoppen of crashen wordt eerlijk bewaard en
een onzekere modelrun wordt niet blind herhaald.

Dit is nog geen subagentfunctie voor de gebruiker. Een echte modelprovider,
starten/volgen/stoppen vanuit Eva-chat, eigenaar-authenticatie, een
product-entrypoint en managed-B-deploy zijn afzonderlijk open.

## Wat onafhankelijk is gecontroleerd

- De technische reviewer herhaalde acht gevallen met een eigen disposable
  PostgreSQL 16.13 en controleerde Ruff en de Git-diff. De genomen lease is vóór
  de call-start zichtbaar; na een harde crash wordt zij pas na verloop veilig
  hernomen.
- De release-reviewer herhaalde dertig productlockchecks. De productversie
  blijft Pydantic AI `2.32.1`; `2.34.0` en Harness `0.25.0` blijven aparte
  kandidaten.
- De productreviewer herhaalde vijfentwintig snelle checks en controleerde dat
  een nette stop als `gestopt` en een harde crash als `contact kwijt` wordt
  uitgelegd.
- Een schone Git-archive van `9898926` gaf 39 snelle tests en 18 echte
  PostgreSQL-tests groen. De vijf afzonderlijke procesgevallen waren daarnaast
  groen op de Pydantic AI `2.34.0`-kandidaat.

De volledige commando's, omgevingsgrenzen, hashes en nonclaims staan in
`evidence/cockpit/durable-subagent-process-9898926-test-receipt.json`.

## Gesloten eerdere blockers

- Het childproces erft niet langer de hele parentomgeving. De proef weigert een
  inline DSN en bewijst dat parent-DSN, modelkeys en een geheime canary het
  childproces niet bereiken.
- De crash vóór call-start gebeurt aantoonbaar ná de duurzame claim. De test
  leest eerst `claimed` en geen `call_id`, doodt dan het proces en bewijst na
  lease-expiry exact één modelrun.
- De worker geeft het stopsignaal na claim één event-loopbeurt vóór de duurzame
  call-startgrens. Ontbrekende Linux-signaalbediening laat de service niet
  halfveilig starten.
- SIGTERM tijdens de modelrun wordt duurzaam `ambiguous/stopped`. SIGKILL na
  call-start blijft onzeker en wordt na lease-expiry als `contact_lost`
  getoond. Geen van beide paden start dezelfde modelrun opnieuw.

## Bewuste open grenzen

- geen echte provider, providerrekening of bewijs dat een externe provider een
  geannuleerde aanvraag werkelijk niet meer verwerkt;
- geen product-providerfactory, langdurige resident-run, release-entrypoint,
  Compose-service, B-deploy of schema-adoptie;
- geen processandbox, netwerkgrens, cgroup of CPU-/geheugenlimiet;
- geen herstel van een extern resultaat na harde kill: Eva kiest terecht voor
  onzekerheid zonder herhaling;
- geen start, status of stop vanuit Eva's privéchat en geen eigenaar-login op
  de cockpit;
- geen `live_proven`, gebruikersacceptatie of complete WB01-WB07-claim.

De volgende verticale slice is daarom niet browser of voice, maar dezelfde
route met één vaste echte modelprovider en daarna drie eenvoudige privéchat-
handelingen: starten, volgen en stoppen.
