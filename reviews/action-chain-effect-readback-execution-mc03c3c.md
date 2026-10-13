# MC03c3c — effectcontrole eenmaal uitvoeren en bewaren

Bijgewerkt: 25 augustus 2026.

## Status

- Implementatie: `implemented` en `tested` op `77923e2`.
- Onafhankelijke review: **open**. Dit bestand beschrijft review-input en keurt
  zijn eigen implementatie niet goed.
- Live bewijs: niet uitgevoerd. Productie A, kandidaat B, netwerk en Home
  Assistant zijn niet benaderd of gewijzigd.
- Optimalisatie: alleen codeomvang is gemeten; latency, geheugen en kosten zijn
  `unmeasured`.

## Wat nu werkt

Na de duurzame call-start voert één executor uitsluitend het aan de eerste
caller vrijgegeven sealed providerpakket uit. Het resultaat wordt daarna in één
PostgreSQL-transactie opgeslagen als provider-evidence, terminale readattempt,
optionele `ReadObservation` en terminale readbacktijdlijn.

Een succesvolle `effect.readback` moet het speciale getypeerde
`ReadbackResult` gebruiken. Een gewone `read_ok` kan daardoor niet als
effectcontrole doorgaan. De projectie controleert opnieuw:

- exacte RPC-methode, connector, request, correlatie en manifest;
- idempotency key, oorspronkelijke effectrequesthash en providerresource;
- outputschema en host-/provider-tijdvenster;
- attempt, grant, deadline, effectstap en eigen readbacktijdlijn.

Een herstart na een opgeslagen resultaat leest dat resultaat terug. Een
herstart na alleen call-start krijgt geen uitvoerbaar pakket en start dus geen
tweede proces.

## Bestanden en omvang

| Bestand | Rol |
|---|---|
| `src/eva/operations/action_chain_effect_readback_executor.py` | Bezit de enige readback-providercall en geeft replay geen callmateriaal |
| `src/eva/operations/action_chain_read_results.py` | Projecteert gewone reads en exact de gereserveerde `effect.readback`-uitkomst |
| `src/eva/adapters/postgres/action_chain_read_results.py` | Bewaart gewone leesresultaten en effectreadbackresultaten achter dezelfde atomische grens |
| `src/eva/adapters/postgres/action_chain_effect_readback_validation.py` | Valideert ook de terminale readbacktijdlijn corruption-sensitive bij reload |
| `tests/postgres/test_action_chain_effect_readback_executor.py` | Bewijst succes, protocolfout, replay, onopgeloste call-start en rollback |

- Netto productgroei: 258 regels.
- Nieuwe executor: 159 regels; ieder gewijzigd productbestand blijft onder 800
  regels.
- Geen schema, migratie of dependency toegevoegd.

## Uitgevoerde controles

- Vier gerichte cases op een verse tijdelijke `postgres:16.13` met verplichte
  `eva2_action_chain_disposable_`-databaseprefix.
- 58 brede action-chaincases op een tweede verse PostgreSQL 16.13-database.
- 24 operationele tijdlijncases op een derde verse PostgreSQL 16.13-database
  met verplichte `eva2_living_disposable_`-prefix.
- Een synthetische `matches`-uitkomst wordt eenmaal uitgevoerd en als exacte
  readbackwaarneming bewaard; replay en een nieuwe engine starten niets opnieuw.
- De echte lokale fixtureworker veroorzaakt een protocolfout; die fout wordt
  eenmaal en exact bewaard en blijft na herstart zichtbaar.
- Een achtergelaten call-start levert geen tweede proces en geen verzonnen
  resultaat op.
- Een geforceerde fout bij het schrijven van de observation rolt evidence,
  observation, terminale attempt en resultaatevent samen terug. De eerdere
  call-start blijft zichtbaar en wordt niet opnieuw uitgevoerd.
- Ruff, compileall en `git diff --check` zijn groen; tijdelijke containers en
  databases zijn verwijderd.

De repositorybrede architectuurchecker blijft op de brede werkboom rood door
de eerder vastgelegde import-boundary-overtredingen buiten deze slice. Deze
slice claimt daarom geen groene repositorybrede architectuurgate.

## Bewijsgrenzen

- De succesvolle provideruitkomst is synthetisch; alleen de protocolfout startte
  de echte lokale fixtureworker.
- De opgeslagen `ReadObservation` is nog geen fysiek `EffectReadback/v1` en
  schrijft geen `STATE_VERIFIED`.
- `matches`, `conflict`, `not_observed` en `inconclusive` zijn nog niet tegen
  effect, receipt en gewenste doeltoestand gepromoveerd.
- Effectstap, continuation en dependencies blijven gesloten en `running`.
- Er is geen onafhankelijke review, B-deploy, Telegramproef of live Home
  Assistant-readback.

## Open vervolg

1. Bind het terminale providerresultaat en de observation aan exact effect,
   attempt, receipt, doelresource en oorspronkelijke effectrequest.
2. Schrijf één fysiek `EffectReadback/v1`; alleen verse status `matches` met
   exacte doeltoestand mag `STATE_VERIFIED` opleveren.
3. Voltooi effect, stap, continuation en vrijgegeven dependencies atomair; alle
   andere statussen blijven gesloten of vragen hulp.
4. Laat dit checkpoint onafhankelijk reviewen en voer daarna de echte
   Telegram-B → Home Assistant-proef met crash tussen effect en readback uit.
