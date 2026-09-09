# Native duplex: experimentele antwoordcoördinatie

Niet live en niet zelfstandig uitvoerbaar. Onderdeel van SP-03 / VB-001.

`reply_lane.py` bindt één geauthenticeerde huishoudspeaker aan één bestaande HA-conversatie. Het gebruikt de bestaande `EvaVoiceClient.respond` en diens request-specifieke HTTP-annulering; geen tweede model-, geheugen- of toolroute. Een nieuwe server-VAD-beurt annuleert de oude aanvraag en laat de audio-uitvoer de vorige generatie ongeldig maken. Daarna kan één definitieve transcriptie voor de nieuwe generatie worden ingestuurd. Verlate of dubbele transcripties worden geweigerd. De audio-uitvoer moet alle buffers van de oude generatie leegmaken en latere writes weigeren.

Vijftien experimentele controles, zeventien bestaande native-clientcontroles en vijfenveertig bestaande HA-mediacontroles slagen (77 totaal). De proeven gebruiken echte lokale HTTP- en WebSocket-verbindingen. `transcribe_new_turn` hergebruikt `HomeAssistantSpeechInput`: de eerste HA-melding `stt-vad-start` maakt de nieuwe antwoordgeneratie; pas definitieve STT-tekst mag naar de bestaande Eva-client.

De onafhankelijke review vond twee races. Sluiten kon een nog lopende stop annuleren en vervolgens overslaan. Stops hebben nu eigen taken die annulering van hun aanroeper overleven; `close` wacht daarop, ook als de lane al gesloten was. Verder kon een late fout uit de oude beurt de nieuwe luisterbeurt sluiten. Replyfouten zijn nu generatiegebonden; een mislukte fysieke stop blijft altijd fataal. Een observeerbaar sluitingssignaal beëindigt lopende STT bij een actuele antwoordfout of expliciete sluiting.

Een echte HA Cloud-proef met synthetische audio bevestigt de callback vóór final text: 1527 ms na de start van de proef, volledige tekst op 5443 ms. Deze tijden bevatten aanloopstilte en zijn **geen gemeten stoplatentie**. De volledige synthetische zin is herkend. Er is geen audio naar de speaker gestuurd en geen Eva-kernbeurt gemaakt. Bewijs: `evidence/product/VB-001-ha-speech-onset-2026-09-09.json`.

De [HA 2026.8.1-segmenter](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/assist_pipeline/vad.py) wacht standaard 300 ms spraak af; [de pipeline](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/assist_pipeline/pipeline.py) gebruikt die standaard. Alleen de HA-callback haalt dus niet de afgesproken p95-stopgrens van 250 ms. Voor snelle onderbreking blijft vroegere lokale spraakdetectie met echo-onderdrukking nodig. De [Assist-protocolbeschrijving](https://developers.home-assistant.io/docs/voice/pipelines/) specificeert de gebruikte STT/VAD-events en audioframes.

Het bestaande `VoicePlaybackController` verzorgt V01-playback voor een reeds uitgegeven `VoiceSession`. Deze huishoudproef maakt geen fictieve private V01-sessie aan en roept `admit_final_voice_turn` niet aan: de bestaande native huishoudroute bepaalt de doelgroep. Bij de definitieve aansluiting moet de overlap met de bestaande controller worden beoordeeld; dit experiment is geen tweede algemene voice-kern.

## Nog nodig voor de gevraagde functie

- De gebouwde TLS-serveradapter voorzien van geverifieerde HA-apparaatbindingen en op de echte speaker beproeven; hostproeven bewijzen geen fysieke ontvangst.
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

De onderstaande aanpassing hergebruikt de bestaande audio-task als enige eigenaar van `play/start/stop`, met voorrang voor stoppen. Een eigen resampler/mixerbron scheidt Eva's invoer van de announcement- en mediabron. Het nieuwe protocol bindt audiobatches en bevestigingen aan sessie en generatie. Reeds gemengde I²S-/DAC-samples vormen een aparte meetgrens; timeout mag niet als bevestigde stilte gelden. De werkende speaker krijgt deze wijzigingen pas als complete kandidaat met bruikbaar herstel.

### Lokale stopaanpassing gebouwd als kandidaat

`speaker-stop.patch` past de bestaande `va_pipecat`-component aan op de pin in `speaker-upstream.json`. De actuele voorbereiding gebruikt `prepare_speaker.py --upstream <bronmap> --websocket-upstream <esp-websocket-client-1.7.0-map> --output <nieuwe-bouwmap>`. Dit controleert de speaker- en transportbronbestanden en hun MIT-/Apache-2.0-licenties op hash, past de patches toe en legt de resulterende hashes vast. Een bestaande bouwmap wordt niet overschreven. Er is geen OTA- of flashactie in dit hulpmiddel.

De bestaande audiotaak is nu de enige aanroeper van speaker `play/start/stop`. Annuleren leegt de ring en vraagt een stop aan, ook als de ring al leeg was. Een begrensde kopie van maximaal20ms PCM voorkomt dat de reader tijdens `play` opnieuw gebruikte ringinhoud leest. Writes controleren bij het vastleggen opnieuw of een stop loopt. Stoppen gaat vóór de netwerkboodschap; verbindingsverlies gebruikt dezelfde lokale stopaanvraag.

`PlaybackStopBarrier` laat een volgende playback pas toe nadat de eigen resampler STOPPED meldt en twee hoofdloopgrenzen zijn verstreken. ESPHome verwerkt stopopdrachten asynchroon: een meteen gelezen STOPPED kan nog een ingeplande START verbergen. De twee grenzen geven de componentlus gelegenheid de opdracht te verwerken, maar deze scheduling-aanname moet nog op het apparaat worden gecontroleerd. De deadline van drie seconden wordt door herhaalde interrupts niet verlengd; verlopen tijd blijft een fout en opent playback niet opnieuw.

De geïnstalleerde ESPHome2026.6.2-bron bevat hier een sterkere voorziening dan `has_buffered_data()`: resampler-STOPPED vereist een gestopte downstreambron en afgesloten resamplertaak. De mixerbron wacht normaal op nul uitstaande frames naar de DAC. Daarom hergebruiken we deze stopstatus. Bij pauze of een interne timeout kan ESPHome echter de gedeelde I²S-uitgang geforceerd stoppen. Een eigen bron voorkomt het direct stoppen van de media-/announcementbron, maar bewijst **geen volledige foutisolatie**.

De C++-regressie in `tests/operations/test_native_duplex_speaker_stop.py` voert de daadwerkelijke barrier uit. `verify_speaker_stop.py --component <voorbereide-component>` voert daarnaast de daadwerkelijke gepatchte drain/flush-methoden uit met host-stubs: annulering tijdens `play`, annulering tussen snapshot en schrijven, lege ring met vastgelopen downstream en gedeeltelijke acceptatie. De ongewijzigde upstream faalt dezelfde proef doordat de reader gewijzigde ringbytes hoort. Dit is geen akoestische of echte FreeRTOS-proef.

`speaker-output.example.yaml` voegt een eigen bron en resampler toe aan de bestaande mixer. De kandidaat wordt alleen gebouwd met een onbereikbaar testendpoint. Ongewijzigd open blijven: sessie-/generatiebinding op de wire, bevestigde normale playbackcompletion, audio-/microfoontransport met authenticatie, native-VA/wakewordarbitrage, echo, hardwarescheduling en fysiek herstel. De bestaande upstream250ms-onderdrukking en kale PCM mogen niet als generatiebevestiging worden gebruikt. SP-03 blijft daarom nog niet beschikbaar voor een praktijktest.

De definitieve kandidaat is succesvol gecompileerd met ESPHome2026.6.2. Gegenereerde C++-bedrading bevestigt de eigen derde mixerbron; de gecompileerde componentbestanden matchen de vastgelegde hashes. Een onafhankelijke review heeft de zes barrier- en vier methodescenario's zelf herhaald. Bewijs: `evidence/product/VB-001-speaker-stop-candidate-2026-09-09.json`. Er is geen firmware op de woonkamer-speaker geplaatst.

### HA-spraak naar generatiegebonden transport

`speaker_wire.py` implementeert nu de Python-kant van het outputcontract, met een expliciete [protocolbeschrijving](speaker-wire-v1.md). Sessienonce, antwoordgeneratie en volgnummer onderscheiden nieuwe audio van oude. Een aparte ontvangstbevestiging houdt de invoer begrensd; alleen een aparte FINISHED met het volledige frameaantal beëindigt `speak()`. STOP heeft een eigen taak die annulering van de aanroeper overleeft. Dit gebruikt de bestaande reply-lane; er komt geen tweede gesprekskern bij.

`ha_pcm.py` zet het bestaande `HomeAssistantSpeechOutput`-resultaat met FFmpeg via geheugenpipes om naar24kHz PCM. De gerichte proeven controleren onder andere exacte WAV-samples, MP3/FLAC, decoderopruiming bij fouten en annulering, stop tijdens stilstaande TTS, herhaalde stops, verbroken verbinding, verkeerde ontvangstbewijzen en de daadwerkelijke aansluiting op `HouseholdDuplexReplyLane`. De onafhankelijke review vond twee opruimgaten: alleen wirefutures sluiten beëindigde een wachtende TTS-bron niet, en spontaan verbindingsverlies moest dezelfde opruiming activeren. De koppeling bezit nu aparte rendertaken en annuleert die eenmalig; decoderopruiming en de fysieke stop kunnen elkaar niet annuleren of op zichzelf wachten.

Een echte HA Cloud-TTS-proef leverde6,432seconden MP3-audio op, volledig omgezet naar154.368 PCM-frames en322 begrensde berichten. Wachten op alleen ontvangst voltooide het antwoord niet; de loopbackpeer moest apart FINISHED sturen. De eerste poging vond een te kleine transportlimiet bij gecomprimeerde PCM; compressie staat in het voorgeschreven transportprofiel uit en de applicatiegrens blijft strikt. Bewijs: `evidence/product/VB-001-ha-pcm-wire-2026-09-09.json`. Deze peer simuleert de speakerbevestiging: **geen fysieke playbackproef**.

### Protocolontvanger gebouwd en met de zender beproefd

`prepare_speaker.py` past nu achtereenvolgens `speaker-stop.patch` en `speaker-wire.patch` toe en kopieert de protocolengine en driver. De optionele `eva_pcm_protocol`-modus gebruikt een begrensde fragmentassembler, permanente invalidatie van gestopte generaties, één wachtend PCM-record bij een volle ring en een aparte receiptwachtrij. Audio ontvangen, naar de uitvoer geschreven en door de uitvoer bevestigd zijn drie afzonderlijke toestanden. FINISHED volgt pas na alle DAC-framecallbacks en de bestaande stopbarrière. De microfoon- en gebruikersbediening zijn nog niet aangesloten op deze modus.

De volledige kandidaat compileert met ESPHome2026.6.2 in244,21seconden. De gegenereerde C++ schakelt het protocol daadwerkelijk in; alle zes gecompileerde componentbronnen matchen de voorbereide hashes. `verify_speaker_wire.py` voert de werkelijke driver en drain/flush uit met hostvervangers voor speaker, klok en locks. De echte Python-zender voltooit een antwoord pas na vrijgegeven uitvoercallbacks, stopt een onderbroken antwoord en voltooit daarna een nieuwe generatie. Iedere opdracht wordt daarbij verdeeld over twee WebSocket-frames en kleinere ESP-IDF-fragmenten. Aanvullende driverproeven controleren volle buffers, STOP tijdens een nog lopende `play` en late callbacks uit de vorige generatie. UBSan meldt geen fout.

De onafhankelijke review heeft protocol/barrier, driverinteroperabiliteit en buildbinding zelf geverifieerd.45 gerichte pytest-controles slagen. Bewijs: `evidence/product/VB-001-firmware-wire-candidate-2026-09-09.json`. Deze kandidaat is niet geflasht. De eerder beschreven lokale stopkandidaat blijft afzonderlijk reproduceerbaar via zijn vastgelegde bron en bewijs.

Na deze ontvangerbuild is de hieronder beschreven serveradapter toegevoegd. De resterende aansluiting betreft native microfoon-/wakewordbediening, inclusief behoud van mute en herstel. Daarna volgen bruikbaar fysiek herstel en de complete speakerproef. SP-01 en SP-02 blijven geaccepteerd; SP-03 is nog niet beschikbaar voor een praktijktest.

### Apparaattoegang en herstel van de verbinding

`speaker_endpoint.py` levert nu de TLS-serveradapter voor deze uitvoer. De samenstellende service moet vooraf gecontroleerde HA-huishoudbindingen en afzonderlijke credentialhashes aanleveren. Een apparaat kan zelf geen persoon, apparaat-ID of gespreks-ID kiezen. De adapter accepteert uitsluitend een apparaatsleutel in de Authorization-header, zonder browser-Origin, URL-token of tweede gelijktijdige eigenaar. Intrekken sluit nieuwe toegang onmiddellijk en wacht op opruiming van de actieve sessie. Verbindingsverlies en een begrensde sessieduur annuleren werk; bij een nog levende verbinding blijft STOP/STOPPED beschikbaar voordat de socket sluit.

Per geauthenticeerd apparaat bewaart de adapter acht voorgaande verbindingsnonces. Maximaal acht achtergebleven receipts met zo'n bekende nonce worden genegeerd; zij kunnen geen nieuwe opdracht bevestigen. Een onbekende nonce of meer oude receipts sluit de verbinding. De historie leeft binnen dit serviceproces. De echte netwerkproef laat een oud FINISHED-bericht tijdens reconnect aankomen en voltooit daarna de nieuwe sessie. Dit vangt de eerder gevonden achtergebleven receipt aan serverzijde op; een fysieke reconnectproef en firmwarefouten rond gelijktijdige send/reset blijven onderdeel van de volledige route.

Normale afspeelvoltooiing krijgt nu de aangeboden PCM-duur plus de receiptmarge om te eindigen. Anders zou een langer antwoord door de korte controldeadline worden afgebroken terwijl de speaker nog terecht afspeelt. De STOP-deadline blijft kort en onafhankelijk. Een ontbrekende FINISHED blijft een fout.

`speaker-auth.patch` configureert WSS, servercertificaatcontrole en de apparaatsleutel op de speaker. In deze modus is automatische endpointprovisioning verboden; een draaiend apparaat kan zijn geconfigureerde bestemming niet met een gewone URL-actie veranderen. De gepinde Espressif-client volgde daarnaast intern HTTP-redirects. `websocket-no-redirect.patch` voegt daarom een optionele weigering toe vóór URI-wijziging of vervolgverbinding. Alleen Eva schakelt die in. De oorspronkelijke client blijft in de voorbereide kandidaat behouden met Apache-2.0-licentie en de herkomst in `websocket-upstream.json`.

ESP-IDF kan volledige upgradeheaders loggen, ook op foutniveau bij een mislukte write. De kandidaat schakelt daarom de tags `transport_ws` en `websocket_client` uit vóór geauthenticeerde I/O en zet de benodigde dynamische logniveaucontrole aan. Dit vermindert diagnostiek voor die twee tags op het hele apparaat; overige logtags blijven beschikbaar. De echte firmware-handshake en een foutscenario moeten nog fysiek bevestigen dat de apparaatsleutel niet in logs verschijnt.

De gerichte Python-proeven gebruiken echte TLS en WebSockets met een tijdelijk testcertificaat: verkeerde sleutels, onbekende servercertificaten, dubbele toegang, intrekking, reconnect en sessieverloop tijdens playback. `verify_speaker_auth.py --component <voorbereide-component>` voert daarnaast de werkelijke gepatchte C-redirectbranch uit onder UBSan en controleert onveilige configuraties. Dit is geen fysieke ESP32-TLS-handshake. De adapter is nog geen geïnstalleerde dienst: continue microfooninvoer, native wakeword/mute-bediening, gehoorde context en fysieke herstel-/acceptatieproeven ontbreken nog.

De definitieve kandidaat is in een verse bouwmap succesvol gecompileerd in234,85seconden.55 gerichte controles slagen; de C++-uitvoerproef en redirectproef slagen ook op deze voorbereide bron. De onafhankelijke review bevestigt de zes C++-bronhashes, de gebruikte gepatchte transportbron, de gegenereerde instellingen en de OTA-hash. Bewijs: `evidence/product/VB-001-authenticated-speaker-candidate-2026-09-09.json`. De kandidaat gebruikt alleen testcredentials en is niet op een speaker geplaatst.
