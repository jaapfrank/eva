# Eva 2: wat werkt en wat nog moet

Bijgewerkt: 24 augustus 2026, 12:16 CEST.

## Wat Eva 2 nu daadwerkelijk kan

- Een normaal gesprek voeren vanuit één configureerbare identiteit.
- Recente gesprekken uit PostgreSQL meenemen zonder na een herstart opnieuw te
  beginnen.
- Persoonlijke feiten, voorkeuren en correcties via Hindsight onthouden.
- Geheugen per persoon en per project gescheiden houden.
- Een Telegramvoorkeur later via een webgesprek van dezelfde persoon herinneren.
- Hindsight snel gebruiken in gesprekken: alleen `recall` zit in de chatroute.
- Nieuwe herinneringen asynchroon via `retain` opslaan.
- Langzame reflectie buiten de chatroute uitvoeren wanneer Eva niets te doen heeft.
- Actuele Home Assistant-toestanden lezen.
- Home Assistant-geschiedenis over een begrensde periode lezen.
- Lampen en schakelaars op natuurlijke namen vinden en bedienen.
- Pas succes melden na een nieuwe, onafhankelijke statuscontrole.
- Eenvoudige en samengestelde Home Assistant-automatiseringen aanmaken.
- Een Tuya-naar-LocalTuya-migratie inventariseren en per apparaat plannen.
- De onderhouden LocalTuya-bron veilig voorbereiden met versie- en
  goedkeuringscontrole.
- Langlopende doelen bewaren, op extern bewijs wachten en later hervatten.
- Na een crash doorgaan zonder hetzelfde bewezen effect opnieuw uit te voeren.
- Een mislukte levering, annulering en echte uitvoering van elkaar onderscheiden.
- Eén opdracht naar meerdere ontvangers sturen en per ontvanger het resultaat
  bewaren.
- Actuele primaire bronnen zoeken en een heropenbaar Markdownrapport bewaren.
- Begrensde document-, bestands- en repositorytaken uitvoeren met hashes,
  validatie en rollback van alleen haar eigen wijziging.
- Zelf een kleine echte verbetering in een geïsoleerde repository kiezen en
  afronden.
- In een testomgeving een productbestand, prijsvoorstel, betaalroute en
  downloadfulfillment maken.
- Vanaf een lege PostgreSQL-installatie starten en na een harde herstart verdergaan.
- Als geïsoleerde B-versie naast productie A draaien zonder A te vervangen.

## Wat nog niet af is

- Eva 2 draait nog niet als de definitieve productie-Eva; Eva A blijft actief.
- Het huidige Home Assistant-token van B moet worden ingetrokken en vervangen;
  het staat nog inspecteerbaar in containerconfig in plaats van als file-secret.
- De recente Living Core-bron, architectuur en receipts hebben nog geen reeks
  kleine, onafhankelijk gecontroleerde Git-checkpoints.
- Sensorbeslissingen en levering moeten uit de Hindsight-cold-path naar eigen
  duurzame workers met backlog- en livenessbewijs.
- De residentpolicy heeft nog geen atomische PostgreSQL-state, exacte urgente
  resourcebinding, TTL, activeringsgrens of hercontrole vlak vóór levering.
- Zeven oude proactieve expressies blijven daarom gesloten en mogen bij een
  latere kanaalactivatie niet alsnog worden verstuurd.
- De definitieve Telegramconnector heeft nog een eigen Eva 2-bottoken en echte
  eigenaar-gecontroleerde persoonsbindings nodig.
- De menselijke gesprekservaring is nog niet door jou geaccepteerd; vooral
  natuurlijkheid, initiatief en bondigheid moeten in echte Telegramgesprekken
  stabiel blijken.
- De eerste residentperiode is na onafhankelijke review een shakedown geworden,
  geen officiële zevendaagse praktijktest. Na het bevriezen van de echte
  kandidaat begint een nieuwe klok van zeven dagen.
- Temperatuurhysterese, camerameldingen samenvoegen en nachtstilte zijn als
  afzonderlijke aandachtsmodule in aanbouw en nog niet live gekoppeld.
- Proactieve Telegrammeldingen hebben nog geen goedgekeurd doelkanaal voor Eva 2.
- Echte LocalTuya-installatie en apparaatmigratie zijn nog niet uitgevoerd;
  bronregistratie, pakketinstallatie en eerste apparaat blijven drie aparte
  mutatiestappen.
- Echte Stripe-testmodus ontbreekt nog. Eva verdient dus nog geen echt geld.
- AgentMail voor Eva 2 wacht op een nieuw eigen read-only secret.
- De volledige productiepromotiegate, ondertekende release en terugrolceremonie
  zijn nog niet met de uiteindelijke kandidaat afgerond.
- Langdurige betrouwbaarheid, stilte, herstel en geheugengedrag moeten nog zeven
  dagen met echte gebeurtenissen worden bewezen.

## Stand tegenover OpenClaw, Hermes en Agent Zero

- Correctie op de eerdere vergelijking: die was technisch echt en gebruikte
  actuele, gepinde builds, maar dekte op alle vier slechts C01, T02 en C04.
  Skills, plugins, gateways, nodes, kanalen, browser/desktop, MCP, schedulers en
  multi-agentwerk waren niet volledig geconfigureerd of vergeleken.
- Eerlijke hoofdconclusie: Eva 2 is nog niet algemeen aantoonbaar beter. Daarvoor
  ontbreken dezelfde volledige verborgen en zevendaagse tests voor alle vier.
- In één geblindeerde gesprekscase had Eva 2 wel de hoogste gemiddelde score:
  `22,6667`. Hermes had `22,3333`; OpenClaw en Agent Zero ieder `21,3333`.
- Dit waren drie afzonderlijke GPT-5.4-mini-judgecalls met verborgen
  frameworknamen en geroteerde volgorde, geen menselijke jury. Eva kreeg twee
  beste-stemmen en Hermes één.
- In twee zichtbare native ontwikkelcaptures was Eva 2 sneller dan alle drie:
  - C01: Eva `7,512 s`; OpenClaw `26,148 s`; Hermes `16,691 s`; Agent Zero `46,272 s`.
  - T02: Eva `6,375 s`; OpenClaw `16,883 s`; Hermes `19,248 s`; Agent Zero `19,468 s`.
- Alleen Eva had bij T02 een echte Home Assistant-readback. De andere drie waren
  daar niet met Home Assistant verbonden, dus dit is geen gelijkwaardige
  toolvergelijking.
- De frameworks gebruikten dezelfde modelnaam en reasoningstand, maar hun eigen
  systemprompts, tools en contextgroottes verschilden. De latency is dus een echte
  native meting, geen zuivere causaliteitsmeting van alleen het framework.
- Eva 2 heeft vijftien core-live- en negen native-receipts, maar die native
  receipts zijn nog over meerdere images verdeeld. Eén eindkandidaat moet ze
  samen met core 15/15 opnieuw doorlopen. De drie concurrenten hebben diezelfde
  volledige reeksen niet doorlopen.
- De huidige voorsprong is daarom: betere eigen evidence, crashherstel,
  Home Assistant-integratie en een kleine winst in één menselijke case.
- De nog onbewezen claim is: structureel menselijker, autonomer en betrouwbaarder
  dan alle drie over alle 36 cases en zeven dagen echt gebruik.
- De volledige capabilitymatrix, de ontbrekende tests en de hergebruikroute via
  ClawHub/OpenClaw staan in `CONCURRENTIE_EN_HERGEBRUIK.md`.

## Waar je de bewijzen vindt

- Actuele voortgang: `PROJECT_STATUS.md`
- Technische architectuur en bestandseigenaarschap:
  `design/architecture/living-core-v1.md`
- Live receipts: `evidence/living-core/`
- Concurrentenrun: `evidence/living-core/lab36-eva2-openclaw-hermes-agent-zero-live-2026-08-23.json`
- Volledige product- en hergebruikmatrix: `CONCURRENTIE_EN_HERGEBRUIK.md`
- Geblindeerde gesprekscase: `evidence/living-core/lab36-c04-blind-2026-08-24.json`
- Resident-shakedown, niet de officiële zevendaagse run:
  `evidence/living-core/resident-long-run-v1-2026-08-24.json`
