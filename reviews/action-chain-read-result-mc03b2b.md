# Verificatiedossier — action-chain readresultaat MC03b2b

Bijgewerkt: 25 augustus 2026.

## Actuele uitkomst

`TESTED`, nog geen `REVIEWED` of `LIVE_PROVEN`.

De bron kan nu precies één eerder geclaimd connectorproces aanroepen, een
succesuitkomst of exacte protocolfout naar Eva's execution- en
evidencecontracten projecteren en alle resultaatrijen plus het terminale
tijdlijnevent atomair bewaren. Een terminale replay of nieuwe database-engine
start geen tweede proces. De route is niet op B gedeployd en heeft nog geen
echte Home Assistant-provider gelezen.

## Waarom dit onderdeel bestaat

MC03b2a bewijst alleen wie de providercall mag beginnen. MC03b2b bezit de
grens daarna: de call draait buiten de SQL-transactie, maar resultaat,
evidence, eventuele observatie en herstelprojectie moeten exact bij dezelfde
readattempt blijven. Anders kan Eva na een crash een read herhalen, een fout als
succes behandelen of bij herstart andere feiten reconstrueren.

## Bestandseigenaarschap

- `src/eva/operations/action_chain_read_results.py` is een pure projector. Het
  bindt request, manifest, schema, resultaat, tijdvenster, evidence en eventuele
  `ReadObservation`; het doet geen I/O en schrijft niets.
- `src/eva/adapters/postgres/action_chain_read_results.py` bezit de ene
  resultaattransactie, exacte replay en het terugladen van een terminale
  uitkomst zonder opnieuw callmateriaal uit te geven.
- `src/eva/adapters/postgres/action_chain_read_result_validation.py` projecteert
  bij iedere ledger-load opnieuw uit de opgeslagen manifest- en providerbytes
  en vergelijkt attempt, evidence, observatie en tijdlijn byte-/hashgebonden.
- `src/eva/adapters/postgres/action_chain_read_validation.py` routeert de
  terminale tijdlijntoestanden naar die strikte herlaadvalidator.
- `src/eva/operations/action_chain_read_executor.py` is de enige eigenaar van
  de echte `run_connector_process`-call. Hij begrenst timeout en deadline en
  roept op replay geen provider aan.
- `src/eva/adapters/connectors/process.py` blijft de bestaande geïsoleerde,
  manifestgebonden connectorprocessgrens; MC03b2b bouwt geen tweede launcher.
- `tests/postgres/test_action_chain_read_dispatcher.py` bezit callclaim,
  resultaatprojectie, transactie-, herstart-, replay- en echte subprocessproeven.

## Bewijs en reviewverloop

- De pure succesprojectie bewaart een schema- en requestgebonden observatie;
  rate-limit, definitieve fout en ambigue transportgrens blijven onderscheiden.
- Een geforceerde fout bij het invoegen van de observatie laat attempt,
  evidence, observatie en terminaal event volledig terugrollen.
- Een echte geïsoleerde testworker produceerde geen geldig protocolantwoord.
  Eva bewaarde de exacte `ConnectorProtocolFailure/v1`; de tweede execute en
  een nieuwe engine startten geen tweede proces.
- De eerste onafhankelijke resultaatreview gaf `NO-GO`: `observed_at` beïnvloedde
  de observatie, maar stond niet in het terminale event. Herstart reconstrueerde
  daardoor ten onrechte call-start als observatietijd.
- Checkpoint `6e4f724` bewaart nu exact `provider_result_observed_at`, bindt
  `call_started <= observed <= recorded`, controleert provider-retrieval tegen
  host- en completionvenster en gebruikt bij de herstartproef bewust drie
  verschillende tijden.
- Die reparatie en executor `184516e` zijn lokaal opnieuw volledig groen. De
  onafhankelijke herreview is nog niet uitgevoerd; dit dossier noemt daarom
  bewust geen `GO` en het productvak blijft open.

## Uitgevoerde controles

- 6 gerichte dispatcher/result/executor-tests op disposable PostgreSQL 16.13.
- 36 gecombineerde application-, ledger-, authorizer-, dispatcher-, result- en
  executortests op de action-chain-database.
- 24 operationele-tijdlijntests op de afzonderlijke timeline-database.
- Ruff, compileall en `git diff --check` groen.
- De runtime-contractkaart is groen voor 76 bestanden. De volledige
  architectuurcheck stopt op reeds bestaande `GITIGNORE_CONTRACT_DRIFT`
  (verwacht 44 uitsluitingen en 2 negaties); deze slice claimt dus geen groene
  repository-inventarisgate.

## Omvang en optimalisatie

De vier nieuwe/hoofdproductbestanden tellen respectievelijk 318, 544, 250 en
153 regels. De implementatie is in kleine checkpoints gesplitst; geen codecommit
voegde netto meer dan 600 productregels toe en geen nieuw productbestand is
groter dan 800 regels. Er is geen nieuwe dependency toegevoegd.

Providerlatency, geheugen, tokengebruik en kosten zijn voor deze slice
`unmeasured`. De echte workerproef bewijst semantiek, niet p50/p95-prestatie.
Er geldt dus geen optimalisatieclaim.

## Open vervolg

1. Onafhankelijke herreview van `6e4f724` en `184516e`.
2. MC03b3 laat alleen na een bewezen read de juiste afhankelijke stap vrij.
3. MC03c hergebruikt daarna voor effecten de bestaande
   `TOOL_PREPARED` → providercall → fysieke-readbackketen.
4. Pas de Telegram-B-/Home Assistant-eindproef levert live providerbewijs.
