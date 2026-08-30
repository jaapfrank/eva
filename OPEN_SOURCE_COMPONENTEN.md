# Eva 2 — open-source componentenbord

Bijgewerkt: 30 augustus 2026.

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

| Onderdeel | Gekozen route | Werkelijkheid nu | Besluit |
|---|---|---|---|
| Denken, tools en modelproviders | [Pydantic AI](https://github.com/pydantic/pydantic-ai) met de officiële provider-SDK's | OpenAI SDK `3.6.0` draait nu op de echte `/eva2`-route. De verzegelde image is uit 38 vaste wheels zonder bouwnetwerk gebouwd, scant 0/0 en is met gekopieerde data en een vooraf uitgevoerde schemamigratie via A/B gepromoveerd. Jaaps echte binding, modelantwoord en geheugen na een coreherstart zijn groen | **SDK live; nu Pydantic de toolloop laten overnemen en dubbele transportcode verwijderen** |
| Werk hervatten na een crash | PostgreSQL met een onderhouden workflowmotor zodra die Eva's ambiguïteitsgrens bewaart | Stock DBOS `2.31.0` kan na een onduidelijke commit dubbel uitvoeren; de lokale rebase voegt 306 regels toe en verwijdert nul veilige workerregels | **DBOS voor deze worker `NO-GO`; Restate/Temporal/andere actuele kandidaten alleen promoveren als zij netto code verwijderen en de crashproef winnen** |
| Langetermijngeheugen | [Hindsight](https://github.com/vectorize-io/hindsight) | Hindsight draait echt, maar Eva heeft daarnaast ongeveer 1.485 regels projectiecode | **Houden; adapter sterk verkleinen via officiële client/MCP** |
| Telegram | [aiogram](https://github.com/aiogram/aiogram) blijft de gevolgde kandidaat | De actuele teksttransportlaag is klein en synchroon; de aiogram-proef voegt 12 distributies, een async-bridge en een Pydantic-upgrade toe zonder Eva's ledger te vervangen | **Nu `NO-GO`; heropenen voor media, webhooks of async-native ingress** |
| Tool- en plug-insysteem | MCP plus officiële provider-API's/SDK's achter één generiek catalogus-, uitvoer- en readbackcontract | Schema, antwoordinstructie, gewone reads, effecten, automatiseringen, migratieplanning en HACS-repository-/pakketbeheer komen uit verwisselbare poorten. Agenda-effect, automatisering, twee reads en drie beheerflows doorliepen dezelfde PostgreSQL- en readbackroute zonder kernbranch. Applicatie, contracten, modeladapter, gesprekspersistence en runtime staan op nul providerspecifieke termen | **Structurele grens groen; echte apparaatmigratie en tweede echte externe provider blijven praktijkgates** |
| Voice | [Pipecat](https://github.com/pipecat-ai/pipecat) als eerste media-pipeline; [LiveKit Agents](https://github.com/livekit/agents) alleen als transportvergelijking; Wyoming blijft lokale STT/TTS-fallback | Losse voicebouwstenen werken, maar vormen nog geen product | **Pipecat-bake-off; eigen streaming/interruptlijm daarna verwijderen** |
| Browser en computer | [Playwright MCP](https://github.com/microsoft/playwright-mcp) tegenover [Browser Use](https://github.com/browser-use/browser-use); daarna precies één motor | Functionele proeven bestaan, maar de geteste images voldeden niet aan de releasepoort | **Eén veilige brongebouwde winnaar kiezen; geen eigen browserengine** |
| Subagents en onderzoek | Pydantic AI voor agentruns; een bewezen workflowmotor voor duurzame uitvoering | De actuele kandidaat heeft een grote eigen databaseworker en commandolaag; DBOS won de gemeten vervanging niet | **Bevroren tot een actuele kandidaat netto code verwijdert en de eenmalige-call/crashproef wint** |
| Dashboard | Een bestaand web-/adminframework boven Eva's API | De cockpit bevat nog veel eigen presentatiecode | **Bestaande UI hergebruiken; Eva houdt alleen haar API en unieke schermlogica** |
| Installatie en updates | Bestaande packaging, Compose en A/B; [Renovate](https://github.com/renovatebot/renovate) voor voorstellen | A/B werkt, maar Renovate stond alleen in een submap en er is nog geen bewijs dat een Renovate-runner actief is | **Configuratie naar repositoryroot; pas ‘automatisch bijgehouden’ na een echte botrun** |
| SBOM en veiligheid | Trivy voor CVE's, Syft voor SBOM en Cosign voor ondertekening | Trivy en ondertekening worden gebruikt; SBOM- en updateketen zijn nog niet één automatische poort | **Samenvoegen tot één releasepoort; geen eigen scanner bouwen** |

## Wat eerst uit de eigen code moet verdwijnen

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
| Modelgesprek plus eigen Responses-transport | 2.835 | providertransport, streaming, toolloop en usage; identiteit, mandaat en eindantwoordbeleid blijven |

Een vervanging is pas geslaagd als deze meting netto daalt. Een extra framework
naast dezelfde eigen laag is juist een mislukte vervanging.

Voor iedere vervanging geldt voortaan dezelfde eindvoorwaarde:

- dezelfde of betere gebruikersuitkomst;
- minder eigen productregels;
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
dependencies in 34 bestanden; de smalle releasewatch vond exact alle 14
componenten en concurrenten zonder waarschuwing of fout. Er is niets geschreven.
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

Op 30 augustus 2026 zijn alle elf gevolgde componentreleases en de gevolgde
releases van OpenClaw, Hermes en Agent Zero rechtstreeks met GitHubs actuele
releasegegevens vergeleken. Alle veertien tags kwamen overeen. Dat bewijst dat
de vergelijking actueel is voorbereid; het bewijst nog niet dat Eva de drie
concurrenten verslaat.

## Altijd voorop blijven

“Beter dan de concurrentie” is geen eenmalig vinkje. Iedere beoogde
productrelease en iedere grote vervanging moet daarom opnieuw bewijzen:

1. dezelfde actuele, openbare taken tegen Eva, OpenClaw, Hermes en Agent Zero;
2. dezelfde modelklasse, gereedschappen, tijd- en kostenlimieten;
3. ruwe trajecten en de officiële scorer, zodat een uitslag controleerbaar is;
4. winst of gelijkspel op betrouwbaarheid, privacy, herstel en gebruikerswerk;
5. geen achteruitgang in snelheid, kosten, geheugenkwaliteit of eigen codeomvang.

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

Hiermee wordt “top of the edge” een terugkerende releasepoort in plaats van een
marketingclaim van vandaag.
