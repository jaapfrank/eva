# AU-006: dezelfde documentuitkomst via de normale opdracht

Status: read-only aansluitingsvoorstel; geen nieuwe goal, bronwijziging, modelcall
of activatie. Productintegratie is onafhankelijk goedgekeurd en centraal opgenomen
in fd7105474d4b68dabd1993199976f55a98b682e2.

## Bestaand pad en concrete gaten

- `checkpoint_document_task` in conversation_actions bewaart een documentcheckpoint
  en geeft het in hetzelfde gesprek terug. Het start geen duurzame tweestappenworker.
- `create_bounded_durable_goal` bestaat, maar alleen als oude v1-wake-afspraak met
  gesloten velden, geconfigureerde provider/ontvanger en een verplichte expliciete
  wakevoorwaarde. De live core heeft die provider niet geconfigureerd. Alleen de
  envvariabele activeren bouwt geen documentplan of juiste native receiptbinding.
- `start_delegated_work` gebruikt de bestaande private owner-service, maar maakt
  een begrensde modelonderzoeksopdracht van vijf minuten. Het is geen documentgoal;
  de onderzoeker of diens resultaatcontract als doelworker presenteren is onjuist.
- De bestaande `ProviderContribution`/`bundled_provider_factories`-route kan gewone
  gesprekstools registreren. `PostgresExternalConversationTools` hergebruikt echte
  gespreksscope, stop, durable tooladmission en VerifiedToolOrchestrator. Daar past
  een kleine documentgoaladapter zonder tweede planner of modelimplementatiewijziging.

## Kleinste voorgestelde normale ingang

Een opt-in providerplug-in biedt start, status/resultaat en stop voor precies het
vooraf afgesproken documentproject. De gewone model-/toolrouter kiest deze tools;
geen trefwoordrouter en geen nieuwe eigenaarvraag binnen het bestaande mandaat.
De startactie accepteert geen owner, goal-ID, pad, native-frame of nieuwe rechten.
Status en stop verwijzen naar hetzelfde hostgebonden project. Start-replay maakt
geen nieuw project; hij toont de bestaande toestand en hetzelfde resultaat.

De plugin leest een veilige host-owned volledige goaldefinitie naast het bestaande
DocumentGoalPlan/v2. De goal-SHA moet exact overeenkomen. Dit ontbreekt nu: een hash
alleen kan de vooraf toegestane GoalSpec niet reconstrueren. De werkelijke private
ConversationTurn blijft van de bestaande ingress; actuele ownerauthority controleert
de caller en iedere actie. De adapter maakt geen fictieve ConversationTurn en
schrijft geen ruimere mandaatfile. De worker houdt zijn eigen verse uitvoercheck.

Bij start wordt alleen dezelfde GoalSpec geadmitteerd/gepland; 'ingepland' is niet
'klaar'. Status/resultaat leest de bestaande goalprojectie met beide artifacts en
alleen bij completion de werkelijke finale inhoud/locatie. Stop gebruikt dezelfde
store.cancel. Menselijke aflevering of automatische externe notificatie wordt niet
beloofd; de normale chat kan de finale tekst en bestaande workspace-locatie tonen.

Dit maakt de ene afgesproken uitkomst bereikbaar. Willekeurige nieuwe documenten
uit elke nieuwe prompt vallen niet stil onder deze stap: daarvoor moeten dynamische
planadmission én het beheer van een nieuw projectjournal/generaties apart zijn
uitgewerkt. Er worden geen actuele fixtures of journals gerecycled als product.

## Exacte minimale bronownership ter afstemming

1. Nieuw `eva2/src/eva/providers/document_goal_actions.py`: providerfactory,
   drie gesloten toolbeschrijvingen en planner/execute/observe over de bestaande
   PostgresExternalConversationTools-route en bestaande goalstore.
2. `eva2/src/eva/providers/bundled.py`: uitsluitend opt-in factoryregistratie.
3. `eva2/src/eva/providers/document_goal_plan.py`: veilig laden en exact koppelen
   van de volledige voorbereide GoalSpec; bestaande v2-plansemantiek behouden.
4. Nieuw `eva2/tests/providers/test_document_goal_actions.py` en
   `eva2/tests/postgres/test_document_goal_actions.py`: normale toolroute, actuele
   owner/stop, replay, andere persoon/groep, resultaatgrens en immutable planbinding.
5. Beperkte aanvulling in `eva2/design/architecture/living-core-v1.md` plus
   hashgebonden evidence. Geen wijziging aan pydantic/modelprovider, hulpagentstore,
   interne goals-API, mandaatimplementatie of andere planners voorgesteld.

Voor de activatie is afzonderlijke provider-/beheerownership nodig. De nieuwe
bron wordt pas gebouwd na scopeafstemming; dit document verleent geen deploy-GO.

## Activeringsafhankelijkheden en proef

- Een echte beheerde atomic-providerinstance op de bestaande private workspace,
  met eigen duurzaam afgeschermd journal en nieuwe correcte host-FD/binary/client-
  binding. De beoordeelde providerimage mag als bron dienen; de verwijderde
  wegwerpinstance, token, toolregistraties en inodebindingen mogen dat niet.
- Registeren van alleen de twee beoordeelde atomic-tools in de bestaande gateway,
  afgeschermde uitvoertoegang en nieuwe catalogusfingerprints waarin de werkelijke
  instancebinding staat. Bestaande gewone workspace-tools blijven afzonderlijk.
- Een exact gebonden echte goaldefinitie en documentplan met nieuwe unieke
  doelpaden en journalgenesis. Geen fixturetekst of fictieve eigenaar als mandaat.
- Afzonderlijke source/image/config-review voor de coreplugin en de documentworker
  (`python -m eva.bootstrap.document_goal_worker`). Alleen een envflip is onvoldoende.
  Bestaande core+delegatedworker, zeven beschermde diensten en data blijven onder
  de bestaande coördinator-/cutoverafspraken; nieuwe doelworker/providertargets moeten
  expliciet in het activatieplan worden opgenomen.
- Voor echte calls: verse readiness/quiescence, actuele eigenaarbinding/mandaat,
  catalogus/instancehashes, nieuwe paden/journal en bestaande budgetgrenzen controleren.
  Geen modelretry, capverhoging of extra eigenaarvragen als herstelroute.
- Geïsoleerde normale-routeproef vóór live: één geldige private opdracht kiest de
  aangeboden starttool, verwerkt hetzelfde project en leest echte status/resultaat;
  een andere persoon/groep en herhaalde start leveren geen nieuwe uitvoerrechten of
  dubbele writes. Gescripte modelkeuze bewijst routering, geen modelkwaliteit.
- Na expliciete activation/probe-GO: een gewone opdracht zoals 'Maak het afgesproken
  documentproject af en laat het eindresultaat zien'. Daarna zo nodig één gewone
  statusvraag; bestaand stop blijft beschikbaar. Technisch afgerond, live en door
  de eigenaar geaccepteerd blijven gescheiden. Herstart/crashbewijs hoeft niet blind
  op productdiensten herhaald te worden als de wijziging dat niet rechtvaardigt.
- Terugweg: stop alleen de nieuwe documentworker, herstel de goedgekeurde core/
  configuratie en behoud doeldata, twee bestanden en native journal. Verberg de
  niet-beschikbare nieuwe tool bij rollback; wis geen receipts om te kunnen herstarten.

## Retentie en cleanup

De providerowner heeft vijf wegwerpcontainers en twee eigen netwerken opgeruimd.
De oude core-prepared gateway-URL is niet meer bruikbaar. Negentien bewijsbestanden
plus native journals zijn privé behouden; de coördinator beheert verdere retentie.
Cleanupreceipt: `/home/administrator/project/.eva2-artifacts/workspace-broker-v2-20260913/cleanup-receipt.json`.
SHA-256: 545f1b86ba1f61a1b180ac6dd6bb1ddcf73bf45c9b02e3276d2cedda3e06888b
