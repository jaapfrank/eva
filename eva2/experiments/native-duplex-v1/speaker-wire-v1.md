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

`speaker-wire.patch`, `eva_output_protocol.h` en `eva_output_driver.cpp` implementeren de optionele ontvanger (`eva_pcm_protocol: true`). De assembler verwerkt zowel echte WebSocket-continuationframes als ESP-IDF-bufferfragmenten, met maximaal één record van988bytes. Een volle ring bewaart één wachtend AUDIO-record; STOP blijft bereikbaar en wist dit record. De audiotaak beheert stoppen en afspelen. Eén netwerktaak verstuurt per iteratie eerst maximaal vier uitvoerreceipts en daarna één invoerrecord, elk met een begrensde write. De hoofdloop beheert microfoontoegang en faseweergave. FINISHED vereist alle framecallbacks plus een bevestigde gestopte bron; een late callback van een vorige bronlevensduur telt niet voor de nieuwe generatie.

`SpeakerEndpoint` bindt de verbinding aan een vooraf geconfigureerd apparaat via TLS en een afzonderlijke apparaatsleutel. Hij levert aan de zender uitsluitend de maximaal acht voorgaande nonces van hetzelfde geauthenticeerde apparaat. De zender mag maximaal acht oude receipts uit deze historie negeren. Een onbekende sessie blijft een fout; oude receipts voldoen nooit aan een nieuwe opdracht. Dit is een begrensde oplossing voor een reeds opgehaalde receipt die tijdens reconnect op de nieuwe verbinding aankomt. De historie overleeft een herstart van de service niet. Firmware-send/reset-fouten en fysieke reconnect moeten nog worden beproefd.

Open voor de complete gebruikersroute: live apparaatprovisioning, aansluiting van continue microfooninvoer, native-VA/wakeword/mute-arbitrage, behoud van gehoorde gesprekcontext en veilige fysieke kandidaatproeven. Host- en loopbackproeven bewijzen geen akoestische stilte, echo-onderdrukking, p95 of menselijke acceptatie.


## Optionele microfoonroute op dezelfde verbinding

De serverextensie vereist expliciete activering van microphone_enabled. De bestaande uitvoerheader blijft gelijk. Het veld generation is voor invoer een opname-epoch en voor uitvoer de antwoordgeneratie; deze tellers zijn onafhankelijk. De invoerextensie is geïmplementeerd in `speaker-input.patch`, `eva_input_protocol.h` en `eva_input_driver.cpp`. De geïnstalleerde woonkamerfirmware gebruikt deze kandidaat nog niet.

| Richting | Code | Naam | generation | sequence | Payload |
|---|---:|---|---|---|---|
| apparaat naar server | 144 | WAKE | 0 | 0 | leeg |
| server naar apparaat | 6 | CAPTURE | volgend epoch vanaf 1 | 0 | leeg |
| apparaat naar server | 148 | CAPTURED | hetzelfde epoch | 0 | leeg |
| apparaat naar server | 145 | MICROPHONE | hetzelfde epoch | oplopend vanaf 1 | exact 640 bytes PCM16LE mono 16 kHz, 20 ms |
| apparaat naar server | 146 | LOCAL_STOP | actief epoch | laatst verstuurde PCM-volgnummer | leeg |
| apparaat naar server | 147 | MUTED | actief epoch | laatst verstuurde PCM-volgnummer | leeg |
| server naar apparaat | 7 | CAPTURE_END | actief epoch | 0 | leeg |
| apparaat naar server | 149 | CAPTURE_ENDED | hetzelfde epoch | 0 | leeg |

WAKE is alleen geldig na OPENED en maximaal eenmaal per verbinding. De server mag zonder die lokale grant geen opname starten. MICROPHONE mag pas na CAPTURED volgen. Een LOCAL_STOP maakt een volgende CAPTURE mogelijk nadat CAPTURE_ENDED is bevestigd; dit vereist geen nieuwe WAKE. MUTED staat geen nieuwe opname toe en sluit de sessie. Een normaal afgerond gesprek sluit eveneens de verbinding; opnieuw beginnen vereist een lokale wake.

De apparaatimplementatie moet lokale stop/mute meteen toepassen. Een enkele geordende zender moet controlerecords en PCM ordenen. Geen oude PCM mag na CAPTURE_ENDED volgen en geen PCM mag door een nieuwe opname-epoch heen lekken. CAPTURE_ENDED is pas geldig als lokale toelating is gesloten en alle eerdere writes zijn afgehandeld. Opnamebuffer-overloop mag niet ongemerkt woorden verwijderen. Tijdens bewust sluiten negeert de server nog onderweg zijnde PCM tot CAPTURE_ENDED, maar controleert wel epoch en volgorde. Een vertraagde microfoonbevestiging blokkeert de afzonderlijke STOP/STOPPED-route niet.

Reconnect maakt een nieuwe verbindingsnonce en vereist een nieuwe lokale WAKE; oude microfoonaudio kan nooit door de historie voor oude uitvoerreceipts worden toegelaten. De server staat maximaal 512 opname-epochs per verbinding toe; daarnaast blijven de bestaande sessieduur en gespreksgrenzen gelden. De native hardwaregate, echo en fysieke stop-/herstelbevestiging zijn nog niet door de localhost-proeven bewezen.

### Lokale invoerregeling in de firmwarekandidaat

De invoer begint gedempt totdat de fysieke mutestatus is gelezen. Een lokale wake opent een begrensde voorbuffer met het toekomstige opname-epoch; PCM verlaat het apparaat pas na CAPTURED. CAPTURE behoudt die voorbuffer. Stop tijdens deze nog onbevestigde toelating sluit de verbinding, zodat een vertraagde CAPTURE de stop niet kan terugdraaien. Stop tijdens een actieve opname volgt de bestaande LOCAL_STOP/CAPTURE_END-route naar een nieuwe opname zonder wakeword.

`EvaMicrophoneSource` gebruikt ESPHome's ingestelde kanaal-, gain- en bitdiepteomzetting. De raw callback legt vóór omzetting een token vast met verbindingsnummer en opname-epoch. De driver controleert dit opnieuw bij iedere begrensde kopie. Het omzetten van oude audio kan daardoor geen nieuwe opname vullen, ook niet als beide verbindingen epoch 1 gebruiken. De conversiebuffer blijft eigendom van de microfoontaak en wordt niet tijdens stop vrijgegeven. Overschrijding van de 16 KiB verzendbuffer sluit de verbinding; actieve audio wordt nooit stil weggeschoven.

Lokale stop/mute blokkeert ook onmiddellijk de eigen uitvoer. Dit levert nog geen STOPPED op: die bevestiging vereist de normale serveropdracht en de fysieke stopbarrière. Hostproeven vervangen microfoon, speaker, klok en FreeRTOS-locks; gedeelde microfoonbesturing, echo en het tijdstip van werkelijke stilte blijven hardwareproeven.
