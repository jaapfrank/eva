# F1.1 — onafhankelijke review van de geïntegreerde kandidaat, 3 oktober 2026

**INSTALLED GO voor de oorspronkelijke F1.1-uitkomst.** De twee beoordeelde images draaien gezond; de gewone installerpakketbinding en het behoud van configuratie, overige diensten en historische L3-receipts zijn onafhankelijk gecontroleerd. De bestaande kleine eindvoorwaarde blijft: alle verplichte afgeronde functies in één geïnstalleerde, herleidbare versie. F1.2, de eindvergelijking, fysieke speaker-/gezinsbeoordeling en nog ontbrekende externe kanaaltoegang worden hiermee niet afgerond. Deze samenvoeging introduceert geen nieuwe functie of experimentele claim.

Basiscommit: `8ab4701a5d89c25a55ecf5d153f68e6a33179f79`. De kandidaat combineert de daadwerkelijk geïnstalleerde L3-versie met de vijf reeds beoordeelde optionele provider-/browsercorrecties. De werker krijgt daarnaast vier bestaande kerncorrecties: de aparte servicecallback-lane, selectie van de actuele native sessie met bijbehorende opslagmethode en het sneller ophalen van reeds gereedstaande resultaten. Die vier inhoudelijke verschillen zijn samen gelezen; het gaat om overname van de reeds werkende kernroute. De 350 geïnventariseerde bron-/lockitems van de twee kandidaatimages zijn inhoudelijk gelijk.

| Dienst | Behouden basisimage | Kandidaatimage |
| --- | --- | --- |
| Kern | `sha256:730524883c9c47c193b33e9a875ad4c4e3f3b722d315c50387fd10c8fdd59661` | `sha256:fd1c7cd3413405fd6b1b5ce408f2028cffed8de735a301550663e4c891ee5902` |
| Gedelegeerde werker | `sha256:0b1375541e87bf8145ef896248836b51d78d14e47b6c32dd8df951efbf4205b1` | `sha256:30d65117cc0f526bc3ed8859a0eee867ff8fe0943c0bf42c8c09d787c2ca3089` |

De exacte L3-bron `adapters/postgres/delegated_work_actions.py` blijft SHA256 `2554605179fcdff8b2b8793b8dbbdecaffdeddaafc31c65063f19e6e9269beb7`. Geen kandidaatwijziging, nieuwe holdoutlezing of modelvergelijking vond plaats. De vijf optionele files behouden hun eerder beoordeelde bytes: `browser_autostart.py`, `agentmail.py`, `mcp_autonomy.py`, `mcp_discovery.py` en `mcp_gateway.py`. Daarmee blijven private owner/audience-controle, de gedeelde directe/geconfigureerde browser-sessiebinding, replay zonder herstart en normale dynamische connector-ID's behouden. De hoofdconfig heeft mail niet ingeschakeld; de daadwerkelijke imagecontrole bevestigt daarvoor None in beide images en aanwezigheid van de drie runtime-eigenaarvelden. Dit is geen inbox- of browsertaakproef.

Onafhankelijk gecontroleerd: alle vijf/negen overlaybestanden komen exact overeen met bouwreceipt en repository; de oorspronkelijke image-lagen zijn behouden met precies één toegevoegde laag en ongewijzigde imageconfiguratie. Python/distributieversies van de drie bestaande uitgangsimages zijn gelijk; er is geen dependencyupgrade. De 26 verschillen met de huidige repository zijn expliciet vastgelegd in `retained-source-variants.json`, inclusief de bestaande lock en metadata. De geïnstalleerde kern is dus niet gelijk aan ieder bestand van HEAD. Actuele installerbron draait als afzonderlijk meegeleverd hostpakket; uitgestelde andere bron wordt niet door een brede overlay geactiveerd. Deze expliciete samengestelde versie is de herleidbare kandidaat.

Het gelezen gerichte bewijs bevat uiteindelijk **85 geslaagde gevallen**: eerst 77 geslaagd/8 testharnessfouten, vervolgens 7 geslaagd/1 ontbrekende fixture en ten slotte dat ene geval geslaagd in 1,94 s. De eerste fouten betroffen de ontbrekende SQLite-testbibliotheek; productie gebruikt PostgreSQL. Daarna ontbrak de bestaande private_tmp_path-fixture door de test-rootselectie. Alleen die noodzakelijke testomgeving werd gecorrigeerd, zonder productcodewijziging of volledige herhaling. Een eerdere imagebouw wees een SHA-ID onjuist als registrynaam aan; de herstelde lokale aliastags zijn aan de exacte bases gebonden. Alle oorspronkelijke foutlogs blijven behouden. De reviewer herhaalde geen tests en deed geen model/provider-/huishoudactie.

## Deployment- en historische grenzen

De oude negen-dienstencompose bevat verouderde verwijzingen voor andere diensten. Die mag niet met een algemene up-opdracht als nieuwe kandidaat worden toegepast. De beoordeelde beperkte beschrijving bevat alleen kern en werker; hun instellingen en de top-level netwerk-/secretdefinities zijn gelijk aan de behouden bronbeschrijving, met uitsluitend de twee nieuwe imagepins als beoogde wijziging. De daadwerkelijke overige diensten krijgen afzonderlijke bestaande image-/container-/configuratiebindings. Zij worden niet opnieuw aangemaakt of dubbel gestart.

Gerichte readonly herkomstcontrole: de bestaande main-projectcontainers gebruiken `unless-stopped`; de lokale eigenaarunit start alleen de bestaande dashboardproxy en de postboot-proof-unit is uitgeschakeld. In deze route is geen bootactie gevonden die de oude brede compose opnieuw up't. Dat is een bron-/metadataoordeel, geen voltooide computerherstartproef; F1.2 blijft daarvoor verantwoordelijk. Bestaande afzonderlijke installerdoelen, voice, HA, gegevens en autoriteit blijven behouden.

De L3-host bevat precies één aanvraag `6ea597a8d7e47362fd270ba0694e1513b109e03561fe6e7e639f62343edbabd5`, status completed. De code selecteert alleen queued/waiting voor toepassing en weigert een nieuwe aanvraag voor dezelfde accepted review. `review_status` leest de historische receipt; completed leidt niet opnieuw tot toepassing van de oude images. Vóór F1 waren request-SHA `223a9992ab13a0ba76146a14029575227f422dc6e9aa7250b3c3324a14fcba3c`, updaterreceipt `dacaf8835969d375954fc67fbf1a595385f5a163310e57f6245970a74d52fe03`, oude compose `e647fa76653361e151c28e653c2ace49462232425345b083af3b2f85d7c88925` en serviceconfig `062cf8a8595d46b1751df3c267abf9662ea1fc2630f50a77776eb8f726b380bd`. Zij blijven historische bewijsstukken; de nieuwe F1-installatie krijgt eigen bewijs.

Tijdens de bronreview resteerde voor installed-GO uitsluitend de gerichte bevestiging van deze twee daadwerkelijke imagepins, gezondheid, behouden mounts/configuratie, actuele pakketbron/imagebinding en onveranderde overige diensten/L3-terminalreceipt. Bron-GO alleen voltooit F1.1 niet. De gezamenlijke werking/herstart en eigenaaracceptatie blijven aparte bestaande eindvoorwaarden. R1 blijft met zijn beschermde gegevens ongewijzigd geblokkeerd voor live uitvoering; geen R1-proces loopt.

## Bewijsbinding

Onder `/home/administrator/project/.eva2-artifacts/f1-integrated-candidate-20261003`:

| Bewijs | SHA256 |
| --- | --- |
| build-receipt.json | `bdbbc9890cbfa955e9aaebe79aa89905605c149c750183c28a33b1cbc000a00f` |
| dependency-preflight.json | `3f52d1173628f75030b358fedfacff7201fc2e9fde4a63655d79331fb9eb1027` |
| candidate-preflight.json | `35e2a41cad59769c1c1723f25f5d5d64c7d0550301e0f2561097bb1ed67ae90d` |
| core-candidate-source.json | `e5e9a21cfa7a2942924c0d7fc7315059eec502e1aa90c3cc0b007419c428031f` |
| worker-candidate-source.json | `8f461f7d66e25d1cb9268a899c192d1a61941c8311d4ab6521ba76cfb68baebf` |
| retained-source-variants.json | `b727f7a78bfc857dba1212aa4b3a50d86258562c1c206e30cb2b9337587086e1` |
| test.log | `2ae111853ef7d18ee738828357bf28c0d3701b79e6fb31928e4ab327d2907a6f` |
| test-failed-only.log | `d899b5c6c857c4fbecce2a86b2b23a708b565ca8585cda80e92c81ec208973ba` |
| test-final-one.log | `3b4875dc515d55439d8abed80997ab1489b094e334bc63b66b9e80213b49f41e` |

De reviewer bezit uitsluitend dit reviewdocument en readonly reviewuitvoer, geen product-, dashboard-, pakket- of deploymentwijziging.

## Definitieve deploymentbeschrijving vóór installatie

**DEPLOYMENT SOURCE GO** voor candidate-compose SHA256 `fb6590361d55d35b7067ef6153cff208ba910015be929e16c899561aaab879fe` en beperkte installed-base `2f447c7905ec08783f7cf852d0311902e0f059f8a00b2bd5446a2ab64ed9a4bd`. De eerste beperkte beschrijving bleek bij Compose-validatie nog naar weggelaten diensten te verwijzen. Dat was een echte descriptorfout vóór enige installatiepoging; zij is gericht gecorrigeerd en blijft zichtbaar in het eerdere log.

Onafhankelijke vergelijking bevestigt: originele brede beschrijving → beperkte base verwijdert uitsluitend de externe depends_on-edges van core naar Hindsight/model-access-lease/Postgres en van worker naar model-access-lease. Worker→core met service_healthy blijft. Base → kandidaat verandert alleen de twee vastgelegde imagepins. Alle overige geselecteerde servicevelden en top-level netwerk-/secretdefinities blijven gelijk. Dit is een expliciete externe-dependencysplitsing; de kandidaatdescriptor is niet bytegelijk aan de oude brede beschrijving.

De vervangen edges hebben concrete gecontroleerde prerequisites: de bestaande Hindsight- en Postgrescontainers zijn running/healthy met respectievelijk image `20d72ad34164bd1a67906c4d61b57a51f2b255c49ddf8cb7ac5e901ebc3b3710` en `7e641ed0cf38285bebfc6151ba48243439dfe9cf3306feab4023a812466cd6c1`; de bestaande model-access-lease is running met `455be8e6f52adcf2f16288ad76793f150684c058753de3d8d84730a7ce623f7e` en heeft geen Docker-healthcheck. De oorspronkelijke projectdirectory blijft behouden voor relatieve bindings. Finale Compose-validatie: exit 0. Dit is geschikt voor de gerichte update van twee diensten, niet voor een nieuwe volledige installatie of gegarandeerde opstartvolgorde na een computerherstart.

Bewijs onder `f1-integrated-delivery-20261003`: candidate-deployment-check SHA256 `7703c202fc3983828cc2be06cef1143b4090089d107563d459574853f40e321d`; existing-runtime-bindings `1dbc474d479c75ace8613066987b4cdd2491913ad1ecb88c0e0e235b2a8499f9`; before-health `11ba32bcc122525aebfbc572169ff6e2673e732b25fdb308f79184a210c64317`. Het voorafgaande GET gaf HTTP 200. Alleen opslaan/uitlezen van die readonly receipt is als directoryeigenaar gecorrigeerd; geen model- of runtimeopdracht is daarvoor herhaald.

## Onafhankelijke geïnstalleerde eindbeoordeling

**F1.1 technisch af binnen de oorspronkelijke eindvoorwaarde.** Eén deploymentopdracht voltooide met exit 0 in **41,895 s** (ruw 41.89533098900574), uitsluitend voor core en delegated-work-worker, zonder force-recreate, algemene up, rollback of herinstallatie van andere diensten. De eerdere descriptor-, bouw- en testharnessfouten hierboven blijven bewaard als voorbereiding; zij waren geen extra installatiepogingen.

De reviewer heeft na de gemelde installatie beide daadwerkelijke containers rechtstreeks uitgelezen. Core draait image `fd1c7cd3413405fd6b1b5ce408f2028cffed8de735a301550663e4c891ee5902`, de werker `30d65117cc0f526bc3ed8859a0eee867ff8fe0943c0bf42c8c09d787c2ca3089`; beide zijn running/healthy. Alle **350 bron-/lockitems per container** komen overeen met de vastgelegde kandidaat. De L3-winnaar is in beide exact `2554605179fcdff8b2b8793b8dbbdecaffdeddaafc31c65063f19e6e9269beb7`.

De relevante omgeving, commando's, runtimegebruiker, healthconfiguratie, gepubliceerde poorten, netwerkmodus, resource-/beveiligingsinstellingen en **25 kernmounts / 21 werkermounts** zijn gelijk aan de behouden pre-installatieinspectie. De eerste readonly vergelijking behandelde de gewijzigde volgorde van Env/Binds/Mounts als verschil; vergelijking van unieke omgevingssleutels en gesorteerde mountmetadata bevestigde dezelfde inhoud en dezelfde daadwerkelijke mounts. Alleen die meetvergelijking is gecorrigeerd, zonder product- of runtimewijziging.

Alle **18 afzonderlijk vastgelegde overige diensten** hebben nog hetzelfde container-ID en dezelfde image en zijn running; aanwezige healthchecks zijn healthy. De bestaande private owner-, browser-, geheugen-, werk-/document-, voice- en autoriteitsroutes zijn zo als bestaande configuratie bewaard. Dit bevestigt behoud van de geïnstalleerde aansluitingen, geen nieuwe zelfstandige taak- of audioproef. Een eigen readonly GET naar `/healthz` gaf HTTP 200. L3 heeft nog precies één completed-aanvraag; request, oude updaterreceipt, oorspronkelijke compose en hostserviceconfig hebben alle vier hun eerder vastgelegde hashes. Er is geen tweede toepassing of nieuwe aanvraag geweest.

Het gewone pakket wijst via `development_candidate.imageDigests.living-core` naar dezelfde kernimage. Onafhankelijke manifestvergelijking toont uitsluitend die gewijzigde imagepin en de expliciete F1-overlayprovenance. De **1.225 bronbindings** zijn onveranderd; de vijf relevante providerbestanden zijn daadwerkelijk gelijk aan bron én manifest. De bestaande pakketverifier controleerde 1.225 bestanden en acht lokale imagepins met de meegeleverde Python. Een eerste helper gebruikte host-Python zonder pydantic en stopte vóór wijziging; de normale gebundelde runtime voerde de verifier daarna uit. Geen nieuwe installatie van een bestaand optioneel doel is hiervoor uitgevoerd.

Pakketmanifest SHA256 `2a0662d69ea935443b3ccae42947f19c7f6a7bb8a994978db57e797021e2813a`; verifierreceipt `bc8cbfe6dcd3d9a7dd6610527cdc8d855ed4812a4d456bd1fb5fbaebacce9f33`. De historische bronbasis `aca98ba68a262c16a11f495d489cb70e81b8c30b`, immutable Pythonruntime `35eba561f26f4745b7995ae849c9341c441361a74106326308781e7e3ffc7a69` en platformlock blijven eerlijk behouden. De overlay noemt integratiecommit, beide nieuwe images, bouwreceipt en de 26 behouden bronvarianten. Dit is een traceerbare ontwikkelkandidaat, geen claim van een volledig opnieuw gebouwde HEAD-release.

| Installatiebewijs | SHA256 |
| --- | --- |
| Root installation.json | `f945271cc33f687fd2a68b20d85a3efdc7fc0dd0b260acb1783ef4249dfc90cd` |
| Root installed-readback.json | `5e90fc7e28d3efcfd2a862a9d109d8e2a9bd2f38705b42b70b8543002ec99c72` |
| Package-binding.json | `eaf39016f0ab1cf0496ebd988fab51ef8e9a5126c2eb1bcfe89c63a30ad629ba` |
| Eigen onafhankelijke installed-readback.json | `e02fce4b4474b997ca6540899b5eca30989f1c6622a6d05750e4ed63d16bc5d7` |

De eigen uitlezing staat onder `/home/administrator/project/.eva2-artifacts/f1-independent-review-20261003`; de overige installatiebewijzen onder `f1-integrated-delivery-20261003`. De publieke evidence-draft `eva2/evidence/product/f1-integrated-installed-20261003.json` is inhoudelijk met deze waarnemingen vergeleken en maakt het juiste onderscheid tussen F1.1 en F1.2. De coördinator beheert de uiteindelijke status en Git-publicatie.

Er is geen verder zelfstandig technisch integratierestpunt binnen **F1.1** aangetoond. **F1.2 gezamenlijke werking en echte computerherstart blijven open**, evenals de bestaande eindvergelijking, noodzakelijke externe Telegram-/mailtoegang, fysieke stem-/speakerbeoordeling en gezins-/eigenaaracceptatie. De historische hoofdgroepteller wordt hiermee niet verhoogd en het gehele F1/F2-product is niet geaccepteerd. Deze review deed nul model/providercalls, geen nieuwe tests en geen runtime- of huishoudmutaties. Ontwikkelmodelgebruik en credits zijn hiermee niet gemeten. R1 blijft ongeëxecuteerd en zijn beschermde fixtures blijven ongewijzigd.
