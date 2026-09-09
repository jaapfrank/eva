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

## Vroege spraakdetectie: geselecteerde kandidaat

`local_onset.py` leest PCM onafhankelijk van de HA-handshake en geeft de geaccepteerde frames zonder wijziging door aan de bestaande STT-adapter. De wachtrij is begrensd tot één seconde; overrun breekt de beurt af in plaats van woorden over te slaan. Deze wachtrij bewaart geen audio van vóór de start van de adapter. De optionele detectorfactory ondersteunt onder meer HA's MicroVad; die is onderzocht maar niet als snelle detector gekozen.

MicroVad1.0.1 gaf in de eerste echte HA-proef slechts6ms voorsprong. Een diagnostische vergelijking met drie synthetische zinnen vond ook bij lage drempels390–770ms achterstand op een eenvoudige energie-indicatie. Daarom gebruikt de kandidaat de ongewijzigde `SileroOnnxModel`-klasse uit Pipecat, met een kleine `Silero10msDetector`-adapter. Bronpin, modelhash en licenties staan naast de code. Er wordt geen extra gesprek-, STT- of TTS-provider geïnstalleerd.

Drie nieuwe HA Cloud-proeven gaven535–603ms voorsprong op HA's startmelding. Vanaf de diagnostische energie-indicatie tot de lokale callback was dat165–228ms. Dit is geen menselijke onsetmeting, fysieke stoplatentie of p95. De derde transcriptie bevatte `morgeneten` als één woord; de onbewerkte uitslag is behouden. Tien seconden synthetische stilte, een vaste440Hz-toon en witte ruis activeerden de detector niet. Dit zegt niets over akoestische echo, televisie of andere gezinsleden.

De configuratie vraagt120ms bevestiging boven0.7. Dit zijn twaalf10ms-controles van de laatst beschikbare voorspelling; Silero levert ongeveer elke32ms een nieuwe voorspelling. Een onafhankelijke review gaf begrensde GO voor de wrapper, de modelkoppeling en de eerlijkheid van dit bewijs. Praktijkacceptatie staat open.

Voor een tijdelijke Linux/CPython3.12-proef: installeer `vad-requirements.lock` met pip `--require-hashes --target` in een proefmap, haal het model op met `python prepare_vad.py --output /tmp/eva-duplex-model.onnx`, en maak de detector via `prepare_vad.verified_detector`. De download en iedere aanroep van deze factory controleren de modelhash. Een afwijkend bestaand bestand wordt geweigerd. Dit is voorbereiding, geen geïnstalleerde dienst.

## Concreet resterend speakerwerk

De onafhankelijke controle van de gepinde `va_pipecat`-component bevestigt dat `flush_playback_queue_` alleen de PSRAM-ring leegt. `drain_audio_` kan daarna nog een oude `speaker_->play` afmaken; de controle na die call herstelt alleen de boekhouding. Het huidige protocol geeft ook geen generatiegebonden stop- of playbackbevestiging.

De onderstaande aanpassing hergebruikt de bestaande audio-task als enige eigenaar van `play/start/stop`, met voorrang voor stoppen. Een eigen resampler/mixerbron scheidt Eva's invoer van de announcement- en mediabron. Audiobatches en bevestigingen moeten nog aan sessie en generatie worden gebonden. Reeds gemengde I²S-/DAC-samples vormen een aparte meetgrens; timeout mag niet als bevestigde stilte gelden. De werkende speaker krijgt deze wijzigingen pas als complete kandidaat met bruikbaar herstel.

### Lokale stopaanpassing gebouwd als kandidaat

`speaker-stop.patch` past de bestaande `va_pipecat`-component aan op de pin in `speaker-upstream.json`. `prepare_speaker.py --upstream <bronmap> --output <nieuwe-bouwmap>` controleert de vier bronbestanden en MIT-licentie op hash, past de patch toe en legt de resulterende hashes vast. Een bestaande bouwmap wordt niet overschreven. Er is geen OTA- of flashactie in dit hulpmiddel.

De bestaande audiotaak is nu de enige aanroeper van speaker `play/start/stop`. Annuleren leegt de ring en vraagt een stop aan, ook als de ring al leeg was. Een begrensde kopie van maximaal20ms PCM voorkomt dat de reader tijdens `play` opnieuw gebruikte ringinhoud leest. Writes controleren bij het vastleggen opnieuw of een stop loopt. Stoppen gaat vóór de netwerkboodschap; verbindingsverlies gebruikt dezelfde lokale stopaanvraag.

`PlaybackStopBarrier` laat een volgende playback pas toe nadat de eigen resampler STOPPED meldt en twee hoofdloopgrenzen zijn verstreken. ESPHome verwerkt stopopdrachten asynchroon: een meteen gelezen STOPPED kan nog een ingeplande START verbergen. De twee grenzen geven de componentlus gelegenheid de opdracht te verwerken, maar deze scheduling-aanname moet nog op het apparaat worden gecontroleerd. De deadline van drie seconden wordt door herhaalde interrupts niet verlengd; verlopen tijd blijft een fout en opent playback niet opnieuw.

De geïnstalleerde ESPHome2026.6.2-bron bevat hier een sterkere voorziening dan `has_buffered_data()`: resampler-STOPPED vereist een gestopte downstreambron en afgesloten resamplertaak. De mixerbron wacht normaal op nul uitstaande frames naar de DAC. Daarom hergebruiken we deze stopstatus. Bij pauze of een interne timeout kan ESPHome echter de gedeelde I²S-uitgang geforceerd stoppen. Een eigen bron voorkomt het direct stoppen van de media-/announcementbron, maar bewijst **geen volledige foutisolatie**.

De C++-regressie in `tests/operations/test_native_duplex_speaker_stop.py` voert de daadwerkelijke barrier uit. `verify_speaker_stop.py --component <voorbereide-component>` voert daarnaast de daadwerkelijke gepatchte drain/flush-methoden uit met host-stubs: annulering tijdens `play`, annulering tussen snapshot en schrijven, lege ring met vastgelopen downstream en gedeeltelijke acceptatie. De ongewijzigde upstream faalt dezelfde proef doordat de reader gewijzigde ringbytes hoort. Dit is geen akoestische of echte FreeRTOS-proef.

`speaker-output.example.yaml` voegt een eigen bron en resampler toe aan de bestaande mixer. De kandidaat wordt alleen gebouwd met een onbereikbaar testendpoint. Ongewijzigd open blijven: sessie-/generatiebinding op de wire, bevestigde normale playbackcompletion, audio-/microfoontransport met authenticatie, native-VA/wakewordarbitrage, echo, hardwarescheduling en fysiek herstel. De bestaande upstream250ms-onderdrukking en kale PCM mogen niet als generatiebevestiging worden gebruikt. SP-03 blijft daarom nog niet beschikbaar voor een praktijktest.

De definitieve kandidaat is succesvol gecompileerd met ESPHome2026.6.2. Gegenereerde C++-bedrading bevestigt de eigen derde mixerbron; de gecompileerde componentbestanden matchen de vastgelegde hashes. Een onafhankelijke review heeft de zes barrier- en vier methodescenario's zelf herhaald. Bewijs: `evidence/product/VB-001-speaker-stop-candidate-2026-09-09.json`. Er is geen firmware op de woonkamer-speaker geplaatst.
