# CP-001 directe modelpogingen: terminale failureusage

Codepatch: `63363793798d81d43ad210d4843b4c4e6831f3f0`.
Drie productbestanden vooraf gelijkgetrokken met canonical
`4cef8cc3be5a07796a83f093a54d7deca252a9e6`. De privé-alignmentcommit `8f2694c9`
**niet integreren**. Pas alleen de codecommit/hunks en deze bewijsnotitie toe;
geen verouderde volledige blobs. De voice-first-admit- en researchguidancewijziging
zijn in de gebruikte productbasis behouden, evenals bestaande budgeterrorusage.

## Probleem en gedrag

Een directe Pydantic-modelpoging had al een unieke duurzame UUID request_id en
CONVERSATION_MODEL_ADMITTED vóór I/O. Bij exception of retry werd geen terminal
resultaat vastgelegd. Nu krijgt een beëindigde poging een bestaand
CONVERSATION_MODEL_RECORDED-event met `outcome=succeeded|failed` en getypeerde usage.
Geen nieuw schema, eventtype of migratie; phase/status blijven bestaand terminal
model_recorded/completed. Dat statuswoord betekent beëindigd, geen succesvol antwoord.

Bekende ConversationModelError.model_usage blijft exact behouden. Als foutafhandeling
een unieke raw providerattestatie kan uitlezen, blijven input/outputtokens behouden
als partial: toolaantal kan bij mislukte parsing onbekend zijn. Zonder die meting
wordt unmeasured met null velden vastgelegd. Geen exceptiontekst, prompt of secret
komt in het terminale pogingevent. Het oudere totaleusagecontract telt bekende
bijdragen en markeert ontbrekende waarden partial; de terminale bronmeting blijft null,
geen verzonnen nulkosten. Een admission zonder terminal door procesverlies of
journalwritefout blijft onzeker en wordt niet als bewezen dispatch gepresenteerd.

Foutregistratie verhoogt geen retries en verandert geen stop- of budgetgrens.
Na ownerstop kan reeds admitted werk worden afgesloten; volgende admission blijft
geweigerd. Bij een mislukte terminale journalwrite wordt de oorspronkelijke exception
opnieuw geworpen, met writefailure als oorzaak; er volgt geen retry. Deadlines worden
behouden, inclusief het bestaande omzetten van cancellation naar de modelfout.

De bestaande idempotentie weigert gewijzigde usage/outcome en verkeerde ownerbinding.
Een identieke herhaling telt niet dubbel. Oude succesvolle recorded-events zonder
outcome blijven compatibel op completionreplay. Geen nieuwe provider-ID-claim: de UUID
identificeert de lokale admission, niet een bewezen externe providerdispatch.

## Ownership en validatie

Producthunks uitsluitend in ConversationModelJournal/contexthelpers,
PostgresConversationJournal.complete_model_request en de directe
PydanticConversationModel.execute retry-loop. Bestaande stoptest behoudt controle op
het aantal echte fixture-dispatches; alleen terminal-failureverwachting/fake aangepast.
Geen initiativeauthority, bootstrapcredentials, Bottie-productcode of helperflow gewijzigd.

87 gerichte tests geslaagd op codecheckpoint; Ruff en git diff --check slagen.
Acht nieuwe tests gebruiken echte isolated PostgreSQL 16.13 en de bestaande migratie-
fixture. Pydantic-streams zijn providerloze fixtures. Bewijs omvat UUID-uniciteit,
success/failure, retrytotalen, nieuwe engine/pool, replyreplay en completiondedup,
known/unknown failureusage, verkeerde owner, legacy completioncompatibiliteit,
writefailure, deadline, raw-attestatie en afsluiten na ownerstop.
Daarnaast bestaande Pydantic-, conversationusage-, journal- en researchfailuretests.
De legacy eventcompatibiliteitstest gebruikt een aangepaste eventreadback op dezelfde
PG-journalinstantie; nieuwe pogingevents/reconnect/totalen zijn echt uit PG gelezen.

Gerichte opdracht vanuit `eva2` met het bestaande testvenv, `PYTHONPATH=src`, een
expliciete `EVA2_TEST_POSTGRES_DSN` voor een lege `eva2_4b_disposable_*` database en
optioneel `CP001_ATTEMPT_EVIDENCE_DIR`:

```bash
python -m pytest -q tests/postgres/test_direct_model_attempts.py \
  tests/model/test_pydantic_conversation_model.py tests/application/test_conversation_usage.py \
  tests/postgres/test_conversation_usage_journal.py tests/postgres/test_research_failure_usage.py
```

Artifact: `/home/administrator/project/.eva2-artifacts/cp001-direct-attempts-20260913`.
Bevat 8 onafhankelijke scoped SQL-eventexports, JUnit/tests.txt, brongebonden receipt,
verify.py en direct-attempts.patch. Tien bronbestanden gebonden aan codecheckpoint.
PG-image SHA `5d143123fdf80462d1778cd4f24b9f7ca13c87174bca19141fb194c5a1ebca59`:
network none, geen poorten, datadir tmpfs, private Unixsocket, geen named volume.
Na de bestaande fixture waren nul eva-schema's over; container verwijderd en afwezigheid
gecontroleerd. Geen live DB, automatische schemadeploy, modelcall of credentialload.
Venv Python 3.12.3 is een contracttestomgeving, geen volledige releaseprofielclaim.

Dit is directe pogingregistratie, geen complete helper-/memory-/productusage en geen
live vergelijking of eigenaaracceptatie. Onafhankelijke review vóór integratie;
centrale beheerder blijft eigenaar van werkbord en gedeelde checkout.


## Reviewcorrectie: single-use raw usage behouden

Onafhankelijke review gaf NO-GO op `63363793`: bij een mismatch was de raw
attestatie al geconsumeerd; failureafhandeling probeerde opnieuw te consumeren,
waardoor de echte single-use-provider bekende usage verloor naar unmeasured.
Correctiecode `084cdc822cd9f7b5e88ea99678379949897e46a5` bewaart per attempt
raw_usage plus de consumptiestatus. Failure gebruikt een reeds verkregen attestatie;
anders probeert zij hoogstens eenmaal te consumeren. De mismatch blijft een fout,
met partial usage; er komen geen nieuwe retries of scopewijzigingen bij.

De nieuwe one-shot-regressie faalde vóór de fix aantoonbaar op unmeasured/null
(`regression-before.txt`). Na de fix schrijft de echte PG-journal één failed event
met 18 input / 7 output, measurement partial. De test controleert precies één
consume en één scripted modelpoging. De fixture weigert een tweede consume zoals
de echte provider. Alle 88 gerichte tests slagen op het correctiecheckpoint; Ruff
slaagt. De eerder beschreven 87-testmeting blijft historisch, geen finale GO.

Nieuw artifact:
`/home/administrator/project/.eva2-artifacts/cp001-direct-attempts-fix-20260913`:
JUnit/tests.txt, negen onafhankelijke scoped PG-eventexports, verifier/receipt,
regression-before.txt en oneshot-fix.patch. Verifier controleert de 18/7-terminal,
tien bronbindingshashes, nul eva-schema's na fixture en afwezigheid van de verwijderde
private netwerkloze PG-container. Geen echte providercalls of livewijziging.
Dezelfde onafhankelijke reviewer moet de correctie beoordelen vóór integratie.
