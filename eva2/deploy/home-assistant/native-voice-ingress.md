# Native household voice ingress

The HA integration uses `http://10.0.20.46:44714`. The gateway accepts only the
configured Home Assistant address and the two native voice POST endpoints. It
forwards the voice credential to Eva2; it does not have or mint a credential.
Other headers, including cookies and caller identity, are not forwarded. There
are no retries, response caches, access logs or transcript temporary files.
Client disconnect closes the upstream connection. This is transport behavior,
not proof of acoustic interruption on the speakers.

The core ports remain on loopback. An unprivileged `eva2-voice` system account
runs the distro nginx binary and the renderer installed under
`/opt/eva2/voice-ingress`. This host uses Ubuntu's `nginx` package
`1.24.0-2ubuntu7.17`, extracted and verified against APT's SHA256 metadata; the
general nginx service is not installed. Future nginx security updates must
replace this retained binary and rerun the ingress verification. The exact
binary, source, service, address configuration and drop-in hashes belong in the
deployment receipt; the core-image release receipt does not cover this service.

`eva2-voice-ingress.service` has `BindsTo`, `PartOf` and `After` relationships to
`eva.service`. `/etc/systemd/system/eva.service.d/35-eva2-voice-ingress.conf`
contains `[Unit]` and `Wants=eva2-voice-ingress.service`. The existing quiescent
cutover stops Eva ingress, which stops voice ingress before data copying. When
Eva ingress starts, the voice renderer reads its loaded `EVA2_CANARY_URL` and
accepts only the retained slot3/slot4 URLs. Invalid routing fails startup closed.
Do not reload a changed upstream in place during data copying. The same
stop/copy/start operation applies to rollback, preserving intervening writes.

Configuration in `/etc/eva2/voice-ingress.env` contains only these addresses:

```
EVA_VOICE_LISTEN_ADDRESS=10.0.20.46
EVA_VOICE_HA_ADDRESS=10.0.20.74
```

On this Windows host, WSL's Hyper-V firewall also needs the narrow rule in
`allow-native-voice-wsl.ps1`. Execute it from an administrator PowerShell. It
allows only HA `10.0.20.74` to local `10.0.20.46`, TCP44714. It does not change
the default firewall policy. `-Remove` removes only that matching rule.
An allowed firewall rule still requires an independent request from HA to prove
network reachability; successful local checks do not prove this.

Before assigning the existing household pipeline: verify the gateway from HA,
activate the independently reviewed native core candidate through quiescent
A/B cutover, configure the HA entry with the private household credential, and
test a native conversation and its cancellation. Preserve the previous pipeline
and config entry backup. Never count this network setup as hardware acceptance.

For a local transport test without house effects:

```
sudo unshare --net python3 tools/verify_household_voice_ingress.py --nginx /opt/eva2/voice-ingress/nginx
```

This runs actual nginx and HTTP in a separate network namespace and tests the
route boundary, source IP, credential forwarding, disconnect, next question and
absence of transcript temporary files. Lifecycle verification separately uses
temporary systemd parent/child units and the renderer's `--source-service`
option; production uses the default `eva.service`.

The native stop path is HA pipeline cancellation through aioesphomeapi's
`VoiceAssistantRequest(start=False)`. The candidate's optional event entity is
not required for that native cancellation path. Earlier notes in the main voice
README describe intermediate development states; current source-bound receipts
and the workboard distinguish what is installed, tested and actually live.


## Achtergrondwerk tijdens reserve-uitrol

De managed-B basisconfiguratie start nieuwe runtimes met
`EVA_BACKGROUND_WORK_ENABLED=false`. De quiescent-cutoverprocedure dwingt
`false` af tijdens staging en `true` pas nadat de gekozen route klaar is en de
ingress stilstaat. Dit geldt ook zonder Telegram- of andere connectorcredentials.
De runtime schakelt daarmee de sensory-ingress en cold-pathworkers uit; dit
maakt de database niet algemeen read-only en schakelt geen afzonderlijke
Hindsight-processen of expliciet aangeroepen HTTP-handelingen uit.

De renderer controleert de fasewaarde naast de toegestane connectorverschillen.
Een tegengestelde waarde in het envbestand wint niet van de expliciet gekozen
procesomgeving. Na recreatie controleert de helper de containerinstelling.
Rechtstreeks starten met alleen de basis-Compose is staging. Proeven die
achtergrondwerk nodig hebben moeten een expliciete, daarvoor toegestane
activeringsfase gebruiken; een gewone kopie- of readinessproef doet dat niet.

Oudere herstelimages herkennen deze runtime-instelling niet. De aanwezige
images `7b75cfe1` en `86ec4e35` zijn daarop afzonderlijk gecontroleerd. Hun
container-env is daarom geen bewijs dat hun achtergrondthreads stilstaan.
De terugrol behoudt de bestaande regel: stop de kandidaat, kopieer ook eventuele
nieuwe gegevens terug en herstel vervolgens de bronroute. Fysieke isolatie van
de nieuwe kandidaat moet nog met worker- en gegevensreadbacks worden bewezen.
