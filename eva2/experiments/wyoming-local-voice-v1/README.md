# V02 — lokale Wyoming-spraakproef

Dit is een kleine, echte keten met onderhouden open-sourcecomponenten:

```text
vaste Nederlandse testzin -> Piper -> Faster-Whisper -> Eva VoiceTurn/v1 -> Piper
```

De controller gebruikt de officiële `wyoming`-client in plaats van een eigen
spraakprotocol. Iedere Piper-PCM-chunk gaat direct naar Faster-Whisper, zonder
dat de controller het volledige invoeraudiobestand opbouwt. De sidecars blijven
afzonderlijke processen; Eva krijgt alleen de eindtekst via
`admit_final_voice_turn`. Ruwe audio, gedeeltelijke transcriptie en de testzin
worden niet in de receipt bewaard.

## Exacte inputs

| Onderdeel | Vaste input |
|---|---|
| Wyoming-client | `wyoming==1.10.0` |
| Piper upstream | `OHF-Voice/wyoming-piper` `v2.4.2` |
| Piper amd64-image | `rhasspy/wyoming-piper@sha256:6b837615b543e38587b735b72abad2dfd7ead50fd10ec1a4d5815241e5539377` |
| Nederlandse stem | `nl_NL-pim-medium` |
| Faster-Whisper upstream | `OHF-Voice/wyoming-faster-whisper` `v3.6.0` |
| Whisper amd64-image | `rhasspy/wyoming-whisper@sha256:731299c56df053dd67978127a44e417c83f0d900fdaad1bd7ab8aad82f84106d` |
| STT-model | `base-int8`, taal `nl` |

De eerste modeldownload krijgt een nieuwe, wegwerp-Dockervolume. Daarna bewijst
`offline-isolation-receipt.json` één echte herstart met die caches: Piper,
Faster-Whisper en de controller draaien in een Docker-netwerk met `--internal`,
zonder gateway of hostpoort. De twee modelvolumes en de roots van de sidecars
zijn alleen-lezen; de controller heeft geen hostmount, alle Linux-capabilities
verwijderd en alleen een tijdelijke receiptvolume. Dit bewijs geldt alleen voor
de gepinde images, de gekozen Nederlandse stem en `base-int8`, niet voor iedere
toekomstige modelupgrade. De proef gebruikt nooit Eva B, een productiedatabase,
secrets of hostaudio.

Een groene receipt betekent uitsluitend: een synthetische lokale stem leverde
PCM, lokale STT leverde de verzegelde korte eindzin, Eva accepteerde die via
haar V01-grens, en Piper leverde weer PCM voor een vaste antwoordzin. Zij
bewijst geen menselijk gesprek, microfoon/speaker, modelantwoord, toolactie,
volledige duplex, onderbreking, latency of herkenningskwaliteit.

`verify_receipt.py` hercontroleert zonder Docker of netwerk de exacte
controllerlock, runnerhash, sidecar-pins, eindtekstbinding en de private
`VoiceTurn/v1`-grens. Audiohashes worden alleen op vorm gecontroleerd, omdat
Piper-audio geen stabiele golden fixture is.

`Dockerfile.controller` is uitsluitend de wegwerpcontroller voor de volgende
proef: Piper, Whisper en de controller draaien dan op één Docker-netwerk met
`--internal`, zonder hostpoort, hostmount of internetroute. De controllerimage
heeft een vaste `python:3.12-slim` amd64-basisdigest en installeert uitsluitend
de hashgebonden experimentlock. Zij is geen Eva-productimage.

`capture_offline_isolation.py` weigert bij de echte proef een niet-intern
netwerk, gepubliceerde poorten, hostmounts, schrijvende modelvolumes of een
verzwakte controller. `verify_offline_isolation.py` hercontroleert daarna de
committed offline-receipt zonder Docker of netwerk.
