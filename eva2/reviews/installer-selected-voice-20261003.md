# O1.2 — onafhankelijke bronreview optionele voice, 3 oktober 2026

Beslissing: **SOURCE GO** voor de hieronder gehashte kandidaat vanaf basis `0610c8320d2fe0076418295b199407b176b61371`. Dit is toestemming vanuit de technische bronreview om de bestaande installerroute te installeren en gericht te controleren; nog geen bewijs van geïnstalleerde voice, een hoorbaar gesprek of C3/C4-acceptatie. Eerdere gesigneerde L3-reviews zijn niet gewijzigd.

De begrensde route is samen beoordeeld: expliciete keuze of overslaan → huidig geverifieerd HA-configuratie-/secretbewijs → alleen pipeline-list → expliciete pipeline- en verwerkingstoestemming → bestaande DashboardVoicePolicy → private materialisatie en coremount → bestaande fresh-start → media/LiveKit na gezonde core → gewone voice-link. Selectie en plan blijven gebonden aan dezelfde installatie-identiteit en actuele HA-evidentie. Overslaan start deze module niet. Falen na corestart bewaart core, gegevens en startbewijs als inspecteerbare gedeeltelijke uitkomst.

Drie concrete reviewbevindingen zijn gesloten. De policy is een directorymount op exact `/run/eva/voice`, wordt via bestaande materialisatie privé leesbaar voor de runtime-identiteit en wordt door media uit exact dezelfde actuele coremount overgenomen. Atomaire vervanging/verwijdering kan daardoor de bestaande actuele policycontrole bereiken. De HA-WebSocket weigert redirects tijdens de handshake vóór verzending van het JSON-authenticatiebericht. Opnieuw ophalen invalideert een eerdere ready-keuze via het bestaande journal vóór HA-hercontrole, zodat een verouderde keuze opnieuw bevestigd of overgeslagen kan worden. De directory-uitzonderingen zijn beperkt tot dit exacte readonly mountdoel.

De twee oudere wizardfixtures zijn aangepast aan de nieuwe expliciete optionele keuze. Bestaande reeds gestarte toegang blijft via het duurzame runtimebewijs beschikbaar. Er is geen automatische inschakeling van voice voor oudere keuzes. Alleen de bestaande, expliciet herkende HA Cloud STT/TTS-combinatie krijgt momenteel de behouden privacybasis; het tonen van andere pipelines bewijst geen ondersteuning van hun bewaarbeleid.

Bestaande werkelijke imagecontrole is hergebruikt: core `sha256:759b638cc283465c10c9130f2aa45df44ee24c3f375d39f055a90f15ec046b02`, media `sha256:2e9092a057dbcaa089bc91b00f27c9abd7feff429257d54f7a90a642917e69f6` en LiveKit `sha256:770bc61bc6ec4dcccbd3e05082d8f6bdafbee4179acab7014cc97202a9e2910b`. De bestaande core-policycontracten en media-interface zijn compatibel; het VAD-modelbestand is aanwezig. Er is geen corewijziging nodig voor deze policy. De nieuwe RTC-configuratie begrenst signalering en UDP tot loopback, schakelt RTC-TCP uit en gebruikt geen automatische externe-IP/STUN-resolutie. Dit bronbewijs is geen bewezen browser-ICE-verbinding.

Bewijs: eerdere gerichte uitvoering meldde 54 geslaagde gevallen plus één UUID-validatiefout, gevolgd door 5/5 na correctie. Die transcriptuitvoeringen zijn niet herhaald. De definitieve correctiegroep is rechtstreeks gelezen: **10 passed in 1.99s**, exit 0, inclusief selectie, module, policy-directoryvervanging en de twee wizardgevallen. Commandobewijs SHA256 `8fbbc1e14e5ddad383cc8517e591f77f43d543a79f9ef19c980c2188321483ec`; pytestlog `d021d45d18fc5c1f84eeecbe992d63466396a54aae844d528aec4e1884062ca6`; resultaat `c365161d39236fe0f1d32937fbc549796b8487d8faf65ed3879b96897c78ea5d`. Deze bestanden staan onder `../.eva2-artifacts/installer-selected-voice-review-20261003/`. De nieuwe regressies bewijzen de directorysemantiek met lokale bestanden en gemockte module-uitvoering, niet met gestarte containers. Deze afrondende review deed geen nieuwe tests, model-, provider-, audio- of cloudoproepen en startte geen services.

Na installatie resteert gerichte uitlezing van de exacte pakket-/imagebinding, actuele core/media-policy-directory en runtime-identiteiten, fresh-startbewijs en media→core-ready. Een echte sessie/ICE-controle kan apart transport aantonen; verstaanbaarheid, microfoononderbreken, Windows-browserervaring en speakers worden daarmee niet automatisch geaccepteerd. De bestaande speakerinstallatie blijft buiten deze wijziging.

## Exact beoordeelde bytes

Paden zijn relatief aan `eva2/`; SHA256 is na de drie correcties rechtstreeks berekend. De oorspronkelijke kandidaatsnapshot blijft historie.

| Productbestand | SHA256 |
| --- | --- |
| src/eva/operations/installer_voice_selection.py | `5a8e90cae5aaa8f9a3e6ee0934ee6df7f8533cb23d6fb85ad7a213b91eec2591` |
| src/eva/operations/installer_capability_workflow.py | `2ebaf2dd201977c7834578e3fa12723a1359e077a1789dabc6b987e67e8f9f18` |
| src/eva/operations/installer_development_candidate.py | `679cc6f8de3df7db768b5d11f2d6a09589dd6f343c4a205c55f3d9d1e855e860` |
| src/eva/adapters/api/installer_capability_api.py | `2f5adbae064088be946f4b702125948d742082f9812023f526a5d650012707b7` |
| src/eva/adapters/api/installer_provider_api.py | `61d6ad573ca13de3801e99ca3fdccd7b724ff378991200590a0af3211739514e` |
| src/eva/adapters/api/installer_wizard.py | `27fefb40eb25d6ea45a82799a6e5931c99d3a1cc2e27e9f6fb4bc64b70f54f87` |
| src/eva/adapters/api/installer_installation_plan_api.py | `b49aa590b8ce019c2a72f727a381ab74e30e742d8acdbf26178cd1890e081b4c` |
| src/eva/bootstrap/installer_wizard_cli.py | `3c39fd189293f8dd76c6681370cdf0c4d287276cfcad0b1d1520e90e926b5e19` |
| tools/installer_voice_module.py | `217cc9223c07276443f81619c69040c3e6e76f672e3741385911f1e5a1f897b7` |
| tools/packaged_installer_target.py | `892e68a8ec8fa9ea7862a8cb388bb7ffb319e1489384fcbb323fbe1f441b494c` |
| tools/installer_target_workflow.py | `74da948f9fb003ab167003a8da1835e0f8dbf29bba8747563c0c8e6f3add5608` |
| tools/installer_target_bindings.py | `e2058d71b748180a6581e3ea98f24ff1d15f53478196f0be39a624145d58f05d` |
| tools/installer_runtime_access.py | `3fc2cdd64499e33cc1088958b99ed91a396a1db5b775cd1f2d711e6e1e049e22` |

| Direct testbestand | SHA256 |
| --- | --- |
| tests/operations/test_installer_voice_selection.py | `0e2845da254ce39063cad2aee321400a47093b6cbaacf267f4678b52bfd581e0` |
| tests/operations/test_installer_voice_module.py | `a5101b74677f06cafd8f583e274b7962e3c951382d9670ebb7ed1c4f15bf109e` |
| tests/operations/test_installer_target_workflow.py | `e0a1f7a50248cb615114496fa488623be1f36c73aeda8327955db19fa8e517d0` |
| tests/operations/test_installer_runtime_access.py | `f6b3a83ef0b5f5685f0241f900c2a572601bee9d8ee1851f897050d0afbe8eb5` |
| tests/api/test_installer_wizard.py | `dfbb864ff6b38bc2a27ad07ed8dfdddbb1c43a2b446743d917aebfbaa355d317` |

## Addendum — expliciete IPv4-loopback na installatie

Beslissing: **SOURCE GO voor de gerichte IPv4-correctie**, met de bovenstaande bronreview als historie. De uitvoerder meldde een geslaagde fresh-start en native RTC-verbinding zonder audio/model, maar Windows bereikte de gegenereerde localhost-link niet terwijl dezelfde IPv4-loopbackroute HTTP 200 gaf. Dit addendum beoordeelt uitsluitend de noodzakelijke bron- en configuratiecorrectie; het is nog geen onafhankelijke eindbevestiging van de gerepareerde installatie.

`tools/installer_voice_module.py` heeft nu SHA256 `2513fc208eaeca1855e07280e5d2082137714ee6ab4d23730aba2c72c26d14b8`. Exact twee generatorregels veranderen: de voice-origin naar `http://127.0.0.1:{media_port}` en publieke LiveKit-signaling naar `ws://127.0.0.1:{livekit_port}`. Terugvertalen van uitsluitend die twee regels leverde onafhankelijk exact de eerder beoordeelde hash `217cc9223c07276443f81619c69040c3e6e76f672e3741385911f1e5a1f897b7` op. `tests/operations/test_installer_voice_module.py` heeft SHA256 `2c7ddbe6b6c7582df48a073be22439c95ac4829089496c29de3244d7b0d3c0e6` en controleert beide IPv4-waarden.

Het bestaande gerichte log is gelezen: **2 passed in 1.28s**, exit 0; niet herhaald. Bewijs onder dezelfde private reviewdirectory: `ipv4-command.json` SHA256 `b1fbb0950cf778eb9621d16e4def3a99e26cb8fad4dd2f72b55755673256e0a6`, `ipv4-pytest.log` `3d5d4adb74b114a61c91f32c4f7e9b71d1c684729712ae18c89dd9966ddabceb` en `ipv4-result.json` `c365161d39236fe0f1d32937fbc549796b8487d8faf65ed3879b96897c78ea5d`.

De bestaande core leest de policy bij bootstrap en bewaart de canonieke hash en allowed_origin in zijn closures; iedere gewijzigde policyhash wordt daarna geweigerd. Media leest origin, authorityhash en publieke signaling bij create_app. Daarom vereist de beschreven reparatie een gerichte herstart van **core én media in uitsluitend dit nieuwe target** nadat bronpolicy, actuele runtimepolicy, plan, owner-link en media-config consistent zijn aangepast. Een directorymount maakt de wijziging zichtbaar en oude authority ongeldig, maar herbouwt de coreconfig niet. LiveKit-config en poorten veranderen niet. Het initiële startbewijs, installatie-identiteit, gegevens en oorspronkelijke proefuitkomsten blijven behouden; de reparatie krijgt afzonderlijk bewijs.

Na uitvoering controleren de actuele package/configbinding en ready/link/session-readbacks deze gewijzigde overgang. De eerdere succesvolle controles hoeven niet volledig opnieuw. Deze review startte niets en deed geen nieuwe provider-, model- of audioaanvraag.

## Installatiereview — behouden target en werkelijk RTC-transport

Beslissing: **INSTALLED GO voor de gekozen voice-deeluitkomst**, op de behouden nieuwe installatie. De 13 bestanden uit de definitieve kandidaatsnapshot zijn onafhankelijk byte voor byte met repository én werkelijk pakket vergeleken; alle hashes en de overeenkomstige stagingbindings kloppen. Directe Docker-uitlezing bevestigt de hierboven gepinde core-, media- en LiveKit-images. Core is healthy; media en LiveKit draaien en hebben geen Docker-healthcheck. De actuele media→core-ready-uitlezing is positief.

Core en media gebruiken exact dezelfde readonly `/run/eva/voice`-directory. Directory en policy zijn eigendom van UID 65532, met respectievelijk modus 0700 en 0600; beide containers draaien als 65532:65532. De tenant-, installatie-, principal- en bindingwaarden zijn privé met de echte coreomgeving vergeleken en komen overeen. Tegen de bewaarde voorcorrectiebestanden is `allowed_origin` de enige gewijzigde policywaarde. Bronpolicy, runtimepolicy, plan, owner-link en media-config verwijzen consistent naar dezelfde IPv4-origin en actuele policyhash `5b4cd9dd2558c6aeea3c5c5cadbf3241e618b8464afab367ff6b843cb67a08fe`. Publieke signaling gebruikt de bijbehorende IPv4-loopbackpoort.

De finale Windows-readback meldt HTTP 200, lokale toegang en de startknop. Het afzonderlijke werkelijke native RTC-bewijs toont verbinding met deelnemer Eva in **0,647 s**, gevolgd door succesvol sluiten. De gecontroleerde uitvoerder opent geen microfoon, publiceert geen audio en vraagt geen modelantwoord. Dit bevestigt de echte media/LiveKit/core-transportovergang op dezelfde host; het bewijst geen Windows-browsermicrofoon, hoorbaar gesprek of fysieke speakeracceptatie. De eerste start en RTC-uitkomst plus de mislukte localhost-route blijven als historie bewaard. De gerichte IPv4-configuratiereparatie met core/media-herstart is menselijke ontwikkelassistentie; geen claim van een volledig ongewijzigde, onbegeleide verse installatie.

Bestaande private bewijzen zijn gelezen, niet opnieuw uitgevoerd:

| Bewijs onder installer-selected-voice-20261003 | SHA256 |
| --- | --- |
| final-candidate-snapshot.json, 13 runtime-/wizardbestanden | `6325b48c632f5797f4603bc2bd404f30faf6e6bc624d26f00b2d2f4965b592e5` |
| ipv4-installed-correction.private.json | `931e6d55ae74f590df1003b8cca3b8d79abc02e9eb2d66e8e034b2330e14201a` |
| installed-final-readback.json | `2675ce7b81ed2b2bd2791c29d6fbedc1f4daed8aa14701fa9bbe5c00a58f9623` |
| session-transport.private.json | `657437a4466b9538afc2470e327d58ff6ef4ea04b84b30b974b603f87ebe4049` |

## Laatste pakketdelta en grens van O1.2

De bestaande developer-packager bleek de twee voice-images nog niet te accepteren. **SOURCE GO** voor de gerichte correctie: `tools/stage_installer_package.py` SHA256 `441f0277936c1d3d5c4be6815be1a02fb44fc59a2cae7fa2d8e884b3686bdc66` biedt optionele `--voice-media-image` en `--livekit-image`, eist beide of geen, behoudt de basisinventory en controleert dezelfde vaste lokale image-identiteiten. Het directe testbestand `tests/bootstrap/test_installer_browser_module.py` heeft SHA256 `4c8062fe638e1d525a0b526269ae8f553e06f605e7e46cd80913a1aab0568e90`. Bestaand log: **8 passed, 7 deselected in 0.34s**, niet herhaald; loghash `125e2045d92abc88755adc65755df2802badccaa6a8c74b050d0f845e46525ad`. Dit verandert geen draaiende voice-service.

Bij deze uitlezing bevatte het hostpakket nog de eerdere stagerhash `9bfd0794bf2255b9c2c33df743ab59365514ff96bc7c5f29302e3000dbb88e40`; staging-SHA256 `4ad2770969be4b22252746cf29aaf8fe35f03bb0192d70adafb34a13f298c336`. De al werkende voice-installatie heeft de juiste imagepins, maar aflevering van de zojuist beoordeelde gewone packagerroute moet nog afzonderlijk worden bevestigd. Daarvoor is geen nieuwe targetinstallatie of runtimeherstart nodig.

**O1.2 blijft technisch open.** De oorspronkelijke blueprint, Step6, noemt tekst plus optionele voice/HA/browser. Die begrensde voice-deelroute is nu geleverd. De canonieke kleine taak behoudt echter als eindvoorwaarde: “Dezelfde installatie werkt met gekozen modules; geen hardcoded eigenaarpaden.” De zichtbare `adaptive_connector`-keuze wordt in `tools/packaged_installer_target.py` regels 270–272 nog expliciet geweigerd omdat een gewone targetconsumer ontbreekt. Dat is een concrete open technische overgang, niet uitsluitend ontbrekende menselijke acceptatie. Het bestaande remaining-veld noemt bovendien echte Telegram-/inboxproeven; de eerder geleverde pakketwiring vervangt die bewijzen niet. Deze review schrapt geen eindvoorwaarde en verhoogt geen taak- of productteller. C3/C4, vergelijking en gezinsacceptatie houden hun eigen scope.

### Bevestigde aflevering laatste pakketdelta

Na bovenstaande tussenstand is de goedgekeurde stager daadwerkelijk afgeleverd. Onafhankelijke uitlezing bevestigt dezelfde SHA256 `441f0277936c1d3d5c4be6815be1a02fb44fc59a2cae7fa2d8e884b3686bdc66` in repository, pakketbestand, stagingbinding en definitieve kandidaatsnapshot. De snapshot bevat nu 14 productiebronnen en heeft SHA256 `e4f4534adb5aeaf2e168f46b1606e08f4d33c9b63e4d91f9d0c20fe066844d25`; het actuele `package-staging.json` heeft SHA256 `502890b4e5508de1f8c52febf3841b8c58c20e57b6bcd56fd6b03d9da5c3ac75`. De hierboven genoemde eerdere hashes blijven historisch bewijs.

Daarmee is ook deze hostpakketaflevering **GO**. De gewone voice-keuze/configuratie/startwiring en bestaande werkelijk verbonden runtime zijn technisch geleverd. Handmatige initiële pinbinding en IPv4-herstel blijven expliciet ontwikkelassistentie; een volledig onbegeleide verse installatie is niet opnieuw uitgevoerd. Geen extra test-, model-, provider- of RTC-proef nodig of gedaan. O1.2 als geheel blijft om de hierboven genoemde concrete connector- en kanaalbewijsgrenzen open.
