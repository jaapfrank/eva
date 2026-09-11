# Bestaand Chrome-tabblad: bewaakte Eva-koppeling

Gebouwd en technisch beproefd op 11 september 2026. **Nog niet afgerond:**
de echte gekozen Chrome-tabdeling en Jaaps beoordeling zijn niet bewezen.
Registratie, ontdekking van zeven tools en één geweigerde paginawaarneming zonder
tabdeling zijn inmiddels via de normale Eva-gespreksroute bewezen. Eva stopte
zonder herhaling of nieuwe verbinding; de permanente registratie is teruggelezen. De oude VB-003-containerproef telt hiervoor
niet als bewijs. De eerdere losse extensieclient is gestopt; geen bestaande
tabdeling wordt stilzwijgend hergebruikt.

`worker.cjs` hergebruikt Microsoft Playwright MCP **0.0.80**, met de exacte
lockfile. De interne `playwright-core/lib/coreBundle`-API is versiegebonden;
een upgrade vereist opnieuw testen. Geen patch van upstream broncode.
De officiële extensie kiest een bestaand Chrome-tabblad met **Allow & select**.
De worker start die dialoog uitsluitend na een lokale eigenaarsklik. Er wordt
geen extensietoken gebruikt, profiel gekopieerd of CDP-poort gepubliceerd.

## Bediening en uitvoering

De lokale pagina op `http://localhost:44718` toont steeds de toestand. Jaap
kijkt rechtstreeks in zijn gedeelde Chrome-tabblad, ook tijdens Eva-bediening.
De pagina vereist geen aanmelding. Mutaties vereisen exact dezelfde lokale
Origin en een loopbackverbinding; het interne MCP-endpoint is apart afgeschermd.

`ChromeGate` accepteert één actie tegelijk, zonder wachtrij. Iedere mutatie
vereist de eenmalige waarnemingscode uit een recente `chrome_snapshot`.
Een eigenaarsactie maakt alle oude codes ongeldig en sluit de Windows-worker.
Pas na bevestigd proces-einde verschijnt eigenaarbediening. Als stoppen niet
bevestigd is blijft `recovery_required` zichtbaar en blijven acties geweigerd.
Teruggeven maakt een nieuwe extensieverbinding: nieuwe selectie en nieuwe
paginawaarneming zijn vereist. Disconnect, gesloten tabblad, extra tabblad of
verloren transport sluit de worker; een toolaanroep kan nooit herverbinden.

Zeven bestaande tools zijn toegelaten: snapshot, gewone klik, navigatie in
hetzelfde tabblad, typen, hover, selecteren en eenvoudige toetsen. Geen nieuwe
tabbladen, willekeurige code, browser-shortcuts, bestandsexport of profielbeheer.
Een door een website zelf geopende extra tab beëindigt de bediening. Reeds
voltooide klikken of website-effecten worden bij stoppen niet teruggedraaid;
een onderbroken actie heeft mogelijk een gedeeltelijk effect en mag niet
automatisch worden herhaald.

## Installatie en verbinding

`npm ci --ignore-scripts` installeert de vastgezette afhankelijkheden.
De WSL-gebruikersdienst `eva-chrome-bridge.service` draait `server.mjs` met een
privéconfiguratie buiten de repository. Deze bevat Windows-node en workerpaden,
`ownerPort`, `mcpSocket`, `packagePath` en `tokenFile`. WSL-start gebruikt
`WSL_INTEROP=/run/WSL/1_interop`; als Windows-interoperabiliteit ontbreekt kan
de worker niet starten en blijft de route gesloten. Geen automatische
herstart of herdeling na een storing. Bron en npm-afhankelijkheden staan in
de gedeelde repository zodat zowel WSL als Windows dezelfde bestanden lezen.

De host bindt MCP uitsluitend aan een Unix-socket. `proxy.py` draait als
onbevoorrechte, alleen-lezen container zonder gepubliceerde poort of Docker-
toegang op het bestaande interne connectornetwerk, `172.30.0.10:45164`.
Alleen gatewaybron `172.30.0.2` wordt doorgelaten. De proxy gebruikt een eigen
automatisch gegenereerd credentialbestand voor de Unix-socket; dit verschijnt
niet in de UI, logs, broncode of taakgegevens.

Het exact afgebakende endpoint is `http://172.30.0.10:45164/mcp`.
De voorgestelde gateway-pinning en eigenaar-endpointlijst staan afzonderlijk
in `/home/administrator/.local/share/eva-chrome-bridge/integration-plan.json`.
Gateway-pinning en endpointbestand zijn op 11 september **geactiveerd en teruggelezen**. De nieuwe coreconfiguratie en de normale Eva-registratie zijn daarna gecontroleerd; zie `../../evidence/product/chrome-normal-route-2026-09-11.json`. Bestaande owner-, tenant-, installatie-
en mandaatbindingen blijven identiek. De eigenaarlijst wordt bij corestart
ingelezen; activatie moet samenvallen met de gecoördineerde cutover. Geen
verruiming van managementadressen, persoonsrechten of geheugenscope.

## Verificatie en terugweg

`node --test gate.test.mjs` test geen impliciete verbinding, eenmalige
waarneming, gelijktijdige/lopende acties, overname, oud werk na hervatten,
verbindingsverlies en een niet-bevestigde stop.

Op Windows: zet `EVA_CHROME_PACKAGE` op het absolute `package.json` en voer
`node --test browser.test.mjs` uit. Dit start een afzonderlijke headless Chrome
met alleen een lokale proefpagina. De gewone klik wordt waargenomen; een
lopende klik die op een disabled knop wacht wordt afgebroken en vuurt later
niet alsnog af. De persoonlijke extensiesessie wordt niet aangeraakt.

Stop de bridge met `systemctl --user stop eva-chrome-bridge.service`; bevestig
dat de worker weg is. Stop vervolgens alleen container `eva2-shared-chrome`.
Na toekomstige activatie: herstel de exacte vorige endpointlijst en gateway-
override tijdens de gecoördineerde core-herstart. Bewaar alle databases,
Chrome-profielen, keuzegegevens en GPU-instellingen. De oude containerbrowser
en haar bestaande endpoint blijven als afzonderlijke route behouden.
