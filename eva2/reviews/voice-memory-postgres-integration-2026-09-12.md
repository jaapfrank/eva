# Voice/geheugen/helper — geïsoleerde PostgreSQL-ketenproef

Datum: 12 september 2026. Alleen tests; geen productiebestanden of runtime gewijzigd.

## Resultaat

`tests/postgres/test_voice_memory_helper_outbox.py`: **1 passed, 1 xfailed in 5.54s**.
De strikte xfail is een werkelijk gereproduceerde replayfout, geen afgeronde eis.
Gebruik `--runxfail -k clock_advance` om de rode regressie expliciet te draaien.

De positieve proef hergebruikt `people_memory_engine` met alle echte migraties,
PostgresOwnerMemorySources (actuele eigenaarbinding), PostgresConversationJournal,
PostgresMemoryOutbox, SemanticMemoryColdPathWorker en de bestaande gewone
voice-API/LivingConversation/helperfixture. Tijdens de wachtende helperbeurt:

1. Eigen bron is zichtbaar; een andere persoonsbron is uitgesloten.
2. De normale correctie-API bewaart de correctiebeurt en geheugen-outbox duurzaam.
3. Een nieuwe SQLAlchemy-engine en nieuwe outbox lezen de correctie vóór enige
   verwerking terug. Het journal staat op memory_queued.
4. Een nieuw cold-path-worker verwerkt de teruggevonden jobs met hashgebonden
   ontvangstbewijzen; er blijven geen jobs open en er is geen workerfout.
5. De oorspronkelijke voicebeurt eindigt; exact dezelfde toegelaten beurt wordt
   uit het echte journal herhaald zonder tweede helper of stop.
6. Een nieuwe voiceconversatie krijgt via het geheugenport de groene correctie;
   de eigenaarbronlijst bevat geen gele andere-persoonsbron.

## Expliciete grenzen

De semantische provider en het antwoordmodel zijn deterministische testdoubles.
De provider kiest alleen de gecontroleerde groene tekst uit aangeleverde bronnen;
dit bewijst geen Hindsight-extractie, natuurlijke-taalcorrectie, ranking of
persistentie van de semantische provider zelf. De provider verwerkt ook de andere
persoonsbron; selectie en ownerbronprojectie blijven afzonderlijk getoetst.
Helperstore/action-timeline zijn de bestaande in-memory ports. Wel echt zijn de
concurrente API/runtime-orkestratie, eigenaarbron-autorisatie, gespreksjournal,
outbox-opslag, herladen via nieuwe DB-engine en cold-path-worker.
Geen microfoon-, echo-, playback- of OS-crashproef. Geen actuele eigenaargegevens.

## Nieuwe concrete fout

`test_voice_http_replay_after_clock_advance_keeps_committed_result` stuurt exact
hetzelfde session_id/message_id/text tweemaal via de echte voice-API. Alleen de
gecontroleerde serverklok schuift twee seconden vooruit. Eerste antwoord slaagt;
tweede geeft `voice_turn_failed`. Het echte journal meldt
`ConversationConflict: conversation message identity was reused with different ingress`.
De route maakt bij ieder verzoek een nieuw occurred_at; dat behoort tot de
gejournalde identiteit. De oude in-memory journalfixture controleerde dat niet.

Deze fout trad eerst ook op in de positieve ketenproef met de gewone systeemklok.
Die positieve proef gebruikt nu expliciet de exact oorspronkelijk toegelaten
runtimebeurt voor haar replayassertie. De afzonderlijke HTTP-regressie behoudt de
nieuwe timestamp en blijft strikt rood/xfail tot de owning spraaktaak repareert.
Geen productiecorrectie door deze reviewer. Een groen testproces met deze xfail
mag niet als volledige voice-replayacceptatie gepresenteerd worden.

## Reproduceerbare uitvoering en isolatie

Bestaande lokale image `postgres:16.13` (image-prefix 5d143123fdf8), aparte container
`eva2-integration-pg-20260912`, uitsluitend synthetische DB
`eva2_4b_disposable_integration`, loopbackpoort en eigen tmpfs. Geen productiemounts,
secrets of productiedatabase gebruikt. Fixture weigert niet-disposable DB-namen en
andere PostgreSQL-versies. Een eerste kandidaat op bestaande PG18-image werd door
deze versieguard afgewezen; daarna is de correcte reeds lokaal aanwezige PG16.13
gebruikt. Geen packages/images gedownload. Alleen deze eigen testcontainer wordt
na de proef verwijderd.

Vanuit `eva2`, met EVA2_TEST_POSTGRES_DSN naar die disposable database:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src <bestaande-testvenv>/bin/python -m pytest \
  -q -p no:cacheprovider --tb=short --show-capture=no \
  tests/postgres/test_voice_memory_helper_outbox.py
```

Bestaande testvenv:
`/home/administrator/project/.eva2-artifacts/native-voice-offline-env-w8rt00c4/venv`.
Werkmap: `/home/administrator/project/.eva2-artifacts/integration-pg-20260912`.
Geen modelcalls, Hindsight-aanroepen, deployment of gedeelde Git-indexwijziging.
