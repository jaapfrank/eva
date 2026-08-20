# Eva 2 architectuurbakeoff v1

Status: afgesloten uitvoeringscontract voor fase 1. De complete winnaar blijft leeg; de gekozen
doelstack en alle resterende validatiepoorten staan in
[ADR-0001](adr-0001-eva2-living-core.md). De gemeten uitkomsten staan in
[de resultatenbundel](eva2-architecture-bakeoff-results-2026-08-20.md).

## Doel

De bakeoff bepaalt eerst welke complete stack Eva 2 gaat gebruiken. Eva 1 is alleen een
bevroren incident- en gedragsbron. Geen kandidaat mag oude schemas, modules of programmeertaal
overnemen omdat zij er al zijn.

De keuze-eenheid is een werkende verticale Eva, niet een los framework. Iedere kandidaat bevat:

- install- en updatepad;
- durable execution;
- model- en toolloop;
- canonieke state en evidence;
- geheugenprojectie;
- connector- en credentialgrens;
- identity pack en natuurlijke replyprojectie;
- observability, export en rollback.

## Voorselectie

Alleen componenten met officiële documentatie, een herhaalbare lokale installatie, een voor Eva's
distributie en zelfhosting juridisch bruikbare licentie en een aantoonbare exitstrategie mogen
worden geprototypeerd. Een source-available licentie wordt niet als open source gerapporteerd.

### Durable execution

| Kandidaat | Rol in de bakeoff | Vooraf bekende grens |
| --- | --- | --- |
| Restate | Volledige disposable prototypekandidaat | De single-binary installatie en multi-language SDK's zijn aantrekkelijk, maar remote-effectreplay, state-export en upgrades moeten werkelijk worden bewezen |
| DBOS | Volledige disposable prototypekandidaat | De library plus PostgreSQL is eenvoudiger dan een cluster, maar databasebeheer, workflowversies en externe commitvensters moeten werkelijk worden bewezen |
| Kleine canonieke SQL-state-machine | Controlebaseline, niet automatisch de eindkeuze | Meet hoeveel eigen code werkelijk nodig is en voorkomt dat frameworkmarketing de vergelijking bepaalt |
| Temporal | Documentatiereferentie en fallback na een harde mislukking van beide kandidaten | Sterke durable execution, maar de self-hosted service en operationele last botsen mogelijk met installatie op één persoonlijk systeem |
| LangGraph | Alleen kandidaat voor modelgraph en interrupts, niet voor effectauthority | De officiële documentatie vermeldt dat nodes na checkpoints opnieuw kunnen uitvoeren, inclusief LLM- en API-calls |

Alle drie de durable opties gebruiken exact dezelfde vastgepinde Pythonversie, Pydantic-contracten,
deterministische modeladapter, effectbroker, fixture en meetinstrumentatie. Alleen Restate, DBOS
of de kleine SQL-state-machine verandert. Voor DBOS is PostgreSQL de vooraf gekozen, scorende
configuratie. SQLite draait uitsluitend als niet-scorende gevoeligheidsproef voor het
zero-installpad en kan nooit als beste-van-twee resultaat worden gekozen.

Restate is technisch prototypewaardig, maar zijn server gebruikt BSL 1.1 met een aanvullende
productiegrant en biedt in de officiële installatielijst geen native Windows-binary. Beide punten
zijn aparte hard gates. DBOS-core is MIT, maar een uitkomst die de proprietary Conductor nodig
heeft, telt niet als de lokale self-hosted kandidaat.

### Model- en toolloop

De eerste frameworkproef gebruikt een deterministische modeladapter. Daardoor kan een betere of
duurdere modelcall geen workflowfout verbergen. Alle hard-gate-overlevenden krijgen daarna
dezelfde echte modelproef; vóór die proef bestaat nog geen winnaar.

Te toetsen model/tool-lagen zijn op dezelfde gekozen Python durable laag:

- OpenAI Agents SDK voor Python;
- Pydantic AI in Python;
- een minimale typed Python direct-loop als controlebaseline.

Strikt TypeScript is daarna een afzonderlijke taal- en workervariant. Het wordt niet als
`loop-only` vergelijking gerapporteerd, omdat runtime, procesgrens en serialisatie dan ook wijzigen.

Geen van deze lagen mag connectorwrites, effectreceipts, identiteit of canoniek geheugen bezitten.
Een modeltool retourneert hoogstens een plan of vraagt een effect aan bij de effectgateway.

### Geheugen

De verticale episode draait eerst met canonieke SQL-recall. Daarna worden Hindsight en Graphiti
als geïsoleerde afgeleide projecties ingeplugd. Hindsight toetst brede temporal/provenance-memory;
Graphiti toetst de bi-temporele graphhypothese. Mem0 blijft een lichtere fallback, maar gaat niet
mee in de eerste proef omdat zijn gedocumenteerde model minder van Eva's moeilijke temporaliteits-
en provenance-eisen afdekt. Letta valt af als component omdat het een concurrerende agentruntime
is; LangGraph Store voegt zonder eigen applicatieschema geen geheugenbetekenis toe.

Geen memoryframework wordt authority voor identiteit, opdrachten, effecten of receipts. Iedere
afgeleide herinnering moet terugwijzen naar een canoniek event met tenant, subject, bronconnector,
`observed_at`, geldigheidstijd, supersession of retraction, truststatus en bronhash.

## Sealed verticale episode

Iedere stack krijgt exact dezelfde Nederlandse invoer, identity pack, connectorfixture, klok,
crashpunten en budgetten. Frameworkspecifieke namen komen niet voor in de stimulus of oracles.

### Identity en geheugen

- Installatie A heet `Eva`; een tweede schone installatie heet `Mira`.
- De gebruiker is dezelfde geattesteerde persoon via web en een tweede kanaal.
- Bekende voorkeur: afspraken van 30 minuten en bevestigingen in gewone Nederlandse taal.
- Correctie tijdens de proef: de eerdere voorkeur voor 14:00 wordt vervangen door 15:00.

### Natuurlijke opdracht

> Lees de nieuwste mail van Noor BV, zoek Noor op in het ERP, controleer wanneer we woensdag
> allebei kunnen, plan een afspraak van een half uur, maak een korte bevestiging en koppel terug
> wat er werkelijk is gelukt.

### Externe fixture

Een afzonderlijk proces met een eigen database simuleert realistische mail-, ERP-, agenda-,
document- en delivery-API's. Eva kan die database niet rechtstreeks lezen. De fixture bewaart
per request de bytes, identity, idempotency key, commitstatus, response, readback en wall clock.

De fixture bevat:

- één nieuwste mail met stabiele message-id en inhoudshash;
- één ERP-klant met stabiele resource-id;
- free/busy-data voor beide partijen;
- een idempotente calendar-create met onafhankelijke GET-readback;
- een idempotente document-create met inhoudshash en GET-readback;
- een idempotente deliverysink met zichtbare message-id en GET-readback;
- injecteerbare 429, timeout, disconnect-na-commit en definitieve reject.

### Verplichte uitkomst

Een geslaagde episode levert exact:

- de gelezen mail-id en inhoudshash;
- de juiste ERP-resource-id;
- één afspraak op de actuele gecorrigeerde tijd;
- één document met de juiste afspraakinhoud;
- één zichtbare bevestiging;
- een receipt en onafhankelijke readback per effect;
- één natuurlijke reply die succes, deelresultaat en onzekerheid niet door elkaar haalt;
- terminale episode- en deliverystate zonder ghost work of stille retrylus.

## Fout- en herstartmatrix

De runner stopt het echte kandidaatproces op ieder van deze grenzen en start het met dezelfde
duurzame data opnieuw:

1. vóór de eerste modelcall;
2. na het duurzame plan en vóór de eerste connectorcall;
3. na ieder extern commit maar vóór lokaal receipt;
4. na lokaal receipt maar vóór de volgende stap;
5. na de laatste deliveryacceptatie maar vóór episodeclosure;
6. tijdens een provider-429 met `Retry-After`;
7. nadat een nieuwere gebruikerscorrectie is toegelaten maar vóór de oude beurt sluit.

Per grens gelden nul dubbele externe effecten, nul verzonnen voltooiingen en dezelfde uiteindelijke
readbacks. Een onzekere commit wordt eerst gereconcilieerd en nooit blind opnieuw verstuurd.

## Installatie- en exitproef

Iedere kandidaat begint in een lege tijdelijke directory of schone VM en krijgt alleen een
versioned identity pack en testcredentials. De proef meet en bewaart:

- alle handmatige stappen, root- of Dockervereisten en wall-clock installatietijd;
- aantal processen/services, directe dependencies, disk en idle/gebruikt geheugen;
- cold- en warm-starttijd;
- upgrade terwijl een episode wacht;
- volledige export zonder dat het framework hoeft te draaien;
- restore op een tweede schoon systeem;
- verwijdering van het framework waarbij canonieke Eva-data leesbaar blijft.

Een release-installer mag uiteindelijk niet afhankelijk zijn van een bestaande Git-worktree,
ontwikkeltoolchain, root-account of vooraf gevulde database.

De OS-proef gebruikt minimaal Linux en macOS plus een schoon Windows-pad. WSL of Docker mag als
apart supportniveau worden gemeten, maar mag niet stil als native Windows-installatie worden
gerapporteerd.

## Memory-subproef

De SQL-baseline, Hindsight en Graphiti krijgen exact dezelfde canonieke events:

1. een voorkeur verandert van gewone koffie naar decaf, met vragen over `nu` en `toen`;
2. twee tenants gebruiken dezelfde persoonsnamen maar hebben unieke canary-feiten;
3. een onbetrouwbare e-mail bevat een kwaadaardige instructie en concurreert met een directe
   gebruikerscorrectie;
4. een memorywrite wordt geaccepteerd waarna het proces vóór readback stopt;
5. één bron wordt ingetrokken en daarna aantoonbaar gewist;
6. dezelfde proef draait met 10.000 en 100.000 canonieke events.

Na iedere run worden de projectie en haar proces vernietigd. Zij moet uitsluitend uit de
canonieke events opnieuw kunnen worden opgebouwd. Acceptatie vereist nul tenantlekkage, nul
promotie van onbetrouwbare instructies, de exacte bron-ID bij iedere recall, oude feiten nooit als
actuele waarheid, erase-readback, fallback naar SQL/FTS bij uitval en gemeten p95-latency, tokens,
RAM, opslag en mislukte writes. Niet-deterministische extractie mag de projectie veranderen, maar
nooit de canonieke gebeurtenissen.

## Hard gates

Een kandidaat valt af zodra één van deze eigenschappen ontbreekt:

1. create-from-zero en restore zijn herhaalbaar;
2. ieder extern effect heeft stabiele idempotency, receipt en readback;
3. alle crashpunten herstellen zonder dubbel effect of verloren commitment;
4. een nieuwere correctie kan oud werk vóór het effect duurzaam superseden;
5. provideruitval veroorzaakt een circuit-breaker in plaats van een retrylus;
6. canonieke identiteit, episodes, effecten en receipts zijn frameworkonafhankelijk exporteerbaar;
7. geheugen kan per bron worden gecorrigeerd, verwijderd en volledig herbouwd;
8. connectorcode kan geen secrets of niet-geautoriseerde effects verkrijgen;
9. een tweede identity pack verandert naam en stem zonder broncodewijziging;
10. observability kan iedere gebruikersclaim aan extern bewijs koppelen.

Aanvullend valt een durable kandidaat af wanneer native installatie op een vereist doel-OS alleen
via een niet-ondersteunde omweg mogelijk is, de licentie Eva's beoogde distributie of selfhosting
beperkt, of betrouwbare productie-exploitatie een verplicht proprietary control plane vereist.
Geen workflowengine krijgt krediet voor exact-once externe effecten wanneer de connector geen
idempotency of betrouwbare readback ondersteunt. In dat geval moet Eva zichtbaar reconciliëren.

## Metingen na de hard gates

Alleen kandidaten die alle hard gates halen worden gewogen:

| Categorie | Gewicht |
| --- | ---: |
| Effectbetrouwbaarheid en crashherstel | 25 |
| Menselijk cross-domain eindresultaat | 20 |
| Installatie, beheer, update en rollback | 15 |
| Data-eigenaarschap, export en vervangbaarheid | 15 |
| Connector- en toolergonomie zonder safetyglue | 10 |
| Latency, resourcegebruik en kosten | 10 |
| Observability en debugbaarheid | 5 |

Een score kan nooit een hard-gatefout compenseren. Bronregels, configuratie en dependencylockfiles
worden naast de meetresultaten gepubliceerd.

## Bewijsbundel

Iedere run produceert één content-addressed `EvaArchitectureBakeoffRun/v1` met:

- stack-, bron-, dependency-, identity- en fixturehashes;
- volledig gesprek en model/tool-events;
- externe before/after-state, receipts en readbacks;
- durable episode- en effectstate;
- procesgeneraties en exacte crashgrens;
- installatie-, resource-, latency-, token- en kostenmetingen;
- export- en restorehashes;
- status per hard gate en een expliciete `not_proven` voor ontbrekend bewijs.

## Live selectie-oracle

Iedere hard-gate-overlevende, inclusief de SQL-baseline, draait vóór weging en Architecture
Decision Record dezelfde sealed live canary met een echte gebruiker en disposable echte
connectoraccounts. Alleen vooraf allowlisted en omkeerbare effecten zijn toegestaan. De
connector-eigen before/after-state, receipts en onafhankelijke readbacks zijn de waarheid.

Menselijkheid wordt blind beoordeeld op een vooraf bevroren semantische rubric: normale taal,
herkenbare identiteit, proportionele vragen, juiste correctie, eerlijk deelresultaat en geen
technisch systeemjargon. Unit-, contract- of fixturetests kunnen deze poort niet sluiten.

## Besluit en stopregels

- Eerst draaien Restate plus Python/Pydantic AI, DBOS Python plus Pydantic AI en de Python
  SQL-baseline uitsluitend op de deterministic episode en met dezelfde effectbroker.
- Na een hard-gatefout wordt niet tijdens dezelfde run gepatcht. De oorzaak wordt geclassificeerd
  als configuratie, kandidaatbeperking, fixturefout of ontbrekend Eva-contract.
- Alleen een vooraf gemotiveerde tweede run mag een configuratiefout herstellen; het eerste
  resultaat blijft zichtbaar.
- Alle hard-gate-overlevenden, inclusief de SQL-baseline, gaan door naar dezelfde echte model-,
  memory- en live-outcomeproef. Als het budget dat niet toelaat, blijft `winnaar` leeg.
- OpenAI Agents SDK voor Python, Pydantic AI en de minimale Python-loop worden pas daarna als
  loop-only variabele vergeleken op iedere overlevende durable laag.
- De gekozen stack vereist één onafhankelijke bewijsreview en één herhaling vanaf een schone
  installatie.
- Het Architecture Decision Record wordt vóór iedere Eva 1-disposition of datamigratie
  gecommit. Tot dat moment blijft `winnaar` leeg.

## Primaire documentatie

- Restate: <https://docs.restate.dev/>
- DBOS: <https://docs.dbos.dev/>
- Temporal: <https://docs.temporal.io/>
- LangGraph persistence: <https://docs.langchain.com/oss/javascript/langgraph/persistence>
- OpenAI Agents SDK: <https://developers.openai.com/api/docs/guides/agents/quickstart>
- Pydantic AI durable execution: <https://ai.pydantic.dev/durable_execution/overview/>
- Hindsight: <https://github.com/vectorize-io/hindsight>
- Graphiti: <https://github.com/getzep/graphiti>
- Mem0: <https://github.com/mem0ai/mem0>
