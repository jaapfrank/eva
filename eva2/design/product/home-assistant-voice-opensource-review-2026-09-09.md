# Home Assistant en opensource voor Eva-spraak

Onderzocht op 9 september 2026. Status: documentatie en gerichte broncodecontrole; geen installatie, firmwarewijziging of geslaagde hardwareproef.

## Besluit voor Eva

Eerst de bestaande verstaanbare Home Assistant-spraakroute hergebruiken en normale vervolgbeurten bewijzen. De MP3-firmware blijft geparkeerd. Gewone-spraakonderbreking apart beoordelen; niet aannemen dat het met alleen een HA-instelling klaar is. Eva blijft verantwoordelijk voor identiteit, geheugen, mandaat en handelingen.

## Wat Home Assistant al biedt

[Conversation entity](https://developers.home-assistant.io/docs/core/entity/conversation/) ondersteunt gespreksidentiteit en een continuation-signaal. Eva's huidige native responder geeft dit signaal al terug en de HA-adapter geeft het door. Het signaal nogmaals toevoegen is dus geen oplossing; de werkelijk gebruikte route en volgende microfoonbeurt moeten worden gecontroleerd.

[Voice chapter 10](https://www.home-assistant.io/blog/2025/06/25/voice-chapter-10) beschrijft vervolgvragen zonder opnieuw activeren, onder meer wanneer een LLM een vraag terugstelt. Dit is geen garantie dat iedere beantwoorde vraag automatisch een onbeperkte sessie opent.

[Start conversation](https://www.home-assistant.io/actions/assist_satellite.start_conversation/) laat een geschikte satellite een gesprek beginnen, inclusief achtergrondcontext. Relevant voor Eva's eigen initiatief. Dit is geen algemene oplossing voor gelijktijdig luisteren en spreken.

[ESPHome Voice Assistant](https://esphome.io/components/voice_assistant/) documenteert `start_continuous`: opnieuw luisteren nadat antwoordaudio klaar is. `conversation_timeout` bewaart gesprekscontext; het is geen instelling die vanzelf de microfoon tijdens afspelen openhoudt. De normale vervolgfunctie is al langer beschikbaar; de actuele webdocumentatie is geen bewijs van alle functies op onze vastgezette versies.

## Opensource-opties

| Project | Bruikbaar voor Eva | Beperking / besluit |
| --- | --- | --- |
| [Pipecat Home Assistant](https://github.com/kyvaith/pipecat-homeassistant) | Meest relevante onderzochte kandidaat voor gewone-spraakonderbreking op ESPHome. MIT. | De standaard HA Assist-brug van dit project ondersteunt juist geen echte onderbreking tijdens spreken. De aparte satellite-component is nodig. |
| [ESPHome Intercom](https://github.com/n-IA-hane/esphome-intercom) | Bestaande gelijktijdige audiopaden en echo-onderdrukking; MIT. | De beschreven Assist-onderbreking gebruikt een wakeword. Een intercomgesprek is geen bewijs van een natuurlijk Eva-gesprek. Alleen gericht audio-onderdeel hergebruiken als nodig. |
| [Linux Voice Assistant](https://github.com/OHF-Voice/linux-voice-assistant) | OHF-project met HA-satelliteprotocol en vervolgbeurten; Apache-2.0. | Experimentele Linux-satellite voor x64/ARM64 met lokale audioapparatuur. Geen directe software-installatie op de bestaande ESP32 Voice PE. Geen bewezen oplossing voor gewone-spraakonderbreking gevonden in de gelezen README. |
| [Fulloch](https://github.com/liampetti/fulloch) | Lokale voice-assistant met HA en satellites; MIT-kern. | Beschrijft onderbreking via wakeword. Volledige tweede assistent zou Eva's kern dupliceren. Modellicenties verschillen van de MIT-kern. |

## Gerichte Pipecat-controle

De [va_pipecat-component](https://github.com/kyvaith/pipecat-homeassistant/blob/a538d1d1bacc8609061719f0b05c1622147ccb4b/components/va_pipecat/README.md) gebruikt bestaande ESPHome microfoon- en speakerinterfaces. Bij ingeschakelde barge-in blijft invoer lopen tijdens uitvoer, mits echo-onderdrukking geschikt is. Dit vraagt een component in de speakerfirmware; alleen een HA-add-on installeren volstaat niet. Een kant-en-klare, op onze woonkamer-Voice-PE bewezen configuratie is niet aangetroffen.

Gerichte lezing van [va_pipecat.cpp](https://github.com/kyvaith/pipecat-homeassistant/blob/a538d1d1bacc8609061719f0b05c1622147ccb4b/components/va_pipecat/va_pipecat.cpp) bevestigt de microfoongating en het legen van de eigen audiowachtrij bij terugkeer naar luisteren. De [architectuur](https://github.com/kyvaith/pipecat-homeassistant/blob/a538d1d1bacc8609061719f0b05c1622147ccb4b/docs/architecture/esphome-satellite.md) scheidt apparaatweergave van servergespreksstatus. Dit is broncodebewijs, geen gemeten stoplatentie, audit van alle buffers of bewijs dat de eerste woorden behouden blijven. De transporttests zijn bekeken, niet uitgevoerd.

De [Pipecat ESP32 SDK](https://github.com/pipecat-ai/pipecat-esp32) heeft geselecteerde ESP32-boardvoorbeelden; de README noemt geen bewezen Voice PE-route. Daarom heeft de ESPHome-component voor deze verkenning de voorkeur boven een complete SDK-port.

## Afvinkbare vervolgproef binnen VB-001

- [x] Officiële HA-documentatie en vier opensourceprojecten vergelijken.
- [x] Pipecat satellite-bron vastzetten en kern van microfoongating lezen.
- [ ] Bestaande goede audio-route exact terugvinden; huidige native route hiermee vergelijken zonder firmware-uitrol.
- [ ] Drie opeenvolgende echte vragen na één Hey Eva, met hoorbaar goed antwoord, dezelfde gesprekscontext en geen extra wakeword.
- [ ] Gesprek beëindigen en stilte-timeout controleren.
- [ ] Voor gewone-spraakonderbreking: componentcompatibiliteit, gedeeld speakergebruik en echo-onderdrukking toetsen vóór een apparaatproef.
- [ ] Praktijk: tijdens een lange zin onderbreken met een nieuwe vraag; geen oude audio, eerste woorden behouden, herhaalbare stopmeting.

Onderzoek voltooid betekent niet dat VB-001 of spraak geaccepteerd is. Er is tijdens dit onderzoek niets op HA of de speakers veranderd.
