> Current1October2026: Woonkamer has the reviewed direct native input-task
> candidate (`91b723d3…` OTA; device compilation14:50:20). The compiled entry
> frame shrank1312→32bytes without increasing its4096-byte task stack.
> The previous physical trial failed with wake detection followed by connection
> reset/restart. Greeting and direct-speech recovery remain unconfirmed; the
> precise crashing task is unknown. Overkapping remains on its ordinary route.
> See `../../evidence/product/c3-native-input-task-20261001.json`.

> Current status, 23 September: Woonkamer candidate rolled back after failure
> following wakeword. Recovery firmware is installed; owner confirms ordinary
> speech works again. Continuous-session installation below is historical and
> must not be repeated before the session-start fault is resolved.

# SP-03 installatiegrens: woonkamer

> Actueel 23 september: de woonkamerfirmware is via ESPHome OTA geïnstalleerd.
> USB is geen voorafgaande eis voor deze draadloze route. De eerdere systemd-
> en USB-installatiestappen hieronder zijn historisch; de Docker-spraakdienst
> en beperkte firewallregel draaien. Fysieke gespreksacceptatie staat nog open.
> Zie `../../evidence/product/voice-pe-woonkamer-ota-installed-20260923.json`.


Dit pakket gebruikt de bestaande Home Assistant Cloud STT/TTS en de bestaande
Eva2-huishoudbinding. Het vervangt op de woonkamer-speaker de gespreksafhandeling
met de voorbereide native verbinding. SP-01 en SP-02 zijn geaccepteerd;
SP-03 wordt pas geaccepteerd na de fysieke onderbreek-/vervolgproef.

## Vastgelegde onderdelen

- Actieve core: bron `32fb12d75f3f78410d1c1e591bc12a93d618f472`, image
  `sha256:cb31295d926e85e7d0328abe611f1cfff522324668991c837270f65e8d4dcfec`.
- Afgesloten servicebundel: `/opt/eva2/native-speaker/releases/eb6dfff16d04c01536746d040de9859e35951d59`.
- Privaat installatiepakket: `/home/administrator/project/.eva2-artifacts/woonkamer-speaker-install-hnd0_fwq`.
- Endpoint: `wss://10.0.20.46:44715/eva-speaker/v1`.
- Alleen Woonkamer: device `9e877ca00f57b1b75e0c2e3318abc704`, huidig IP `10.0.20.202`.
- `service/config.json` bevat de bestaande HA-/Eva-tokens en uitsluitend de hash
  van de nieuwe speakercredential. Die credential en de publieke CA staan in de
  private firmwareconfiguratie. Geen van deze private bestanden publiceren.
- De leaf-servercertificaatketen is een jaar geldig. Vervang vóór vervaldatum de
  leaf met dezelfde private CA; de CA-sleutel blijft buiten de runtimecredentials.

## Installatievolgorde

1. Identificeer de woonkamer-speaker via USB. Lees zijn huidige volledige flash
   uit en controleer de backup voordat firmware wordt vervangen. De bijgeleverde
   `recovery/`-images zijn een herbouw van de oude configuratie; zij zijn geen
   bewezen hardwareterugrol en vervangen die fysieke toegang niet.
2. Controleer `preparation.json`, de definitieve buildreceipt en de reviews.
   Vergelijk de aanwezige image-, bundel-, config- en firmwarehashes. Nieuwe
   code of een verlopen certificaat vereist de bijbehorende beperkte hercontrole.
3. Plaats de bestaande service-unit en een `current`-verwijzing naar bovenstaande
   bundel. Kopieer alleen `config.json`, `tls.crt` en `tls.key` naar een root-private
   `/etc/native-speaker/`. De launcher kiest de geladen actieve core en
   vervangt de credential- en modelpaden voor gebruik. Voeg de bestaande
   `Wants=eva2-native-speaker.service`-relatie aan `eva.service` toe.
4. Pas `allow-native-speaker-wsl.ps1` toe vanuit een verhoogde Windows PowerShell.
   De regel laat alleen TCP van `10.0.20.202` naar `10.0.20.46:44715` toe. Controleer
   bij gewijzigd DHCP-adres eerst opnieuw de fysieke apparaatbinding.
5. Start de service, controleer de concrete TLS-listener en installeer uitsluitend
   de vastgelegde woonkamerfirmware. Verander de Overkapping niet. Bevestig de
   firmwareversie en de geauthenticeerde speakerverbinding voordat de proef start.
6. SP-03: laat Eva spreken, onderbreek met een gewone nieuwe vraag en controleer
   dat de oude audio stopt en de nieuwe vraag zonder nieuw wakeword wordt verwerkt.
   Controleer ook Stop en fysieke mute. Jaap beoordeelt hoorbare kwaliteit en gesprek.

## Terugweg

Stop de native-speakerservice en verwijder haar Wants-relatie. Herstel via de
geverifieerde USB-route de vastgelegde oude speakerflash, controleer de bestaande
HA-route en verwijder uitsluitend de eigen firewallregel met `-Remove`.
De actieve core ondersteunt de bestaande HA-route; een speakerterugrol vereist
geen herbouw of terugrol van de afgesloten core-uitrol.

Dit document is een installatieplan. Alleen een uitvoeringsreceipt en de echte
readback bewijzen dat installatie of terugrol heeft plaatsgevonden.
# Current development installation — 1 October 2026

The Woonkamer Voice PE now runs the retained continuous PCM/LiveKit candidate
with one narrow pre-grant microphone-buffer correction. The ordinary HA
wakeword/relisten overlay remains on Overkapping. Do not infer that both devices
use the continuous route from an earlier C4 installation record.

After the first `Hey Eva`, the intended behavior is direct speech during thinking
and playback without another wakeword. Persistent capture and interruption are
connected in the existing candidate; the physical conversation outcome remains
unconfirmed. The automatic HTTP transport phrase is no longer synthesized by
either the HA conversation entity or the shared media agent. Actual Eva answers
and acknowledgements of admitted work remain available.

The ARMED microphone state previously overflowed while waiting for CAPTURE.
Only that state now retains a rolling 16 KiB pre-roll. STARTING/ACTIVE overflow,
capture ordering, mute, stop and reconnect fences remain strict. Under a long
admission delay, only the latest approximately 0.512 seconds survive; this fix
does not guarantee the entire initial utterance or establish the historical
device fault's cause.

One reviewed Woonkamer OTA used binary SHA-256
`80c03833bb3108ad2651aa27cf6de82cfa81289def7f23d4d4df73ff0c6a8c14`.
Encrypted device identity after boot and the native server's actual peer
connection were checked. No automatic rollback was performed. Previous firmware,
module bytes and immutable images remain privately retained.

See [installation evidence](../../evidence/product/c3-native-preroll-20261001.json)
and [media evidence](../../evidence/product/c3-media-transport-status-20261001.json).
The sections below describe the historical preparation and rejected candidate.
