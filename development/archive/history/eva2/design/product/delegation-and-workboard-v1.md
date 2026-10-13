# Eva 2 — subagents en duurzaam werkbord

Bijgewerkt: 26 augustus 2026.

Deze ontwerp- en statusbeschrijving is historisch. De hulpagentroute is later
technisch live opgeleverd; zie [AU-002 livebewijs](../../evidence/product/AU-002-concurrent-stop-live-2026-09-12.json)
en het [actuele werkbord](../../WERKBORD.md). De toenmalige providerloze/niet-live
stand hieronder is geen huidige installatieclaim. Eigenaaracceptatie blijft apart.

## Productbelofte

Eva kan straks vanuit een gesprek werk opdelen, één of meer begrensde
subagents inzetten en langdurig werk op een zichtbaar Kanbanbord volgen. Dit
zijn twee verschillende vormen van samenwerken:

- een **subagent** is een korte fork/join-opdracht voor onderzoek, analyse,
  uitvoering of onafhankelijke controle;
- een **werkbordtaak** is duurzaam, kan wachten, herstarten, van worker wisselen,
  opmerkingen ontvangen en pas na bewijs worden afgerond.

Eva blijft de gesprekspartner en eigenaar van het eindantwoord. Een worker
krijgt een rol en een afgeleid mandaat, maar geen eigen Eva-persona, geen
ongefilterd gezinsgeheugen en geen impliciete toegang tot secrets of tools.

## Huidige eerlijke stand

- Eva heeft nu één **providerloze, technisch geteste verticale subagentlijn**:
  een toegelaten taak wordt atomair in PostgreSQL gezet, geleased, precies één
  keer door de Pydantic-runner gestart, duurzaam afgerond en als gesaneerde
  kaart op het read-only Kanbanbord getoond. Herstart na `call_started` doet
  geen tweede modelcall. Dit is nog geen echte provider-, proces-, chat- of
  B-liveproef.
- De generieke subagentmotor hoeft niet meer zelf gebouwd te worden: een exact
  gepinde Pydantic AI `2.34.0` + Harness `0.25.0`-kandidaat voert echte
  toolvrije child-runs uit. De 22 tests en onafhankelijke review zijn groen.
  Een tweede providerloze proef gebruikt voor reeds toegelaten duurzame taken
  rechtstreeks de publieke Pydantic AI-runner. Getypeerde output, usage,
  cancellation en gesaneerde foutcategorieën zijn daarmee na een eerste
  `NO-GO` in 23 extra tests groen. De onafhankelijke herreview van `b7e9af7`
  is groen. Het contract en de zesde taaksoort `delegated_work` zijn inmiddels
  ook version-matched geregistreerd en in echte PostgreSQL 16.13 beproefd.
  Daarboven werken nu de handoffwriter, lease/reclaim, call-startgrens,
  resultaatopslag en veldarme Kanbanprojectie als één providerloze kandidaat.
  Portable-v2-export, eigenaar-authenticatie, een harde processandbox, echte
  provider en B-promotie zijn nog niet gekoppeld.
- `plans`, `plan_steps`, `attention_items` en de operationele tijdlijn vormen
  al een duurzame taakgraaf met leases en crashherstel. Zij bewijzen nog geen
  multi-workerdelegatie.
- De lokale Eva-cockpit toont status, identiteit, Mijn kring en het duurzame
  vijfkoloms-Kanbanbord op één pagina. Het bord leest taken uit Eva's opslag en
  bevat geen hardgecodeerde demokaarten. Authenticatie en bordbediening zijn
  nog open.

## Dependency-first uitvoerkeuze

Eva bouwt geen eigen subagentproces, modelrunner, budgetmeter of algemene
workerqueue. Pydantic AI `2.34.0` met Harness `0.25.0` draait in een aparte
hashgebonden 42-package-omgeving. Harness levert korte parentgestuurde
fork/join; Pydantic AI `Agent.run()` levert de reeds duurzaam gekozen worker
met getypeerde output, usage, cancellation, request-/tokenlimieten en
coöperatieve async-time-out. Eva voegt alleen de absolute deadline,
verzegelde model-/worker-/prijsrelease, private telemetrygrens en duurzame
contracthash toe. De proef staat in
`experiments/harness-subagents-v1/`; de
onafhankelijke `GO` voor exact `55cf7cc` staat in
`reviews/harness-subagent-engine-55cf7cc.md`.
De begrensde directe-workerreview staat in
`reviews/pydantic-tool-free-child-candidate-b7e9af7.md`.

Harness' publieke `delegate_task` retourneert alleen tekst en is daarom niet de
duurzame workerinterface. Eva bewaart uitsluitend haar Pydantic-contract,
resultaat en genormaliseerde usage; Pydantic AI voert de modelrun uit. Dit is
nog geen processandbox of harde kill. De eerste productkoppeling blijft daarom
privé, contextvrij, toolvrij, workspacevrij en effectvrij. Een procescrash na
`call_started` wordt ambigu en mag geen blinde tweede modelcall veroorzaken.
`Child_network_access=false` sluit tools en vrije child-egress; de vastgepinde
modeladapter mag uiteraard alleen naar zijn vooraf toegelaten provider. Ook
telt Pydantic AI voltooide antwoorden en niet betrouwbaar ieder transport-
attempt, zodat Eva's ledger de callstart vóór I/O zelf moet vastleggen.

DBOS blijft alleen kandidaat voor start, wake-up en scheduling. Stock `2.31.0`
mist Eva's begrensde retry/no-DDL/commit-ambiguïteitsgaranties; de volgende proef
is een smalle `2.31.0+eva.1`-rebase. DBOS wordt geen vervanger van grants,
taakbetekenis, extern actiebewijs of readback.

Eva houdt uitsluitend de afgeleide grant, duurzame handoff, eigen usage-receipt,
taakgeschiedenis,
bewijscontrole en de beslissing wat in de parentcontext terugkomt. Het Kanban
wordt een React-Admin-OSS-projectie boven diezelfde service; `dnd-kit` levert
drag-and-drop. Dit is geen tweede taakdatabase. React-Admins Enterprise-Kanban
wordt niet als gratis open source aangenomen. Pas een gemeten ontbrekende
uitkomst in de verticale proef rechtvaardigt eigen infrastructuurcode.

## Eén autoriteit, drie bedieningsvormen

Chat, dashboard en workers gebruiken dezelfde PostgreSQL-service en dezelfde
taak- en event-ID's:

| Oppervlak | Bedoeling | Grens |
|---|---|---|
| Eva-chat | “Deel dit op”, “laat een onderzoeker meekijken”, “zet dit stil” of “wat blokkeert?” | Eva controleert identiteit, mandaat en gevolgen vóór iedere wijziging |
| Dashboard | Borden, kaarten, afhankelijkheden, workers, pogingen, kosten, bewijs en blokkades bekijken en gecontroleerd bedienen | Geen domeinlogica in de browser; geen ruwe secrets; iedere write via dezelfde service |
| Worker/subagent | Eén exact toegewezen taak lezen, heartbeat geven, bewijs/artifacts toevoegen, afronden of blokkeren | Alleen de eigen taak, contextsnapshot, werkruimte en toegelaten tools |

Er komt dus geen losse Kanban-SQLite-database naast Eva's ledger. De UI is een
projectie van dezelfde duurzame waarheid waarop crashherstel, consent,
idempotency en readback al rusten.

## Subagentcontract

Iedere spawn bindt minimaal:

- parent episode, plan, taak en aanvragende geverifieerde persoon;
- doel, rol, verwachte uitkomst en stopvoorwaarde;
- expliciete contextreferenties met persoon-/groep-/projectscope;
- toegelaten tools, resources, netwerk, bestanden en providerprofielen;
- model/provider en maximum input-, output-, kosten- en tijdsbudget;
- maximale paralleliteit, pogingenteller, lease, heartbeat en deadline;
- outputschema, bewijsvereiste en teruglevering aan de parent;
- intrekking, pauze, annulering en opruiming van tijdelijke werkruimte.

V1 kent alleen parentgestuurde delegatie: Eva mag workers spawnen, maar een
worker mag niet zelfstandig een onbeperkte nieuwe boom maken. Een worker kan
wel een vervolgtaak voorstellen. Recursieve delegatie is een latere,
afzonderlijke policygate. Zo voorkomt Eva runaway swarms en onzichtbare token-
of kostengroei zonder de toekomstige mogelijkheid uit te sluiten.

## Werkbord en dashboard

Het eerste bord toont minimaal:

- `inbox`, `planned`, `ready`, `running`, `waiting`, `needs_help`, `review`,
  `done` en `cancelled`;
- parent/child- en dependencyrelaties;
- assignee/workerrol, modelkeuze, lease, heartbeat en actuele poging;
- doel, korte context, opmerkingen, artifacts, receipts en readback;
- token-, kosten-, looptijd- en retrybudget versus werkelijk gebruik;
- pauze, stop, hervatten, opnieuw toewijzen, review vragen en intrekken;
- live maar gesaneerde updates, met een heropenbare append-only geschiedenis.

Een kaart naar `done` slepen is nooit zelf bewijs van succes. Voor een extern
of fysiek effect blijft de kaart `review`/`waiting` totdat Eva de vereiste
providerreceipt en onafhankelijke readback heeft. Destructieve bordacties
vereisen een herstelbare bevestiging en verwijdering wordt standaard archiveren.

## Kleine opleverslices

De eerste verticale kandidaat is technisch gesloten: opslag → lease →
eenmalige providerloze Pydantic-call → resultaat → read-only bord. Dat sluit
nog niet automatisch de bredere WB-gates hieronder; die eisen onder meer
portable export, echte provider/processen, chatbediening en live bewijs.

- [ ] WB01 — Eén connectorneutraal `DelegatedWork/v1`-contract en duurzame
  PostgreSQL-projectie boven de bestaande plan-, attention- en eventtabellen;
  geen tweede taakdatabase. WB01a (contract/register) en WB01b (de zesde
  taaksoort in de bestaande rij) zijn getest en onafhankelijk gereviewd. De
  handoffwriter, cross-objectbindingen en portable-v2-exportgrens sluiten WB01
  in de volgende afzonderlijke slice.
- [ ] WB02 — Parentgestuurde subagentspawn met afgeleid capability-, context-,
  token-, kosten-, tijd- en workspacegrant; harde paralleliteits- en dieptelimiet.
- [ ] WB03 — Workerprotocol voor show/heartbeat/comment/artifact/complete/block/
  review, met schema- en bewijsvalidatie voordat resultaat de parentcontext in
  mag.
- [ ] WB04 — Lease-expiry, procescrash, reclaim, retrylimiet, stop en hervatten
  bewijzen zonder dubbele provideractie of verloren handoff.
- [ ] WB05 — Dashboard D07: de eerste read-only projectie met workers,
  pogingen, gebruik en blockers staat in de cockpit. Afhankelijkheden, live
  events, eigenaar-authenticatie en de volledige taakdetailweergave sluiten
  deze bredere gate later.
- [ ] WB06 — Dashboard D08: dezelfde gecontroleerde bordacties via Eva-chat en
  dashboard:
  create, comment, prioriteit, assign, pause, cancel, resume en review. Beide
  schrijven via één service en één auditpad.
- [ ] WB07 — Echte onderzoek→uitvoering→onafhankelijke review-opdracht met
  meerdere workers, crashherstel, token-/kosten-/latencyreceipt en expliciete
  gebruikersacceptatie op de bevroren eindimage.

## Concurrentie- en nonclaimgrens

Hermes documenteert zowel korte `delegate_task`-subagents als een duurzaam,
multi-profile Kanbanbord met dashboardbediening. Eva heeft die productervaring
nu nog niet. WB01-WB07 is daarom een expliciete concurrentiegate; een intern
ontwikkelteam met agents telt niet als bewijs van Eva-runtimefunctionaliteit.

Eva hoeft Hermes' opslag of procesmodel niet te kopiëren. De vergelijkbare
uitkomst is: zichtbaar parallel werk, duurzame handoffs, menselijke controle,
herstart en meetbare grenzen, terwijl Eva's bestaande identiteit, consent,
ledger en fysieke readback leidend blijven.

## Primaire bronnen

- Pydantic AI Harness subagents:
  <https://pydantic.dev/docs/ai/harness/subagents/>
- Pydantic AI Harness exact compatibele kandidaat:
  <https://github.com/pydantic/pydantic-ai-harness/tree/v0.25.0>
- Pydantic AI met DBOS:
  <https://github.com/pydantic/pydantic-ai/blob/v2.34.0/docs/durable_execution/dbos.md>
- React-Admin OSS: <https://github.com/marmelab/react-admin>
- `dnd-kit`: <https://github.com/clauderic/dnd-kit>
- Kanban en onderscheid met `delegate_task`:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md>
- Webdashboard:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md>
