# LiveKit-stemgesprek rond de bestaande Eva-kern

Jaap hervatte op 10 september expliciet de uitvoering: ontbreken van een complete
referentieapp is geen stopvoorwaarde. Noodzakelijke integratie is toegestaan.
Dit besluit vervangt de stopvoorwaarde uit het eerdere OSS-selectierapport.

Gebruikersuitkomst: in natuurlijk Nederlands echte opdrachten geven, tijdens werk
doorpraten, spraak onderbreken/corrigeren, context behouden en werkelijk resultaat
ontvangen. Laptop is de eerste toegang; telefoon en bestaande huisapparaten blijven
onderdeel van de volledige bereikbaarheidsuitkomst. HA bepaalt de keuze niet.

Keuze: LiveKit Agents 1.8.0 voor VAD/turns, audio, afspeelcontext en async tools.
Geen apart model of geheugen voor de stemlaag: ieder inhoudelijk antwoord en elke
werkelijke actie loopt door Eva's bestaande runtime en toegestane provider.
Bestaande HA-spraakproviders worden hergebruikt zonder nieuwe betaalde API-route.

Actief werk: een afzonderlijke voice-service met browserclient; een begrensde,
eigenaargebonden voice-route; integratie van afgespeelde context en lopende taken
met de bestaande gesprekroute. Native LiveKit-async-tools houden werk actief terwijl
nieuwe beurten binnenkomen. Gewone barge-in stopt audio, expliciete taakstop gebruikt
Eva's duurzame stopgrens. Een onderbroken beurt mag geen dubbele actie veroorzaken.

Beoogde bestanden: src/eva/application/voice_context.py,
src/eva/adapters/api/voice_turns.py, src/eva/voice_service/*,
deploy/voice-session/*, tests/application/test_voice_context.py,
tests/api/test_voice_turns.py, tests/voice_service/*; kleine aansluitingen in
application/conversation.py, adapters/model/pydantic_conversation.py,
bootstrap/runtime.py en adapters/api/app.py. Geheugen- en dashboardwerk blijven
inhoudelijk bij hun bestaande taken.

Eindvoorwaarden: echte taak met onafhankelijk gecontroleerd resultaat; tweede vraag
tijdens uitvoering; gesproken correctie en expliciete taakstop; geen laat oud resultaat
als actueel; context bevat alleen bevestigd afgespeelde tekst of gemarkeerde onzekerheid;
eigenaarsgrenzen en herstel getest. Geïsoleerde tests vóór gecontroleerde uitrol.
Meet eerste hoorbare reactie en onderbrekingsvertraging; Jaap beoordeelt vloeiendheid.
Gebouwd, getest, live en menselijk geaccepteerd worden afzonderlijk vastgelegd.

Status: de laptoproute is gebouwd, getest en live op http://localhost:44716.
Aanmelden: plak het bestaande eigenaar-dashboardtoken in het aanmeldveld; kies Aanmelden, Begin gesprek en
microfoontoegang toestaan. Probeer een opdracht, een tweede vraag tijdens uitvoering
en “Wacht, ik bedoel…” tijdens het antwoord. Gesprek beëindigen sluit de audioruimte;
expliciet een taak stoppen gebruikt de duurzame stopgrens van de kern.

De live synthetische proef behoudt de hele correctie en gaat binnen circa 0,47 seconde
naar luisteren; alleen de gecorrigeerde korte zin volgt. Intrekken weigert nieuwe
spraak en sluit de bestaande ruimte (2,3 seconden gemeten); opnieuw openen werkt.
Bestaande huisingang is behouden via slot3. Beide databases zijn bij de gecontroleerde
uitrol gesynchroniseerd; actuele AU-007-uitkomsten en eigenaargeheugenresolver blijven behouden.

Menselijke acceptatie en fysieke echo-/microfoonproef staan open. De kernwachttijd
is nog onvoldoende voor bewezen vloeiendheid (korte vragen 11–13 seconden, zeszinnen-
antwoord in de live audioproef 17,7 seconden). Telefoon en de volledige natuurlijke
VoicePE-route blijven open opleverwerk. Het algemene voice-doel is niet afgerond.
[Technisch meetbewijs](../../evidence/product/voice-livekit-integration-2026-09-10.json).
[Installatie en beheer](../../deploy/voice-session/README.md).

Loginherstel 661de8d: de zichtbare browser opent de pagina nu zonder HTTP Basic-
blokkade. Lege pagina 200, beschermde readiness zonder token 401, eigenaar 200.
Tien gerichte tests en onafhankelijke review GO. Media-image
`sha256:05c62d4cce4f50e7574e994b34186528664baa56a10b93a5dd67c9636d17ea7a`.
Nieuwe profielmeting lokaliseert 9,738 seconden bij Hindsight-geheugenophalen;
active_context 92 ms en modelgeneratie 5,035 seconden. Verbetering wordt gemeten
zonder relevante herinneringen of privacycontroles over te slaan.
