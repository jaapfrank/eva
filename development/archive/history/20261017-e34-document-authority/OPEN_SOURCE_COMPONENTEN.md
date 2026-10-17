# Eva 2 — open-source componentenbord

Historische beslisstand: 1 september 2026. Statusverwijzingen bijgewerkt op 12 september 2026.

## Actuele status lezen

De onderstaande oude keuzes, versies en proeven blijven historisch bewijs.
Voor huidige uitvoering geldt [development/docs/WERKBORD.md](WERKBORD.md); voor hergebruik en
onderhoud gelden [AGENTS.md](../archive/history/root/AGENTS.md#onderhouden-opensource-en-bestaande-besluiten)
en de [bestaande dependencykeuzes](../archive/history/eva2/OPEN_SOURCE_EN_DEPENDENCYKEUZES.md).
Een versie die ooit is onderzocht, is niet automatisch geïnstalleerd of de
nieuwste beschikbare versie. Hier is geen nieuwe upstream- of CVE-controle gedaan.

| Onderdeel | Gedateerde opvolging van de oude stand | Bewijs / bron |
|---|---|---|
| Modelkern en versie | De oude Pydantic- en SDK-versies hieronder zijn historische bindings. De huidige samengestelde kern wordt aan haar eigen bron/imagebewijs gebonden; niet aan de oude versietabel. | [Actuele kernopbouw en image, 12 sep](../evidence/product/MCP-catalog-pagination-live-2026-09-12.json) |
| Geheugen | GE-01 en GE-02 zijn geaccepteerd; dat wordt niet heropend door oudere adapterplannen. | [Eigenaaracceptatie GE-02](../evidence/product/GE-02-owner-acceptance-2026-09-12.json) |
| Voice | LiveKit en de bestaande HA-telefoonroute zijn technisch live. De oude Pipecat-eerste-keuze is geen huidige bouwopdracht. Fysieke telefoon-/luisterproeven blijven open. | [Voice-lifecyclebewijs](../evidence/product/voice-client-lifecycle-2026-09-12.json) |
| Tools en gateway | Een nieuwe GitMCP-endpoint is in één beurt gebruikt. De catalogusopbouw leest alle paginas; geen algemene onbekende-aanbiederclaim. | [TL-003](../evidence/product/TL-003-unassisted-2026-09-12.json), [paginatiefix](../evidence/product/MCP-catalog-pagination-live-2026-09-12.json) |
| Hulpagenten | De route is technisch live; de oude bevroren/providerloze stand is opgevolgd. De eigenaarproef blijft open. | [AU-002 timing en livecontrole](../evidence/product/AU-002-concurrent-stop-live-2026-09-12.json) |
| Dashboard en browser | React-admin is in gebruik. Chrome-route en transportherstel zijn technisch bewezen; persoonlijke Chrome-acceptatie blijft apart. | [Dashboard-/initiatiefbewijs](../evidence/product/AU-008-current-review-delivery-2026-09-12.json), [Chrome-herstel](../evidence/product/chrome-inflight-recovery-2026-09-12.json) |
| Onderhoud | Renovate-configuratie staat in de root. Npm-dekking en een aantoonbaar actieve bot zijn vervolgwerk; configuratie en oude dry-run bewijzen geen huidige botactiviteit. | [Configuratie](../../renovate.json), [documentatie-audit](../reviews/documentation-alignment-2026-09-12.md) |

`component-watch.json` is een gedateerde onderzoeksregistratie. Houd laatst
onderzocht, geïnstalleerd, kandidaat en nieuwste beschikbaar afzonderlijk.
Actuele onderhoudsclaims vereisen een bron en controledatum. Het bestaande
werkbord registreert de ontbrekende npm-dekking/botcontrole; er wordt geen
externe bot gestart vanuit deze documentatiecorrectie.

## Historische beslisstand en metingen — 1 september 2026

De resterende tekst bewaart de toenmalige afwegingen en uitslagen. Woorden als
“nu”, “actueel” en “live” daarin gelden voor die oude meetdatum; recente besluiten
en gedateerde opvolgbewijzen hierboven gaan voor. Een oude NO-GO wordt niet
stil verwijderd en een oude groene scan is geen scan van de huidige image.

Dit bord beantwoordt maar één vraag: gebruikt Eva voor ieder algemeen probleem
de beste onderhouden bouwsteen, of onderhoudt zij onnodig eigen code?

## Hoofdregel

Eva bouwt alleen zelf wat haar Eva maakt: identiteit, personen en groepen,
privacy, toestemming, budgetten, persoonlijkheid, het bewijs van een actie en
de controle of die actie echt is gelukt.

Modelverkeer, chattransport, workflows, geheugenopslag, externe tools,
spraak, browserbediening, dashboards, installatie en dependencyupdates horen
zoveel mogelijk uit actieve open-sourceprojecten te komen. Een eigen vervanger
mag alleen blijven na een gemeten vergelijking waarin hij aantoonbaar beter is.

Een losse proef of een genoemd GitHub-project telt niet als hergebruik. Het
telt pas wanneer de dependency de gebruikersroute werkelijk uitvoert én de
overlappende Eva-code is verwijderd.

## Besluit per begrijpelijk onderdeel

| Onderdeel | Toen gekozen route | Werkelijkheid op 1 september | Toenmalig besluit |
|---|---|---|---|
| Denken, tools en modelproviders | [Pydantic AI](https://github.com/pydantic/pydantic-ai) als `pydantic-ai-slim`, met alleen de gekozen officiële provider-SDK | `slim` bevat dezelfde agentkern zonder ongebruikte provider-, CLI- en UI-extra's; Eva pint de officiële OpenAI-SDK afzonderlijk. Pydantic AI `2.36.0` is de enige eigenaar van gewone modelgesprekken, streaming en toolkeuze. De exacte minimale Distroless-image streamde via echte Codex, koos een uitgestelde tool, vervolgde uit een receipt en herstelde na fysieke `SIGKILL` tijdens een echte externe providercall; SBOM en scan zijn 0 hoog/0 kritisch | **Exacte imagepariteit groen; toestandwijzigende providerproef en A/B-livegang nog open** |
| Werk hervatten na een crash | PostgreSQL met een onderhouden workflowmotor zodra die Eva's ambiguïteitsgrens bewaart | Stock DBOS `2.31.0` kan na een onduidelijke commit dubbel uitvoeren; de lokale rebase voegt 306 regels toe en verwijdert nul veilige workerregels | **DBOS voor deze worker `NO-GO`; Restate/Temporal/andere actuele kandidaten alleen promoveren als zij de verantwoordelijkheid aantoonbaar overnemen, onverklaarde overlap verwijderen en de crashproef winnen** |
| Langetermijngeheugen | [Hindsight](https://github.com/vectorize-io/hindsight) | Hindsight draait echt, maar Eva heeft daarnaast ongeveer 1.485 regels projectiecode | **Houden; adapter sterk verkleinen via officiële client/MCP** |
| Telegram | [aiogram](https://github.com/aiogram/aiogram) blijft de gevolgde kandidaat | De actuele teksttransportlaag is klein en synchroon; de aiogram-proef voegt 12 distributies, een async-bridge en een Pydantic-upgrade toe zonder Eva's ledger te vervangen | **Nu `NO-GO`; heropenen voor media, webhooks of async-native ingress** |
| Externe gereedschappen | Pydantic AI met runtime-ontdekte tools; [IBM ContextForge](https://github.com/IBM/mcp-context-forge) alleen als optionele gateway; officiële API's/SDK's achter verwisselbare providers | De brede 69-distributieproef is afgewezen. De actuele route houdt 38 pakketten en Eva's ledger/readback. Op de exacte image doorliep een echte IBM-provider uitvoering, fysieke `SIGKILL`, aparte readback en verse replay zonder dubbelen. De provider was alleen-lezen; een toestandwijzigende provider met afzonderlijke observatie en een tweede echte dienst blijven open | **Geen tweede toolframework; sluit de muterende crashpoort en promoveer daarna pas via A/B** |
| Voice | [Pipecat](https://github.com/pipecat-ai/pipecat) als eerste media-pipeline; [LiveKit Agents](https://github.com/livekit/agents) alleen als transportvergelijking; Wyoming blijft lokale STT/TTS-fallback | Losse voicebouwstenen werken, maar vormen nog geen product | **Pipecat-bake-off; eigen streaming/interruptlijm daarna verwijderen** |
| Browser en computer | [Playwright MCP](https://github.com/microsoft/playwright-mcp) tegenover [Browser Use](https://github.com/browser-use/browser-use); daarna precies één motor | Functionele proeven bestaan, maar de geteste images voldeden niet aan de releasepoort | **Eén veilige brongebouwde winnaar kiezen; geen eigen browserengine** |
| Subagents en onderzoek | Pydantic AI voor agentruns; een bewezen workflowmotor voor duurzame uitvoering | De actuele kandidaat heeft een grote eigen databaseworker en commandolaag; DBOS won de gemeten vervanging niet | **Bevroren tot een actuele kandidaat de verantwoordelijkheid aantoonbaar overneemt, onverklaarde overlap verwijdert en de eenmalige-call/crashproef wint** |
| Dashboard | [React-admin](https://github.com/marmelab/react-admin) boven Eva's bestaande eigenaar-API | React-admin `5.15.1` won de echte build- en browserproef van Refine: 200 locked packages, geen peerbotsing, nul auditbevindingen, geen externe browsercalls en geen browseropslag. De 252 kB gzip JavaScriptbundel is wel groter. De kandidaat is nog niet live en de 428 regels oude cockpitpresentatie bestaan nog | **React-admin geselecteerd; pas geslaagd wanneer de oude presentatie is verwijderd en dezelfde functies via A/B live zijn** |
| Installatie en updates | Bestaande packaging, Compose en A/B; [Renovate](https://github.com/renovatebot/renovate) voor voorstellen | A/B werkt, maar Renovate stond alleen in een submap en er is nog geen bewijs dat een Renovate-runner actief is | **Configuratie naar repositoryroot; pas ‘automatisch bijgehouden’ na een echte botrun** |
| SBOM en veiligheid | Trivy voor CVE's, Syft voor SBOM en Cosign voor ondertekening | Trivy en ondertekening worden gebruikt; SBOM- en updateketen zijn nog niet één automatische poort | **Samenvoegen tot één releasepoort; geen eigen scanner bouwen** |

## Waar dubbele verantwoordelijkheid moet verdwijnen

De actuele Pythonkern telt ongeveer 74.000 regels. De grootste concrete
verdubbelingskansen zijn:

1. de eigen algemene worker-, retry- en crashherstellaag tegen actuele
   workflowmotoren uitspelen; DBOS is voor deze route al gemeten `NO-GO`;
2. de Hindsight-adapter terugbrengen tot persoons-/privacybinding en de
   officiële client de opslag laten doen;
3. Pydantic AI de model-, tool- en streamingloop laten uitvoeren;
4. Pipecat de voice-pipeline laten dragen;
5. één browserprovider kiezen en alle alternatieve browserlijm verwijderen.

De eerste meting maakt dit concreet. Dit zijn te onderzoeken regels, niet
regels die blind weg mogen:

| Huidige eigen laag | Gemeten regels | Wat een dependency moet overnemen |
|---|---:|---|
| Telegram Bot API-adapter plus polleroperatie | 504 | De aiogram-bake-off is nu `NO-GO`: twaalf nieuwe distributies en async-lijm leveren voor tekst geen nettowinst. Dit wordt heropend zodra media/webhooks de vergelijking veranderen |
| Duurzame subagentstore, worker en service | 1.794 | queue, lease, retry, hervatten en workerlevenscyclus; grants en ambiguïteitsbewijs blijven |
| Hindsight-projectieadapter | 1.485 | opslag, recall, clientprotocol en retries; persoon-/groep-/consentbinding blijft |
| Gewoon modelgesprek en toolloop | bronvervanging loopt | Pydantic AI heeft deze verantwoordelijkheid in de bron overgenomen en de oude transportklasse is verwijderd; vier koude achtergrondmodelpaden gebruiken nog de oude HTTP-helper en moeten apart worden vervangen of verantwoord |
| Eigen MCP-client plus gatewayconfiguratie | 679 | catalogusontdekking, protocolsession en toolcall; Eva's grant, duurzame ledger en onafhankelijke readback blijven |

Een vervanging is pas geslaagd als de oude verantwoordelijkheid werkelijk is
overgenomen en de dubbele route verdwijnt. Het totaal aantal regels hoeft niet
te dalen wanneer de nieuwe bouwsteen aantoonbaar nieuwe kwaliteit of
functionaliteit toevoegt. Een extra framework naast dezelfde eigen laag zonder
apart doel is wel een mislukte vervanging.

Voor iedere vervanging geldt voortaan dezelfde eindvoorwaarde:

- dezelfde of betere gebruikersuitkomst;
- geen dode of onverklaard dubbele code en een duidelijke eigenaar voor iedere
  blijvende laag;
- recente release, meerdere onderhouders, duidelijke licentie en securityroute;
- exacte versie/digest, SBOM en nul hoge of kritieke scanbevindingen;
- upgrade én terugrol op gekopieerde data;
- Eva's identiteit, privacy, toestemming en controle achteraf blijven intact.

## Updates

Renovate mag alleen kleine updatevoorstellen maken. Iedere kandidaat doorloopt
daarna automatisch tests, imagebouw, SBOM, scan en een tijdelijke
upgrade-/downgradeproef. Productiepromotie blijft A/B en gebeurt nooit door de
dependencybot zelf.

De eerste echte uitvoering van die keten is nu groen voor OpenAI SDK `3.6.0`:
één hashlock maakte alle dependencymetadata, 38 wheels en de netwerkloze build;
de brede bouwimage werd met 5 kritieke en 29 hoge bevindingen afgewezen, waarna
de minimale runtime 0/0 scoorde en een echte Codex-beurt plus gezamenlijke
herstart doorstond. Daardoor is dit een werkende upgradepoort en geen papieren
afspraak. Dezelfde route is daarna opnieuw vanaf de volledig verzegelde actuele
commit uitgevoerd. De minimale image bleef 0/0, gaf in de volledige tijdelijke
negen-dienstengrafiek een exact echt modelantwoord en bleef na de gezamenlijke
database-/Hindsight-/coreherstart klaar. De A/B-operator kopieerde daarna beide
databases, voerde vóór de nieuwe writers de kandidaatmigratie uit en schakelde
de echte `/eva2`-route van poort `44711` naar `44710`. Jaaps echte
Telegrambinding antwoordde via de nieuwe SDK en herinnerde na een coreherstart
exact hetzelfde testwoord. Daarna liep ook de volledige terug→heen-proef. De
operator ontdekte daarbij dat het oude schema-installatieprogramma een nieuwere
versie terecht niet kende. De bestaande, transactionele Alembic-downgrade
controleert nu eerst of de nieuwe data zonder verlies oud-representabel zijn;
alleen dan start het oude slot. Beide omschakelingen hielden Eva1 groen en
Jaaps herinnering bleef exact behouden.

De Renovate-configuratie staat vanaf nu op de repositoryroot. Dat maakt haar
vindbaar, maar bewijst nog geen actieve bot. De status wordt pas groen nadat de
GitHub Renovate App of een vaste Renovate-runner een echte dashboardissue en
een testvoorstel heeft gemaakt.

De configuratie is inmiddels wel echt met Renovate `44.52.0` onder de vereiste
Node `24.11.0` uitgevoerd in volledige dry-run. De brede scan vond 99
dependencies in 34 bestanden; de eerdere smalle releasewatch vond exact de toen
gevolgde 14 componenten en concurrenten zonder waarschuwing of fout. Er is niets geschreven.
GitHub heeft op dit moment geen Renovate App, tokensecret, dashboard of Renovate-
PR en Actions heeft alleen leesrecht. Automatische voorstelvorming blijft dus
eerlijk geel totdat die externe runner bewust is geactiveerd.

`component-watch.json` volgt daarnaast de releases van de gekozen componenten
én van OpenClaw, Hermes en Agent Zero. Een nieuwe concurrentrelease krijgt het
label `benchmark-required` en heropent dus de vergelijking in plaats van stil
aan Eva voorbij te gaan.

De handmatige upstreamcontrole is hoogstens dertig dagen geldig. Daarna maakt
de dependencycheck iedere nieuwe releasekandidaat rood, ook wanneer de Renovate-
runner zelf stil zou zijn gevallen. Een nieuwe concurrentversie maakt bovendien
de oude 22-categorievergelijking direct ongeldig totdat alle gehashte resultaten
opnieuw aan die exacte tag en commit zijn gebonden.

Op 31 augustus 2026 volgt het manifest 31 upstreamcomponenten plus OpenClaw,
Hermes en Agent Zero. Alle 34 tags zijn aan een exact receipt gebonden. De
dekking bevat nu niet alleen de grote frameworks, maar ook alle elf directe
packages uit de live kern en de gebruikte build-, scan-, SBOM- en
ondertekenkandidaten. Dat bewijst dat de onderhoudsblik compleet is voorbereid;
het bewijst niet dat iedere nieuwste versie al veilig kan worden gepromoveerd of
dat Eva de drie concurrenten verslaat.

### Live dependencies en veilige upgradegroepen

De actuele live kern heeft elf directe runtimeafhankelijkheden. Acht staan al op
de gemelde actuele upstreamversie: OpenAI SDK, Alembic, Cryptography, FastAPI,
Psycopg, PyNaCl, SQLAlchemy en Setuptools. Drie blijven bewust op hun bewezen
live versie totdat een kleine, afzonderlijke kandidaat groen is:

| Upgradegroep | Huidig | Kandidaat | Waarom afzonderlijk? |
|---|---|---|---|
| Contractvalidatie | Pydantic `2.12.5` | `2.13.5` | Geen stil meeliften met Pydantic AI of MCP; eerst alle Eva-contracten en providers apart controleren |
| ASGI-transport | Uvicorn `0.41.0`, WebSockets `17.0.1` | `0.52.4`, `17.1` | Streaming, disconnects, time-outs en 200-beurtenlatency horen bij één transportproef |
| Geheugensidecar | Hindsight `0.9.1` | `0.9.2` | Een kopie van de geheugendatabase, persoonsisolatie, gezamenlijke herstart en terugrol zijn verplicht |

De Pydantic AI/MCP-lijn is geen vierde routine-update. De huidige proef legt 46
extra distributies en een tweede HTTP-familie naast bestaande routes. Zij blijft
daarom `HOLD` totdat één dependency per verantwoordelijkheid wint en de oude
route in dezelfde promotie verdwijnt. Harness beweegt alleen met die agentstack:
de geteste `0.25.0` loopt inmiddels achter op `0.27.0`, en upstream staat bij de
0.x-lijn expliciet brekende wijzigingen tussen minorversies toe.

Build- en securitymiddelen tellen eveneens als beheerde dependencies. Trivy
`0.74.0` is versie- en digestgebonden. Syft heeft nu alleen een digestbinding en
moet nog aan release `1.51.0` worden geattesteerd. Cosign `3.1.2` heeft nog geen
reproduceerbare lokale binary- of imagepin. De Python-, Distroless- en
Chainguard-basisimages zijn wel digestgebonden, maar hun actuele upstreamtag is
bewust `unverified`: geen gok wordt als actuele versie opgeschreven.

De volledige machineleesbare tabel, bronhashes, veilige batches en expliciete
`HOLD`-redenen staan in
[`live-dependency-maintenance-audit-2026-08-31.json`](../evidence/product/live-dependency-maintenance-audit-2026-08-31.json).

## Altijd voorop blijven

“Beter dan de concurrentie” is geen eenmalig vinkje. Iedere beoogde
productrelease en iedere grote vervanging moet daarom opnieuw bewijzen:

1. dezelfde actuele, openbare taken tegen Eva, OpenClaw, Hermes en Agent Zero;
2. dezelfde modelklasse, gereedschappen, tijd- en kostenlimieten;
3. ruwe trajecten en de officiële scorer, zodat een uitslag controleerbaar is;
4. winst of gelijkspel op betrouwbaarheid, privacy, herstel en gebruikerswerk;
5. geen achteruitgang in snelheid, kosten, geheugenkwaliteit of
   beheersbaarheid; groei zonder eigenaar, functie of verwijderde overlap is
   een verlies, omvang op zichzelf niet.

Eva hoeft niet ieder nichecijfer te winnen. Zij mag wel pas “beter” heten als
zij de afgesproken gezinsassistenttaken over de hele linie wint en op geen
veiligheids- of privacygate verliest. Een concurrent die een categorie beter
doet, heropent automatisch de dependencykeuze voor dat onderdeel.

De architectuur blijft daarom verwisselbaar:

- contracts en persoons-/privacyregels horen bij Eva;
- providers en sidecars kennen Eva's interne database niet;
- providerdata wordt in providerneutrale contracten bewaard;
- een nieuwe versie draait eerst op een kopie, daarna in het vrije A/B-slot;
- terugrollen gebruikt het vorige artifact en een vooraf geteste
  data-downgrade of forward-fix;
- nooit twee concurrerende frameworks tegelijk in de productroute houden.

Home Assistant is binnen dit bord dus geen gekozen Eva-component. Het is
alleen één huidige testadapter. Dezelfde Eva-kern moet zonder bronwijziging een
agenda-, mail-, browser-, bestands- of andere MCP-provider kunnen laden.

De gatewayvergelijking koos ContextForge `v1.0.8`; de eerste geïsoleerde
integratieproef is nu groen. De stabiele exacte image is scan-schoon en heeft
registry, proxy, beheer-UI en authenticatie al zelf. Eva voegt daar geen eigen
gatewaycode aan toe. Alleen een kleine file-secretwrapper blijft nodig omdat de
upstreamcontainer zijn drie startsleutels nog uitsluitend als procesvariabelen
accepteert. Docker MCP Gateway `v0.43.3` blijft reserve totdat zijn
prerelease-status en onvolledige Go-dependencyscan zijn opgelost.
Een synthetische MCP-provider is door Eva's bestaande generieke contract,
duurzame PostgreSQL-tijdlijn en aparte readback gegaan. Een nieuwe
runtime-instantie las dezelfde receipt terug zonder de provider opnieuw aan te
roepen. Dezelfde providerneutrale Eva-image is daarna via de verliesloze A/B-
route live gezet. Haar modelantwoord, gesprek na coreherstart en een echte
externe read plus `STATE_VERIFIED` zijn groen. ContextForge zelf staat nog niet
aan in het beheerde slot en blijft dus releasebewijs, geen actieve gateway.
ContextForge wordt eerst eigenaar-only gebruikt; de open upstreamkwestie `#6015` verhindert
dat zijn eigen RBAC nu al Eva's persoonsgrens vervangt.

De scheiding is daarna ook zonder Home Assistant bewezen. Op een minimale,
scan-schone kandidaat koos het echte model zelfstandig een externe IBM-
tijdtool uit de catalogus. ContextForge voerde haar uit, Eva deed een aparte
readback en een nieuwe runtime-instantie deed bij replay nul providercalls. De
generieke MCP-aansluiting is broncode in Eva's providergrens; de concrete tools
en hun aanbieders zijn runtimeconfiguratie. Externe Python-providerpakketten
blijven optioneel via entry points laadbaar.

Dezelfde exacte image is daarna gestopt met een fysieke `SIGKILL`, nadat de
echte tijdprovider al had geantwoord maar vóór Eva dat resultaat had
vastgelegd. Een vers proces deed precies één readback; een derde vers proces
deed nul extra calls en las dezelfde receipt. Dit sluit de echte read-only
providercrashpoort. Omdat uitvoering en observatie op de draad nog dezelfde
MCP-tool gebruiken, blijft een toestandwijzigende provider met afzonderlijke
observatie de volgende eerlijke poort.

Hiermee wordt “top of the edge” een terugkerende releasepoort in plaats van een
marketingclaim van vandaag.
