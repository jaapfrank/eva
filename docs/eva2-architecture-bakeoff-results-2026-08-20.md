# Eva 2 architectuurbakeoff: resultaten

Status: afgesloten eerste selectieproef

Datum: 20 augustus 2026

Besluit: [ADR-0001](adr-0001-eva2-living-core.md)

## Samenvatting

De bakeoff heeft niet aangetoond dat een complete kandidaat productierijp is. Hij heeft wel de
componentkeuze voldoende vernauwd om geen tijd meer te besteden aan Eva 1-reparaties of een
nieuwe brede frameworkvergelijking.

- Restate valt af voor Eva 2.
- Een zelfgebouwde SQL-workflowengine valt af.
- Het eerste DBOS-prototype valt af, maar DBOS-core blijft de gekozen durable component voor één
  schone verticale validatie.
- Pydantic AI wordt de dunne typed planneradapter en geen effect- of stateauthority.
- PostgreSQL wordt de enige canonieke datalaag.
- SQL/full-text wordt de eerste memoryprojectie; externe memoryframeworks vallen voorlopig af.

`winnaar` uit het oorspronkelijke sealed contract blijft leeg. ADR-0001 heeft daarom status
`selected_for_validation` en niet `accepted` of `production_ready`.

## Uitgevoerde verticale kandidaten

| Kandidaat | Positief bewijs | Beslissend negatief bewijs | Uitkomst |
| --- | --- | --- | --- |
| Kleine SQL-state-machine | Happy path en afzonderlijke crashpaden waren reproduceerbaar | Werkelijk overlappende ingress deed twee modelcalls en 21 connectorcalls; definitieve reject gaf geen bruikbare terminale uitkomst | Afgevallen als workflowengine |
| DBOS Python 2.30.0 en PostgreSQL | Overlappende identieke invocations gaven bytegelijke uitkomst, één modelcall en drie externe effecten; lokale Postgres-state overleefde containerreconstructie | Disconnect na remote 201 werd duurzaam `ERROR`; replay bleef fout. Dezelfde workflow-ID met andere inhoud gaf stil de oude uitkomst. Canonieke Eva-data ontbrak buiten DBOS-internals | Prototype v1 afgewezen; component geselecteerd voor één nieuwe vertical |
| Restate Server 1.7.2 en Python SDK 1.0.4 | Duplicate ingress, crash/restart, 429, 422 en een versiegebonden raw-statecopy werkten in de fixture | Echte correctie tijdens een lopende run supersedede niets; oude effecten committen. Providerbreaker, secretisolatie en frameworkonafhankelijke export bleven onbewezen. Geen native Windows-serverpad | Afgevallen |

## DBOS-v1: waarom de fout zichtbaar blijft

De onafhankelijke disconnect-na-commitproef accepteerde de agenda-aanmaak extern met HTTP 201.
De verbinding werd daarna verbroken. De connectorbroker liet
`http.client.RemoteDisconnected` ongetypeerd ontsnappen en de DBOS-effectstep had retries uit.
DBOS sloeg de fout daarom duurzaam op. Eerste invocation en replay eindigden beide met exitcode 1.

De foutartefacten uit de onafhankelijke run hebben deze hashes:

| Artefact | SHA-256 |
| --- | --- |
| Eerste failurelog | `978d2766e544a8ae197828f055768c12415114e607e3d1bcb0048c2dc65bccb7` |
| Replay failurelog | `f6f050496a2d3d653cd0455c564856e715ed01ca148343b307fc7e06d0b4b18e` |
| Concurrent invocation A | `50a8121f24c8424c2ee3838068b2255b23768d53e3d6de1c6b1a38ce3729575d` |
| Concurrent invocation B | `50a8121f24c8424c2ee3838068b2255b23768d53e3d6de1c6b1a38ce3729575d` |
| Oorspronkelijke conflictuitkomst | `3e6e2aaa0c3472c8baa1d3655e8aa7724a4164439c052a52280bfe87d92fb603` |
| Gewijzigde conflictuitkomst | `3e6e2aaa0c3472c8baa1d3655e8aa7724a4164439c052a52280bfe87d92fb603` |
| Dump van alleen DBOS-internals | `002c52f0169f1f4c420a794fff693814ec224faf6ec98f5e3a2af0ec390b4aa9` |

Een afzonderlijke API-proef bewees dat dit geen noodzakelijke DBOS-beperking is. Met maximaal
twee attempts en retry uitsluitend voor een getypeerde ambigue transportfout deed attempt twee
eerst GET-readback. Er ontstond één extern effect, een gereconcilieerde receipt en een terminale
DBOS-success. Deze proef repareert alleen dat commitvenster. Zij bewijst nog geen supersession,
circuit-breaker, canonieke export of connectorisolatie.

Daarom wordt DBOS-v1 niet gerepareerd. De nieuwe vertical begint bij de ADR-invarianten en niet
bij deze prototypecode.

## Restate: doorslaggevende grenzen

Twee werkelijk overlappende invocations met dezelfde workflowkey waren correct gededupliceerd.
Een daaropvolgende supersessionproef hield de oude modelrun drie seconden vast en diende een
nieuwere correctie voor 16:00 in. De oude 15:00-run committeerde toch alle effecten; beide clients
kregen de oude uitkomst. De kandidaat had geen duurzame correctionhandler of fence.

De offline restore kopieerde Restate's eigen data-directory naar een tweede runtime van dezelfde
versie. Dat bewees disaster recovery, geen export die zonder Restate leesbaar is. De worker erfde
bovendien de volledige procesomgeving en zag een geheime sentinel. Restate zou dus naast de
canonieke database nog een stateful server, een extra exitpad en extra secretisolatie vereisen.

Deze combinatie, plus BSL 1.1 en het ontbrekende native Windows-serverpad, maakt Restate voor Eva
minder beheersbaar dan de DBOS-library met application-owned PostgreSQL.

## Memoryresultaten

### Canonieke SQL-events

Dezelfde semantische proef draaide op 10.000 en 100.000 events. Beide schalen bewezen:

- actuele en historische temporaliteit;
- exacte bronprovenance;
- nul tenantlekkage;
- nul promotie van onbetrouwbare instructies;
- crash na geaccepteerde write en vóór readback;
- retraction, fysieke erase en equivalente rebuild;
- fallback naar SQL/full-text wanneer een afgeleide kandidaat uitvalt.

| Schaal | p95 recall | Totale proefduur | Opslag | Projectierebuild |
| ---: | ---: | ---: | ---: | ---: |
| 10.000 | 0,125 ms | 3,18 s | 15.093.760 bytes | 306,9 ms |
| 100.000 | 0,125 ms | 20,85 s | 169.971.712 bytes | 1.840,7 ms |

### Hindsight en Graphiti

Hindsight 0.9.1 draaide echt lokaal met `provider=none`. De 10k-infrastructuurproef kon crash,
erase en rebuild aan, maar bewees zonder extractiemodel geen semantische kwaliteit. Gemeten p95
was circa 6,17 seconden, de piek circa 1,1 GiB RAM en de volledige 10k-run circa 837 seconden.
Een 100k-run is daarom niet uitgevoerd en staat `not_proven`.

Graphiti is niet geïnstalleerd. Er was geen geautoriseerd modelaccount en de officiële lokale
Kuzu-route is deprecated. Alle claims blijven `not_proven`.

Deze resultaten kiezen geen extern memoryframework. SQL-events blijven authority; SQL/full-text
wordt de eerste herbouwbare projectie.

## Model- en plannerbesluit

Alle kandidaten gebruikten dezelfde getypeerde Pydantic AI TestModel-grens. Dat bewees contracten
en reproduceerbaarheid, niet echte modelkwaliteit.

Pydantic AI 2.32.1 is gekozen als dunne planneradapter omdat het:

- de bestaande getypeerde `EpisodePlan`-grens al uitvoerde;
- meerdere providers achter één modelinterface ondersteunt;
- als slim MIT-pakket zonder ongebruikte integraties kan worden geïnstalleerd;
- achter Eva-eigen `PlannerRequest` en `ModelDecision` volledig vervangbaar blijft.

Alle Pydantic AI-tools, sessions, memory, MCP en durable-executionfeatures blijven uit. De
OpenAI Agents SDK wordt niet gekozen omdat Eva juist niet wil dat de model-SDK tools, handoffs,
sessions of tracingauthority overneemt. Een minimale zelfbouwloop blijft alleen de exitproef.

## Wat uitdrukkelijk nog niet bewezen is

- de nieuwe DBOS/PostgreSQL-vertical bestaat nog niet;
- application-owned transacties en DBOS-checkpoints zijn nog niet samen in de Eva 2-schema's
  bewezen;
- echte in-flight supersession en provider-circuit-breaking zijn nog niet in Eva 2 bewezen;
- de connectorworker- en credentialgrens bestaat nog niet;
- Pydantic AI is nog niet met dezelfde live Eva-cases op twee echte providers beoordeeld;
- installer, portable export, tweede-systeemrestore, A/B-update en uninstall bestaan nog niet;
- geen echte connectorcanary of concurrentvergelijking met OpenClaw, Hermes of Agent Zero is
  uitgevoerd;
- geen Eva 1-data is geselecteerd of gemigreerd.

Deze punten zijn bouw- en acceptatiepoorten van ADR-0001. Zij maken de doelstack niet opnieuw
open, tenzij een engine-intrinsieke invariant faalt.

## Bewijsbestanden

- [Content-addressed beslismanifest](../experiments/eva2-architecture-bakeoff/evidence/architecture-bakeoff-decision-442b6e0dc1434da9dcd31f88d8395be0332d0d46b15d74f7ed5850ce262dbf1c.json)
- [Sealed bakeoffcontract](eva2-architecture-bakeoff-v1.md)
- [ADR-0001](adr-0001-eva2-living-core.md)
- [Memory-eindmanifest](../experiments/eva2-architecture-bakeoff/memory/evidence/memory-run-2652a3e97aafccc7c083dc4af7cf766932b7acb0b53215e3dff9c793512ae092.json)
- [SQL-memorybewijs](../experiments/eva2-architecture-bakeoff/memory/evidence/sql-memory-probe-b3388b9c29c2d3ce5cee19a757d5e06a1839da925567ab891d2733aef0d34ae6.json)
- [Hindsight-infrastructuurbewijs](../experiments/eva2-architecture-bakeoff/memory/evidence/hindsight-memory-probe-514c30a8e7dae3751f72629bb2c0c552f35ca2550728a349b3bdf7c530c962c8.json)
- [Kandidaatblockers](../experiments/eva2-architecture-bakeoff/memory/candidate-blockers.json)

## Freezecontrole

Alle JSON-contracten en manifests parsen, alle Pythonbestanden compileren, `ruff check`, de
shellsyntax en `git diff --check` zijn groen. Het beslismanifest heeft exact SHA-256
`442b6e0dc1434da9dcd31f88d8395be0332d0d46b15d74f7ed5850ce262dbf1c`.

`ruff format --check` zou tien gedeelde prototypebestanden mechanisch herschrijven. Dat is bewust
niet gedaan nadat hun bytes de uitgevoerde runs en het beslismanifest hadden bepaald. Deze
wegwerpprototypes worden niet naar productcode gepromoveerd; de nieuwe vertical begint met een
eigen groene formatgate.
