# AU-006: beheerplan voor de gewone documentopdracht

Status: concreet voorbereidingsplan, geen uitgevoerde installatie. De nieuwe
chataansluiting moet onafhankelijk worden beoordeeld. De bestaande productproef
voor de documentworker/native provider blijft afzonderlijk bewijs. De normale
routerproef gebruikt echte SQL/ingress/authority met een opgeslagen
resultaatfixture; zij bewijst geen nieuwe native publicatie of modelkeuze.

## Eigenaarschap en configuratie

- Coördinator: leg één echte GoalSpec/v2 vast voor het al afgesproken project,
  met werkelijke eigenaar, twee instructies, lokale ontvanger en wakevoorwaarde.
  Geen fixturetekst of testidentiteit promoveren. De host beheert dit bestand;
  model, gesprek en toolantwoord kunnen het niet schrijven of overrulen.
- Providerbeheer: reserveer een nieuwe beheerde atomic-instance, voorgestelde
  naam `eva2-document-workspace-au006`, zonder publieke poort. Gebruik de
  beoordeelde image
  `sha256:f7d9feba8359a0d7d3fbe18703cb8c3a6bbe5e274aeccda15b83c26b6010bd00`
  uitsluitend na verificatie van de actuele provenance. Bind de echte private
  workspace als `/workspace`, een nieuw eigen blijvend afgeschermd journal als
  `/journal` en een nieuw veilig `/run/eva2/atomic-binding.json`. Controleer
  directory-dev/inode en binary/clienthashes volgens de bestaande providerprocedure.
  Geen verwijderde testcontainer, inodebinding, token of journal hergebruiken.
- Gatewaybeheer: registreer alleen `workspace_create_bound` en
  `workspace_observe_bound` voor deze instance in de bestaande afgeschermde
  gateway. Pin de echte namen, schemas, beschrijvingen en catalogusfingerprints.
  De worker ontvangt deze catalogus. De chatprovider heeft geen raw-native tools
  nodig: voeg die niet als onderdeel van deze wijziging aan het modelcatalogus toe.
  Bestaande gatewaytools, toegang en eigenaartoewijzing blijven onder hun eigen beheer.
- Coördinator en beheer samen: kies twee nieuwe unieke platte `.txt`/`.md`-paden
  onder `/proof-state`, controleer afwezigheid zonder iets te schrijven en leg
  `DocumentGoalPlan/v2` vast met de echte catalogus- en authorityhash. Maak daarna
  `EvaPreparedDocumentGoal/v1`: volledige `goal`, exacte `plan_sha256` en exacte
  `conversation_project_id` (null voor de normale projectloze private chat).
  Beide bestanden krijgen de huidige veilige runtimebestandsrechten en worden
  read-only gemount. De planinhoud plus definitie wordt vooraf concreet beoordeeld.
- Corebeheer: bouw/pin een kandidaatimage met de gereviewde bron. Voeg alleen
  `EVA_DOCUMENT_GOAL_DEFINITION_FILE` en de planmount/config toe aan de goedgekeurde
  coreconfiguratie; behoud bestaande volledige MCP-autonomyconfiguratie. De
  gebundelde MCP-autonomyfactory heeft eigen configuratievereisten: een losse
  mandate-envvariabele is dus geen veilige activeringsprocedure. Controleer de
  gehele providercompositie vóór vervangen van de core.
- Workerbeheer: voorgestelde nieuwe naam `eva2-document-goal-worker-au006`, dezelfde
  gereviewde bronimage, entrypoint `python -m eva.bootstrap.document_goal_worker`.
  Mount database-DSN, plan, bestaand mandaat, worker-catalogus en gatewaytoken als
  veilige runtimebestanden. Gebruik exact dezelfde onbewerkte tenant/installatie
  als de core (geen opnieuw afgeleide UUID-scope). Geef alleen de benodigde
  private database/gatewaynetwerken; geen modelendpoint of externe ontvanger.

De twee nieuwe containernamen zijn voorstellen, geen gevonden of aangemaakte
live-instanties. Hun definitieve namen, mounts, netwerken, envdigest en image-ID
moeten vóór activatie in de centrale beheerscope staan. Geen blind envflip.

## Volgorde, readiness en terugweg

1. Onafhankelijke bronreview en integratie. Beheer levert een exacte compose/config-
   diff voor provider, gatewayregistratie, core en worker met imageherkomst,
   mountrechten, budgetgrenzen en een terugzetbare kopie van de huidige coreconfig.
2. Controleer actuele kern/quiescence, eigenaar en mandaat, protected diensten,
   databasebereik, native binding en lege journalgenesis. Houd de nieuwe worker
   nog uit. Controleer catalogus via read-only list en composeer de kandidaat
   zonder een modelcall of documentwrite. Een mislukte preflight geeft geen retry-
   of budgetverruimingstoestemming.
3. Na afzonderlijke activatie-GO: activeer exact de gereviewde instance/core/worker.
   Zonder goaladmission doet de documentworker geen werk. Controleer dat alleen
   de bedoelde private eigenaar de drie tools krijgt; groep/andere persoon niet.
   De gedelegeerde onderzoeksworker en zeven beschermde services vallen niet
   onder deze wijziging. Een noodzakelijke kernaanwending valt onder de bestaande
   centrale cutoverprocedure, nooit onder een impliciete toestemming hier.
4. Na probe-GO: één normale private opdracht: “Maak het afgesproken documentproject
   af en laat het eindresultaat zien.” Verwacht eerst een eerlijke ingeplandstatus.
   Vraag zo nodig daarna “Laat het eindresultaat van het documentproject zien.”
   Verwacht exact de geverifieerde finale tekst, locatie en completed-status.
   Geen tweede toestemmingsvraag bij bestaand mandaat. Noteer werkelijke modelcalls,
   toolcalls, wachttijd, kosten (onbekend als niet gemeten), twee native receipts en
   overeenkomst van finale inhoud/hash. Dit is de nog open model-/gebruikersproef.
5. Controleer herhaalde start: dezelfde goal en geen derde write. Een aparte
   stopproef op productie vereist een eigen expliciet begrensde proefscope;
   reeds bewezen crash/stopgevallen worden niet blind op live opnieuw uitgevoerd.

Terugweg: stop alleen de nieuwe documentworker; herstel de vastgelegde coreimage
en configuratie en verberg de drie tools door de opt-in uit te zetten. Behoud
goal/timeline, beide documenten en native journal. Stop indien nodig uitsluitend
de nieuwe provider, behoud zijn volumes en gatewayregistratiebewijs. Wis geen
receipts om een nieuwe poging mogelijk te maken. Een actief doel blijft zichtbaar
als niet verder uitgevoerd; rollback maakt het niet technisch voltooid.

Gebouwd, getest, gereviewd, live en door Jaap geaccepteerd blijven afzonderlijk.
Er is nog geen claim voor willekeurige nieuwe documentopdrachten of meerdaags werk.
