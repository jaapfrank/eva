# O1.2 — onafhankelijke bronreview adaptive connector, 3 oktober 2026

Beslissing: **SOURCE GO** voor de finale kandidaat vanaf `5c5c6c54f3f6fff0549fe36bfe20421b229f51c5`. Beoordeeld is de gewone optionele scopekeuze → planbewijs → verse installatie-identiteit en eigenaarmandaat → afzonderlijke credentials → één meegeleverde ContextForge-gateway → bestaande MCP-autonomyprovider. Ook de combinatie met de gekozen browser en overslaan zijn meegenomen. Dit is nog geen installatiereview of bewijs van een werkelijk aangesloten nieuwe dienst.

De eigenaar bevestigt expliciet publieke HTTPS-toegang en/of concrete private CIDRs. De bestaande private evidence-opslag bindt de scope aan het capabilityjournal en de planhash. Zonder bevestigde scope wordt geen mandaat afgeleid. De gegenereerde OwnerConnectorMandate/v1 gebruikt de verse tenant, installatie en principal; persoonlijke accounttoegang wordt niet afgeleid uit deze keuze. Bestaande private-owner/audiencecontrole, endpointcontrole, stop, budget en bewaarde effectresultaten blijven de uitvoeringsroute.

Zonder browser wordt één gateway met één nieuwe gewone bridge voorbereid. Met browser worden exact dezelfde gateway en bestaande gecontroleerde browsernetwerken gebruikt. Gatewaybeheercredentials blijven buiten de core; registratie krijgt uitsluitend gateways.create/gateways.read/tools.read en uitvoering tools.read/tools.execute. De registratiecredential wordt op de verwachte rechten en geweigerde tokenadministratie gecontroleerd. Publieke registratie gebruikt expliciet een exclusieve gateway voor deze eigenaar. De private netwerkuitzonderingen zijn de bevestigde CIDRs en, indien gekozen, de exacte beheerde browser-/32; algemene private toegang blijft uitgeschakeld. Bron- en runtimefiles worden privé voor de daadwerkelijke reader gematerialiseerd. Het mandaat is een directorymount, zodat de bestaande actuele autoriteitscontrole atomaire vervanging/verwijdering kan zien. Gedeeltelijke start blijft inspecteerbaar; geen automatische herhaling van resources of registratie.

Drie concrete reviewbevindingen zijn gesloten:

- Browserloze autonomy had ten onrechte een statische catalogus nodig. Alleen bij expliciete autonomie-invoer en afwezige catalogus wordt de statische provider nu overgeslagen; de apart gecomponeerde autonomyfactory blijft volledige configuratie vereisen. Geen dummytool of lege catalogus.
- De geconfigureerde browsercatalogus had een andere connector-ID én sessiebinding dan de directe browserroute. Een host-afgeleide alias gebruikt nu exact dezelfde bestaande ownerbinding/HMAC voor die catalogus. Normale dynamische connector-ID’s blijven gelijk; controle-overname, generatiecontrole en replay zonder herstart volgen dezelfde bestaande lifecycle.
- Een bevestigde connectoromgeving kon niet meer worden overgeslagen. De gewone knop en API invalideren nu uitsluitend deze ready scope vóór wanted:false. De echte API-regressie bevestigt ready→skipped; andere readyproviders behouden hun bestaande weigering. Dit is een installatiekeuze, geen claim dat een draaiende module daarmee wordt gestopt.

Het bestaande bewijs is gelezen, niet herhaald: **103 tests geslaagd in 4,83 s**, daarna **1 gerichte API-regressie geslaagd in 1,08 s**. Het oorspronkelijke log en de eerste freeze blijven historie. Onder `../.eva2-artifacts/installer-adaptive-source-20261003/`: oorspronkelijke pytestlog SHA256 `2ae2092bb8e4321e2004142ce27a6d5e8384ee9fc3dff2f3793d5716cbec4c77`, withdrawal-log `320418a142dbb681dcc95cf0e2eab02616c1e2706712b2586cfb6de4b81dfb5b`, finale 18-bestandsinventaris `9d1349e222e32ed875ec166dcb8c329459505c3f90cefa8a896a444ff6bed3b4`. Alle finale hashes zijn onafhankelijk met de werkelijke bestanden vergeleken.

Root heeft de vier runtimebestanden in image `sha256:f7a6d902905bf1042e9eeee4423b27e938dff26c615770778acf80517821f9ec` vanaf `759b638cc283465c10c9130f2aa45df44ee24c3f375d39f055a90f15ec046b02` verwerkt. Het gelezen geïsoleerde network-none hash-/importbewijs is positief, SHA256 `48002c467417323effac29721361424802e3a459cb0936e6343376f792b73a3d`. Dit bewijst de imagelevering en imports; nog geen core/gateway-compositie of externe dienstaanroep. De UI-correctie wijzigt deze vier runtimehashes niet.

Na aflevering blijven exacte package/image/mount/ownerbinding en een begrensde werkelijke registratie/discovery/uitvoering met onafhankelijke resultaatcontrole te bevestigen. De bestaande route ondersteunt MCP via Streamable HTTP; nieuwe accounttoegang en werkelijke ontvangst/antwoordgedrag van een nieuw kanaal zijn hiermee niet automatisch geleverd. O1.2 wordt door bron-GO niet afgerond. Deze reviewer wijzigde alleen dit document en deed geen nieuwe tests, model-, provider- of netwerkproeven, service-starts of installaties.

## Exact beoordeelde bestanden

Paden relatief aan `eva2/`; finale bytes na de ready→skip-correctie.

| Bestand | SHA256 |
| --- | --- |
| src/eva/adapters/api/installer_capability_api.py | `19b5a0fd0aeec4b3c46e23dd79500ecd79c808cfdfafe9c6e474eb4d2389e7f0` |
| src/eva/adapters/api/installer_provider_api.py | `b4daadabe59709e118982167d42a918431e1287dbdf37e60a5e66cd012e76eb4` |
| src/eva/adapters/connectors/browser_autostart.py | `5f7f3f436038f844c4aeeb8dbe4af62885d5c1afab34dfe60967c378c0cd0e86` |
| src/eva/operations/installer_capability_workflow.py | `8382460c31611ddaa9d7cbe24afebdbd23116f55704cb4ba0b8a2e1f104e60ae` |
| src/eva/operations/installer_connector_selection.py | `31523ebbf9e7142a9b9e1e56103ef85e85b761fed5ebff7879255ecfe2723cd3` |
| src/eva/providers/mcp_autonomy.py | `a44543c27c89d54df1cc2a1c4a8cb25f02f7f784885c16c62b8801c7a9ead7a6` |
| src/eva/providers/mcp_discovery.py | `e89dbc2a60121b6d74698cc3b6cdcd2d10dedb56b21e12c27e3c9bb2f4c2f855` |
| src/eva/providers/mcp_gateway.py | `a3323a96b6c5305b36a4856f9a8cb2dba8f6c23be023dc0ea9a345a26fd4802b` |
| tools/installer_browser_gateway.py | `c758bc5ddabf97661881bb219354ec489ec6c6907e1d2ef89086fa911d2dafb7` |
| tools/installer_runtime_access.py | `f17e6131f154046d8a184744e496f4fad4c42fb159519214f56bff27954ba9af` |
| tools/installer_target_bindings.py | `fc9bfef84719f0a776cdf311a95118d7632112a60290bac6a766f7c5581fb3a9` |
| tools/packaged_installer_target.py | `f75ab3529eb3d2a6e8186cbb32d3c7c3c314a45b92b34b654452baa056a6af49` |
| tools/stage_installer_package.py | `82e0687f64cfb5b8ad69a11263676e1254440d2eeef2b295fab53a16ff74ae91` |
| tests/bootstrap/test_installer_browser_module.py | `cbf19aa4de6df2c8a3008387059b240cc22f8b613850a32e179455eab67f25bb` |
| tests/connectors/test_mcp_autonomy_provider.py | `c475ebedb9397f5e90ce4b2d9940fdf6383938c94d0907fead9b8f2ada9ac757` |
| tests/operations/test_installer_connector_selection.py | `36c75029397aef37e47e57fbf488ffdc98182db5d0d917f1ff91c461e683d52a` |
| tests/operations/test_installer_browser_gateway.py | `77ca94d33b9df3029ffe6dc7b405e6501251a8a7936569ab32b59895039019ff` |
| tests/operations/test_packaged_installer_target.py | `7f318177a3a725dea7819ba9ea1e55307a873406568f17117fe091457fac7a7a` |

## Onafhankelijke installatiereview

Beslissing: **INSTALLED GO voor de gewone connectoraansluiting.** Eén gewone prepare-aanvraag voltooide in **152,851 s** (ruw 152.8511904689949). Directe readonly Docker- en bestandsuitlezing bevestigt core `sha256:f7a6d902905bf1042e9eeee4423b27e938dff26c615770778acf80517821f9ec` en gateway `sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd`, beide healthy. De enige gateway gebruikt één passende nieuwe bridge, de vastgelegde subnet-/namespacebinding en de private gepubliceerde hostpoort die de core werkelijk adresseert. Er is geen browser of statische catalogus; voice en HA zijn hier overgeslagen.

De actuele mandate-directory is readonly, eigendom UID 65532, modus 0700; het mandaat is 0600 met dezelfde eigenaar. Tenant, installatie en principal zijn privé met de echte coreomgeving vergeleken en komen overeen. Public HTTPS is expliciet aan, local_networks is leeg, gateway SSRF-private blijft false en SSRF_ALLOWED_NETWORKS is leeg. Uitvoerings- en registratiecredentials zijn verschillend; private files bevatten de waarden. Alle 13 actuele bron-/pakketbestanden en stagingbindings matchen de finale broninventaris. Het runtime-image is hetzelfde eerder gecontroleerde immutable image. Staging-SHA256 bij deze uitlezing: `7c2e92cb7eb69a67297c339dd69190c2c85efd7030aeae8a057c352fe1a3c112`. `source-candidate.json` blijft de eerste freeze; de finale bronbinding is de hierboven vermelde `final-source-hashes.json`.

Vóór de chat waren nul upstreamregistraties aanwezig. Eén gewone HTTP-chatvraag duurde **40,537 s** en sloot `https://gitmcp.io/python/cpython` aan. Eva deed eerst een discovery met de nog onbekende endpointreferentie, daarna connect, discovery van de verkregen connector-ID en werkelijk gebruik van het documentatietool. Deze eerste misser is meegenomen; vier STATE_VERIFIED-records betekenen hier niet vier geslaagde externe dienstaanroepen. De gateway-uitlezing toont daarna één nieuwe juiste registratie met vier tools.

De reviewer heeft de opgeslagen capture zelf gedecodeerd: **9.164 bytes**, SHA256 `3c06c493fbd4737bad9411e27fc97c099eef0fe4ee4e742619c20a988a327f7e`, exact gelijk aan het sealed bewijs. De inhoud bevat de echte Python-README, bron-/bouwinformatie en configure/make-instructies. Eva’s korte antwoord en README-link passen bij die inhoud. De gestreamde tekst is gelijk aan het finale antwoord, diens teksthash klopt, en alle vier acties zijn gebonden aan dezelfde oorspronkelijke chatvraag. Er is geen nieuwe netwerk- of modelaanvraag gedaan om dit opnieuw te controleren.

Gemeten chatgebruik: **5 modelpogingen, 4 toolcalls, 21.329 inputtokens en 369 outputtokens**. De afzonderlijke kleine modelverificatie duurde 1.7079254429845605 s; haar receipt bevat geen tokenaantallen. Credits blijven onbekend. Er was geen taakretry of runtimecorrectie. Een eerste uitleespoging van de readonly meethelper gebruikte een niet-canonieke timestamp; alleen die helper werd naar Z-notatie gecorrigeerd, zonder modelherhaling. Dit is afzonderlijk bewaard.

| Gelezen bewijs onder installer-adaptive-connector-20261003 | SHA256 |
| --- | --- |
| installed-before-chat.json | `2d1f79fb7466330bef1b5596af13596191c2008e98addc6e9dcb5df7f28ae48e` |
| connector-chat-response.private.ndjson | `8d226a921a999b7bfd4e6288ccc4ae1931188b27d7cca1c97aa365177d0feb99` |
| connector-chat-terminal.json | `0914efe752a8bc1223c73db421776d88a4d89a0f48e11c227b9a087562ba6c6b` |
| chat-journal-readback.json | `21753adb8b757d9f9cf66ce7e243cdc6384e08be171a5c2dae978f2c6a75ff99` |
| gateway-after-chat.json | `c8460660175cacdb3094df02e358f64fe465c242c7087039f9a9c2cbfe0cba53` |
| verified-actions.json | `ba265833650b451a39439edf9499658c958239cc2494057fce2403cb69f8cc1c` |
| connector-effects.private.json | `94bfbd6510cae5be2255d88127574fa743167f46e96de5571dbc258fcbc829fe` |
| source-content-readback.json | `613d2b509e4eed216841eb834d39ab8721fe0ef5fe0f581bb7b28d9cf48bea56` |
| journal-readback-correction.json | `d19360fe0aa5ffebded4e0fa03e5a1ef5aa98f47da7d9ef73df315ce94024858` |

## Technisch eindpunt en behouden grenzen

De oorspronkelijke Step6/O1-afspraak in `delivery-plan.md` betreft een verse ondersteunde tekstinstallatie via de eigenaarwizard met voice/HA/browser als toe te voegen of over te slaan modules. De bovenste aanvulling plaatst O1.2 bij optionele compositie/lifecycle zonder iedere module verplicht te maken. De bestaande kleine eindvoorwaarde blijft: dezelfde installatie werkt met gekozen modules, zonder hardcoded eigenaarpaden.

Met het eerdere versiegebonden tekst-, HA-, browser- en voicebewijs en deze geïnstalleerde zichtbare connectorroute is het concrete zelfstandig uitvoerbare technische O1.2-restpunt gesloten. **Geen verder zelfstandig technisch O1.2-bouwwerk is op dit bewijs aangetoond.** Dit oordeel verwijdert geen oorspronkelijke eindvoorwaarde en claimt geen gebruik van ongekozen diensten.

De nieuwe Telegram-/inboxproeven blijven zichtbaar als externe toegang- en gebruiksbewijzen: hun eerder beoordeelde installerwiring is geen geslaagd nieuw Telegramgesprek of gelezen mailbox. Zij zijn zonder een concreet defect geen nieuwe programmeerblokkade voor de oorspronkelijke optionele compositie-uitkomst. Nieuwe kanaalontvangst/antwoordgedrag, persoonlijke accounttoegang, fysieke speakerervaring C3/C4 en de samenvoeging tot één finale kandidaat bij F1 houden hun eigen voorwaarden. De browser+connectorcombinatie is bron-/testmatig beoordeeld; deze verse liveproef koos uitsluitend de connector. Er is geen nieuwe taak of teller door deze review toegevoegd; de coördinator beheert de status.
