# Eva 2 — subagents en duurzaam werkbord

Bijgewerkt: 25 augustus 2026.

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

- Het ontwikkelproces kan onafhankelijke agents gebruiken; de Eva-runtime zelf
  kan nog geen algemene subagent spawnen.
- `plans`, `plan_steps`, `attention_items` en de operationele tijdlijn vormen
  al een duurzame taakgraaf met leases en crashherstel. Zij bewijzen nog geen
  multi-workerdelegatie.
- Dashboard D01 en D02 tonen lokaal read-only runtime- en IdentityPackinformatie.
  Er is nog geen Eva-Kanbanbord en geen live workerbediening.

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

- [ ] WB01 — Eén connectorneutraal `DelegatedWork/v1`-contract en duurzame
  PostgreSQL-projectie boven de bestaande plan-, attention- en eventtabellen;
  geen tweede taakdatabase.
- [ ] WB02 — Parentgestuurde subagentspawn met afgeleid capability-, context-,
  token-, kosten-, tijd- en workspacegrant; harde paralleliteits- en dieptelimiet.
- [ ] WB03 — Workerprotocol voor show/heartbeat/comment/artifact/complete/block/
  review, met schema- en bewijsvalidatie voordat resultaat de parentcontext in
  mag.
- [ ] WB04 — Lease-expiry, procescrash, reclaim, retrylimiet, stop en hervatten
  bewijzen zonder dubbele provideractie of verloren handoff.
- [ ] WB05 — Dashboard D07: read-only Kanban met taakdetails, afhankelijkheden,
  workers, pogingen, gebruik, blockers en live gesaneerde events.
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

## Primaire Hermes-bronnen

- Kanban en onderscheid met `delegate_task`:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md>
- Webdashboard:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md>
