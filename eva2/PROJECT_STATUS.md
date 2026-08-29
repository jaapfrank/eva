# Eva 2 — korte projectstatus

Laatst bijgewerkt: 29 augustus 2026, 13:15 CEST

## Heel kort

Eva2 werkt echt naast Eva1. Alleen een bericht met `/eva2` gaat naar Eva2.

De nieuwe DBOS-loze Eva2 staat nu op slot 3. Zij praat via de bestaande
Codex-koppeling, heeft haar eigen persoonlijkheid, bewaart gesprekken in
PostgreSQL en gebruikt Hindsight voor langetermijngeheugen.

De route is verliesloos heen, terug en opnieuw heen geschakeld. Een nieuw
testfeit kwam na iedere databasekopie exact terug. Eva1 bleef gezond.

Eva2 is nog geen volledige vervanger voor Eva1. Brede huisbediening, volledige
voice, algemene browserbediening, een consumenteninstallatie en de lange
praktijktest zijn nog niet klaar.

## Wat werkt al

- Eva1 blijft de gewone standaardservice.
- `/eva2` gaat nu naar de nieuwe 35-pakket-Eva2 op slot 3, poort `44710`.
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
- De huidige kern kiest per vraag maximaal vier passende tools. Een gewone
  tekstvraag krijgt geen onnodige tools mee.
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
- De harde codeteller is nog rood, maar de eerste sanering is echt uitgevoerd:
  7.432 oude test- en proefregels zijn uit de runtime gehaald. Eva2 telt nu
  89.442 productregels en mag er maximaal 75.000 hebben.
- De automatische rebootcontrole verwacht nog slot 4 als actieve route. Zij
  moet eerst voor de nieuwe actieve slot-3-route worden herbewapend; anders zou
  een latere rebootcontrole terecht rood worden.
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

1. Na een echte Windows/WSL-reboot controleren of Eva1, Eva2, beide databases,
   geheugen, dashboard en de terugvalroute vanzelf gezond terugkomen.
2. Daarna de langere praktijktest starten en fouten, vertraging en geheugengroei
   bewaken.
3. De begrensde huisbediening stap voor stap uitbreiden zonder de aparte
   controle en herstartveiligheid kwijt te raken.
4. De automatische rebootcontrole voor actief slot 3 en terugvalslot 4
   herbewapenen voordat iemand de echte Windows/WSL-reboot uitvoert.
5. Nog minstens 14.442 productregels gericht verwijderen of vervangen zonder
   bewezen functies weg te laten of de meetgrens te versoepelen.

## Belangrijk verschil

- **Eva1:** de huidige standaardservice in `/home/administrator/project/Bottie`
  met SQLite.
- **Eva2:** de beheerde kandidaat in
  `/home/administrator/project/Bottie-candidates/v2/eva2` met PostgreSQL en
  Hindsight.
- Alleen een expliciet `/eva2`-bericht gaat naar Eva2.

## Laatste bewijs

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
- [Kleinere DBOS-loze runtimekandidaat met scan en databaseproef](evidence/product/performance-complexity-pc01-dbosless-runtime-candidate-2026-08-29.json)
- [Exacte DBOS-loze eindimage met scan en databaseherstart](evidence/living-core/eva2-runtime35-exact-image-schema-restart-2026-08-29.json)
- [Dezelfde image met echt Codex, Hindsight en volledige herstart](evidence/living-core/eva2-runtime35-real-model-hindsight-restart-2026-08-29.json)
- [Nieuwe image live met verliesloze heen-terug-heenproef](evidence/living-core/eva2-runtime35-live-bidirectional-cutover-2026-08-29.json)

Technische details staan in [PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[EVA2_MOGELIJKHEDEN_EN_GATEN.md](EVA2_MOGELIJKHEDEN_EN_GATEN.md) en
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md).
