# Native duplex: experimentele antwoordcoördinatie

Niet live en niet zelfstandig uitvoerbaar. Onderdeel van SP-03 / VB-001.

`reply_lane.py` bindt één geauthenticeerde huishoudspeaker aan één bestaande HA-conversatie. Het gebruikt de bestaande `EvaVoiceClient.respond` en diens request-specifieke HTTP-annulering; geen tweede model-, geheugen- of toolroute. Een nieuwe server-VAD-beurt annuleert de oude aanvraag en laat de audio-uitvoer de vorige generatie ongeldig maken. Daarna kan één definitieve transcriptie voor de nieuwe generatie worden ingestuurd. Verlate of dubbele transcripties worden geweigerd. De audio-uitvoer moet alle buffers van de oude generatie leegmaken en latere writes weigeren.

Vijftien experimentele controles, zeventien bestaande native-clientcontroles en vijfenveertig bestaande HA-mediacontroles slagen (77 totaal). De proeven gebruiken echte lokale HTTP- en WebSocket-verbindingen. `transcribe_new_turn` hergebruikt `HomeAssistantSpeechInput`: de eerste HA-melding `stt-vad-start` maakt de nieuwe antwoordgeneratie; pas definitieve STT-tekst mag naar de bestaande Eva-client.

De onafhankelijke review vond twee races. Sluiten kon een nog lopende stop annuleren en vervolgens overslaan. Stops hebben nu eigen taken die annulering van hun aanroeper overleven; `close` wacht daarop, ook als de lane al gesloten was. Verder kon een late fout uit de oude beurt de nieuwe luisterbeurt sluiten. Replyfouten zijn nu generatiegebonden; een mislukte fysieke stop blijft altijd fataal. Een observeerbaar sluitingssignaal beëindigt lopende STT bij een actuele antwoordfout of expliciete sluiting.

Een echte HA Cloud-proef met synthetische audio bevestigt de callback vóór final text: 1527 ms na de start van de proef, volledige tekst op 5443 ms. Deze tijden bevatten aanloopstilte en zijn **geen gemeten stoplatentie**. De volledige synthetische zin is herkend. Er is geen audio naar de speaker gestuurd en geen Eva-kernbeurt gemaakt. Bewijs: `evidence/product/VB-001-ha-speech-onset-2026-09-09.json`.

De [HA 2026.8.1-segmenter](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/assist_pipeline/vad.py) wacht standaard 300 ms spraak af; [de pipeline](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/assist_pipeline/pipeline.py) gebruikt die standaard. Alleen de HA-callback haalt dus niet de afgesproken p95-stopgrens van 250 ms. Voor snelle onderbreking blijft vroegere lokale spraakdetectie met echo-onderdrukking nodig. De [Assist-protocolbeschrijving](https://developers.home-assistant.io/docs/voice/pipelines/) specificeert de gebruikte STT/VAD-events en audioframes.

Het bestaande `VoicePlaybackController` verzorgt V01-playback voor een reeds uitgegeven `VoiceSession`. Deze huishoudproef maakt geen fictieve private V01-sessie aan en roept `admit_final_voice_turn` niet aan: de bestaande native huishoudroute bepaalt de doelgroep. Bij de definitieve aansluiting moet de overlap met de bestaande controller worden beoordeeld; dit experiment is geen tweede algemene voice-kern.

## Nog nodig voor de gevraagde functie

- Geauthenticeerde speakertransport-aansluiting en generatiebinding op de werkelijke audio-uitvoer; de huidige Pipecat-wirelaag bewijst dat niet automatisch.
- Continue microfooninvoer, echo-onderdrukking, VAD en behoud van de eerste woorden bij onderbreken.
- STT is op de experimentele beurtcoördinatie aangesloten. De bestaande HA TTS-adapter moet nog op de werkelijke audio-uitvoer worden aangesloten; geen andere realtime-modelprovider is geactiveerd.
- Stemuitvoer en overige media exclusief overnemen en weer vrijgeven. De compatibiliteitsfirmware is nadrukkelijk geen gebruiksconfiguratie.
- Sessiebeëindiging, mute, inactiviteit en verbindingverlies laten doorwerken naar close. De sessie-eigenaar verwerkt continuation; deze module opent de microfoon niet eigenmachtig.
- Bijhouden welk deel van een antwoord hoorbaar was. De bestaande kern kan een tekst al hebben opgeslagen voordat de audio is voltooid; deze proef lost dat nog niet op.
- Werkelijke speakerstilte en p95-stoplatentie meten; lokale HTTP/sinktests zijn geen hardwareacceptatie.

Geen ruwe audio wordt door deze module opgeslagen. De authenticatie en begrensde microfoonbuffers moeten door de uiteindelijke transportadapter worden geleverd voordat dit experiment kan worden gebruikt.
