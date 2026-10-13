# AU006: twee eigen documenten vanuit het gewone gesprek

## Gebruikersuitkomst en grens

De nieuwe opt-in route neemt een titel en twee kleine zelfstandige teksten aan vanuit de normale private eigenaarsconversatie. Bijvoorbeeld een uitnodiging en een korte appversie. De bestaande modelbeurt stelt de teksten op; de bestaande duurzame worker bewaart en verifieert beide documenten. Geen nieuw model in de worker, algemene planner, publicatie, nieuw rechtenbeheer of tweede scheduler.

Na activering: vraag om twee lokale documenten als duurzaam project. Eva gebruikt start_document_project met uitsluitend title, first_document en second_document. Het antwoord bevat goal_id en een opgeslagen status. get_document_project toont voortgang en geverifieerde bestanden; stop_document_project stopt nieuwe stappen en bewaart bestaande bestanden. De eindstatus completed betekent lokale beschikbaarheid, geen verstuurd bericht.

## Hergebruik en binding

DurableGoalSpec, PostgreSQL operational timeline, bestaande DurableGoalWorker, owner/source-ingress loader, verified MCP-orchestrator en native create/observe blijven de uitvoeringsketen. De ontbrekende mogelijkheid was een vaste beheerderdefinitie per opdracht en een tweede tekst die alleen de eerste tekst kon omsluiten. DocumentGoalPlan/v3 biedt een zelfstandige tweede tekst; bestaande v2-payload en hash blijven ongewijzigd.

Taakdata maximaal 300 UTF-8 bytes titel en 12000 bytes per document. De host leidt identiteit en paden af uit tenant, installatie, eigenaar, gesprek, bericht en echte call-ID. De inhoudhash staat apart, zodat gewijzigde argumenten onder dezelfde ingress-identiteit conflicteren. Geen modelargumenten voor eigenaar, paden, provider, generatie, budget of rechten.

EVA_DOCUMENT_TASK_POOL_FILE wijst naar een gesloten hostconfig EvaDocumentTaskPool/v1 met 1–16 slots: slot_id en tool_configuration_sha256. Iedere SHA moet vooraf een eigen geverifieerde native providerinstance/journal met bewezen genesis binden via de bestaande catalogus. Generaties blijven 0 en 1 voor die ene toewijzing. Slots worden atomisch en onomkeerbaar in hetzelfde operationele journal geclaimd; ook geannuleerde slots worden niet hergebruikt. Bij uitputting: project_status blocked, reden ‘geen vrij projectslot’. Geen reset, automatische providerprovisioning of onbeperkte-projectclaim.

Opnieuw starten leest hetzelfde plan en huidige eigenaarautoriteit. De oorspronkelijke echte ingress wordt opnieuw geladen; gewiste bron, gewijzigd project, mandaat of stop blokkeert verdere uitvoering. Een crash na slotclaim maar vóór goal-admission kan de bestaande worker herstellen. Healthcontrole leest de pool/scoped database zonder writes; status van een goal bepaalt geen procesgezondheid. Zonder poolconfig blijft de bestaande prepared route behouden.

## Uitgevoerd bewijs

- 50 gerichte offline controles geslaagd in 1.01 s, inclusief bestaande v2-plancontracten en duurzame workercontracten.
- 19 echte PostgreSQL-controles geslaagd in 6.14 s; de oude opnieuw te consumeren native-proef bewust uitgesloten. Nieuwe gewone-routeproef: twee losse opdrachten zonder planwissel, replay, opgeslagen status, stoppen, uitputting zonder slotrecycling. Parallelle identieke slotclaim convergeert; gewijzigde input onder dezelfde identiteit conflicteert. Andere eigenaar en ingetrokken mandaat maken geen claim. Read-only poolhealth laat lege slots ongemoeid.
- Nieuwe echte private gateway → translate → SDK → actuele ba9eda native-providerketen, met de normale startactie en bestaande workercomposition: 1 passed in 15.22 s. Eerste taak crasht hard na eerste effectcheckpoint; nieuw childproces voltooit de tweede onafhankelijke tekst en laat het eerste bestand bytegelijk. Tweede aparte taak gebruikt de tweede provider, wordt na document één gestopt en maakt na restart geen tweede bestand. Eerste provider blijft bytegelijk. Dit bewijst geen afgeronde tweede taak; die is bewust de stopproef.

De nativefixture gebruikt UID 0 en nieuwe lege testjournals. Dit is geen production-UID/DAC-claim. Er waren geen betaalde modelcalls, productmounts, runtimeactivaties of echte externe berichten. De tweede taak is geen modelkwaliteitstest: taakteksten zijn fixturedata. De bestaande modelverbruiksregistratie blijft leidend; de worker maakt nul aanvullende modelcalls. Een echte gewone modelopdracht en Jaaps beoordeling blijven na bronreview en gecontroleerde activering open.

## Retentie en activering

Bron: .eva2-artifacts/document-task-input-20260913/source-worktree. Nativebewijs en private snapshots: ../native-evidence; proof SHA256 a80a0b73828615731a8cf61d20cf5f310ce7eab0fc4acea6aa6ed5bd6ed79017. Beide fixturejournals zijn verbruikt, nooit opnieuw als genesis gebruiken. Parent verzorgt cleanup na onafhankelijke review.

Activering is apart: huidige e66 core, documentworker1d0a704 en providerba9eda blijven ongemoeid. De runtimewriter moet huidige overlaymanifesten behouden, nieuwe hostslots gecontroleerd provisionen, beide geselecteerde catalogus-SHA’s in de pool binden en legacy gezondheids-/planmounts correct afhandelen. Geen oude voltooide PJ01-journal of plan wijzigen. Nieuwe slots toevoegen is beheerwerk en zichtbaar resterend werk, geen impliciete bevoegdheid voor Eva.


## Compatibiliteitscorrectie: bestaand prepared project naast pool

Bij expliciete plan- én definitieconfiguratie blijven alle bestaande prepared acties naast de poolacties beschikbaar. Een voltooid legacyproject zonder slotclaim blijft daarmee via zijn bestaande status/resultaattool leesbaar. De workerselectie neemt zowel expliciet geconfigureerd legacyplan als poolclaims mee. De bestaande goalidentiteit wordt niet gemigreerd en krijgt geen verzonnen slotclaim. Een identiteit die via beide plannen voorkomt wordt vóór dispatch afgewezen, zodat geen dubbele selectie ontstaat. Health controleert beide configuraties read-only.

Gerichte echte PostgreSQL-proeven controleren de completed legacy-resultaatprojectie via de gewone route na inschakelen van de pool; daarnaast selecteert de echte bootstrap één legacygoal en één poolgoal met elk de juiste bestaande providerbouwroute. Providerdispatch is in deze compatibiliteitsproef gecapteerd: geen herhaalde nativefixture, geen nieuwe claim van fysiek legacyherstel. De eerder uitgevoerde nativeketen blijft afzonderlijk bewijs. Geen live wijzigingen.
