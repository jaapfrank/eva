# Eva Conversational Voice v1

Bijgewerkt: 27 augustus 2026.

## Productuitkomst

De eigenaar kan een doorlopend gesprek met Eva voeren zoals in een moderne
voice mode: spreken, snel antwoord horen, tijdens Eva's antwoord opnieuw gaan
praten, Eva onmiddellijk laten stoppen en daarna vanuit dezelfde context
doorgaan. Dit is iets anders dan een voice note of de spontane
deurbel-/speakerpresence uit `OUDE_EVA_VS_EVA2.md`.

Eva's identiteit, persoonsbinding, Hindsight-geheugen, policy, toolledger en
eindantwoord blijven dezelfde als in tekstchat. STT, TTS, WebRTC en Home
Assistant zijn vervangbare media-capabilities; zij worden geen tweede brein.

## Dependency-first uitvoerkeuze

De eerste verticale proef gebruikt Pydantic AI Realtime op exact `2.34.0` in
een aparte kandidaatlock; Eva's productlock blijft tot promotie op `2.32.1`.
Upstream levert de realtime-agentloop, audio-/transcriptevents, usage,
reconnectbouwstenen en `RealtimeSession.interrupt(played_ms)` voor barge-in.
`RunContext.cancel()` is een andere grens: die annuleert een run en vervangt
niet het leegmaken van de lokale speakerbuffer.

Pydantic bezit dus niet Eva's microfoon-/speakerbridge of hoorbare playbackstop.
Stil zijn binnen 250 ms moet afzonderlijk worden bewezen. Eva voegt alleen
persoon, sessiegrant, privacy, duurzame toolstatus en eindantwoord toe. We
bouwen geen eigen realtime-agent-, WebRTC-server-, STT-, TTS-, VAD- of
jitterbufferplatform zolang de upstream/providerroutes de vaste p95-gates
halen. Alleen de dunne client/media-aansluiting en gemeten playbackflush zijn
Eva-integratiewerk.

Alleen wanneer diezelfde afgeschermde proef een exact gemeten gat laat zien,
wordt LiveKit Agents als tweede geïsoleerde mediakandidaat beproefd. Een
providerwissel verandert Eva's `VoiceSession`-contract niet en levert de
provider geen geheugen-, policy- of taakautoriteit op.

## Kanaalkeuze

1. **Voorkeur — Telegram Mini App:** de Eva-bot toont één knop `Bel Eva`. De
   ingebedde HTML5-app opent na geverifieerde Telegram-initdata een tijdelijke
   full-duplex audiosessie met Eva's voice-gateway. Dit voelt als bellen binnen
   Telegram en houdt de bestaande persoonsbinding.
2. **Lokale fallback — Home Assistant:** Assist/Wyoming levert microfoon,
   wake-word, streaming STT en TTS. De STT-run stopt vóór Home Assistants
   `intent`-fase; alleen Eva verwerkt de definitieve tekst. Een afzonderlijke
   TTS-run krijgt uitsluitend Eva's antwoord. HA-intent, conversation-agent en
   servicecalls zijn op deze route uitgeschakeld.
3. **Niet de basis — native Telegram-call:** de Bot API ondersteunt
   voiceberichten, geen bottelefoongesprek. Telegrams `phone.requestCall` is
   uitsluitend voor useraccounts. Een apart Eva-useraccount/MTProto-prototype
   is daarom pas toelaatbaar na een afzonderlijke account-, voorwaarden-,
   privacy- en stabiliteitsgate; V1 is er niet van afhankelijk.

## Sessiestroom

```text
microfoon -> streaming audio -> VAD/STT -> Eva ConversationTurn
          <- streaming TTS  <- antwoord/zinchunks <- Eva Living Core
                  ^
                  | barge-in: playback annuleren en huidige generatie stoppen
```

- Iedere sessie bindt server-side exact één geverifieerde persoon, conversation-id,
  IdentityPack-versie, policyversie en kanaal/device.
- Partial transcripts zijn vluchtig. Alleen de definitieve beurt gaat, volgens
  dezelfde toestemming als tekstchat, naar gesprek en geheugen.
- Barge-in annuleert audio-uitvoer en de nog niet geleverde antwoordgeneratie,
  maar is op zichzelf geen geauthenticeerde tool-cancel. Vóór `prepare` kan een
  definitieve expliciete stop/correctie de geplande actie sluiten. Na prepare
  maar vóór dispatch volgt de gewone duurzame cancelroute. Vanaf
  `dispatch_authorized` of zodra provideracceptatie mogelijk is, wordt nooit
  teruggedraaid of blind opnieuw uitgevoerd: Eva markeert alleen voice-delivery
  als interrupted en wacht op observation/readback, reconcile, `ambiguous` of
  `needs_help`. Zij spreekt geen succes uit vóór bewijs.
- Een toolactie volgt dezelfde prepare/call/readbackketen als tekstchat. Eva
  spreekt geen succes uit voordat de bestaande bewijsgrens succes kent.
- Ruwe audio wordt standaard niet duurzaam bewaard. Opt-in diagnostiek is
  maximaal 24 uur versleuteld met een per-sessie-DEK onder de privacyservice,
  los van Hindsight, met append-only toegangslog. Verwijderen wist data en DEK;
  back-ups volgen uiterlijk binnen zeven dagen. Logs bevatten geen audio,
  tokens of secrets. Een externe STT/TTS-provider mag audio alleen na aparte,
  providergebonden toestemming bewaren; standaard is providerretentie uit.

## Vertrouwde sessiegrens

- De server accepteert uitsluitend ruwe Telegram `initData`, nooit
  `initDataUnsafe`: officiële Telegram-HMAC, `auth_date` maximaal 60 seconden
  oud, exact bot/audience en een single-use replayrecord moeten kloppen.
- De server kiest principal en conversation uit de geverifieerde Telegram-
  binding. Clientvelden kunnen die IDs niet aanleveren of overschrijven.
- Daarna ontstaat één kortlevende sessiecredential met single-use `jti`, exact
  principal, conversation, Telegram-user/chat, sessie, device/channel,
  audience en deadline. Tokens staan alleen in headers/protocolmessages, nooit
  in URL, SDP, ICE, logs of audioframes.
- Signaling en media gebruiken uitsluitend HTTPS/WSS of geauthenticeerde
  WebRTC-signaling met een exacte Origin-allowlist. Authenticatie gaat vóór
  SDP/ICE. Frame-, byte-, bitrate-, sessie- en rategrenzen zijn fail-closed.
  WebRTC gebruikt een gepinde relay-only TURN-route; een publieke STUN/directe
  peerroute mag geen IP-adressen lekken.
- De mediaworker krijgt alleen de sessiegrant en mediaframes. Hij krijgt geen
  Telegram-/HA-token, PostgreSQL, Hindsight, IdentityPack-signer of
  secret-storetoegang.
- Voor Home Assistant is `device_id`/`conversation_id` geen identiteit. De
  server kiest een vooraf geverifieerde device→principal-binding. Een onbekend
  of gedeeld apparaat krijgt geen privégeheugen of toolrechten. Een negatieve
  gate bewijst dat een HA-native intent nooit een antwoord, tool of servicecall
  kan veroorzaken.

## V01 staat nu in de kern, zonder te doen alsof bellen al werkt

Checkpoint `57f36f0` levert de eerste echte voicegrens op:
`src/eva/contracts/voice.py` bevat `VoiceSession`, `VoiceTurn`, `BargeIn` en
de expliciete privacykeuze. `src/eva/application/voice.py` zet alleen een
servergebonden **definitieve** gesproken beurt om naar Eva's bestaande private
`ConversationTurn`. Daardoor wordt voice geen tweede geheugen of tweede
gesprekskern.

De gerichte tests weigeren ruwe audiobytes, partial transcripts, een onjuiste
transcripthash, een andere persoon of gesprek, verlopen sessies en een
barge-in buiten de sessie. Een barge-in heeft expliciet geen toestemming om
een tool-effect te annuleren: hij mag alleen hoorbare output en een nog niet
afgemaakte antwoordgeneratie stoppen. **21** contract-, gesprek- en
schemachecks zijn groen. Er is nog geen microfoon, speaker, echte persoon,
voiceprovider, token, opslag, latencyclaim of onafhankelijke review; V01
blijft daarom op het aftekenbord open totdat de review is uitgevoerd.

De types staan bewust nog niet in de permanente portable-wire-registry. Die
registry is de exportgrens voor bestaande duurzame records; er bestaat nog
geen V01-voiceconnector of migratie die zo'n extern/draagbaar record mag
schrijven. V03/V04 promoveren de exact dezelfde shapes pas samen met die
concrete route en opslagautoriteit.

Checkpoint `20490a0`, hersteld in `585eabf`, sluit daarop de kleine afspeelgrens
aan. De nieuwe
`VoicePlaybackController` kent alleen de gebonden sessie, beurt,
antwoordgeneratie en ingeplugde `play`/`stop`- en generatiestopgrenzen. Hij
bewaart geen audio; hij geeft begrensde chunks door. Een geldige `BargeIn`
start tegelijk de speakerstop én een gebonden stopopdracht voor uitsluitend de
nog lopende antwoordgeneratie. De speaker hoeft dus niet op een trage
modelprovider te wachten. Mislukt één van beide stops, dan krijgt de caller
een fout en nooit een onterechte
`interrupted`-receipt. De generatie-stop krijgt geen toolgrant en kan geen
huisactie terugdraaien of herhalen. Verkeerde persoon, beurt of generatie,
lege chunks en te grote chunks worden geweigerd. De extra afspeeltests plus de
bestaande voice- en gesprekstests zijn samen **28/28 groen**. De concrete
Pydantic-/TTS-adapter moet deze verplichte generatiestopgrens nog aansluiten.
Dit is geen speaker, microfoon, providerverbinding, full-duplex- of
latencybewijs; V02 blijft dus open.

De benodigde upstreamroute is ook echt afzonderlijk geïnstalleerd:
`experiments/pydantic-realtime-v1/` bindt Pydantic AI
`2.34.0[openai-realtime]` op upstreamcommit `bfa8e91…` aan een 47-package
hashlock. De kandidaat behoudt `websockets==17.0.1`; `websockets>=14` is dus
geen blocker. De echte `Agent.realtime(...).session()`- en
`RealtimeSession.interrupt(played_ms=...)`-interface importeert groen. Dit is
dependencybewijs, geen audio- of providerbewijs: V02 blijft een echte
microfoon→speaker streamingproef nodig hebben.

## Eerste lokale V02-keten is echt uitgevoerd, maar nog geen voice-call

Checkpoint `73561ce` gebruikt geen zelfgebouwde STT- of TTS-engine. De proef
in `experiments/wyoming-local-voice-v1/` gebruikt de officiële
`wyoming==1.10.0`-client, een gepinde lokale Piper-sidecar met Nederlandse
`nl_NL-pim-medium`-stem en een gepinde lokale Faster-Whisper-sidecar met
`base-int8`. Een vaste, synthetisch gesproken Nederlandse testzin werd als PCM
naar lokale STT gestuurd. Checkpoint `d28ee3a` relayt iedere PCM-chunk direct
naar Whisper vóór Piper klaar is en bewaart daarbij alleen teller, formaat en
SHA-256—nooit het volledige invoeraudiobestand. Alleen de teruggekomen
**definitieve** tekst ging door `VoiceTurn/v1` naar Eva's gewone private
gesprekbeurt; daarna maakte Piper weer PCM voor een vaste antwoordzin. De
controllerlock, bronhashes,
sidecar-pins, teksthashes en PCM-hashes staan in één receipt;
`verify_receipt.py` controleert dit zonder Docker of netwerk opnieuw.

Dit is nuttig omdat de route nu niet alleen op papier bestaat: audioformaat,
officiële Wyoming-protocolgrens, Nederlandse STT, Eva's privacygrens en lokale
TTS zijn samen geraakt. Maar het is nadrukkelijk **geen** gesprek met een mens:
geen microfoon of speaker, geen modelantwoord, tool, opslag, barge-in,
full-duplexstream of latencyclaim. Een herhaalde `--internal`-start vond
vervolgens twee verborgen dependencygrenzen: de oude Whisper-cache miste de
tokenizer die de actuele upstream bij het laden gebruikt, en Piper plus Whisper
hebben ieder een kleine tijdelijke schrijfruimte nodig. Daarom is checkpoint
`8af6a15` alleen nog een historisch inspectierecord, geen actuele offlineclaim.
De vernieuwde proef
`offline-isolation-v2-receipt.json` gebruikt een exact gepinde
`openai/whisper-tiny`-tokenizercache naast het read-only `base-int8`-model en
laat uitsluitend een begrensde `tmpfs` toe. Zij bewijst met een intern
Docker-netwerk zonder hostpoorten of internetgateway dat alle drie processen
onder die voorwaarden starten en de synthetische keten afmaken. Zij bewees ook
dat de controller na drie lokale TTS-blokken de onafgemaakte stream sluit zonder
de rest te bufferen. Dit is nog steeds geen microfoonbarge-in, full-duplex,
latency- of menselijke voiceproef; V02 blijft onafgevinkt.

## Snelheidsbudgetten

Deze waarden zijn acceptatiedoelen, nog geen gemeten productclaims.

| Meting | V1-doel (p95) |
| --- | ---: |
| Sessiestart tot audio-ready | maximaal 2,0 s |
| Laatste gebruikersspraak tot eerste hoorbare Eva-audio, gewone beurt | maximaal 1,5 s lokaal; maximaal 2,0 s via internet |
| Onderbreking tot Eva stil is | maximaal 250 ms |
| Hervatten na onderbreking zonder contextverlies | maximaal 1,0 s tot nieuwe verwerking start |

De gateway streamt STT en TTS en wacht niet op een volledig audiobestand. De
metingen bevatten device-, netwerk-, STT-, model- en TTS-tijd afzonderlijk,
zodat een trage provider zichtbaar blijft.

Iedere route gebruikt client-monotonic meetpunten voor
`speech_end→first_playout` en `barge_vad_start→playout_stopped`, plus één
trace-id door gateway, STT, Eva, model en TTS. De verzegelde V07-campagne bevat
per route minimaal 100 gewone beurten en 40 onderbrekingen, verdeeld over
minstens drie sessies. Dropouts, reconnects en time-outs blijven failures in de
noemer; onvoldoende samples is rood. Device, netwerkklasse en warm/koud worden
vooraf vastgelegd en niet achteraf weggefilterd.

## Opleverslices en eindgate

- [ ] V01 — Eén connectorneutraal `VoiceSession`-/`VoiceTurn`-/`BargeIn`-
  contract met persoon, gesprek, deadlines, cancel-generation en privacy.
- [ ] V02 — Lokale full-duplex testclient met echte streaming STT/TTS,
  onderbreken en gescheiden latencyreceipts; nog geen Telegram of HA.
  `73561ce`/`d28ee3a` sluiten de synthetische Piper→Faster-Whisper→V01→Piper-
  keten inclusief chunk-relay; echte microfoon/speaker, twee richtingen tegelijk,
  interrupt en latency blijven open. `8af6a15` is alleen een historisch record;
  `offline-isolation-v2-receipt.json` sluit de gecorrigeerde no-egress-
  sidecarproef met vaste tokenizercache en noodzakelijke `tmpfs`-ruimte.
- [ ] V03 — Home Assistant Assist/Wyoming-route tegen Eva's bestaande
  gesprekcore, met dezelfde persoon/context en zonder tweede intentbrein.
- [ ] V04 — Telegram Mini App `Bel Eva`: server-side initdataverificatie,
  HMAC/auth-date/single-use-replay, servergekozen persoon/gesprek, kortlevende
  scoped sessiecredential, Origin/TURN/framegrenzen, WebRTC/WebSocket-audio en
  directe hang-up.
- [ ] V05 — Toolbeurt via voice: minstens één echte Home Assistant-read en één
  begrensd effect met fysieke readback; gesproken status blijft eerlijk.
- [ ] V06 — Crash, reconnect, dubbel audioframe, gestolen/expired sessietoken,
  Origin/signalingmisbruik, cross-person leakage en barge-in vóór prepare, na
  prepare, tijdens dispatch en na mogelijke provideracceptatie fail-closed;
  nul dubbel effect en geen gesproken succes vóór readback/reconcile.
- [ ] V07 — Op de bevroren eindimage een echt gesprek van minimaal tien
  minuten via voorkeurroute én HA-fallback. De eigenaar onderbreekt Eva
  in iedere kritieke toolfase; de verzegelde campagne haalt per route de vaste
  100 gewone beurten/40 onderbrekingen. P95-budgetten, failure-noemer, context,
  privacy en gebruikersacceptatie zijn groen.

V01-V06 moeten technisch vóór de imagefreeze sluiten. V07 wordt op exact
dezelfde bevroren kandidaat uitgevoerd en blokkeert productiepromotie.

## Taalkeuze

De Living Core blijft Python 3.12: daar zitten nu contracten, planning,
PostgreSQL, Hindsight, policy en connectors. Een volledige Rust-herschrijving
zou de huidige bewijsketen opnieuw openen zonder dat dit op zichzelf voice
sneller maakt.

Rust is wel een gerichte optie voor de geïsoleerde media-dataplane—VAD,
resampling, jitterbuffer, WebRTC-audioworker of protocol-sandbox—als de
Python/standaardcomponenten het gemeten p95-budget niet halen. De Telegram Mini
App zelf is HTML/TypeScript. Elke Rust-component blijft een vervangbare
capability achter hetzelfde contract en krijgt eigen fuzz-, crash- en
latencytests; zij bezit geen identiteit, geheugen, policy of secretsauthority.

## Primaire technische bronnen

- Pydantic AI realtime op exact `2.34.0`:
  <https://github.com/pydantic/pydantic-ai/blob/v2.34.0/docs/realtime/overview.md>
- Exacte actuele proefrelease:
  <https://github.com/pydantic/pydantic-ai/releases/tag/v2.34.0>
- LiveKit Agents, alleen fallback:
  <https://github.com/livekit/agents>
- Telegram Bot API: <https://core.telegram.org/bots/api>
- Telegram `phone.requestCall`: <https://core.telegram.org/method/phone.requestCall>
- Telegram Mini Apps: <https://core.telegram.org/bots/webapps>
- Home Assistant Assist pipelines:
  <https://developers.home-assistant.io/docs/voice/pipelines/>
- Home Assistant Wyoming:
  <https://www.home-assistant.io/integrations/wyoming/>

## VB-001 herstart — installatie gecontroleerd op 8 september 2026

De actuele productkandidaat gebruikt Pydantic AI 2.37. De oudere
2.34/2.32-versies hierboven blijven historische proefbindingen, geen actuele
installatiekeuze. De bestaande voicecontract- en playbacktests slagen op de
huidige ontwikkelomgeving: 19 tests. Er is nog geen echte duplexgebruikersroute.

Een read-only `assist_pipeline/pipeline/list` via de bestaande live
Home Assistant-connector vond drie pipelines, waarvan twee Nederlands.
Deze gebruiken `stt.home_assistant_cloud` en `tts.home_assistant_cloud`;
de geconfigureerde conversation-engine is `conversation.home_assistant`.
Er zijn geen pipelines veranderd, geen microfoon geopend en geen audio of
modelaanroepen verstuurd. Een aanwezige cloudconfiguratie bewijst niet dat
nieuwe audioversturing of providerretentie al is geaccepteerd.

De actuele officiële Assist-interface ondersteunt afzonderlijk
`start_stage=stt,end_stage=stt` en `start_stage=tts,end_stage=tts`.
Daarmee kan de bestaande HA-media-aansluiting worden onderzocht zonder de
HA-intentfase aan Eva's gesprek toe te voegen. De dunne serveradapter moet
iedere onverwachte `intent-start`/`intent-end` als ongeldige route afwijzen.
Een HA-device-ID blijft geen bewijs van een persoonlijke identiteit.

De volgende verticale stap is een geauthenticeerde media-aansluiting die één
finale transcriptie via `admit_final_voice_turn` in de bestaande gesprekskern
brengt en uitsluitend Eva's antwoord terugspreekt. Microfoon en afspelen moeten
naast elkaar kunnen werken; lokale playbackstop mag niet wachten op de
server of modelprovider. De bestaande lokale Wyoming/Piper/Whisper-proef
blijft de beschikbare route voor audio zonder nieuwe cloudverwerking.
De keuze tussen deze media-aansluitingen wordt pas definitief na hun concrete
sessie-, privacy- en interruptproef; geen nieuwe tweede agentkern toevoegen.

Bronnen gecontroleerd op 8 september 2026:
- [Assist pipelines](https://developers.home-assistant.io/docs/voice/pipelines/)
- [Wyoming Piper](https://github.com/OHF-Voice/wyoming-piper)
- [Wyoming Faster-Whisper](https://github.com/OHF-Voice/wyoming-faster-whisper)

### Serveraansluiting na de echte HA-mediaproef

Media-checkpoint `04ebe03` levert STT/TTS-adapters met afzonderlijke pipeline-
stappen. De echte HA-proef onthulde `run-end.data=null`; beide adapters
accepteren dat nu uitsluitend voor dit afsluitende event. De synthetische zin
werd via echte TTS, vluchtige ffmpeg-PCM en echte STT teruggelezen. Bronreview
is groen; dit is nog geen browsermicrofoon, speaker of Eva-gesprekslus.

De serverroute gebruikt de bestaande eigenaar-dashboardauthenticatie, met
verplicht aanwezige token en exacte Origincontrole vóór WebSocket-accept.
Geen credential komt in een URL of frontendconfiguratie. Browser-Basic-auth
moet fysiek worden bewezen; indien nodig wordt een eenmalig kort ticket via
geauthenticeerde POST gebruikt. De bestaande ontwikkelmodus zonder
dashboardtoken mag voice nooit openen.

De runtime kiest dezelfde tenant, installatie, eigenaarprincipal en
`dashboard:{binding}` als de bestaande dashboardgesprekken. Voice kopieert
geen Telegramprincipal of diens bevoegdheden. De server genereert sessie-,
beurt- en bericht-ID, hercontroleert grant, klok, actuele identiteit/policy en
eigenaarbinding vóór STT en vóór gespreksadmission. De finale transcriptie gaat
via `admit_final_voice_turn` naar `runtime.respond` en `after_response`.
`dashboard_turn` mag niet alsnog een tweede bericht-ID voor dezelfde beurt
maken. Synchroon modelwerk draait buiten de WebSocket-eventloop; een
geannuleerde thread-wacht betekent niet dat dat werk zelf geannuleerd is.

Playback en iedere chunk blijven aan een generatie gebonden. Barge-in stopt
TTS/playback en onderdrukt achterhaalde uitvoer. Alleen een expliciete
opdrachtstop gebruikt de bestaande duurzame stoproute. De mediaconfiguratie
moet werkelijk providerretentiebeleid bevatten: lokaal geen audio bewaren
bewijst geen uitgeschakelde retentie bij Home Assistant Cloud.


### VB-001 — dashboardtransport gebouwd, nog niet live (8 september 2026)

De route `/internal/v1/dashboard/conversation/voice` staat standaard uit.
Expliciete serverconfiguratie levert de eigenaargebonden sessie, actuele
identity/policy/consentcontrole en media-adapters. Een dashboardtoken én exacte
Origin zijn verplicht; querycredentials, clientpersoon en clienttranscripten
worden geweigerd. De bootstrap die deze actuele grant uitgeeft en de browser
met microfoon en speaker moeten nog worden aangesloten.

Het transport accepteert `start`, binaire 16 kHz mono PCM-s16le frames, `end`,
`interrupt` en `stop`. Eén verwerking per verbinding voorkomt overlappende
modeltaken; een volgende start tijdens verwerking meldt `busy`. Audio-uitvoer
is base64 met MIME en servergeneratie. De toekomstige browser moet bij
`clear_audio` onmiddellijk de speakerbuffer wissen en oude generaties weigeren.
Deze transportmelding bewijst geen hoorbare stop of volledige duplexervaring.

Finale herkenning gebruikt gewone ConversationTurn-admission, respond en
hetzelfde after_response-geheugenpad. Onderbreken onderdrukt spraakuitvoer;
alleen expliciet stoppen roept de bestaande duurzame taakstop aan. Een lopende
synchrone model/tooltaak wordt bij disconnect afgewacht inclusief after_response;
threadcancellation geldt niet als bewijs dat externe effecten gestopt zijn.

Gerichte API-, contract-, playback- en mediatests: 67 geslaagd. Onder andere
weigering vóór media, grantverval na STT, server-ID's, gewone geheugenafhandeling,
onderbreking tijdens model/TTS en geblokkeerde antwoordverzending zijn getoetst.
De bredere API-run heeft een open cockpit-evidencehashcontrole op gewijzigde
bronbestanden. Geen live-, release- of gezinsacceptatieclaim.


### Browsercheckpoint — 8 september 2026

De bestaande eigenaar-gesprekpagina heeft nu microfoon aan/uit en onderbreken.
De browser gebruikt AudioWorklet met 16 kHz mono PCM, korte voorbuffer en
spraak/stiltedetectie. De microfoon blijft tijdens afspelen actief. Vroege
STT-afronding ruimt de lopende capture op; een nieuwe opgenomen zin tijdens
modelwerk blijft tijdelijk lokaal tot dezelfde sessie weer vrij is. Maximaal
vier audioframes wachten op bevestiging door de server na consumptie. Een
complete audiofile wordt begrensd samengesteld vóór decode/afspelen; dit is
nog geen incrementele spraaksynthese of streaming-playback.

Bij onderbreken stoppen de lokale speaker en gebufferde audio onmiddellijk;
ook late decode-uitkomsten en fouten uit oudere sessies worden genegeerd.
Sluiten, een verborgen pagina en verbindingsverlies stoppen microfoontracks.
Een microfoontoestemming die pas na sluiten terugkomt opent geen nieuwe opname.

Vijftien JavaScriptproeven, inclusief een ingeschakelde echte Chromium1228 /
Uvicorn-proef, zijn geslaagd zonder skips; daarnaast 67 Python-stemtests en
Ruff. De browserproef gebruikt een synthetisch WAV-bestand als fake microfoon,
werkelijke AudioWorklet/PCM en Basic-auth op WebSocket, maar testfixtures voor
STT, modelantwoord en TTS. WAV-decodering en afspeelplanning, onderbreken en
trackcleanup zijn werkelijk in Chromium uitgevoerd. Dit bewijst nog geen
hoorbare gezinsinteractie, HA/modelintegratie, p95-stoplatentie of live gebruik.
De exacte browseropdracht staat op VB-001; zonder de Playwright-omgeving wordt
die proef overgeslagen en mag zij niet als releasebewijs meetellen.

Onafhankelijke reviewer co006_review gaf begrensde bron-/browsercheckpoint-GO.
Bootstrap met echte eigenaar, actuele identiteit, policy en providerconsent,
volledige gebruikersroute, fysieke eindproeven en veilige release blijven open.

API-keuzes gecontroleerd tegen MDN: AudioWorkletNode,
https://developer.mozilla.org/en-US/docs/Web/API/AudioWorkletNode ; volledige
file bij decodeAudioData,
https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/decodeAudioData ;
getUserMedia, https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia .


### Runtimebinding — 8 september 2026

`EVA_DASHBOARD_VOICE_POLICY_FILE` is optioneel. Zonder bestand blijft voice uit;
een ingesteld maar ongeldig bestand weigert startup. Het private bestand draagt
`DashboardVoicePolicy/v1`: een nieuwe, echte mediaspecifieke eigenaarpolicy,
geen bewering dat de runtime een globale PolicyPack heeft geladen. Het bindt
beide bestaande scopes afzonderlijk: de runtimegespreksscope heeft Identifier-
waarden, de beheeromgeving heeft UUID's. Gesproken beurten volgen precies
`dashboard_turn`, inclusief `dashboard:UUID`-bericht-ID voor de vergeetroute.
Er worden geen bestaande scopegegevens of toolbevoegdheden aangepast.

De geconfigureerde ondertekende identiteit/trustbinding is gedeeld met de
cockpitloader. Verwijdering, wijziging, permissiedrift, intrekking of verloop
van de voicepolicy maakt bestaande grants ongeldig. Nieuwe sessies duren
hoogstens dertig minuten en nooit langer dan de policy. De policy geeft
uitsluitend toegang tot media; persoonsgeheugen en handelingen lopen via
bestaande gesprekadmission en bevoegdheden.

STT controleert geselecteerde pipeline, taal en engine vóór PCM-overdracht.
TTS gebruikt de bestaande `/api/tts_get_url`-route met expliciet `engine_id`,
`language` en `cache:false`; de veranderbare pipeline kiest daardoor niet de
TTS-provider. Alleen een begrensd `/api/tts_proxy/`-pad op dezelfde HA-origin
wordt opgehaald. Cache:false vraagt geen HA-schijfcache; het bewijst niet dat
alle interne/externe buffers ogenblikkelijk verdwijnen. Providerretentie blijft
een eigenaarassertie met bewijsreferentie, geen autonoom geverifieerde garantie.
Bron voor deze route: Home Assistant tts/__init__.py,
https://github.com/home-assistant/core/blob/dev/homeassistant/components/tts/__init__.py .
De daadwerkelijke Cloud-engines zijn in de installatie gecontroleerd; relevante
providerverklaring: https://www.nabucasa.com/privacy/ .

218 gerichte bootstrap-/stemtests en Ruff slagen. Een aparte proef met de echte
runtimeconfiguratie heeft de geconfigureerde ondertekende identiteit, eigenaar,
beide scopes en intrekking na verwijderen van een tijdelijk policybestand
bewezen. Dat bestand is verwijderd; geen live voice-instelling is geactiveerd.
Een echte HA-proef met directe TTS en enginegebonden STT herkende de vaste
synthetische testzin correct. Geen menselijke opname, speaker, modelcall of
geïntegreerde browser/modelproef wordt daarmee geclaimd. Bronnen, reproduceerbare
runners en receipts zijn gebonden in audit.vb001RuntimeIntegration; onafhankelijke
co006_review gaf beperkte GO. De gekopieerde-data-kandidaat, fysieke eindproeven
en volledige veilige release blijven open.
