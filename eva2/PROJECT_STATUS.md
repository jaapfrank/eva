# Eva 2 — korte projectstatus

Laatst bijgewerkt: 30 augustus 2026, 03:56 CEST

## Heel kort

Eva2 werkt echt naast Eva1. Alleen een bericht met `/eva2` gaat naar Eva2.

De nieuwe DBOS-loze Eva2 staat nu op poort `44713` achter de gewone
`/eva2`-route. Zij praat via de bestaande Codex-koppeling, heeft haar eigen
persoonlijkheid, bewaart gesprekken in PostgreSQL en gebruikt Hindsight voor
langetermijngeheugen.

De route is verliesloos heen, terug en opnieuw heen geschakeld. Een nieuw
testfeit kwam na iedere databasekopie exact terug. Eva1 bleef gezond.

Ook de volledige omvangs- en imagepoort PC01 is groen. De actuele bronteller
staat op 74.817 productregels,
10 directe dependencies, 31 imagelagen en nul hoge of kritieke scanbevindingen.
De vorige Eva2-route op poort `44712` draait gezond en blijft de directe
terugval.

De echte Windows/WSL-rebootproef is opnieuw bewapend op deze beheerde route.
De eerste marker kwam pas na ongeveer 94 seconden in Hindsight; met de begrensde
wachttijd van 180 seconden werd de nieuwe marker wel aantoonbaar gevonden.

De nieuwe streamingkandidaat werkt echt: een gewoon antwoord kwam in 32
tekststukjes binnen en was exact gelijk aan het eindantwoord. Een huisvraag
koos eerst alleen de veilige leesactie en streamde pas daarna het antwoord.
Daarbij zijn twee echte fouten in de Codex-toolplanner gevonden en hersteld.

De nieuwe live canary gebruikt geen aparte plannerbeurt meer. Het model krijgt
één vaste, uitgestelde gereedschapscatalogus en haalt alleen het passende
gereedschap op wanneer dat nodig is. Een gewone echte beurt antwoordde exact
`werkend`; een echte alleen-lezen huisvraag antwoordde terecht dat de spotjes
overkapping uit staan. De image is zonder bouwnetwerk gemaakt en scant op 0
kritisch en 0 hoog. Na omschakeling antwoordde Eva1's echte `/eva2`-router
exact `live`; na een herstart wist Eva2 nog exact `koraalvink`. De vorige
route op poort `44710` bleef daarbij gezond. Na de nieuwste omschakeling is
poort `44712` de directe rollback.

Eva2 is nog geen volledige vervanger voor Eva1. Brede huisbediening, volledige
voice, algemene browserbediening, een consumenteninstallatie en de lange
praktijktest zijn nog niet klaar.

De nieuwe geheugencache houdt een gewone privévraag niet meer onnodig op. De
eerste volledige 200-beurtenproef vond daarna twee echte provider-time-outs.
De live versie probeert zo'n gewone, nog effectvrije modelcall nu eenmaal kort
opnieuw. De herhaalde proef gaf 200/200 antwoorden; vier herstelde pogingen
kregen eerlijk onbekend tokengebruik. De mediaan was 1,00 seconde, maar p95
2,285 seconde. Betrouwbaarheid is dus beter; de latencygate blijft rood.

## Wat werkt al

- Eva1 blijft de gewone standaardservice.
- `/eva2` gaat nu naar de nieuwe 35-pakket-Eva2 op poort `44713`.
- Eva2 gebruikt alleen een korte, vernieuwbare toegang uit Eva1's Codex-login.
  Zij krijgt de volledige login en refresh-token niet.
- Eva2 heeft een warme, jonge, nieuwsgierige, grappige en eigenwijze identiteit.
- Gesprekken blijven bestaan na een herstart.
- Jaap en Lola hebben ieder een eigen profiel en geheugen.
- Een echte Telegramproef met Jaap, Lola en een groep is groen.
- Groepsinformatie wordt vóór toestemming niet naar het model of privégeheugen
  gestuurd. Toestemming intrekken sluit alleen die persoon af.
- PostgreSQL bewaart het gesprek en Hindsight bewaart langetermijngeheugen.
- Geheugen bleef aanwezig na een gezamenlijke herstart van kern, Hindsight en
  beide databases.
- Eén persoonsgebonden wisopdracht verwijdert de gekozen beurt uit PostgreSQL,
  Hindsight en de wachtende geheugenkopie. Een andere persoon kan dat niet.
- Model-, antwoord-, geheugen- en dagelijkse kosten-/groeibudgetten worden
  begrensd. Onbekende autonome kosten worden geweigerd.
- Eva2 kan Home Assistant lezen.
- Eva2 kan één vaste lamp veilig uitzetten en controleert daarna apart of die
  echt uit staat.
- Bij een onduidelijke lampnaam vraagt Eva welke lamp je bedoelt. Zij gokt niet.
- Andere lampen en `aanzetten` worden vóór Home Assistant geweigerd.
- De kern, Hindsight en beide PostgreSQL 18-images zijn lokaal 0 kritisch en
  0 hoog gescand.
- De huidige kern gebruikt één verzegelde catalogus met uitgestelde
  gereedschappen. Een gewone vraag doet geen aparte planneroproep; alleen een
  passend gereedschap wordt opgehaald wanneer dat nodig is.
- De volledige gesprekroute, echte Codex-beurt en echte alleen-lezen Home
  Assistant-vraag zijn groen. Dezelfde route bleef na een kernherstart gezond.
- Tweehonderd gewone beurten gaven 200/200 antwoorden. Vier providerstalls
  werden eenmaal veilig hersteld; p95 eerste inhoud was nog 2,285 seconde.
- De live router antwoordde na de nieuwste omschakeling exact `RETRYLIVE`. Na
  de kandidaat-herstart kwam het eerder opgeslagen woord `granaatveer` exact
  terug.
- Een aparte kandidaat kan een toolvrij antwoord veilig stukje voor stukje
  doorgeven. De 32 stukjes vormden exact hetzelfde uiteindelijke antwoord.
- Een echte Home Assistant-vraag hield tekst dicht tijdens de toolkeuze en gaf
  pas daarna 11 veilige antwoordstukjes door.
- Het eigenaar-dashboard geeft met de juiste private binding HTTP 200 en zonder
  binding HTTP 401.
- De installatiewizard kan identiteit en basiskeuzes bewaren, hervatten,
  ondertekenen, activeren en terugzetten.
- De wizard kan model, Telegram, Home Assistant en AgentMail afzonderlijk en
  alleen-lezen controleren met synthetische providers.
- Een kleine niet-lege installatie kan naar een nieuwe database worden
  hersteld. Oude connectors blijven daarbij bewust uit tot ze opnieuw gekoppeld
  zijn.
- Slot 3 en slot 4 hebben ieder eigen databases. De nieuwe operator stopt eerst
  alle schrijvers, maakt een gecontroleerde Eva1-back-up, kopieert beide
  databases en vergelijkt aantallen, schema en inhoud vóór de route wijzigt.
- De nieuwe image ging groen 4→3, 3→4 en opnieuw 4→3. Het testfeit dat op slot 3
  was geschreven kwam exact terug op slot 4 en daarna weer op slot 3.
- Eva1 is actief, heeft nul herstarts en alle vijf controles geven HTTP 200.
- De lange Eva1-vastloper is gevonden en live hersteld. Eén oude bewijsquery
  kostte 24,6 seconden op de Node-hoofdthread; dezelfde selectie kost nu circa
  0,05-0,15 seconde en geeft exact dezelfde 40 regels terug. Na de herstart
  bleven drie echte autonome modelbeurten bereikbaar.
- Slot 3 is actief en gezond. De oude slot-4-kern staat stil; zijn databases
  blijven gezond beschikbaar voor een later gesynchroniseerde terugrol.
- Oude proefproviders en de losse Telegram-testlauncher zitten niet meer in de
  gewone Eva2-runtime. De aparte Telegram-testimage is daarna echt opnieuw
  zonder bouwnetwerk gemaakt en kan haar twee diagnoseprocessen laden.
- DBOS is nu ook uit de officiële releaselock en eindimage verwijderd.
- De nieuwe eindimage bevat exact 35 runtimepakketten. `websockets` en PyNaCl
  blijven bewust staan, omdat Eva ze zelf gebruikt.
- `pip` en `wheel` zijn na de bouw niet meer in de eindimage aanwezig.
- De nieuwe image is volledig zonder bouwnetwerk gemaakt en scant opnieuw
  0 kritisch en 0 hoog.
- Op een verse PostgreSQL-database installeerde zij het hele schema. Na een
  echte databaseherstart deed zij terecht geen migratie dubbel.
- Dezelfde image praatte daarna echt via de korte Codex-lease en schreef twee
  documenten naar een volledig verse Hindsight-database.
- Na een gezamenlijke herstart van kern, Hindsight en beide databases wist zij
  haar vorige antwoord exact. Hindsight behield zijn bank en documenten.
- De twee minimale testzinnen leverden bewust nog geen Hindsight-geheugenunit
  op. Dit bewijst dus wel de echte opslag- en modelroute, maar nog niet de
  kwaliteit van semantische herinneringen.
- De automatische rebootcontrole is nu opnieuw voor actief slot 3 en terugvalslot
  4 bewapend. Zij schreef een private testherinnering naar Hindsight.
- Een controle op dezelfde Windows-start werd terecht geweigerd en maakte geen
  vals bewijsbestand. De echte Windows-reboot moet van buiten Codex komen.
- Negentien databaseonderdelen gebruiken nu één gedeelde, identieke
  rol- en persoonsafscherming. Daardoor verdwenen netto 149 productregels;
  41 gewone tests, alle 95 actionchain-databasegevallen en een aparte
  delegated-work-scopecontrole bleven groen.
- De read- en effectdispatcher delen nu ook hun autoriteits- en call-start-
  opslag. Zestien echte PostgreSQL-dispatchtests bleven groen; netto verdwenen
  nog 23 productregels.
- Read en effect delen nu ook de neutrale grant- en envelopopbouw. Een nieuwe
  vaste UTC-helper vervangt tientallen letterlijke kopieën. Alle 95 echte
  actionchain-databasegevallen en 43 snelle tests zijn groen; netto verdwenen
  opnieuw 96 productregels.
- De 35 hierdoor geraakte Pythonbestanden zijn daarna normaal geformatteerd.
  Hun AST bleef 35/35 identiek; overbodige witruimte en wrapping scheelden nog
  69 productregels zonder uitvoerbaar gedrag te veranderen.
- De bewijscontrole voor een voorbereide read/effect-workerclaim gebruikt nu
  één implementatie. Na herstel van een direct gevonden importfout waren alle
  95 echte PostgreSQL-actionchaintests groen; dit scheelde 61 productregels.
- Ook het autoriseren en duurzaam opslaan van een read/effect-poging gebruikt
  nu één gedeelde scope-, bevoegdheids-, grant- en tijdlijngrens. Vier gerichte
  en alle 95 PostgreSQL-actionchaintests zijn groen; netto verdwenen opnieuw
  48 productregels.
- De terminale read/effect-resultaatopslag deelt nu ook de PostgreSQL-scope,
  ledger, evidence-write/read en tijdlijnpayloadcontrole. Zestien gerichte en
  alle 95 PostgreSQL-actionchaintests zijn groen; netto verdwenen nog 24
  productregels.
- Persoonlijke exploratie, prototypebouw en releasevoorbereiding delen nu één
  lease-, herstel- en claimprotocol. Alle drie normale claims en alle drie
  verlopen-leaseroutes zijn gericht getest; samen met de bestaande contracten
  28/28 groen. De grootste runtimefile werd netto 64 productregels kleiner.
- Diezelfde drie persoonlijke werkstappen delen nu ook afronden, tonen en
  fouten opslaan. De volledige route liep echt door een tijdelijke PostgreSQL
  16.13-database tot `release_ready`, met precies tien verwachte gebeurtenissen.
  Alle 29 gerichte tests zijn groen en er verdwenen netto nog 33 productregels.
- Het laatste persoonlijke prototype en de laatste release gebruiken nu ook
  één persoonsgebonden zoekroute. Een echte databaseproef vond daarbij dat het
  prototype na `release_ready` onzichtbaar werd. Dat is hersteld: prototype en
  release blijven terug te vinden, terwijl een andere persoon niets krijgt.
  Alle 33 gerichte tests zijn groen en er verdwenen netto nog 15 productregels.
- Tien actionchainstores delen nu één PostgreSQL-, scope-, rol- en
  ledgeropstart. Hun elf verschillende foutmeldingen blijven exact behouden.
  48 snelle controles en alle 95 echte PostgreSQL 16.13-actionchaintests zijn
  groen; netto verdwenen nog 135 productregels.
- Veertien duurzame gesprek-ID's gebruiken nu één canonieke afleiding. De oude
  en nieuwe code gaven voor twee verschillende beurten alle 28 ID's bytegelijk
  terug. Dertig gesprekactietests zijn groen; netto verdwenen 17 productregels.
- Gespreksjournal, geheugenoutbox en fysieke geheugenquota gebruiken nu dezelfde
  fail-closed PostgreSQL-scope als de andere stores. Een vreemde bestaande
  tenantbinding kan niet meer stil worden overschreven. Zes snelle en 46 echte
  PostgreSQL 16.13-tests zijn groen; netto verdwenen 25 productregels.
- Zes installer-workflows gebruiken nu één eigenaar-lock. Acht mapcontroles en
  zeven UTC-controles delen eveneens één fail-closed implementatie. Alle 186
  installer-, wizard-, API- en connectorproeven zijn groen; netto verdwenen
  opnieuw 141 productregels.
- Het installatieprofiel en de budgetten gebruiken nu ook dezelfde veilige
  klaarzet- en bevestigingsroute. Alle 186 installatieproeven bleven groen en
  er verdwenen nog 44 productregels.
- De releasecontrole schrijft niet langer dezelfde 239 bronpaden en 130
  testpaden ook nog eens in Python uit. Twee vaste sethashes houden ontbrekende,
  extra, gewijzigde en verkeerd geordende paden fail-closed. De gerichte proef
  is groen; hiermee verdwenen netto nog 360 productregels.
- Negentien modeltools en hun zestig velden delen nu één strict tool- en
  schemabouwer. In de echte 35-pakketimage bleef de volledige catalogus
  canoniek bytegelijk. Alle 44 modeltests zijn groen, met één bestaande skip;
  netto verdwenen opnieuw 197 productregels.
- Ook de vijf losse tools voor geheugenkeuze, repositoryvoorstel, prototype,
  verkooptekst en een passieve huisprikkel gebruiken diezelfde bouwers. Hun vijf
  afzonderlijke hashes bleven in de echte image gelijk; dit scheelde nog 74
  productregels.
- Zeventien connectorfouten gebruiken nu één gebonden antwoordroute. Alle 299
  connectortests zijn groen; dit verwijderde nog 118 productregels zonder het
  connectorprotocol of de bewijsinformatie te veranderen.
- Lezen, handelen en de controle achteraf gebruiken nu één herstartveilige
  connectoruitvoering. Alle 106 actionchain-databaseproeven zijn groen; dit
  scheelde nog 40 productregels.
- Oude model-, lees- en effectgeschiedenis gebruikt bij herstel nu één veilige
  omzetter. Alle 123 portable tests en drie echte PostgreSQL-writes zijn groen;
  dit scheelde nog 58 productregels.
- Elf LocalTuya-API-routes delen nu dezelfde begrensde veldcontrole en drie
  goedkeuringsroutes dezelfde foutafhandeling. Alle 64 relevante API-tests zijn
  groen; dit scheelde nog 64 productregels.
- Stoppen en afronden van uitbesteed werk gebruiken nu dezelfde database-lock.
  Alle 48 gerichte contract- en PostgreSQL-tests zijn groen. Dit scheelde vier
  productregels en één extra databasequery per stop of afronding.
- De runtime leest gewone waarden, booleans, lijsten en file-only secrets nu via
  gedeelde controles. Alle 107 bootstraptests zijn groen; dit scheelde nog 28
  productregels zonder een instelling of veiligheidsgrens te versoepelen.
- De drie Playwright-bestanden van de afgewezen browserproef zitten niet langer
  in Eva's runtime. Ze blijven als testfixtures bestaan en alle 299
  connectortests zijn groen. Een nieuwe netwerkloze wheel bevat nul van deze
  proefmodules; hierdoor verdwenen 996 productregels en drie productbestanden.
- Ook de oude handgeschreven MCP-client had nul productgebruikers. Hij blijft
  bytegelijk als testfixture, maar zit niet meer in de runtimewheel. Alle 299
  connectortests zijn opnieuw groen; dit verwijderde nog 415 productregels.
- Drie offline kandidaatimagebouwers stonden onnodig in de runtime. Ze blijven
  volledig beschikbaar onder `tools/`; 16 build- en smoketests zijn groen en
  de netwerkloze wheel bevat ze niet. Dit scheelde nog 990 productregels.
- Ook wheelhousecontrole, offline-preflight en release-inputcontrole zijn nu
  bouwgereedschap in plaats van runtimecode. Veertig gerichte tests zijn groen;
  dit verwijderde nog 1.758 productregels uit de runtimewheel.
- LocalTuya-pakket- en repositoryresultaten delen nu één receiptcontrole. Alle
  30 gesprekactietests zijn groen; dit scheelde nog acht productregels.
- De duurzame toelating van die twee LocalTuya-stappen en de eindcontrole van
  huisacties en automatiseringen gebruiken nu ook ieder één gedeelde route.
  Dezelfde 30 tests zijn groen; dit scheelde nog vijf productregels.
- De ongebruikte aparte switch-wrapper is verwijderd. Licht en switch gaan
  intern nu direct door dezelfde bestaande veiligheidsroute; 61 gesprek- en
  API-tests zijn groen en dit scheelde nog 30 productregels.
- De runtime maakte dezelfde persoons- en installatiegebonden taakopslag elf
  keer apart. Dat loopt nu via één kleine interne constructor; 102 gerichte
  tests zijn groen en dit scheelde nog 29 productregels.
- Eenvoudige en geavanceerde huisautomatiseringen houden hun eigen veilige
  configuratiebouw, maar delen nu de identieke uitvoer- en bewijsstaart. Alle
  68 gerichte tests zijn groen; dit scheelde nog zeven productregels.
- Gewone modelantwoorden en webonderzoek delen nu één begrensde decoder voor
  SSE-framing, UTF-8 en JSON. Acties en bronverwerking blijven apart; alle 68
  modeltests zijn groen en dit scheelde nog zestien productregels.
- De runtime maakte dezelfde Stripe-publicatieopslag zes keer apart. Plannen,
  goedkeuren, publiceren, webhook en download delen nu één scopegebonden
  constructor; 37 tests zijn groen en dit scheelde nog negen productregels.
- De gesprekcore maakte advanced-task- en duurzame-doelopslag respectievelijk
  vijf en vier keer apart. Beide gebruiken nu één aan de huidige beurt gebonden
  constructor; 54 tests zijn groen en dit scheelde nog veertien productregels.
- Afgeronde lees- en effectpogingen delen nu één strenge hersteldecoder voor
  grants, hashes, scopes en kolommen. Hun eigen tabellen, contracten en
  eindstaten blijven apart; 123 portable-tests zijn groen en dit scheelde 71
  productregels.
- Read- en effectvoortgang delen nu één controle voor vrijgegeven afhankelijke
  stappen. De volledige 106-case PostgreSQL-action-chainreeks en zeven
  applicationtests zijn groen; dit scheelde nog 26 productregels.
- Read- en effectreplay zoeken hun laatst voorbereide child-event nu via één
  scopegebonden query. Veertien echte PostgreSQL-cases zijn groen; dit scheelde
  nog tien productregels.
- Terminale read- en effectresultaten delen nu één parser voor de drie exact
  toegestane connectorcontracten. Een eerste importcyclus werd vóór commit
  gevonden en hersteld; 22 echte PostgreSQL-cases zijn groen en dit scheelde
  acht productregels.
- Telegramgroeptoestemming, groepswaarneming, bezorging en duurzame doelen
  maken hun vaste tijdlijnidentiteit nu op één plek. De bestaande ID's en vier
  eigen foutmeldingen bleven gelijk; 45 tests zijn groen en dit scheelde vijf
  productregels.
- Gewone modelgesprekken en begrensde subagents delen nu één strenge controle
  van hun OpenAI-endpoint. De volledige kandidaatproef vond ook een late socket
  na een time-out; die wordt nu alsnog gesloten. Alle 40 tests zijn groen en
  dit scheelde acht productregels.
- Secretinvoer en providercontrole in de installatiewizard delen nu één
  begrensde JSON-ingang. Hun verschillende groottelimieten en foutmeldingen
  bleven gelijk; alle 29 API-tests zijn groen en dit scheelde tien productregels.
- Lezen, handelen en de aparte controle achteraf delen nu één vaste executor
  voor de providerprocesgrens. Hun eigen autorisatie, opslag en foutsoort blijven
  apart; alle 106 echte PostgreSQL-cases zijn groen en dit scheelde 29 regels.
- Dezelfde drie routes delen nu ook hun identieke PostgreSQL-, ledger- en
  dispatcheropbouw. Read, effect en readback houden ieder hun eigen preparer,
  bevoegdheid en fouttekst; 106 echte cases zijn groen en dit scheelde 34 regels.
- Twee oude action-chain-writers van samen 766 regels hadden geen enkele
  productgebruiker en werden alleen door vijf historische tests geïmporteerd.
  Ze staan nu eerlijk bij testondersteuning; hun 37 PostgreSQL-cases blijven groen.
- Ook de 244-regelige oude effect-readback-resultaatstore had nul
  productgebruikers. Zij staat nu bij testondersteuning; haar 29 lokale
  PostgreSQL-cases blijven groen.
- De vier historische browsergevallen zijn daarna opnieuw in hun bedoelde,
  volledig gesloten Dockeromgeving uitgevoerd. Alle 4 zijn groen: één echte
  klik met aparte controle, twee crashgevallen zonder tweede klik en stoppen
  na toestemming zonder klik. De browser had geen hostpoort of internetroute.
- De 223-regelige eenmalige Eva1-geheugenproef had eveneens nul
  runtimegebruikers. Zij staat nu bij de operator-tools; 19 lokale en twee echte
  PostgreSQL-proeven blijven groen zonder Eva1-geheugen te wijzigen.
- De 1.219-regelige portable-restorewriter had nul runtimegebruikers en alleen
  zeven testgebruikers. Hij staat nu bij de operator-tools; 34 lokale en vijf
  echte PostgreSQL-herstelgevallen blijven groen. De aparte herstelbevoegdheid
  blijft wel in Eva's kern.
- Ook de 676-regelige portable-exportpublisher had nul runtimegebruikers en
  alleen vier testgebruikers. Hij staat nu bij de operator-tools; 66 lokale en
  twee echte volledige PostgreSQL-export→restoregevallen blijven groen. De
  snapshotadapter en archiefcontracten blijven in Eva's kern.
- De 337-regelige objectmateriaalstap van portable restore had eveneens nul
  runtimegebruikers en staat nu bij de operator-tools. Met de vastgezette
  cryptodependency zijn zeven lokale en alle vijf objectgrensgevallen, inclusief
  de echte PostgreSQL-`ObjectRef`, groen.
- De 471-regelige PostgreSQL-exportadapter bleek ook uitsluitend bij operator-
  en testcode te horen. Na verhuizing zijn 124 lokale controles en vier echte
  databasegrenzen groen: persoonsisolatie, leeg en niet-leeg herstel en de
  versleutelde objectverwijzing.
- De 224-regelige controle van portable objecten staat nu eveneens naast de
  operator-materializer. Negen lokale en alle vijf objectgrensgevallen zijn
  groen; een gewijzigde byte of verkeerde sleutel blijft vóór opslag geweigerd.
- De verzegelde B-release-inputlijst liep door eerdere wijzigingen 41 hashes
  achter en verwees nog naar drie oude operatorpaden. Alle 369 verplichte bron-
  en testpaden bestaan en zijn nu opnieuw gebonden. Ook 26 cockpitbronhashes
  zijn bijgewerkt zonder claims te veranderen. De fail-closed tests zijn 38/38
  groen en het exacte Git-archief bindt 239 inputs plus 130 tests.
- De 932-regelige verliesloze A/B-omschakelaar had nul runtimegebruikers en staat
  nu als host-operatorprogramma bij `tools`. Zes cutovertests en samen 44
  cutover-/releasechecks zijn groen; het exacte Git-archief is opnieuw gebonden.
- De 258-regelige managed-B-renderer staat nu eveneens bij de operator-tools.
  Alle 24 renderchecks en samen 62 render-/releasechecks zijn groen. De bron was
  eerder niet zelf gepind; hij is nu als 240e verplichte release-input toegevoegd.
- De 888-regelige managed-B-preflight draait nu als hostoperator uit `tools`.
  Wrapper, renderer en tests gebruiken hetzelfde nieuwe pad. Alle 84 inhoudelijke
  en samen 122 releasechecks zijn groen. De eerder ongepinde shellwrapper is nu
  samen met de preflightbron als verplichte release-input 241 verzegeld.
- De 282-regelige kleine canary-omschakelaar staat nu ook bij de operator-tools.
  Alle 15 canary-/quiescenttests en samen 53 releasechecks zijn groen. Zowel de
  implementatie als switchtool en de eerder ongepinde canarytest zijn nu
  verzegeld: 243 inputs en 131 tests.
- De 197-regelige private object-keyring had nul runtimegebruikers en staat nu
  naast de portable restoretools. Veertien lokale en alle vijf echte
  objectgrensgevallen zijn groen. De bron en drie eerder ongepinde gerichte
  tests zijn nu verzegeld: 244 inputs en 134 tests.
- De 167-regelige provider-verificatie-CLI had nul runtimegebruikers en staat nu
  bij de installer-operator-tools. Alle 58 provider-/connectorchecks en 38
  releasechecks zijn groen. De actuele cockpitbron en drie bewijscommands zijn
  aan het nieuwe pad gebonden; historische receipts blijven historisch intact.
- De complete portable-transportcluster van 734 regels staat nu bij de
  operator-tools. De vijf versleutel/open/tamper/no-overwrite-tests, CLI-smoke
  en samen 43 releasechecks zijn groen. Bron en test zijn nu als release-inputs
  gepind.

## Wat nog niet klaar is

- Brede huisbediening staat nog uit.
- Het token van de tijdelijke Telegramtestbot moet via BotFather worden
  ingetrokken.
- Eva1-geheugen is nog niet algemeen naar Eva2 overgezet. Eerst moet ieder oud
  persoonlabel betrouwbaar aan het juiste nieuwe profiel worden gekoppeld.
- Full-duplex praten, luisteren en onderbreken met echte audiohardware is nog
  niet bewezen.
- De algemene browserimages zijn nog niet veilig genoeg voor vrij gebruik.
- De schone images en bouwpakketten staan nog alleen op deze computer. Er is nog
  geen gecontroleerde kopie buiten deze computer.
- De harde broncodeteller is nu voor het eerst groen. In totaal zijn 15.369
  oude test-, proef-, operator- en dubbele regels uit de runtimewortels gehaald.
  Eva2 telt nu 74.817 productregels bij een maximum van 75.000. De marge is dus
  183 regels. De actuele imagebasis is ook gebouwd, gescand, herstart en
  live geschakeld; de volledige PC01-gate is daardoor groen.
- De negen grootste runtimebestanden kunnen niet meer stil verder groeien.
  Ieder heeft nu een vaste eigenaar, contract en bovengrens; een nieuw
  niet-geregistreerd productbestand boven 1.000 regels maakt PC02 rood.
- De blokkade van 18-27 seconden is weg. Een langere meting gaf 597 van 600
  lokale gezondheidscontroles binnen 0,4 seconde. De drie missers waren los van
  elkaar; p95 was 11 ms, p99 286 ms en de langzaamste meting 401 ms. Dit bewijst
  de lokale event-loop, nog niet Telegram-, model- of antwoordlatency.
- De nieuwe live route vermijdt de extra plannerbeurt. De eerste volledige
  200-beurtenmeting is foutloos, maar p95 eerste inhoud is met 2,285 seconde nog
  285 milliseconden te langzaam. PC05 blijft daarom rood.
- De wizard kan nog niet zelf een volledige verse installatie, update en
  herstel op een tweede computer uitvoeren.
- De aparte Eva2-Telegramingang staat uit om dubbel pollen naast Eva1 te
  voorkomen. Berichten gaan nu via Eva1's expliciete `/eva2`-router.
- De duurzame doelworker staat uit totdat een echte provider is goedgekeurd.
- Een echte Windows/WSL-rebootproef moet nog door iemand buiten Codex worden
  gestart.
- De volledige zevendaagse praktijktest moet nog.
- Officiële AgentBench-, GAIA- en OSWorld-scores zijn nog niet uitgevoerd.
- De volledige eindvergelijking met OpenClaw, Hermes en Agent Zero is nog niet
  groen.

## Waar ik nu aan werk

1. Na de al bewapende echte Windows/WSL-reboot controleren of Eva1, Eva2, beide databases,
   geheugen, dashboard en de terugvalroute vanzelf gezond terugkomen.
2. De gemeten providerstaart verkorten zonder tooltekst vrij te geven; daarna
   dezelfde verzegelde 200-beurtenreeks opnieuw uitvoeren.
3. Daarna de langere praktijktest starten en fouten, vertraging en geheugengroei
   bewaken.
4. De begrensde huisbediening stap voor stap uitbreiden zonder de aparte
   controle en herstartveiligheid kwijt te raken.

## Belangrijk verschil

- **Eva1:** de huidige standaardservice in `/home/administrator/project/Bottie`
  met SQLite.
- **Eva2:** de beheerde kandidaat in
  `/home/administrator/project/Bottie-candidates/v2/eva2` met PostgreSQL en
  Hindsight.
- Alleen een expliciet `/eva2`-bericht gaat naar Eva2.

## Laatste bewijs

- [Betrouwbaardere modelroute live: 200/200 antwoorden, p95 nog rood](evidence/living-core/eva2-transient-retry-canary-cutover-live-2026-08-30.json)
- [Retrykandidaat: image 0/0, 108 tests, HA en geheugen na herstart](evidence/product/performance-complexity-pc05-transient-retry-candidate-2026-08-30.json)
- [Ruwe tweede 200-beurtenreeks met vier duurzaam gemarkeerde retries](evidence/product/performance-complexity-pc05-text-200-retry-candidate-2026-08-30.json)
- [Snelle geheugenkandidaat live op `/eva2`, met gezonde terugval en geheugen na herstart](evidence/living-core/eva2-fast-memory-canary-cutover-live-2026-08-30.json)
- [Twintig koude beurten, geheugen- en huisregressie, image 0/0](evidence/product/performance-complexity-pc03-fast-memory-candidate-2026-08-30.json)
- [Live zichtbare stream: mediaan groen, p95 en één time-out nog rood](evidence/product/performance-complexity-pc03-live-visible-stream-development-2026-08-30.json)
- [Nieuwe uitgestelde tools live achter `/eva2`, met herstart en gezonde rollback](evidence/living-core/eva2-deferred-tools-canary-cutover-live-2026-08-30.json)
- [Kandidaatbouw, 89 tests, regelbudget, scan en echte huisvraag](evidence/product/performance-complexity-pc03-deferred-tools-candidate-2026-08-30.json)
- [Parallelle Luna-kandidaat: image 0/0, directe tekst snel, geïntegreerde latency nog rood](evidence/product/performance-complexity-pc03-parallel-luna-candidate-2026-08-30.json)
- [Veilige echte tekststream; image 0/0, maar latencydoel nog rood](evidence/product/performance-complexity-pc03-safe-text-stream-2026-08-30.json)
- [Eva1-vastloper gevonden en live van 24,6 seconden naar circa 0,05-0,15 seconde gebracht](evidence/living-core/eva1-autonomy-event-loop-responsiveness-2026-08-29.json)
- [Langere Eva1-event-loopmeting: 600 controles, p95 11 ms en drie losse missers](evidence/living-core/eva1-event-loop-responsiveness-long-600-2026-08-29.json)
- [Verliesloze tweerichtings A/B-omschakeling](evidence/living-core/eva2-managed-b-quiescent-bidirectional-cutover-2026-08-29.json)
- [Bewapende automatische controle voor de echte reboot](evidence/living-core/eva2-managed-b-postboot-proof-armed-2026-08-29.json)
- [Actuele schone negen-dienstenproef](evidence/living-core/eva2-managed-b-resource-budget-semantic-tool-canary-2026-08-29.json)
- [Bewaard slot 4](evidence/living-core/eva2-managed-b-retained-slot4-2026-08-29.json)
- [Echte Telegramproef met twee personen en een groep](evidence/living-core/eva2-telegram-native-two-person-group-live-2026-08-28.json)
- [Twee echte privégesprekken na herstart](evidence/living-core/eva2-telegram-human-private-two-person-restart-2026-08-28.json)
- [Echte begrensde huisactie](evidence/living-core/eva2-home-assistant-bounded-effect-live-2026-08-28.json)
- [Persoonsgebonden wissen](evidence/living-core/eva2-owner-conversation-erasure-control-2026-08-29.json)
- [Eerlijke rode code-, dependency- en imagebudgetmeting](evidence/product/performance-complexity-pc01-current-2026-08-29.json)
- [Eerste echte codesanering met behoud van regressies](evidence/product/performance-complexity-pc01-test-support-remediation-2026-08-29.json)
- [Gedeelde installerbeveiliging met 186 groene regressies](evidence/product/performance-complexity-pc01-installer-safety-consolidation-2026-08-29.json)
- [Releasepadensets compact en nog steeds fail-closed gebonden](evidence/product/performance-complexity-pc01-release-path-set-binding-2026-08-29.json)
- [Negentien modeltools na consolidatie bytegelijk gebleven](evidence/product/performance-complexity-pc01-conversation-tool-schema-consolidation-2026-08-29.json)
- [Ook de vijf overige modeltools bytegelijk gebleven](evidence/product/performance-complexity-pc01-remaining-model-tool-schema-consolidation-2026-08-29.json)
- [Gedeelde PostgreSQL-afscherming: 149 productregels minder](evidence/product/performance-complexity-pc01-scoped-role-consolidation-2026-08-29.json)
- [Gedeelde read/effect-dispatchopslag: 23 productregels minder](evidence/product/performance-complexity-pc01-dispatch-consolidation-2026-08-29.json)
- [Gedeelde grant-, envelop- en UTC-opbouw: 96 productregels minder](evidence/product/performance-complexity-pc01-authority-timestamp-consolidation-2026-08-29.json)
- [Semantisch identieke opmaaknormalisatie: 69 productregels minder](evidence/product/performance-complexity-pc01-format-normalization-2026-08-29.json)
- [Gedeelde duurzame claimvalidatie: 61 productregels minder](evidence/product/performance-complexity-pc01-claim-validation-consolidation-2026-08-29.json)
- [Gedeelde dispatchautorisatie: 48 productregels minder](evidence/product/performance-complexity-pc01-dispatch-authorization-consolidation-2026-08-29.json)
- [Gedeelde terminale resultaatopslag: 24 productregels minder](evidence/product/performance-complexity-pc01-result-persistence-consolidation-2026-08-29.json)
- [Gedeeld persoonlijk claim-/leaseprotocol: 64 productregels minder](evidence/product/performance-complexity-pc01-personal-claim-lease-consolidation-2026-08-29.json)
- [Gedeelde persoonlijke lifecycle en echte PostgreSQL-route: 33 productregels minder](evidence/product/performance-complexity-pc01-personal-lifecycle-consolidation-2026-08-29.json)
- [Persoonsgebonden artifacts blijven zichtbaar: fout hersteld en 15 productregels minder](evidence/product/performance-complexity-pc01-personal-artifact-selection-2026-08-29.json)
- [Gedeelde actionchain-storebasis: 135 productregels minder en 95/95 PostgreSQL-cases groen](evidence/product/performance-complexity-pc01-action-chain-store-base-consolidation-2026-08-29.json)
- [Gedeelde stop- en afrondingslock voor uitbesteed werk](evidence/product/performance-complexity-pc01-delegated-terminal-lock-consolidation-2026-08-29.json)
- [Gedeelde runtime-instellingen en file-only secrets](evidence/product/performance-complexity-pc01-runtime-environment-consolidation-2026-08-29.json)
- [Afgewezen Playwright-proefcode uit de runtime: 996 productregels minder](evidence/product/performance-complexity-pc01-rejected-playwright-proof-extraction-2026-08-29.json)
- [Ongebruikte MCP-bewijsclient uit de runtime: 415 productregels minder](evidence/product/performance-complexity-pc01-unused-mcp-proof-extraction-2026-08-29.json)
- [Offline kandidaatimagebouwers uit de runtime: 990 productregels minder](evidence/product/performance-complexity-pc01-candidate-build-tool-separation-2026-08-29.json)
- [Offline releasehulpen uit de runtime: 1.758 productregels minder](evidence/product/performance-complexity-pc01-candidate-release-tool-separation-2026-08-29.json)
- [Gedeelde LocalTuya-receiptcontrole](evidence/product/performance-complexity-pc01-localtuya-receipt-consolidation-2026-08-29.json)
- [Gedeelde LocalTuya-toelating en Home Assistant-eindcontrole](evidence/product/performance-complexity-pc01-localtuya-admission-ha-proof-consolidation-2026-08-29.json)
- [Ongebruikte Home Assistant-switch-wrapper verwijderd](evidence/product/performance-complexity-pc01-home-assistant-binary-wrapper-removal-2026-08-29.json)
- [Elf identieke taakopslagconstructies samengebracht](evidence/product/performance-complexity-pc01-advanced-task-store-consolidation-2026-08-29.json)
- [Gedeelde veilige uitvoerstaart voor huisautomatiseringen](evidence/product/performance-complexity-pc01-automation-execution-tail-consolidation-2026-08-29.json)
- [Eén begrensde SSE-framedecoder voor gesprek en onderzoek](evidence/product/performance-complexity-pc01-sse-frame-decoder-consolidation-2026-08-29.json)
- [Zes identieke publicatieopslagconstructies samengebracht](evidence/product/performance-complexity-pc01-publication-store-consolidation-2026-08-29.json)
- [Turn-scoped opslag voor advanced tasks en duurzame doelen](evidence/product/performance-complexity-pc01-turn-scoped-task-store-consolidation-2026-08-29.json)
- [Eén strenge hersteldecoder voor afgeronde lees- en effectpogingen](evidence/product/performance-complexity-pc01-terminal-attempt-restore-consolidation-2026-08-29.json)
- [Gedeelde dependencyvrijgavecontrole voor read- en effectvoortgang](evidence/product/performance-complexity-pc01-action-chain-release-validation-consolidation-2026-08-29.json)
- [Gedeelde lookup van voorbereide read- en effect-child-events](evidence/product/performance-complexity-pc01-prepared-child-event-lookup-consolidation-2026-08-29.json)
- [Eén parser voor terminale read- en effectproviderresultaten](evidence/product/performance-complexity-pc01-provider-result-parser-consolidation-2026-08-29.json)
- [Eén vaste tijdlijnscope voor Telegramgroepen, bezorging en doelen](evidence/product/performance-complexity-pc01-external-timeline-scope-consolidation-2026-08-29.json)
- [Gedeelde OpenAI-endpointcontrole en late time-outsluiting](evidence/product/performance-complexity-pc01-openai-endpoint-timeout-consolidation-2026-08-29.json)
- [Eén begrensde JSON-ingang voor installer-secrets en providercontrole](evidence/product/performance-complexity-pc01-installer-json-intake-consolidation-2026-08-29.json)
- [Eén providerproces-executor voor lezen, handelen en effectcontrole](evidence/product/performance-complexity-pc01-action-chain-connector-executor-consolidation-2026-08-29.json)
- [Gedeelde PostgreSQL- en dispatcheropbouw met aparte bevoegdheden](evidence/product/performance-complexity-pc01-action-chain-authority-wiring-consolidation-2026-08-29.json)
- [Historische action-chain-writers uit de runtime, met browser-DNS-gat zichtbaar](evidence/product/performance-complexity-pc01-historical-action-advancers-test-support-2026-08-29.json)
- [De vier historische browsergevallen groen in de bedoelde gesloten omgeving](evidence/living-core/eva2-browser-a02-closed-network-current-source-2026-08-29.json)
- [Portable herstelwriter naar de operatorgrens, met vijf echte databaseproeven](evidence/product/performance-complexity-pc01-portable-restore-operator-boundary-2026-08-29.json)
- [Portable exportpublisher naar de operatorgrens, met twee volledige databaseproeven](evidence/product/performance-complexity-pc01-portable-export-operator-boundary-2026-08-29.json)
- [Portable objectmateriaalstap naar de operatorgrens, inclusief echte ObjectRef-proef](evidence/product/performance-complexity-pc01-portable-object-operator-boundary-2026-08-29.json)
- [PostgreSQL-exportadapter naar de operatorgrens, met vier echte databasegrenzen](evidence/product/performance-complexity-pc01-postgres-portable-export-operator-boundary-2026-08-29.json)
- [Portable objectverifier naar de operatorgrens, met echte ObjectRef-proef](evidence/product/performance-complexity-pc01-portable-object-verifier-operator-boundary-2026-08-29.json)
- [Alle 369 B-release-inputs opnieuw gebonden en op exact Git-archief gecontroleerd](evidence/living-core/eva2-managed-b-release-input-rebind-2026-08-29.json)
- [Verliesloze A/B-omschakelaar naar de operatorgrens, releasearchief opnieuw groen](evidence/product/performance-complexity-pc01-quiescent-cutover-operator-boundary-2026-08-29.json)
- [Managed-B-renderer naar de operatorgrens en als nieuwe release-input gepind](evidence/product/performance-complexity-pc01-managed-b-render-operator-boundary-2026-08-29.json)
- [Managed-B-preflight naar de operatorgrens, wrapper en bron samen verzegeld](evidence/product/performance-complexity-pc01-core-b-preflight-operator-boundary-2026-08-29.json)
- [Canary-omschakelaar naar de operatorgrens, volledige switchketen verzegeld](evidence/product/performance-complexity-pc01-canary-cutover-operator-boundary-2026-08-29.json)
- [Private object-keyring naar de operatorgrens, bron en tests verzegeld](evidence/product/performance-complexity-pc01-object-keyring-operator-boundary-2026-08-29.json)
- [Provider-verificatie-CLI naar de operatorgrens, cockpitbron opnieuw gebonden](evidence/product/performance-complexity-pc01-provider-verification-cli-operator-boundary-2026-08-29.json)
- [PC01-bronbudget groen: 74.819 regels, 181 regels marge](evidence/product/performance-complexity-pc01-source-budget-passed-2026-08-29.json)
- [PC02-hot-pathgrens groen: negen eigenaars en nul ongeregistreerde reuzenbestanden](evidence/product/performance-complexity-pc02-hot-paths-passed-2026-08-29.json)
- [PC03-meetlaag voor eerste modeltekst en voltooiing](evidence/product/performance-complexity-pc03-provider-timing-instrumentation-2026-08-30.json)
- [Historische effect-readback-store uit de runtime, lokale PostgreSQL-proef groen](evidence/product/performance-complexity-pc01-historical-readback-store-test-support-2026-08-29.json)
- [Eenmalige Eva1-geheugenproef uit de runtime naar operator-tools](evidence/product/performance-complexity-pc01-legacy-memory-trial-operator-boundary-2026-08-29.json)
- [Veertien duurzame gesprek-ID's bytegelijk uit één helper: 17 productregels minder](evidence/product/performance-complexity-pc01-conversation-id-derivation-consolidation-2026-08-29.json)
- [Gedeelde geheugen-/journalscope: 25 productregels minder en 52 tests groen](evidence/product/performance-complexity-pc01-memory-scope-consolidation-2026-08-29.json)
- [Kleinere DBOS-loze runtimekandidaat met scan en databaseproef](evidence/product/performance-complexity-pc01-dbosless-runtime-candidate-2026-08-29.json)
- [Exacte DBOS-loze eindimage met scan en databaseherstart](evidence/living-core/eva2-runtime35-exact-image-schema-restart-2026-08-29.json)
- [Dezelfde image met echt Codex, Hindsight en volledige herstart](evidence/living-core/eva2-runtime35-real-model-hindsight-restart-2026-08-29.json)
- [Nieuwe image live met verliesloze heen-terug-heenproef](evidence/living-core/eva2-runtime35-live-bidirectional-cutover-2026-08-29.json)
- [Nieuwe actieve slot-3-route voor de externe Windows-reboot bewapend](evidence/living-core/eva2-runtime35-postboot-proof-armed-2026-08-29.json)
- [Actuele PC01-image: netwerkloze build, scan, schema, model, herstart en live omschakeling](evidence/product/performance-complexity-pc01-current-image-live-2026-08-29.json)
- [Volledige PC01-poort groen zonder overtredingen](evidence/product/performance-complexity-pc01-passed-2026-08-29.json)
- [PC01-image beheerd op slot 3 en de echte Windows/WSL-reboot opnieuw bewapend](evidence/living-core/eva2-pc01-managed-slot3-reboot-armed-2026-08-29.json)

Technische details staan in [PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[EVA2_MOGELIJKHEDEN_EN_GATEN.md](EVA2_MOGELIJKHEDEN_EN_GATEN.md) en
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md).
