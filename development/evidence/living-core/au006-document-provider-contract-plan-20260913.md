# AU-006: plan voor de echte lokale documentaansluiting

Status: voorgesteld, geen bronimplementatie of live-activatie. De bestaande
2-stappenbron is centraal geïntegreerd in 87bccbd07ee6be3b0bde0275f5de6ebdd26fb86f.
Deze aansluiting is nodig voordat dat een uitvoerbaar productpad is.

## Begrensde uitkomst

Een door de operator vastgelegd klein document wordt als tussenresultaat in een
unieke private workspace opgeslagen. Stap 2 gebruikt exact dat bewaarde document
om het finale document te schrijven. De aflevering is de geverifieerde lokale
resultaatlocatie met hash in de bestaande goalstatus; geen bericht aan een persoon,
geen extra schrijfoperatie voor aflevering en geen modelcall. Alleen deze twee
writes; geen shell, code-evaluatie, browse, toolregistratie of algemene executor.

## Gevraagde bestandsownership

- Nieuw `eva2/src/eva/providers/document_goal_plan.py`: gesloten, onveranderlijk
  providerplan voor precies twee documentstappen; schema/hashes/inputgrenzen.
- Nieuw `eva2/src/eva/providers/document_goals.py`: samenstelling en implementaties
  van bestaande GoalEvidencePort/GoalWorkPort/GoalDeliveryPort, op bestaande
  McpGatewayToolAdapter en VerifiedToolOrchestrator. Geen HTTP-fixtureservice.
- `eva2/src/eva/adapters/postgres/durable_goals.py`: uitsluitend duurzaam binden en
  teruglezen van het uitvoerplan/authority-configuratie op de bestaande goaltimeline.
  Geen tabel of nieuwe scheduler; bestaande state/cancel/worker blijven leidend.
- `eva2/src/eva/operations/durable_goal_worker.py`: uitsluitend expliciete opt-in
  compositie van deze provider, met de bestaande database/goalworker; alleen de
  exacte host-admitted goal verwerken. De bestaande HTTP-compositie blijft intact.
- Nieuw `eva2/tests/providers/test_document_goal_plan.py` en
  `eva2/tests/postgres/test_document_goal_provider.py`: contract en echte
  geïsoleerde opslag/provider/recoverygrenzen.
- `eva2/design/architecture/living-core-v1.md`: beperkte aansluitings-/operatorinstructie.
- Een afzonderlijk hashgebonden evidencebestand na bouw/tests.

Geen wijzigingen aan app.py, gesprekstools, model/provider-SDK, installer,
McpOwnerAuthority, MCP-catalogusparser, VerifiedToolOrchestrator of deployment in
het voorstel. Blijkt daarin een noodzakelijk gat, eerst terug naar afstemming.

## Autorisatie en aanvraaggrens

De nieuwe worker laadt het bestaande actuele McpOwnerAuthority en de bestaande
hashgebonden MCP-gatewayconfiguratie. Eigenaar/tenant/installatie komen uitsluitend
uit deze hostconfiguratie; een aangeleverde request-owner wordt nooit een principal
of private ConversationTurn. Er wordt geen gesprek-ingress verzonnen.

Een runtime-owned, via read_runtime_secret_file gelezen uitvoerplan bindt één
exacte goal-SHA aan de al toegestane lokale schrijfuitkomst: authorityconfiguratie,
MCP-configuratie, geselecteerde effect-/observatietools, twee unieke private paden,
begrensde tekst en de stap-2-binding aan previous_artifact. Dit plan is een concrete
operatoropdracht binnen het bestaande mandaat, geen nieuw algemeen mandaat.
Een gewone /goals/admit-aanvraag zonder deze exacte hostbinding krijgt hierdoor
geen uitvoerrechten. Zowel een andere owner als een vervalste goalhash sluit;
herindienen van precies de reeds toegestane goal is idempotent.

Het plandigest en authoritydigest worden één keer op de bestaande goaltimeline
gebonden; wijziging bij herstart sluit. Vóór iedere nieuwe write: plan opnieuw
veilig lezen, exact digest vergelijken, goal nog waiting, actuele ownerauthority
`authorize_current(phase="execute", operation_kind="effect")`, en actuele
catalogusbinding controleren. Uitgeschakeld/ontbrekend/onveilig mandaat en duurzame
intrekking stoppen nieuwe uitvoering. Bij readback van een reeds toegelaten effect
wordt de bestaande authority-observegrens behouden: bewijsherstel is geen nieuwe
write. Aflevering start geen extern effect en mag uitsluitend bestaand geverifieerd
final bewijs als lokaal beschikbaar projecteren.

## Uitvoering en duurzame bewijsbinding

Alleen een reeds geconfigureerde MCP `effect`-tool met afzonderlijke observatie
komt in aanmerking. Geen `invoke`-tool krijgt op basis van zijn naam schrijfgaranties.
Er is geen tool discovery of configuratie vanuit providertekst.

Stap 1 schrijft host-toegelaten begrensde documenttekst. Stap 2 schrijft een
begrensd document samengesteld uit vastgelegde prefix, exact previous_artifact.content
en suffix. Dit is een gesloten documentbinding, geen template-executor of vrije
instructieinterpretatie. Goal-instructies zijn beschrijving, geen bevoegdheid.

Per stap wordt een bestaande ToolRequest samengesteld en op de bestaande
operational timeline verzegeld met goal-SHA, plandigest, stapnummer,
previous-artifact-payloadhash, toolconfiguratie, arguments en expected_proof.
De bestaande VerifiedToolOrchestrator voert uit en controleert onafhankelijk
het resultaat. Het GoalArtifact verwijst deterministisch naar dat bewijs en
bevat uitsluitend geverifieerde inhoud. Bij crash vóór GoalEffect-registratie
herleest dezelfde stap dezelfde tooltimeline en/of werkelijke workspace-inhoud;
geen nieuwe key, nieuw doel of nieuw pad. Bewijs van een andere goal/stap/path,
andere inhoud of gewijzigde configuratie sluit.

Unieke bestaande doelbestanden met afwijkende inhoud mogen niet worden overschreven.
De gekozen producttool moet daarvoor voldoende observatie/precondities bieden;
als een atomaire create-if-absent nodig blijkt maar ontbreekt, wordt dat als
providergrens gemeld. Een filesystem-observatiegarantie wordt niet tot algemene
exactly-once providersemantiek uitgebreid. Reeds toegelaten in-flight werk kan na
stop eindigen; vervolgstappen starten niet. Token-/geldverbruik blijft onbekend
waar de productprovider dat niet rapporteert.

## Bewijs vóór eventuele activatie

1. Gesloten plan: onbekende velden, te grote inhoud, onbekende tools, andere
   owner/tenant/installatie, goalhash, capability/configuratie of buiten-scopepaden sluiten.
2. Onjuiste request-owner en een echte ownernaam zonder exacte hostplanbinding
   leveren nul providerwrites op; interne goalsroute krijgt geen nieuwe rechten.
3. Echte geïsoleerde PostgreSQL en dezelfde gepinde product-workspace/gatewaycode;
   geen tests/support-provider als productbewijs. Alleen eigen tijdelijke volumes/netwerk.
4. Twee writes met exact inhoudelijk afhankelijke uitkomst; abrupt workerprocesverlies
   na stap-1-checkpoint en na extern effect vóór lokaal receipt; herstart alleen
   ontbrekend werk. Per-stap hashes en lokaal eindresultaat uitleesbaar.
5. Stop en verse mandaatintrekking tussen prepare/readback en write, gewijzigde of
   ontbrekende veilige config, verkeerde receiptbinding en bestaande afwijkende file:
   geen nieuwe write en een concrete blokkade; oude toegelaten receipts blijven behouden.
6. Herhaalde goalverwerking levert eenmaal de lokale eindlocatie op. Aflevering is
   beschikbaarheid van het geverifieerde bestand, niet menselijke ontvangst.

Daarna onafhankelijke bron-/grensreview. Alleen na aparte activatieopdracht volgt
een synthetische operatorproef via bestaande /goals/admit, /state en /cancel op
unieke paden, met worker-only herstart. Geen live of deploy in deze planopdracht.
