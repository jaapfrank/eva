# Doorlopende spraak: vergelijking met OpenAI-documentatie

Status: onderzocht ontwerp, niet gebouwd of live bewezen. 2026-09-09.

Jaap wil tijdens één gesprek gewoon kunnen praten, Eva kunnen onderbreken en direct een vervolgvraag stellen zonder opnieuw Hey Eva. Een stopwoord plus handmatig opnieuw activeren voldoet niet. Hij bevestigde dat de directe MP3-test op de woonkamer-speaker volledig verstaanbaar was; FLAC-weergave liet stukken spraak weg. Dat is één geslaagde audioproef, geen voltooide sessie of bewezen algemene codecdiagnose.

## Wat de officiële bronnen vastleggen

[ChatGPT Voice](https://learn.chatgpt.com/docs/features/voice) beschrijft natuurlijk beurtwisselen, tijdens een antwoord onderbreken en direct vervolgen. Dit beschrijft productgedrag; het geeft niet de volledige interne implementatie van de app.

[Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations) beschrijft onderbreken op gedetecteerde spraak, annuleren van de lopende generatie en afbreken van afspelen. Ongehoorde uitvoer wordt afgekapt uit de gesprekscontext. Bij WebSocket beheert de client playback en meldt hij tot waar de audio is afgespeeld; WebRTC/SIP verzorgen dit anders. Deze API-documentatie is een bruikbaar technisch referentieontwerp, geen bewijs dat de ChatGPT-app exact dezelfde integratie gebruikt.

[VAD](https://developers.openai.com/api/docs/guides/realtime-vad) beschrijft detectie van begin/einde van spraak en semantische beoordeling van wanneer een gebruiker klaar is. Alleen wachten op stilte heeft andere afwegingen dan inhoudelijk beoordelen of een zin af is.

## Gevolgen voor Eva

- Eén expliciet geactiveerde, begrensde sessie per speaker met behouden gespreksidentiteit.
- Audio-invoer moet ook tijdens uitvoer bruikbaar blijven. Controleer op echte hardware dat eigen speakeruitvoer niet als nieuwe gebruikersspraak wordt behandeld.
- Nieuwe gebruikersspraak onderbreekt lokaal de audio én annuleert de relevante antwoordgeneratie. Leeg achtergebleven uitvoerbuffers; late callbacks mogen een gestopte beurt niet herstarten.
- Vervolg zonder wakeword binnen de actieve sessie. Normale HA-continuation kan normale vervolgbeurten ondersteunen, maar bewijst niet luisteren tijdens afspelen of direct hervatten na Stop.
- Houd bij wat daadwerkelijk is afgespeeld. Een ongehoord antwoord mag niet worden behandeld alsof de gebruiker het heeft gehoord.
- Mute, gesprek beëindigen, stilte-timeout, intrekbaar mandaat en verbindingverlies moeten de sessie betrouwbaar beëindigen.
- Houd identiteit, geheugen en handelingen in Eva's bestaande kern. Een eventuele realtime-spraakprovider wordt een adapter binnen bestaand mandaat en budget, geen automatische vervanging van Eva of stilzwijgende nieuwe gegevensdeling.

## Eerstvolgende bewijs en werk

1. Behoud de geslaagde MP3-proef en herstel de native audioweergave afzonderlijk. Nog geen firmware uitrollen zonder uitvoerbare herstelroute. De eerste codec-overlayconfig faalde de package-mergevalidatie; er is niets geïnstalleerd.
2. Controleer na de herstelde HA-routering een echte beurt via conversation.eva2, met native request-readback en continuation. De eerdere praktijkbeurt was onderschept door de oude catch-all; die is behouden maar uitgeschakeld voor de native proef.
3. Beproef gelijktijdige invoer/uitvoer en echo-onderdrukking op de bestaande Voice PE voordat een realtime-provider wordt gekozen of een bruikbaarheidsgarantie wordt gegeven.
4. Bouw en bewijs daarna de volledige sessie: onderbreken met gewone spraak, direct vervolgen, geen oude audio, juiste context na onderbreken, mute/einde/herverbinding en gemeten stoplatentie.

Niet concluderen dat MP3, continue_conversation=true of een Realtime API-koppeling afzonderlijk het gevraagde product oplevert. Geen nieuwe browser-spraakroute voor Jaap introduceren.
