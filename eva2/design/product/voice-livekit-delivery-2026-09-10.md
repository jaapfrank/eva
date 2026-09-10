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

Status: in uitvoering. Geen nieuwe stemervaring live of geaccepteerd.
