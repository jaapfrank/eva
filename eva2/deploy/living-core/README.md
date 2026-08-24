# Living Core B deployment

Deze map gebruikt uitsluitend expliciete configuratie en file-secrets. Neem
nooit environmentvariabelen over uit een draaiende container en gebruik geen
`docker inspect`-dump als invoer voor een nieuwe B-core.

## Home Assistant-secretgrens

Voeg `home-assistant.compose.yml` alleen toe als Home Assistant voor deze
installatie is ingeschakeld. De overlay zet in de container uitsluitend
`EVA_HOME_ASSISTANT_TOKEN_FILE=/run/secrets/home_assistant_token`; de inline
variabele `EVA_HOME_ASSISTANT_TOKEN` hoort niet in een env-bestand.

De runtime stopt vóór connectoropbouw wanneer het tokenbestand niet aan **alle**
voorwaarden voldoet:

- absoluut pad;
- regulier bestand en geen symlink;
- maximaal 64 KiB en na UTF-8-decoding niet leeg;
- exact eigendom van de effectieve runtime-UID (`65532` in de core-container);
- geen enkele group- of other-permissie, bijvoorbeeld mode `0600` of `0400`.

Provision het bestand buiten de repository en image via de gebruikte secret
manager. Controleer alleen metadata; print of log nooit de inhoud. De hostroute
staat expliciet in het B-env-bestand als
`EVA_HOME_ASSISTANT_TOKEN_FILE_HOST=/run/eva2/secrets/home-assistant-token`.
Een inline token én file-route tegelijk wordt eveneens fail-closed geweigerd.

## Alleen-lezen B-preflight; nog geen recreate

De huidige B-container is niet aantoonbaar uit deze Composebestanden beheerd.
Bovendien bevatten `stack.compose.yml` plus `home-assistant.compose.yml` niet de
volledige actuele capabilityconfiguratie van durable goals en fan-out. Daarom
bestaat hier bewust **geen** toepasbare recreate-opdracht: die zou stilzwijgend
functionaliteit kunnen verliezen.

`preflight-core-b.sh` maakt één immutable byte-snapshot van één expliciet,
absoluut B-env-bestand. Het bestand zelf moet regulier, niet-symlink, maximaal
64 KiB, niet leeg, owner-readable, exact van de uitvoerende operator en geheel
gesloten voor group/other zijn. De wrapper wist de shellomgeving met `env -i`,
bewaart ook `HOME` niet en accepteert geen `--apply`.

De snapshot eist exact `/usr/bin/docker`: regulier, geen symlink, root-owned,
owner-executable en niet schrijfbaar voor group/other. Configdirectory, context
en daemonendpoint zijn eveneens expliciet gepind. Na de eenmalige contextread
gebruikt iedere containerread het geverifieerde endpoint via `--host`; een
gewijzigde contextconfig kan daardoor niet opnieuw autoriteit kiezen. De
preflight gebruikt alleen read-only context- en labelreads,
controleert vervolgens dat het huidige B exact door een later gereviewd
`eva2-explicit-b-manifest-v1` is beheerd en weigert de huidige unmanaged B dus
fail-closed. De secretinhoud wordt daarbij niet gelezen.

```bash
./deploy/living-core/preflight-core-b.sh /absolute/path/to/eva2-b-runtime.env
```

Een volledige actuele manifestset en afzonderlijk gecontroleerde adoptie zijn
eerst vereist. Tot die tijd betekent een geslaagde bestands-/Dockerpreflight
niet dat deployment of recreate is toegestaan.

De losse secretreaders en de precieze nonclaims van deze vertical staan in
[`SECRET_BOUNDARY_INVENTORY.md`](SECRET_BOUNDARY_INVENTORY.md).
