# Voice PE: diagnose van natuurlijk gespreksverloop

10 september 2026. Diagnose en voorstel; geen nieuwe runtimewijziging, firmware,
provider of rechten geactiveerd. De specifieke onbereikbaarheidsfout is door
Jaap in de praktijk als hersteld bevestigd. Dit is geen acceptatie van realtime
voice. GE-01 blijft geaccepteerd; het geheugengoal en browserwerk blijven apart.

## Gewenste gebruikersuitkomst

Jaap wil tijdens langer werk kunnen blijven praten. Eva kan een korte nuttige
zin uitspreken, verder werken, een onderbreking begrijpen en daarop voortbouwen.
Een snellere monoloog of een vaste opvulzin voldoet niet. Eva mag onvoltooide
handelingen niet als een bewezen resultaat uitspreken.

## Actuele proef, geen nieuwe audioproef

HA bewaart twee volledige runs van 14:50:11 en 14:50:29 lokale tijd en een derde
luisterstart om 14:50:46 zonder herkende tekst. Alle drie hebben dezelfde HA
conversation_id. De vervolgcontext is dus technisch behouden; luisteren tijdens
het antwoord is daarmee niet bewezen. Jaap meldt expliciet dat onderbreken niet werkt.

| Onderdeel | Eerste beurt | Tweede beurt | Betekenis |
|---|---:|---:|---|
| STT na gemeld einde spraak | 0,019 s | 0,016 s | HA VAD-einde tot transcript |
| Eva-intentfase | 6,635 s | 8,777 s | HA verzoek tot volledig antwoord |
| Household-geheugenopvraag | circa 2,206 s | circa 0,968 s | Passende Hindsight-logregels; geen totale systeembenchmark |
| Model tot eerste inhoud | 1,846 s | 6,779 s | Providerfase, niet eerste hoorbare woord |
| Model tot gereed | 3,118 s | 6,861 s | Duur uit opgeslagen model_timing |
| Antwoordlengte | 17 woorden / 93 tekens | 14 woorden / 68 tekens | Geen lange tekst in deze twee antwoorden |
| HA TTS-start tot TTS-end | 0,006 s | 0,007 s | Alleen pipelineoverdracht; geen gemeten synthesetijd of audiostart |

Van VAD-einde tot HA TTS-end duurt het 6,661 en 8,802 seconden. Eerste hoorbare
woord en echte afspeelduur zijn niet gemeten. Pipeline-einde betekent niet dat
de speaker klaar is. De volgende luisterstart volgt 9,050 en 5,378 seconden na
TTS-end; dat omvat mogelijk afspelen en omschakelen en mag niet als exacte
speelduur worden gepresenteerd. Operational REPLY_COMMITTED gebruikt de
beurttijd; die timestamp is evenmin een afspeelmeting. De latere verduidelijking
van Jaap maakt gespreksverloop, niet alleen snelheid, de centrale eis.

## Waar het huidige gedrag ontstaat

1. De geïnstalleerde HA-koppeling wacht in `conversation.py` op
   `EvaVoiceClient.respond`. Dat verwacht één volledig JSON-antwoord. De native
   core-route in `adapters/api/native_voice.py` retourneert pas na `worker.result()`.
   Er is geen kanaal voor tussentijdse gesproken gebeurtenissen. De beurt heeft
   bovendien een begrenzing van 32 seconden; langdurig werk is nog geen aparte
   voortlopende gesprekssessie.
2. De kern heeft `stream_conversation_text`, gebruikt door de dashboardroute.
   Native voice sluit daar niet op aan. De modeladapter laat bewust alleen
   geschikt eindantwoord door; providercommentaar wordt niet zomaar als een
   gesproken werkbericht gepubliceerd. Alleen die filter weghalen is geen veilige
   oplossing voor betrouwbare tussenzinnen.
3. HA meldt op beide apparaten ESPHome 2026.6.2. De bewaarde bron van die versie
   verzendt microfoonaudio in `STREAMING_MICROPHONE`, niet in
   `STREAMING_RESPONSE`. Bij `RESPONSE_FINISHED` begint de volgende microfoonbeurt
   alleen als continuation aanstaat. Dit verklaart Jaaps waarneming. Er is geen
   nieuwe uitlezing/hashvergelijking van de fysieke firmware gedaan.
4. De bestaande annulering bindt een stop aan device/request en kan late uitvoer
   onderdrukken. Ze detecteert zelf geen menselijke spraak tijdens afspelen.
   Een stopwoord/wakeword is geen gewone spraakonderbreking. Stoppen met spreken
   en stoppen met een langer werkende taak moeten ook afzonderlijke besluiten zijn.
5. De actieve HA-koppeling rapporteert geen afgespeelde fractie van haar antwoord.
   De kern bewaart het volledige antwoord vóór de hoorbare weergave. Bij echte
   onderbreking ontbreekt dus nog de koppeling tussen gehoorde context, gestopte
   audio en voortlopende taak.

Dit zijn ontbrekende onderdelen van de huidige gesprekssessie, niet opnieuw de
herstelde bronverwijzingsfout. HA blijft bruikbaar voor bestaande functies, maar
hoeft niet de uiteindelijke audiotransportroute te bepalen.

## Hergebruik en klein vervolgvoorstel

Hergebruik Eva's identiteit, householdmandaat, geheugen, taakstatus en
requestgebonden annulering. Beoordeel de al vastgezette
[va_pipecat-component](https://github.com/kyvaith/pipecat-homeassistant/blob/a538d1d1bacc8609061719f0b05c1622147ccb4b/components/va_pipecat/README.md)
en bestaande lokale stop-/uitvoerproeven; er liggen al gebouwde kandidaten,
geen fysiek bewezen installatie. De actuele
[upstreambeschrijving](https://github.com/kyvaith/pipecat-homeassistant)
onderscheidt zelf de klassieke HA Assist-brug zonder echte onderbreking van een
rechtstreekse ESPHome PCM-/WebSocketroute en WebRTC. Die beschrijving is een
hergebruikshypothese, geen bewijs voor onze apparaten. De
[ESPHome-documentatie](https://esphome.io/components/voice_assistant/)
beschrijft de bestaande assistentcomponent; de online versie is nieuwer dan de
hier gerapporteerde firmware. Er is geen reden om een complete tweede assistent
of nieuwe modelprovider te installeren voor deze diagnose.

**Voorgestelde ene vervolguitkomst:** op één bestaand apparaat kan Jaap één
onschadelijke, langer durende opzoektaak mondeling bijsturen terwijl Eva werkt
en af en toe iets relevants zegt. Eerste begrensde stap is vaststellen dat de
bestaande speaker gelijktijdig schone microfooninvoer en uitvoer kan leveren,
met een uitvoerbare herstelroute. Hergebruik daarvoor de voorbereide component;
controleer exclusief speakergebruik, alle buffers, echo en eerste woorden.
Bij falen is de uitkomst een onderbouwd hardware-/routebesluit, geen brede
firmwarebouw. Firmware-installatie behoort tot een expliciet vastgestelde
vervolgscope, niet tot deze diagnose.

Voor de complete kleine gebruikersproef is daarnaast één sessieadapter nodig:
spraakbegin/-einde, gesproken segmenten, voortgang uit echte taakgebeurtenissen,
afgespeelde audio, antwoordannulering en een afzonderlijke taakidentiteit.
Een correctie mag geen dubbele taakactie starten. Gebruik alleen een korte
nuttige voortgangszin als de taakstatus daar grond voor geeft; geen vast
wachtriedeltje, geen interne redeneertranscripten en geen vooruitgelopen resultaat.

Voorgestelde vooraf meetbare eindvoorwaarden (nog niet behaald):

- Eén start, daarna drie beurten met dezelfde context zonder opnieuw activeren.
- In tien onderbrekingen met gewone spraak stopt uitvoer in minstens negen gevallen
  binnen 500 ms vanaf lokaal vastgesteld spraakbegin; eerste woorden blijven behouden.
- In een proef met minstens 15 seconden echt werk verschijnt een relevante korte
  zin vóór voltooiing. Een mondelinge correctie tijdens die zin past dezelfde taak
  aan; één gecontroleerd eindresultaat, geen dubbele handeling.
- Geen ongehoorde antwoordrest als gehoord veronderstellen en geen oude audio
  na de correctie. Taak voortzetten of stoppen volgt Jaaps bedoeling.
- Twee minuten eigen uitvoer zonder menselijke stem veroorzaken geen valse
  onderbreking. Mute/einde/verbindingverlies sluiten de sessie gecontroleerd.
- Eerste hoorbare reactie, stoplatentie en taakduur apart vastleggen; Jaap
  beoordeelt daarna of het gesprek natuurlijk genoeg verloopt.

Nu al klein verbeterbaar als apart vervolg: een betrouwbare gebeurtenisroute
voor functionele werkberichten en audiotiming zichtbaar maken, voortbouwend op
bestaande streaming en taakstatus. Alleen tekststreaming aan HA hangen kan eerder
spraak leveren, maar lost gelijktijdig luisteren/context tijdens werk niet op.
Er is in deze diagnose geen van deze vervolgwijzigingen uitgevoerd.
