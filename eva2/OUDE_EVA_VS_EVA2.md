# Oude Eva tegenover Eva 2 — behoud van aanwezigheid

Bijgewerkt: 25 augustus 2026.

## Eerlijke conclusie

- Eva 2 is **nog niet klaar om de oude Eva te vervangen**.
- Eva 2 is aantoonbaar sterker in persoonsisolatie, duurzaam geheugen,
  crashherstel en controleerbare receipts.
- De oude Eva heeft in de bron al de rijkere fysieke aanwezigheid: deurbel,
  intercom, speakers, voice, eigen initiatief en bewust zwijgen.
- De eigenaar ervaart in dagelijks gebruik dat Eva soms spontaan via deurbel
  of speaker praat. Dat is geldig gebruikersbewijs voor de beleving. In de
  repository is nog geen onveranderlijke oude hardware-receipt gevonden die
  exact bewijst welk apparaat het bericht werkelijk afspeelde.
- Deze minimale bestaande huisaanwezigheid is voortaan een **blokkerende
  V1-behoudgate**. Daarnaast is een ChatGPT-achtige, onderbreekbare voice mode
  nu een eigen blokkerende V1-productgate. Algemene video/media en veel
  apparaten mogen later; wat Eva nu al herkenbaar Eva maakt mag niet verdwijnen.

## Bewijslabels

- `Gebruiker-live`: door de eigenaar in het echte huis waargenomen.
- `Bron-getest`: implementatie en lokale/gesimuleerde providertests bestaan.
- `Receipt-live`: gebeurtenis, providerlevering en fysieke uitkomst zijn
  onveranderlijk aan dezelfde draaiende versie gebonden.

## Vergelijkingsmatrix

| Beleving | Oude Eva | Eva 2 nu | Vereist vóór overgang |
|---|---|---|---|
| Spontaan iets zeggen | `Gebruiker-live`; de autonomy-scheduler en private deurbel-wake bestaan ook in de bron | Echte HA-events en een keuze tussen reageren en zwijgen zijn bewezen; de residentroute zit niet in de draaiende kandidaat | Eva 2 initieert tijdens een zevendaagse proef zelf enkele relevante, korte momenten en blijft bij ruis stil |
| Deurbel als aanleiding | Privé deurbelincident, foto, modelkeuze en `silent`/Telegram/intercom zijn `Bron-getest`; hoorbare deurbel-/speakeruitvoer is `Gebruiker-live` | Geen toegelaten deurbel-/cameramanifest en geen Eva2-intercomroute | Echte deurbel → duurzaam incident → actuele context → stilte, tekst of exact gebonden intercom; na crash nooit dubbel spreken |
| Praten via speaker | Exacte deurbel-speakerbinding, TTS-audio-preflight en voice-ACK-herstel zijn `Bron-getest`; speakergebruik is `Gebruiker-live` | Geen algemene TTS- of speakercapability in de V1-B-route | Eerst één expliciet gekozen speaker: readiness, delivery-ACK en doelreadback; nooit stil terugvallen op een andere speaker |
| Doorlopend voicegesprek | Geen gelijkwaardige full-duplex oude-Eva-receipt gevonden | Geen streaming sessie, barge-in of lage-latencygate | V01-V07: `Bel Eva` via Telegram Mini App, Assist/Wyoming-fallback, dezelfde context en tools, onderbreken binnen 250 ms p95 en echte gebruikersacceptatie |
| Nuttig zwijgen | De deurbelkeuze kent `silent`; bron bevat incident-cooldown, nacht- en budgetgrenzen | Ruis en gewone lamp-events zijn aantoonbaar stilgehouden; volledige bewonerspolicy nog niet live | Quiet hours, cooldown, dagbudget en urgente uitzondering worden vlak vóór levering opnieuw gecontroleerd |
| Timing en dosering | Scheduler, versnelde deurbel-wake en dagbudget bestaan in de bron | PostgreSQL-aandachtsontwerp bestaat; officiële residentrun heeft nog nul geslaagde cases | Geen vaste chatbotcadans: alleen relevante aanleiding, juiste timing, begrensde frequentie en directe stopmogelijkheid |
| Persoon en context | Personen, SQLite-geheugen en gesprekarchief bestaan; geen onafhankelijke oude isolatie-/crashreceipt gevonden | PostgreSQL + Hindsight, persoonsisolatie, herstart en cross-channel recall zijn Eva2's aantoonbare voorsprong | Gesproken tekst gebruikt alleen toegestane persoon-/groepscontext en lekt nooit privégeheugen naar een aanwezige groep |
| Waarheid na spreken | Oude voice-ledgers en ACK-herstel zijn `Bron-getest`; geen gebundelde fysieke receipt gevonden | Eva2's eventtijdlijn en readback zijn sterker, maar nog niet aan een speaker gekoppeld | Eva zegt alleen “afgespeeld” na provider-ACK en waar mogelijk apparaatreadback; onzeker blijft zichtbaar onzeker |
| Gevoel van een entiteit | Volgens de eigenaar aanwezig doordat Eva zelf momenten kiest en in het huis hoorbaar wordt | Nog vooral een betrouwbare kern en chatroute | De eigenaar accepteert na de eindproef expliciet dat initiatief, stilte, toon en aanwezigheid weer als Eva voelen |

## Kleine presence-opleverstukken

- [x] P00 — Oude bron, Eva2-bron, bestaande receipts en gebruikerswaarneming
  afzonderlijk geïnventariseerd; bewijsgrenzen zijn niet door elkaar gehaald.
- [ ] P01 — De bestaande connectorneutrale `FreeWillDecision`- en
  `ProactiveExpressionClaim`-keten uitbreiden met aanleiding, persoon/groep,
  keuze `silent`/Telegram/speaker/intercom, prioriteit, TTL, quiet hours,
  cooldown, dagbudget en exact doel; geen tweede orchestrator ontwerpen.
- [ ] P02 — Eén bestaande huisspeaker achter Eva2 aansluiten met TTS-preflight,
  exacte apparaatbinding, provider-ACK, beschikbare apparaatreadback,
  stopknop, crash/replaytest en bevestiging dat de eigenaar het geluid hoorde.
- [ ] P03 — Deurbelroute aansluiten: private gebeurtenis, eventuele toegestane
  observatie, contextkeuze en maximaal één intercom- of Telegramlevering.
- [ ] P04 — Residentpolicy live sluiten: nuttig zwijgen, nachtstilte, cooldown,
  budget, urgente uitzondering en hercontrole direct vóór levering.
- [ ] P05 — Op dezelfde bevroren eindimage een echte zevendaagse huisproef
  uitvoeren. De eigenaar beoordeelt initiatief, natuurlijkheid, timing, stilte
  en het gevoel dat Eva aanwezig is; hardware- en tijdlijnreceipts bewijzen de
  uitgevoerde route.

## Overgangsregel

Eva A blijft leidend totdat P01–P05, V01-V07 én de overige V1-eindgates groen zijn. Een
technisch betere kern is geen geslaagde opvolger wanneer de herkenbare spontane
huisaanwezigheid verdwijnt.

Conversationele voice is geen vervanging voor presence: P01-P05 meet of Eva
zelf het juiste moment kiest om in huis te spreken; V01-V07 meet een door de
gebruiker gestarte, snelle tweerichtingssessie met onderbreken. Het volledige
voiceontwerp staat in `design/product/conversational-voice-v1.md`.

## Lokale bronnen

- Oude deurbel/intercom: `/home/administrator/project/Bottie/src/integrations/homeassistant.js`,
  `src/mind/perception.js`, `src/mind/perceptiondecision.js` en
  `src/mind/perceptionexecutor.js` onder dezelfde oude-Eva-root.
- Oude autonomie/voice: `src/mind/autonomy.js`, `src/mind/voicebridge.js` en
  `src/voiceauthority.js` onder de oude-Eva-root.
- Oude tests: `test/doorbell-intercom.test.js` en
  `test/native-ha-voice-authority-fixture.mjs` onder de oude-Eva-root.
- Eva2: `src/eva/application/sensory.py`, `src/eva/application/free_will.py`,
  `src/eva/application/resident_attention.py` en
  `src/eva/adapters/postgres/operational_timeline.py`.
- Residentbewijsgrens:
  `evidence/living-core/resident-long-run-v1-2026-08-24.json`.
- Onafhankelijke review: `reviews/old-eva-presence-parity-p00.md`.
