# Eva 2 — korte projectstatus

Laatst bijgewerkt: 28 augustus 2026, 19:20 CEST

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
- De gewone gebruiker kan Eva2 nog niet zelf installeren, herstellen en
  bijwerken met één eenvoudige wizard.
- De kernhelft van de beheerde negen-dienstenopstelling is nu de actieve
  `/eva2`-route. De aparte doelworker en Telegramingang zijn nog niet gestart:
  de gevonden doelprovider is alleen een testprovider en Telegram mag niet met
  een nep-/ingetrokken token of naast Eva1 dubbel pollen. Daarna moeten ook een
  computerreboot en de zevendaagse praktijktest volgen.
- Eva1 moest bij de derde geplande servicewissel na twintig seconden hard
  stoppen. Daarna waren alle vijf controles groen en de tijdelijke traagheid
  verdween, maar dit blijft een zichtbaar onderhoudspunt.
- Bij deze cold start probeerde Hindsight één ontbrekend cachekenmerk op zijn
  alleen-lezen bestandssysteem te noteren. Dat gaf een logwaarschuwing maar geen
  fout: 262 geheugenunits, 16 persoonsmodellen en de API bleven beschikbaar.
  De offline-cache-instelling moet vóór de echte hostreboot nog stiller worden.
- Officiële AgentBench-, GAIA- en OSWorld-scores zijn nog niet uitgevoerd.
- De volledige eindvergelijking met OpenClaw, Hermes en Agent Zero is nog niet
  groen.

## Waar ik nu aan werk

1. Begrensde huisbediening stap voor stap uitbreiden zonder de harde controle
   en herstartveiligheid kwijt te raken.
2. De rebootproef uitvoeren en controleren dat Eva1, `/eva2`, beide databases,
   geheugen, dashboard en terugval vanzelf goed terugkomen.
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
- [Live `/eva2`-koppeling](evidence/living-core/eva2-current-candidate-live-canary-2026-08-28.json) en [afgeschermde, vernieuwbare Codex-toegang](evidence/living-core/eva2-rotating-access-only-model-lease-live-2026-08-28.json)

Meer details staan in [PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[EVA2_MOGELIJKHEDEN_EN_GATEN.md](EVA2_MOGELIJKHEDEN_EN_GATEN.md) en
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md).
