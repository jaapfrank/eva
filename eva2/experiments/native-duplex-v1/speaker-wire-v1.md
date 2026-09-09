# Experimentele speakeruitvoer, versie 1

Dit protocol heeft een Python-zender en een gebouwde C++-ontvanger voor de gepinde `va_pipecat`-component. De geïnstalleerde woonkamerfirmware spreekt dit protocol **niet**. Geen zelfstandig luisterendpoint of nieuw TTS-/gespreksmodel wordt hiermee gestart. De aanroepende integratie moet de speaker authenticeren en aan het juiste apparaat en huishouden binden voordat `FramedSpeakerOutput.start()` wordt aangeroepen. Een sessienonce voorkomt verwarring tussen verbindingen; hij vervangt geen authenticatie.

Per nieuwe verbinding wordt een willekeurige, niet-nul nonce van acht bytes uitgegeven. Ieder binair WebSocket-bericht bevat precies één record. Configureer het transport met `compression=None`, een WebSocket-berichtlimiet van2048bytes en een kleine begrensde ontvangstwachtrij. De applicatieparser accepteert maximaal988bytes per record. In de eerste echte HA-proef overschreed een gecomprimeerd frame de te strak gekozen transportlimiet van988bytes; stille synthetische PCM had dat niet blootgelegd.

Alle headergetallen zijn unsigned big-endian. De PCM-payload is signed16-bit **little-endian**, mono24000Hz.

| Byte | Inhoud |
| --- | --- |
| 0–3 | ASCII `EVA2` |
| 4 | Soort bericht |
| 5–7 | Nul, gereserveerd |
| 8–15 | Sessienonce |
| 16–19 | Antwoordgeneratie |
| 20–23 | Volgnummer |
| 24–27 | Payloadlengte |
| 28… | Payload: maximaal960bytes, dus20ms PCM |

| Opdracht | Nummer | Verwachte bevestiging | Betekenis |
| --- | --- | --- | --- |
| OPEN | 1 | OPENED,129 | Nieuwe verbinding; generatie/volgnummer0; eerst eventuele oude audio stoppen |
| BEGIN | 2 | BEGUN,130 | Nieuwe strikt oplopende generatie; volgnummer0; oude bron is vrij |
| AUDIO | 3 | ACCEPTED,131 | Niet-lege PCM met oplopende volgnummers vanaf1 |
| END | 4 | FINISHED,132 | Volgnummer van de laatste AUDIO; pas bevestigen na werkelijk afgespeelde frames |
| STOP | 5 | STOPPED,133 | Volgnummer0; generatie definitief ongeldig maken en resterende audio stoppen |
| — | — | ERROR,134 | Fout; verbinding en actieve audio worden afgesloten |

Een bevestiging echoot sessie, generatie en volgnummer van de opdracht. ACCEPTED, FINISHED en STOPPED bevatten één unsigned32-bit frameaantal. ACCEPTED telt cumulatief geaccepteerde frames van dit antwoord. FINISHED moet exact overeenkomen met alle aangeboden PCM. STOPPED geeft een bevestigde ondergrens van afgespeelde antwoordframes, maximaal het aangeboden aantal; eventuele interne stilte voor het starten van de resampler telt niet als antwoordaudio. De ontvanger bewaart de laatste acht generatietellingen. Voor oudere, uit deze geschiedenis verdwenen generaties is die ondergrens nul. Dit mag niet worden geïnterpreteerd als bewijs dat niemand iets hoorde, of als toestemming om geheugen te wissen.

Er staat maximaal één PCM-batch zonder ontvangstbevestiging uit. Wachten op ACCEPTED houdt de schrijftoegang vrij voor STOP. Acceptatie opent geen gespreksvervolg en beëindigt `speak()` niet. De Python-kant wacht op FINISHED, met maximaal de aangeboden PCM-duur plus de receiptmarge. STOP houdt zijn eigen korte deadline. Een ontbrekende of verkeerde bevestiging levert een fout op; timeout telt nooit als stilte.

De firmwareontvanger moet voor STOP onmiddellijk de generatie ongeldig maken, alle eigen nog wachtende PCM wissen en de lokale stopbarrière uitvoeren. Hij mag STOPPED pas sturen nadat de eigen downstreambron gestopt is. Late PCM/BEGIN/END voor die generatie moeten ook na een wachttijd en bij een nieuw antwoord geweigerd blijven. Een stop voor een oudere generatie mag een nieuwer antwoord niet stoppen. Een stop voor een nog niet uitgesproken generatie maakt ook die generatie ongeldig. Herverbinden vereist een nieuwe sessie en bevestigde lokale opruiming; geen impliciete heropening door oude faseberichten.

`FramedSpeakerOutput` bezit de render-, stop- en sluittaken. Annulering van een aanroeper annuleert de fysieke stop niet. Stoppen of verbindingsverlies beëindigt ook een stilstaande HA-/decoderinvoer. De renderer wacht nooit op zijn eigen stoptaak; fysieke stop wordt eerst afgehandeld, daarna wordt decoderopruiming afgewacht. Herhaalde stops annuleren een lopende opruiming niet opnieuw.

`HomeAssistantPCM` gebruikt de bestaande HA Cloud-TTS-adapter. FFmpeg decodeert via begrensde stdin/stdout-pipes; audio komt niet op schijf. WAV, FLAC en MP3 zijn gericht getest. Fouten, tijdoverschrijding, te lange audio en annulering beëindigen de decoder en sluiten de provideriterator. HA mag de hele tekst synthetiseren voordat ophalen begint; dit is geen claim van incrementele TTS.

`speaker-wire.patch`, `eva_output_protocol.h` en `eva_output_driver.cpp` implementeren de optionele ontvanger (`eva_pcm_protocol: true`). De assembler verwerkt zowel echte WebSocket-continuationframes als ESP-IDF-bufferfragmenten, met maximaal één record van988bytes. Een volle ring bewaart één wachtend AUDIO-record; STOP blijft bereikbaar en wist dit record. De audiotaak beheert stoppen en afspelen. De hoofdloop verstuurt maximaal vier receipts per iteratie, elk met een begrensde write. FINISHED vereist alle framecallbacks plus een bevestigde gestopte bron; een late callback van een vorige bronlevensduur telt niet voor de nieuwe generatie.

`SpeakerEndpoint` bindt de verbinding aan een vooraf geconfigureerd apparaat via TLS en een afzonderlijke apparaatsleutel. Hij levert aan de zender uitsluitend de maximaal acht voorgaande nonces van hetzelfde geauthenticeerde apparaat. De zender mag maximaal acht oude receipts uit deze historie negeren. Een onbekende sessie blijft een fout; oude receipts voldoen nooit aan een nieuwe opdracht. Dit is een begrensde oplossing voor een reeds opgehaalde receipt die tijdens reconnect op de nieuwe verbinding aankomt. De historie overleeft een herstart van de service niet. Firmware-send/reset-fouten en fysieke reconnect moeten nog worden beproefd.

Open voor de complete gebruikersroute: live apparaatprovisioning, aansluiting van continue microfooninvoer, native-VA/wakeword/mute-arbitrage, behoud van gehoorde gesprekcontext en veilige fysieke kandidaatproeven. Host- en loopbackproeven bewijzen geen akoestische stilte, echo-onderdrukking, p95 of menselijke acceptatie.
