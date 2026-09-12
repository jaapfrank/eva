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

De lokale pagina op `http://127.0.0.1:44718` toont steeds de toestand. Jaap
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
Alleen het actuele adres dat Docker-DNS op dit netwerk voor `eva2-mcp-gateway` retourneert wordt doorgelaten. De resolutie moet precies één privaat adres opleveren; ontbrekende of dubbelzinnige resolutie weigert toegang. De proxy gebruikt een eigen
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

Herstel 11 september na WSL-herstart: de gebruikersdienst start nu bij de gebruikerssessie en de eigen interne proxy heeft `unless-stopped`. De bridge begint altijd zonder tabdeling. Een achtergebleven Unix-socket wordt alleen verwijderd na een geweigerde verbinding, controle van type/eigenaar en ongewijzigde inode; een levende listener blijft behouden. De IPv4-ingang vermijdt de lokaal vastlopende localhost/IPv6-route.

## Gewone Eva-route — 12 september

De volledige connectorroute is beproefd met een eigen Windows Chrome met leeg
profiel: paneel openen/sluiten, Geheugen in het projectdashboard openen/sluiten,
een lopende klik onderbreken, gelijktijdige en latere acties blokkeren, na
teruggeven een gewijzigde pagina opnieuw lezen en toegang na intrekken weigeren.
De operationele Eva-events en afzonderlijke tellercontrole staan in
`../../evidence/product/chrome-isolated-route-2026-09-12.json`.
De precieze oorzaak van de eerdere persoonlijke extensieklikweigering is niet
vastgesteld; die fout trad in deze geïsoleerde proeven niet opnieuw op.

`proxy.py` behoudt MCP-tekstblokken en biedt ze ook als één gestructureerd
tekstresultaat aan. Afwijzingen loggen alleen een begrensde redencategorie,
geen paginainhoud. De actiewachttijd is 15 seconden, binnen de huidige
20 seconden van de gewone Eva-aanroep. Overname beëindigt wachten direct.

`isolated-harness.mjs PRIVATE_CONFIG_PATH` start uitsluitend een testbrowser,
een lokale pagina op 44720 en testbediening op 44719. Met `EVA_TEST_WINDOWS=1`
gebruikt hij Windows-node en een eigen headless Chrome via `browser-fixture.cjs`.
Voor Linux kan `EVA_CHROME_TEST_EXECUTABLE` een geïnstalleerde testbrowser aanwijzen.
De test gebruikt `test-mcp.sock` en verandert de productieproxyroute niet zelf.
De gatewayproef van 12 september gebruikte tijdelijk die testsocket en herstelde
vervolgens `/bridge/mcp.sock`; de persoonlijke bridge bleef disconnected.

## Eén resterende eigenaarproef

1. Open `http://127.0.0.1:44718/proef` in Google Chrome. Deel precies dat tabblad
   via de gewone Chrome-bediening en Allow & select. Meld Gedeeld.
2. Laat Eva Toon proeftekst en Verberg proeftekst bedienen, met telkens een
   verse paginawaarneming. Controleer het zichtbare openen en sluiten.
3. Laat Eva op Tel één proefklik wachten. Klik tijdens ACTIE BEZIG op Neem over.
   Wacht op Jij hebt de bediening en klik dan Geef proefknop vrij. De teller
   moet 0 blijven. Bewaar het bewijs dat de klik werkelijk liep bij overname.
4. Geef expliciet terug, selecteer hetzelfde tabblad opnieuw en laat Eva de
   vrijgegeven knop met teller 0 opnieuw lezen. Trek daarna de verbinding in
   en controleer één geweigerde leesaanroep via Eva.

De reeds bevestigde persoonlijke deelproeven van 11 september blijven bewaard.
Deze laatste ronde en Jaaps oordeel staan nog open; automatische proeven geven
geen eigenaaracceptatie.
## Proxyherstel na WSL-herstart — 12 september

De gateway kreeg na de herstart adres 172.30.0.4, terwijl de oude proxy uitsluitend
172.30.0.2 toeliet. De exacte service-DNS-controle voorkomt deze adresafhankelijkheid
zonder toegang tot het hele subnet toe te staan. De eigen gezondheidscontrole
gebruikt `http://127.0.0.1:45164/healthz`, controleert de hostsocket met tools/list
en vraagt geen tabdeling of browseractie. De oude geërfde corecheck op poort8080
was ongeschikt voor deze proxy.

`proxy.compose.yml` legt image, bestaande netwerknaam en proxy-IP, alleen-lezen
mounts, beperkte rechten, resourcegrenzen, herstartbeleid en de juiste healthcheck
vast. Stel `EVA_CHROME_PRIVATE_ROOT` in op de bestaande private bridgemap; deze
configuratie geldt uitsluitend voor de eigen proxy. Voer geen brede stackrecreate
uit. Het herstelbewijs staat in
`../../evidence/product/chrome-proxy-recovery-2026-09-12.json`.

Gerichte controles: `python test_proxy_peer.py`, `python test_proxy_health.py`
en `python test_proxy_output.py`. Ze controleren adreswisseling, weigering van
andere bronnen, onbeschikbare/ambigue DNS, socketuitval en behoud van MCP-output.
## Lopende acties bij transportverlies en procesherstart

De proxy bewaakt tijdens een aanvraag of de caller nog verbonden en volgens de
exacte gateway-DNS-controle toegestaan is. Verlies sluit de bijbehorende Unix-
verbinding. De host stopt dan uitsluitend de actie die bij die aanvraag en epoch
hoort. Een afgewezen gelijktijdige aanvraag kan de lopende actie niet intrekken.
Dit voorkomt dat een wachtende klik alsnog afgaat nadat de oproeper is verdwenen.

`recovery.test.mjs` gebruikt een eigen loopbackproxy, tijdelijke Unix-socket,
testcredential, brugproces en leeg headless Chromium-profiel. De test onderbreekt
een werkelijk wachtende browserklik bij workerverlies, HTTP-verlies,
gatewayadreswisseling, proxycrash en brugcrash. Na ieder geval blijft de teller
ongewijzigd, zijn oude waarnemingen ongeldig en werkt een volgende klik pas na
expliciete nieuwe testtoestemming en een verse snapshot. Bij brugcrash controleert
de test ook dat het oude workerproces is geëindigd. Dit is geen persoonlijke
extensieproef of eigenaaracceptatie en herstart geen productieservices.

Uitvoeren op Linux met een bestaande testbrowser:
`EVA_CHROME_TEST_EXECUTABLE=/pad/naar/chromium node --test recovery.test.mjs`.
Bewijs: `../../evidence/product/chrome-inflight-recovery-2026-09-12.json`.

## Taakresultaat behouden bij intrekken en adresherstel

De herstelregressie voltooit nu eerst via een echte browserklik een synthetisch
taakrapport in een tijdelijk bestand van de testwebsite. Daarna wordt een tweede,
werkelijk wachtende klik expliciet ingetrokken. De geautoriseerde gateway verhuist
in de fixture van logisch adres .2 naar .4: de oude caller krijgt 403, de nieuwe
caller krijgt pas browserbediening na expliciete nieuwe testtoestemming.
Een verse browsersnapshot leest hetzelfde afgeronde rapport. De bestaande vijf
uitvalgevallen volgen met dit rapport aanwezig; inhoud en eenmalige uitvoering
blijven behouden, ook na de gecontroleerde brugherstart.

Dit test browserherstel met een duurzaam resultaat bij de testwebsite. Het bewijst
geen gezamenlijk herstel van de kern, helperresultaten, geheugen, voice of de
persoonlijke Chrome-extensie. Er is geen productiecode gewijzigd of herstart.
Bewijs: `../../evidence/product/chrome-result-recovery-chain-2026-09-12.json`.
