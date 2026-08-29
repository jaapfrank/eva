# Eva 2 — korte projectstatus

Laatst bijgewerkt: 29 augustus 2026, 09:45 CEST

## Heel kort

Eva2 werkt nu echt als aparte proef naast Eva1.

Je kunt via `/eva2` met haar praten. Ze gebruikt een echte Codex-koppeling,
heeft haar eigen persoonlijkheid, bewaart het lopende gesprek in PostgreSQL en
kan Home Assistant lezen. Eén al uitgeschakelde lamp kan ze nu ook veilig
uitzetten en daarna apart controleren.

Eva2 is nog geen volledige vervanger voor Eva1. Brede huisbediening,
gezinsgroepen, volledige voice, algemene browserbediening en de definitieve
installatie zijn nog niet klaar.

## Wat werkt al

- Eva1 draait nog gewoon en blijft de standaard.
- Een bericht met `/eva2` gaat naar de aparte Eva2-kandidaat.
- Twee verschillende privéaccounts krijgen nu ieder hun eigen Eva2-profiel.
- Een Telegramgroep zonder gekoppelde toestemming wordt geweigerd en kan dus
  niet per ongeluk in iemands privégeheugen belanden.
- Na een echt gezien groepsbericht kan die persoon privé tijdelijk delen.
  Intrekken sluit alleen die persoon direct weer af.
- Vóór toestemming bewaart Eva geen groepsberichttekst en stuurt zij niets
  daarvan naar het model.
- De echte proef met Jaap, Lola en een aparte testgroep is groen. Hun
  toestemming bleef ook na een echte pollerherstart netjes gescheiden.
- Jaap en Lola hebben daarna ieder privé met de echte Eva2-core gepraat. Na
  een volledige herstart wist Eva voor allebei exact het eigen testwoord en
  niet dat van de ander. Alle vijf menselijke antwoorden bereikten Telegram.
- De vier wegwerp-testcontainers zijn daarna netjes gestopt. De testdatabase
  en containers zijn niet verwijderd, zodat het bewijs controleerbaar blijft.
- Eva2 gebruikt Eva1's Codex-login, maar ziet alleen een automatisch vernieuwde toegangssleutel en nooit de volledige login.
- Eva2 klinkt volgens haar ingestelde jonge, warme, nieuwsgierige en eigenwijze
  persoonlijkheid.
- Een gesprek blijft bewaard na een herstart van Eva2 en PostgreSQL.
- Twee aparte proefpersonen onthielden ieder hun eigen testwoorden. Na een
  herstart van Eva2, PostgreSQL én Hindsight kwamen beide exact en zonder lek terug.
- De toegangs- en identiteitsbestanden voor deze echte gesprekstest worden nu
  eenmalig veilig gekopieerd. Een refresh-token of ongeldige identiteit wordt
  geweigerd en de volledige Compose-opstelling rendert groen.
- Eva2 draait in een eigen container met een eigen PostgreSQL-database.
- Hindsight is aangesloten als langetermijngeheugen van de kandidaat.
- Eén persoonsgebonden wisopdracht kan nu zowel het exacte Hindsight-document
  als de ruwe vraag en Eva's antwoord uit PostgreSQL verwijderen. Een
  verkeerde persoon wordt geweigerd. Na een nagebootste crash hervatte dezelfde
  opdracht zonder tekst terug te brengen of zichzelf vals als klaar te melden.
- In de nieuwe kandidaat van de afgeschermde eigenaarspagina staat bij ieder
  antwoord `Vergeet deze beurt`. De browser kiest geen persoon, installatie of
  gesprek; Eva leidt die vaste binding zelf af en meldt pas succes na de
  wisopdracht. Deze kandidaat is nog niet op de live `/eva2`-route gezet.
- Eva2 kan Home Assistant echt lezen. Op de vraag naar de spotjes overkapping
  antwoordde ze terecht dat die uit stonden.
- Bij een vage huisnaam vraagt Eva nu gewoon welke lamp je bedoelt. Ze gokt
  niet en leest of bedient het huis dan niet.
- Via een gewoon `/eva2`-bericht kan ze die spotjes uitzetten en apart
  controleren of ze echt uit staan.
- Alleen die ene lamp en alleen `uitzetten` zijn toegestaan. Bij `aanzetten`
  legt Eva normaal uit wat ze niet mag en schrijft ze nul huisacties.
- Na een echte herstart gaf dezelfde opdracht binnen 0,1 seconde het bewaarde
  resultaat terug, zonder de lamp nogmaals te bedienen.
- De actuele Eva2-image kan nu veilig worden klaargezet en omgeschakeld.
- De echte omschakeling heen, terug en opnieuw heen bleef groen. Bij een fout
  zet de operator automatisch de vorige route terug.
- Er is een private back-up van Eva1 gemaakt en gecontroleerd.
- De vorige code draait als veilige rollback en ziet evenmin de volledige login.
- Een kleine gesprek- en geheugenset kan veilig naar een lege database worden
  hersteld.
- De kleinere nieuwe Eva2-kern werkte echt met Jaap en Lola, hield hun
  gesprekken gescheiden en heeft in de strenge scan 0 kritisch en 0 hoog.
- Ook Hindsight en zijn nieuwe PostgreSQL/pgvector-database hebben nu schone
  kandidaten met 0 kritisch en 0 hoog. Eva vond een testherinnering terug na
  een volledige herstart van beide.
- De pgvector-database is daarna nogmaals volledig zonder internet gebouwd.
  De bestanden waren bytegelijk aan de eerste build en de scan bleef 0/0.
- De schone kern, Hindsight en twee verse PostgreSQL 18-databases draaiden
  daarna samen. Een echt modelantwoord en het geheugen bleven werken nadat de
  hele proefstack was gestopt en gestart.
- Hindsight kan zijn databasewachtwoord nu uit één privébestand lezen. Het
  staat niet meer in de Docker-instellingen; onveilige bestanden worden
  geweigerd. De aangepaste image werkte echt en de scan bleef 0/0.
- Het beheerde B-startbestand gebruikt nu negen diensten en geeft Eva en
  Hindsight ieder een eigen PostgreSQL-database. Alle images zijn exact
  vastgezet en worden tijdens starten nooit stil opgehaald.
- Die hele nieuwe startopstelling draaide echt met verse volumes. Het schema
  werd geïnstalleerd, Codex antwoordde exact `beheerd` en na het stoppen en
  starten van beide databases, Hindsight en de kern was Eva weer klaar.
- Tijdens de proef zijn twee echte oude-manifestfouten gevonden en hersteld:
  Python werd dubbel gestart en het webcommando ontbrak in de nieuwe image.
  De uiteindelijke 82 releasecontroles en de volledige praktijkproef zijn groen.
- De werkelijk gebruikte `/eva2`-database en Hindsight zijn daarna naar twee
  nieuwe PostgreSQL 18-databases gekopieerd. Eva gaf een echt antwoord en kwam
  na een volledige herstart weer gezond terug.
- Alle 258 bestaande geheugenunits en 14 persoonsmodellen bleven daarbij
  behouden. Dit geheugen gebruikt lokale 384-dimensionale embeddings; het
  beheerde startbestand houdt die keuze nu vast.
- De overgang maakt de vier vaste, streng beperkte Eva-databaserollen alleen
  op een volledig lege doeldatabase. Een bestaande database wordt geweigerd.
- De nieuwe herstelcode zit nu ook in één vaste, kleine Eva2-image. Die image
  is volledig zonder downloads gebouwd, draait als beperkte gebruiker en de
  strenge scan vond 0 kritisch en 0 hoog.
- De echte `/eva2`-data en alle bestaande Hindsight-data zijn met precies die
  nieuwe image opnieuw naar verse databases gekopieerd. Het echte modelantwoord,
  geheugen en de gezamenlijke stop/start bleven groen; alle proefmiddelen zijn
  daarna verwijderd.
- De nieuwe vaste image bedient nu werkelijk `/eva2` op poort `44708`. De
  route ging gecontroleerd heen, terug en opnieuw heen; Eva1 bleef steeds
  gezond met nul herstarts en de vorige route op `44706` draait nog intact.
- Via Eva1's echte `/eva2`-client antwoordde de nieuwe route exact zoals
  gevraagd. Na een eigen herstart wist zij het testwoord nog en zij las veilig
  uit Home Assistant dat de spotjes overkapping uit staan.
- De beheerde kern, beide toegangsleases, Hindsight en twee aparte PostgreSQL
  18-databases zijn nu de actieve `/eva2`-route op poort `44710`. De vorige
  route op `44708` draait nog als directe terugval.
- De nieuwe kernimage is zonder internet gebouwd en opnieuw gescand: 0 kritisch
  en 0 hoog. Gesprekken en het dashboard hebben ieder hun juiste, aparte scope.
- Het eigenaar-dashboard heeft nu een echte geverifieerde binding. Met de
  private toegang geeft het werkbord HTTP 200; zonder toegang geeft het 401.
- De route ging echt heen, terug en opnieuw heen. Na een eigen herstart wist
  Eva exact `groene gloed` terug en las zij in Home Assistant dat de spotjes
  overkapping uit staan.
- De eerste modelpoging direct na omschakeling gaf tijdelijk 503. Dezelfde
  duurzaam opgeslagen beurt is daarna één keer afgemaakt, zonder dubbele
  ingress of dubbel antwoord.
- De afgeschermde lease-maker volgt nu Eva1's actuele private Codex-bron. De
  kern ziet alleen de korte toegangssleutel; de eenmalige kopie met de volledige
  login is uit het actieve slot verwijderd. Een nieuw modelantwoord was groen.
- Tussen de momentopname en de eerste omschakeling zag alleen de oude route tien
  korte apparaat-/bewegingsgebeurtenissen. Er ging geen gebruikersgesprek of
  modelantwoord verloren; dit is daarom eerlijk geen volledig gatloze
  verhuizing van de oude sensorgeschiedenis.
- De beheerde kern bood nog ten onrechte een duurzame-doeltool met een
  proefprovider aan, terwijl de echte worker niet draaide. Dat is nu live
  dichtgezet: workerbedrading en gesprekstoelating zijn apart. Eva neemt pas
  weer zo'n afspraak aan nadat een echte provider afzonderlijk is vrijgegeven.
  109 gerichte controles zijn groen; na de kernwissel antwoordde Eva via de
  echte `/eva2`-client exact `eerlijk` en er bleven nul wachtende doelen.
- De hele actieve beheerde runtime is daarna achter de gezonde terugvalroute
  koud gestopt: kern, beide toegangsleases, Hindsight en beide databases waren
  werkelijk uit. Zij zijn expres in een ongunstige volgorde gestart en waren
  binnen 13 seconden weer groen, zonder herstartlus. Gesprekscontext gaf exact
  `eerlijk` terug, Home Assistant meldde opnieuw dat de spotjes uit staan en
  dashboardtoegang bleef 200/401. De route staat weer op poort `44710`.
- Hindsight gebruikt zijn ingebouwde lokale modellen nu expliciet zonder
  internet of telemetrie en met UTC. Na een nieuwe start op het alleen-lezen
  systeem bleven de oude cache- en tijdzonewaarschuwingen weg. Alle 262
  geheugenstukken en 18 persoonsmodellen waren er nog en Eva gaf via de echte
  `/eva2`-route opnieuw exact `eerlijk` terug.
- De WSL-herstart vond een echte terugvalfout: de oude terugvalcontainer leek
  gezond, maar zijn tijdelijke geheugenmap was verdwenen. Die map wordt nu bij
  iedere start veilig opnieuw gemaakt. De actieve én de terugvalroute zijn
  weer groen; alle tien betrokken containers hebben nul herstarts.
- De computerrebootcontrole is geïnstalleerd en privé klaargezet. Zij kan niet
  zelf rebooten, onderscheidt Windows-, WSL- en alleen-systemd-herstarts en
  schrijft pas bewijs nadat Eva1, Eva2, geheugen, dashboard en terugval echt
  terug zijn. Een proef zonder reboot werd terecht geweigerd zonder receipt.
- Eerdere geldige wijzigingen hadden hun releasezegel nog niet meegekregen.
  Veertien releasebestanden en vier cockpitbronnen zijn nu opnieuw aan hun
  actuele inhoud gebonden. Alle 86 gerichte controles zijn groen.
- De lokale installatiewizard kan Eva's kleine `SOUL.md` nu echt downloaden,
  voorvertonen, privé bewaren, ondertekenen, activeren en direct terugzetten.
  Een echte lokale HTTP-proef doorliep alle stappen. De privésleutel kwam niet
  in de browser of Eva-runtime en na rollback was de installatiesessie ongeldig.
- Die identiteitsstappen overleven nu ook een gesloten browser en herstart van
  de installer. Vier nieuwe serverprocessen hervatten achtereenvolgens bewaren,
  ondertekenen, activeren en terugzetten. Een halve activatie of rollback wordt
  herkend en niet dubbel uitgevoerd.
- De wizard bewaart nu ook installatienaam, eigenaarlabel, taal en tijdzone.
  Drie echte serverprocessen hervatten dezelfde keuzes en bevestigen ze apart.
  Dit maakt nog geen account, secret, container of actieve installatie.
- Model, Telegram, Home Assistant, e-mail, een later ander kanaal en budgetten
  hebben nu ieder een eigen hervatbare status. Een keuze heet nooit automatisch
  klaar: alleen een aparte controle met bewijs mag dat later vastleggen.
- Voor een gekozen model-, Telegram-, Home Assistant- of e-mailkoppeling kan
  de wizard nu een toegangscode eenmalig privé bewaren. Zij toont alleen een
  vaste verwijzing terug; lezen, overschrijven en automatisch groen verklaren
  kan niet.
- De eerste aparte Telegramcontrole werkt nu ook van begin tot eind. Zij doet
  alleen `getMe`, bewaart geen botgegevens of toegangscode en doet na `ready`
  niet stiekem nog een tweede provideroproep. De praktijkproef gebruikte een
  lokale Telegram-nabootsing en volledig synthetische gegevens; een echt
  Telegramaccount is dus nog niet via de installer gekoppeld.
- Dezelfde aparte controle werkt nu voor Home Assistant met alleen `GET /api/`.
  Zij weigert openbare serveradressen, volgt geen omleiding en leest geen
  apparaten. Ook na `ready` volgde geen tweede oproep. De proef gebruikte een
  lokale nabootsing; het echte huis is hiermee nog niet via de installer
  gekoppeld.
- Een gewone gebruiker kan in de wizard een opgeslagen toegangscode veilig
  vervangen, verwijderen of na een onderbreking herstellen. Eva blokkeert
  eerst de koppeling en verandert pas daarna het private bestand. Zelfs als
  een oude controle pas na de vervanging antwoordt, kan die de nieuwe toegang
  niet ten onrechte klaar verklaren. Vier echte wizardstarts doorliepen dit
  met synthetische gegevens; niets geheims kwam terug in browser of bewijs.
- De installer kan nu ook één gekozen AgentMail-inbox alleen-lezen
  controleren. Zij leest daarbij geen berichten en bewaart de sleutel,
  inboxgegevens en het providerantwoord niet. Na `ready` volgt bij een
  herstart geen tweede oproep. De proef gebruikte een lokale nabootsing en dus
  nog geen echte mailbox.
- De verplichte modelkoppeling heeft nu ook een echte installercontrole. Zij
  accepteert uitsluitend Eva's korte toegangspakket zonder refresh-token en
  doet één kleine modelgeneratie zonder tools. De proef mat 19 invoer- en 5
  uitvoertokens, bewaarde geen antwoord of login en deed na `ready` geen tweede
  oproep. De provider gaf geen geldbedrag terug; Eva verzint daarom geen kosten.
- Een gewone gebruiker kan in de lokale wizard nu zelf een model- of
  Telegramkoppeling kiezen, de toegang eenmalig invoeren en de aparte controle
  starten. Twee echte lokale wizardservers deden samen precies één
  provideroproep; na de herstart bleef de groene status staan en volgde geen
  tweede oproep. De proef gebruikte synthetische toegang en benaderde geen
  echte provider.
- Home Assistant en AgentMail hebben nu dezelfde gebruikersroute. De wizard
  bewaart de lokale URL of inbox-ID privé, toont alleen dat de instelling er
  is en bindt de hash daarvan aan de providercontrole. Twee echte lokale
  wizardservers deden precies één Home Assistant- en één AgentMail-call; na
  herstart bleven beide groen zonder nieuwe call. De proef gebruikte lokale
  nabootsingen en dus geen echt huis of echte mailbox.
- De wizard laat vóór iedere providercontrole nu in gewone taal zien wat Eva
  met die toegang doet. Telegram leest alleen de botidentiteit, Home Assistant
  alleen de API-startpagina, AgentMail alleen de gekozen inboxidentiteit en de
  modelroute doet één kleine generatie zonder tools of opslag. Deze vier vaste
  grenzen zitten met een hash in ieder controlebewijs en zijn via echte lokale
  providerproeven gecontroleerd. Een provider kan zelf nog steeds een bredere
  toegangscode uitgeven; echte least-privilege-providerrechten blijven daarom
  een aparte praktijkcontrole.
- De wizard laat een gewone gebruiker nu ook harde maxima kiezen voor
  modelcontext, output, geheugen, bewaartijd, achtergrondwerk en externe
  autonome uitgaven. Onbekende autonome kosten blijven geblokkeerd. Deze
  limieten worden apart bevestigd en exact aan het installatieplan gebonden.
- Uit alle controles maakt de wizard één leesbaar plan dat eerlijk “klaar voor
  releasevoorbereiding” heet. Zolang iets ontbreekt toont zij concrete
  blokkades. Vijf echte lokale wizardstarts bewezen hervatten, dezelfde
  planvingerafdruk na herstart, herstel na een afgebroken budgetbinding en
  directe blokkering na intrekking. Er bestaat bewust nog geen installatiestart.
- De bevestigde limieten gaan nu echt mee naar Eva's runtime. Een te grote
  modelvraag en zelfstandig werk met onbekende kosten stoppen vóór de provider.
  Een providerantwoord boven de uitvoergrens wordt niet opgeslagen. Een normale
  beurt bewaarde het gemeten gebruik echt in een nieuwe PostgreSQL-database.
- Ook achtergrondreflecties hebben nu een echte daglimiet vóór Hindsight iets
  mag starten. Bij acht gelijktijdige verzoeken voor één testpersoon werden er
  precies vier toegelaten. Na een nieuw proces bleef de gezamenlijke limiet zes;
  pas op de volgende UTC-dag begon de teller opnieuw. Hindsights eigen
  ongecontroleerde nachtplanning is uit deze kandidaat verwijderd.
- De kandidaat begrenst gewoon gespreksgeheugen nu per persoon, Telegramgroep,
  project én hele installatie op regels en echte UTF-8-bytes. Bij acht
  gelijktijdige verzoeken om de laatste plek kwam op ieder niveau precies één
  binnen. Ruimte komt pas vrij nadat Hindsight de verwijdering heeft bevestigd;
  een afgebroken verwijdering wordt veilig hervat. De huidige Hindsight-opslag
  is daarnaast zonder teksten of namen gemeten: 135 brondocumenten gebruiken
  samen 73.941 bytes. De proefdatabase en de live meting wijzigden geen geheugen.
- Ook Eva's eigen verkenningen, proefbouwsels en releasevoorstellen kunnen die
  geheugenlimiet niet meer omzeilen. Zij lopen nu door precies dezelfde
  persoonsgrens en bewaartijd vóór Hindsight. Een bronbrede controle weigert
  voortaan iedere nieuwe directe geheugenwrite buiten de twee vaste
  opslaggrenzen; 76 gerichte regressies zijn groen.
- Eén exact semantisch geheugenstuk kan nu op verzoek duurzaam uit Hindsight
  worden verwijderd. Een andere persoon kan die verwijdering niet aanvragen,
  een herstart hervat dezelfde poging zonder dubbele provideropdracht en Eva
  markeert het stuk pas verwijderd nadat Hindsight dat heeft bevestigd. Het
  achterblijvende bewijs bevat alleen hashes en tijden, geen geheugentekst.
- `Vergeet dit` wist in de kandidaat nu ook Hindsight-afleidingen en Eva's
  wachtende geheugenkopie. In een echte wegwerpproef gingen het bronfeit, de
  daarvan gemaakte observatie en de persoonsstijl alle drie naar nul. Na een
  volledige herstart bleven ze weg. Een onderbroken opdracht kan veilig verder.
- Een herstel uit een oude back-up kan vergeten tekst niet meer ongemerkt
  terugbrengen in de kandidaat. Herstellen vraagt nu eerst een klein privébestand
  met de exacte toegestane back-up. Na een wisactie telt de oude lijst niet meer;
  zelfs een per ongeluk opnieuw aangewezen oude back-up wordt vóór de eerste
  databasevraag geweigerd. Een verse niet-lege PostgreSQL-herstelproef bleef groen.
- De wisknop en die private back-upgrens zijn nu één herstartveilige handeling.
  Eva maakt eerst alle oude back-ups ongeldig en wist daarna pas Hindsight, ruwe
  gesprekstekst en de wachtende geheugenkopie. Een nagebootste crash hervatte
  dezelfde opdracht zonder een tweede generatie te maken. Twee gelijktijdige
  wisacties kregen netjes generatie één en twee; geen van beide ging verloren.
- De nieuwe kerncode zit nu in een kleine Debian 13-kandidaatimage. Een eerste
  Debian 12-build werd terecht afgekeurd op één HIGH-bevinding. De vervanger
  importeert als beperkte gebruiker op een alleen-lezen systeem en de verse
  scan van 29 augustus telt 0 kritisch en 0 hoog. Zij is nog niet live gezet.
- De volledige wegwerp-B vond daarna twee echte integratiefouten: het verplichte
  resourcebudget ontbrak in het startbestand en Eva stuurde bij ieder gesprek
  blind alle tools mee. Beide zijn hersteld. De bestaande semantische planner
  kiest nu maximaal vier passende tools voor de actuele beurt; een gewone
  tekstvraag krijgt er nul.
- De nieuwe image vanaf commit `022afba` is zonder bouwnetwerk gemaakt en met
  de verse database opnieuw 0 kritisch/0 hoog gescand. Negen wegwerpdiensten
  gaven een echt exact Codex-antwoord en kwamen na een volledige database- en
  serviceherstart weer groen terug. Alle proefmiddelen zijn verwijderd. Deze
  image is nog niet naar de live `/eva2`-route omgeschakeld.

## Wat nog niet klaar is

- Brede huisbediening staat nog uit. Nu werkt alleen één veilig begrensde
  `uitzetten`-actie; andere lampen en `aanzetten` zijn nog geblokkeerd.
- Het token van de wegwerp-testbot moet nog door Jaap via BotFather worden
  ingetrokken. Die instructie is naar beide testaccounts verstuurd.
- Eva1-geheugen is nog niet naar Eva2 gekopieerd. Eerst moet duidelijk zijn
  welk oud persoonlabel bij welk nieuw profiel hoort.
- Full-duplex praten, luisteren en onderbreken met een echte microfoon en
  speaker is nog niet getest.
- De browserimages zijn nog niet veilig genoeg voor algemeen gebruik.
- De schone kern-, Hindsight- en database-images staan alleen op deze computer.
  De bouwpakketten en images moeten nog veilig buiten deze computer worden
  bewaard en opnieuw herbouwbaar worden bewezen.
- De gewone gebruiker kan in de wizard nu wel een gecontroleerd installatieplan
  krijgen, maar Eva2 nog niet zelf installeren, herstellen en bijwerken. De
  aparte uitvoerknop en volledige fresh-installproef ontbreken nog.
- De runtime handhaaft nu de model-, antwoord-, Hindsight-recall- en dagelijkse
  reflectiegrenzen, plus blokkering van onbekende autonome kosten. De kandidaat
  begrenst gespreksgeheugen per persoon, groep, project en hele installatie op
  regels, bytes en bewaartijd. Eén opdracht kan een exact Hindsight-document
  én de bijbehorende ruwe vraag en het antwoord herstartveilig verwijderen.
  De eigenaarspagina heeft nu een knop voor één dashboardbeurt. Ook de wachtende
  geheugenkopie, Hindsight-observaties en de persoonsstijl worden gewist. Een
  oude back-up wordt bij herstel nu geblokkeerd en de wisknop werkt die grens
  eerst bij. De nieuwe kernimage is gebouwd, 0/0 gescand en in een volledige
  wegwerp-B groen herstart. De private map moet nog voor een bewaard inactief
  slot worden gemaakt en via A/B worden beproefd. Losse applicatiecaches en
  procedurekandidaten ontbreken nog. Ook
  fysieke database-/vectorindexquota en echte geldbedragen blijven open.
- Model, Telegram, Home Assistant en AgentMail hebben nu gebruikersbediening
  in de wizard, inclusief toegang vervangen, loskoppelen en een onderbroken
  wijziging herstellen. Eva's minimale gebruiksrechten zijn zichtbaar en aan
  het bewijs gebonden. Echte accounts, provider-granted least privilege,
  browserlogin en tokenvernieuwing ontbreken nog.
- De kernhelft van de beheerde negen-dienstenopstelling is nu de actieve
  `/eva2`-route. De aparte doelworker en Telegramingang zijn nog niet gestart:
  de gevonden doelprovider is alleen een testprovider en Telegram mag niet met
  een nep-/ingetrokken token of naast Eva1 dubbel pollen. De rebootcontrole
  staat klaar, maar de echte externe Windows-reboot en de zevendaagse
  praktijktest moeten nog volgen.
- Eva1 moest bij de derde geplande servicewissel na twintig seconden hard
  stoppen. Daarna waren alle vijf controles groen en de tijdelijke traagheid
  verdween, maar dit blijft een zichtbaar onderhoudspunt.
- Officiële AgentBench-, GAIA- en OSWorld-scores zijn nog niet uitgevoerd.
- De volledige eindvergelijking met OpenClaw, Hermes en Agent Zero is nog niet
  groen.

## Waar ik nu aan werk

1. Een bewaard inactief slot met de nieuwe 0/0-kernimage maken en via de
   terugvalroute beproeven; daarna caches en procedures wissen.
2. Na een echte, buiten Codex gestarte Windows-reboot het al klaargezette
   bewijs laten controleren voor Eva1, `/eva2`, beide databases, geheugen,
   dashboard en terugval.
3. Daarna de langere praktijktest starten en de tijdelijke 503- en
   Eva1-stopwaarnemingen actief bewaken.

## Belangrijk verschil

- **Eva1:** de huidige standaardservice in `/home/administrator/project/Bottie`
  met SQLite.
- **Eva2:** de aparte kandidaat in
  `/home/administrator/project/Bottie-candidates/v2/eva2` met PostgreSQL en
  Hindsight.
- Alleen een expliciet `/eva2`-bericht gaat nu naar Eva2.

## Laatste bewijs

- [Echte alleen-lezen huisvraag](evidence/living-core/eva2-home-assistant-readonly-live-2026-08-28.json)
- [Echte begrensde huisactie](evidence/living-core/eva2-home-assistant-bounded-effect-live-2026-08-28.json) en [natuurlijke weigering zonder huisactie](evidence/living-core/eva2-home-assistant-natural-denial-live-2026-08-28.json)
- [Veilige echte omschakeling en rollback](evidence/living-core/eva2-atomic-canary-cutover-live-2026-08-28.json)
- [Aparte privéprofielen voor Telegramaccounts](evidence/living-core/eva2-telegram-private-principal-isolation-2026-08-28.json)
- [Persoonsgebonden toestemming voor Telegramgroepen](evidence/living-core/eva2-telegram-group-consent-live-2026-08-28.json)
- [Echte Telegramproef met twee personen, intrekken en herstart](evidence/living-core/eva2-telegram-native-two-person-group-live-2026-08-28.json)
- [Normale vraag bij een onduidelijke lampnaam](evidence/living-core/eva2-home-target-clarification-live-2026-08-28.json)
- [Gesprek blijft bestaan na herstart](evidence/living-core/eva2-durable-conversation-restart-2026-08-28.json)
- [Twee echte modelgesprekken blijven apart na volledige teststackherstart](evidence/living-core/eva2-telegram-real-core-two-person-restart-2026-08-28.json)
- [Jaap en Lola privé via Telegram, apart gebleven na volledige herstart](evidence/living-core/eva2-telegram-human-private-two-person-restart-2026-08-28.json)
- [Releaseblokkerende scan van de echte gesprekimages](evidence/living-core/eva2-telegram-conversation-images-vulnerability-scan-2026-08-28.json)
- [Kleinere Eva2-kern: echte gesprekken en scan 0/0](evidence/living-core/eva2-distroless-core-crypto50-candidate-2026-08-28.json)
- [Schone Hindsight-API en PostgreSQL/pgvector: geheugenherstart en scans 0/0](evidence/living-core/eva2-distroless-hindsight-pgvector-candidate-2026-08-28.json)
- [Pgvector opnieuw zonder internet gebouwd en bytegelijk gecontroleerd](evidence/living-core/eva2-pgvector-offline-rebuild-2026-08-28.json)
- [Alle schone runtimeonderdelen samen, inclusief echte model- en geheugenherstart](evidence/living-core/eva2-clean-runtime-stack-restart-2026-08-28.json)
- [Hindsight-databasewachtwoord veilig uit een privébestand](evidence/living-core/eva2-hindsight-database-file-secret-2026-08-28.json)
- [Schone beheerde B-grafiek met twee databases, echte Codex-beurt en herstart](evidence/living-core/eva2-managed-b-clean-graph-2026-08-28.json)
- [Werkelijke `/eva2`-data en Hindsight veilig gekopieerd en herstart](evidence/living-core/eva2-managed-b-current-data-transition-2026-08-28.json)
- [Nieuwe vaste kernimage: offline build, scan 0/0 en herhaalde echte data-overgang](evidence/living-core/eva2-managed-b-current-immutable-image-2026-08-28.json)
- [Nieuwe vaste kernimage live op `/eva2`, inclusief heen/terug/heen en echte HA-read](evidence/living-core/eva2-current-immutable-canary-cutover-live-2026-08-28.json)
- [Blijvend tweede slot met eigen PostgreSQL 18-volumes en gezamenlijke herstart](evidence/living-core/eva2-managed-b-retained-slot-2026-08-28.json)
- [Beheerde PostgreSQL 18-route live met eigenaar-dashboard, terugrol en herstart](evidence/living-core/eva2-managed-b-dashboard-cutover-live-2026-08-28.json)
- [Vernieuwbare Codex-toegang zonder refresh-login in de Eva2-kern](evidence/living-core/eva2-managed-b-rotating-model-lease-live-2026-08-28.json)
- [Geen schijnafspraak zonder echte duurzame-doelprovider](evidence/living-core/eva2-managed-b-durable-goal-admission-gate-live-2026-08-28.json)
- [Volledige beheerde cold start achter de A/B-terugval](evidence/living-core/eva2-managed-b-full-cold-start-fallback-live-2026-08-28.json)
- [Schone offline Hindsight-start zonder cache- of tijdzonewaarschuwingen](evidence/living-core/eva2-managed-b-hindsight-offline-clean-start-live-2026-08-28.json)
- [Externe rebootcontrole klaargezet en kapotte terugval hersteld](evidence/living-core/eva2-managed-b-external-reboot-arm-2026-08-29.json)
- [Actuele releasebestanden en cockpitbewijs opnieuw verzegeld](evidence/living-core/eva2-managed-b-release-input-reseal-2026-08-29.json)
- [SOUL.md via de lokale wizard ondertekend, geactiveerd en teruggezet](evidence/living-core/eva2-installer-identity-lifecycle-loopback-2026-08-29.json)
- [Installer hervat dezelfde identiteitswijziging na iedere serverherstart](evidence/living-core/eva2-installer-identity-resume-loopback-2026-08-29.json)
- [Installer hervat de eerste vier niet-geheime installatiekeuzes](evidence/living-core/eva2-installer-profile-resume-loopback-2026-08-29.json)
- [Connectorneutrale installerstatus zonder schijnbaar groene koppelingen](evidence/living-core/eva2-installer-capability-status-loopback-2026-08-29.json)
- [Eenmalige private secretinvoer zonder echo of schijnverificatie](evidence/living-core/eva2-installer-one-time-secret-loopback-2026-08-29.json)
- [Telegram-read-only verifier met succes, weigering en herstart zonder tweede call](evidence/living-core/eva2-installer-telegram-readonly-verification-2026-08-29.json)
- [Home Assistant API-root verifier met lokale adrespinning en nul huisacties](evidence/living-core/eva2-installer-home-assistant-readonly-verification-2026-08-29.json)
- [Veilig roteren, verwijderen en herstellen na een onderbreking](evidence/living-core/eva2-installer-secret-lifecycle-2026-08-29.json)
- [Dezelfde toegangshandelingen via vier echte wizardstarts, inclusief een oude controle die te laat antwoordt](evidence/living-core/eva2-installer-secret-lifecycle-wizard-2026-08-29.json)
- [AgentMail-inboxcontrole zonder berichten, mutaties of bewaarde providerdata](evidence/living-core/eva2-installer-agentmail-readonly-verification-2026-08-29.json)
- [Begrensde modelcontrole zonder refresh-login, tools of verzonnen kosten](evidence/living-core/eva2-installer-codex-model-verification-2026-08-29.json)
- [Model en Telegram kiezen, toegang invoeren en controleren via echte lokale wizardservers](evidence/living-core/eva2-installer-provider-wizard-2026-08-29.json)
- [Home Assistant en AgentMail instellen en controleren via echte lokale wizardservers](evidence/living-core/eva2-installer-endpoint-wizard-2026-08-29.json)
- [Telegramcontrole met zichtbaar en hashgebonden minimaal gebruiksrecht](evidence/living-core/eva2-installer-provider-wizard-scopes-2026-08-29.json)
- [Home Assistant en AgentMail met ieder hun eigen zichtbare minimale gebruiksrecht](evidence/living-core/eva2-installer-endpoint-wizard-scopes-2026-08-29.json)
- [Toolvrije modelcontrole met een apart hashgebonden gebruiksrecht](evidence/living-core/eva2-installer-codex-model-scopes-2026-08-29.json)
- [Eén hervatbaar plan met bevestigde harde limieten en herstel na procesuitval](evidence/living-core/eva2-installer-budgeted-installation-plan-wizard-2026-08-29.json)
- [Bevestigde limieten echt afgedwongen in de modelruntime en PostgreSQL](evidence/living-core/eva2-runtime-resource-budget-postgres-2026-08-29.json)
- [Dagelijkse Hindsight-reflectielimieten blijven hard bij race, herstart en daggrens](evidence/living-core/eva2-daily-reflection-budget-postgres-2026-08-29.json)
- [Geheugenquota per persoon, groep, project en installatie blijven hard bij race, herstart en verwijdercrash](evidence/living-core/eva2-memory-storage-budget-postgres-2026-08-29.json)
- [Eén herstartveilige wisopdracht voor Hindsight én ruwe gesprekstekst](evidence/living-core/eva2-cross-store-conversation-erasure-postgres-2026-08-29.json)
- [Bronfeit, afgeleide observatie, persoonsstijl en wachtende geheugenkopie echt gewist](evidence/living-core/eva2-derived-memory-erasure-disposable-2026-08-29.json)
- [Oude back-up na wissen geweigerd vóór iedere databasevraag](evidence/living-core/eva2-portable-restore-erasure-authority-2026-08-29.json)
- [Wisknop maakt oude back-ups eerst ongeldig, ook bij crash en gelijktijdige wisacties](evidence/living-core/eva2-owner-erasure-restore-authority-fail-closed-2026-08-29.json)
- [Nieuwe Debian 13-kernimage: beperkte start en verse scan 0 kritisch/0 hoog](evidence/living-core/eva2-owner-erasure-debian13-candidate-image-2026-08-29.json)
- [Resourcebudget, semantische toolkeuze en volledige negen-dienstencanary](evidence/living-core/eva2-managed-b-resource-budget-semantic-tool-canary-2026-08-29.json)
- [Eigenaar-geauthenticeerde knop om één dashboardbeurt te wissen](evidence/living-core/eva2-owner-conversation-erasure-control-2026-08-29.json)
- [Huidige Hindsight-omvang gemeten zonder teksten of namen te lezen](evidence/living-core/eva2-current-hindsight-storage-measurement-2026-08-29.json)
- [Ook verkenning, proefbouw en releasevoorstel gebruiken dezelfde persoonsgrens](evidence/living-core/eva2-personal-memory-write-boundary-2026-08-29.json)
- [Live `/eva2`-koppeling](evidence/living-core/eva2-current-candidate-live-canary-2026-08-28.json) en [afgeschermde, vernieuwbare Codex-toegang](evidence/living-core/eva2-rotating-access-only-model-lease-live-2026-08-28.json)

Meer details staan in [PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[EVA2_MOGELIJKHEDEN_EN_GATEN.md](EVA2_MOGELIJKHEDEN_EN_GATEN.md) en
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md).
