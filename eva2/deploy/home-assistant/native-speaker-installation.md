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
   `/etc/eva2/native-speaker/`. De launcher kiest de geladen actieve core en
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
