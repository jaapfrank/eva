# CP-001 lokale streamafsluiting bij directe poging

Code `de3031e463d47ba59499aad079827fe2af94f598`.
Vier productbestanden plus nieuwe PG-test binnen de afgestemde ownership:
openai_codex_provider, pydantic_conversation, application/conversation en
postgres/conversation_journal. Vooraf gelijkheid met canonical `2ddcd96f` gecontroleerd.
Geen auth/websocket/API/dependency/quotumwijziging, schema of tweede orchestrator.

## Betekenis en registratiegrens

De bestaande HTTP-body- en transportclose krijgen uitsluitend observatiestatus.
Een body geldt pas gesloten wanneer de echte onderliggende aclose is teruggekeerd.
Elke closefout of cancellation blijft onbekend; een nog lopende close evenzeer.
Dezelfde regels gelden voor transportclose. De provider markeert zijn lokale scope
pas na geslaagde SDK-close en eventuele HTTP-clientclose, aan het eind van de
bestaande __aexit__. Er worden geen cleanupcalls toegevoegd, geskipt of herhaald.
Bestaande deadline/oversize-close gebruikt dezelfde observatie rond dezelfde call.

OpenAICodexSession.local_stream_closed geeft alleen True wanneer alle bijgehouden
bodies, transport en providercontext succesvol gesloten zijn; anders None. De
bestaande directe success/failurehook leest dit na het verlaten van de provider-
context. Een echte lokale True wordt in het bestaande MODEL_RECORDED-dataobject
opgenomen als optioneel local_stream_closed. Afwezig blijft backward-compatible.
Het journal weigert ongeldige veldwaarden en gewijzigde replayinhoud. De observatie
komt uit de interne providersession, niet uit clientinvoer of provider-response-JSON.

Dit is geen remoteproviderquiescence, geen bewijs van een volledig ontvangen antwoord,
geen totaleusageclaim en geen nieuw stopmandaat. Een leesfout of ownerstop kan lokale
sluiting met ongemeten usage hebben; een terminale poging kan omgekeerd zonder bewezen
lokale sluiting bestaan. De bestaande preflight-server_quiescent blijft unknown.

## Actual-stream- en PG-bewijs

De nieuwe test gebruikt de echte gedeelde OpenAICodexProvider, OpenAI SDK,
Pydantic-streamparser en httpx2 AsyncByteStream. Alleen het netwerktransport/SSE
zijn lokale fixtures, met vaste testcredentials zonder credentialfiletoegang.
Bestaande echte disposable-PG-fixture en scoped onafhankelijke SQL-readback hergebruikt.

Zes cases: success, read_error, body_close_error, transport_close_error, ownerstop,
en ownercancel tijdens bodyclose. In elke body/transportclosecallback controleert
de test dat nog geen terminale journalregistratie bestaat. Alle cases hebben exact
één netwerkrequest, één bodyclose en één transportclose. Geen extra retry.

Success/read_error/stop krijgen lokale True na de geslaagde closes; de overige
cases houden het veld afwezig. De oorspronkelijke transportclose-exception behoudt
objectidentiteit; ownercancel blijft WorkInterrupted, overige fouten volgen bestaande
sanitisatie. Stopregistratie gebruikt het bestaande request_turn_stop en CancellationSignal.
Replay geeft één onveranderd terminalrecord. Een onbekende sluiting kan niet via een
gewijzigde completionreplay naar True worden gepromoveerd. Ongeldige stringattestatie
wordt geweigerd. Geen nieuwe publieke clientattestatie-ingang.

131 gerichte tests slagen op immutable codecheckpoint, plus Ruff/diffcheck: zes
nieuwe cases, bestaande directe pogingen/one-shot usage, gedeelde provider/model,
budgetguard en conversation/researchfailureusage. Geen volledige releasepoortclaim.

Artifact `/home/administrator/project/.eva2-artifacts/cp001-stream-settlement-20260913`
bevat JUnit/tests, zes concrete streamobservaties gekoppeld aan onafhankelijke
PG-terminalexports, overige directe PG-regressieexports, verifier/receipt en patch.
Tien bronbestanden aan codecheckpoint gebonden. PG16.13: network none, geen poorten,
tmpfs datadir/private Unixsocket. Na fixture nul eva-schema's; container verwijderd
en afwezigheid gecontroleerd. Geen liveprovider, livedatabase of automatische migratie.

Proberen in de bestaande offline testomgeving: `PYTHONPATH=src`, expliciete lege
EVA2_TEST_POSTGRES_DSN met databaseprefix eva2_4b_disposable_, vanuit eva2
`python -m pytest -q tests/postgres/test_direct_stream_settlement.py`.
Optioneel CP001_ATTEMPT_EVIDENCE_DIR voor de readbacks en closeobservaties.

Onafhankelijke review vóór integratie/deploy. Werkbord blijft bij centrale beheerder;
geen image/live/eigenaaracceptatieclaim. De bronpatch moet afzonderlijk worden
beoordeeld, niet automatisch in de eerdere admissionbundel worden meegenomen.
