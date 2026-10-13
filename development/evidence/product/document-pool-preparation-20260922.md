# Documentpool: voorbereiding op de actuele runtime — 22 september 2026

Status: **voorbereiding gereed; geen installatie-GO**. Geen providerregistraties, modelcalls, effectcalls, stops, starts of wijzigingen aan bestaande runtimebestanden. Alleen de nieuwe private stagedirectory en dit bewijsbestand zijn geschreven.

Private stage: `/var/lib/eva2/document-pool-preparation-20260922` (root 0700; bestanden 0600). `preparation.json` bevat de conclusie; `gateway-pool-policy.proposed.compose.json` is een niet-toegepaste overlay, afgeleid van de actuele policy met uitsluitend twee toegevoegde endpoint/IP-paren. De overige bestanden bevatten de beperkte inventaris, scripts en historische reconciliatie. Private inhoud hoort niet in Git.

## Basis en hergebruik

Gelezen: `audit.ownerDocumentProjectAU00620260913` in `eva2/design/product/execution-workboard-v1.json`, `eva2/reviews/document-task-input-20260913.md`, de oude phase1-scripts vóór uitvoering en de bewaarde compatibiliteitsverificatie. Alleen de read-only readers en offline reducers zijn uitgevoerd; de oude installer is nooit uitgevoerd.

- Actuele core bij opname: `sha256:980afa510aab4ab75260acba8d8597780548f30999aa925150e528b59bb8414a`. De coordinator bereidde `sha256:67e1e99713479431db13c48a90961ca9df9c0ac13ad8621678310c21764fa27b` voor in `/var/lib/eva2/document-task-candidate-20260922`: zeven gereviewde modules boven op die actuele core. De onafhankelijke review daarvan ligt buiten dit bewijs. Bij latere corewijzigingen opnieuw rebasen/verifiëren; deze opname is geen blijvende activatiebaseline.
- Actuele documentworker: `sha256:1d0a704d32255bc7630c8670d8137d6d6c2a7c76e4533f5186c8f79613205fa0`. Bewaarde kandidaat `sha256:e07ae155c4f72087b6b186443e4594a33c0ae7b831f21fd062e25c246b1b389a` is aanwezig. Image-inspect bevestigt dezelfde Config behalve labels en behoud van alle parentlagen, UID 65532:65532. Daarmee is deze specifieke worker herbruikbaar; hij vervangt niet de gedelegeerde worker of de core.
- Bronbewijs worker: `.eva2-artifacts/document-task-runtime-20260913/compat-789fb0c6/candidate-images.json`, commit `789fb0c613f202c3ce71ba3f05798a221f6804dc`: precies zeven modules gewijzigd, overige 266-source-inventaris behouden; health SHA `cf2929f594b9e96e9a22dd2b787f62947164b71646cebc1f55474a9d56430a45`. Deze bestaande source/healthverificatie is niet opnieuw uitgevoerd. De huidige image-identiteit/configvergelijking is wel nieuw.
- Providerimage `sha256:ba9eda41299033a19e25c483e07e973b40545b226b062da6a29ccb70482f1b5e` draait al voor het oorspronkelijke project. De oude twee readonly-providerdefinities zijn bruikbare bouwstenen, geen actuele executable seal.
- Oude `phase1-sealed-r1` bevat geen `seal.json`, `review-go.json` of `reviewed-reconciliations.json`. Zijn installer beschermt oude containeridentiteiten/configuraties en mag niet tegen de huidige runtime worden uitgevoerd. Oude corekandidaten zouden actuele terugkoppelings-/voicefuncties verliezen.

## Actuele beperkte inventaris

De gecontroleerde SQL-reader gebruikt een read-only transactie, scopefilters en geen leeftijdsfilter: 577 events, 135 dispatchadmissions, nul gedelegeerde runs. De SQLite-reader opent de registry met `mode=ro`: zes gateways en 55 tools. Beide nieuwe poolproviders ontbreken. De oorspronkelijke documentprovider is nog private; bestaande registraties blijven behouden.

De offline inventory-reducer noemt zes unresolved regels, maar drie daarvan zijn het niet-herkende type DOCUMENT_STEP_ADMITTED. De werkelijke laatste-eventverdeling is 130 STATE_VERIFIED, negen TOOL_REJECTED, één GOAL_COMPLETED, één GOAL_NEEDS_HELP en drie RECOVERY_FAILED. Dit is geen bewijs dat al het werk succesvol eindigde.

Het geïnspecteerde bestaande reconciliatiescript leverde zonder replay:

- `f74f99…`: exacte historische registratie is aanwezig, met overeenkomstige endpoint/owner/id-binding.
- `3668c9…`: discover-resultaat is read_deferred; geen documenteffect en geen claim van geslaagde ontdekking.
- Twee documentstappen: native journal P,L,C,P,L,C, checksums, request/receipt-binding en actuele bestandshashes komen overeen met de opgeslagen terminale resultaten.
- De eerdere afgewezen documentstap: gatewayafwijzing, geen native record en geen doelbestand; GOAL_NEEDS_HELP blijft behouden.
- `1de22947d3daf383f191b4869305c733`: Chrome-snapshot, binding `34ed49d54ae63f70bf5539bb90c5c2daf4997528598729e0861087f5be7f9416`, alleen TOOL_REQUEST_ADMITTED, TOOL_PREPARED, RECOVERY_FAILED; geen TOOL_EXECUTED. Terminal routebewijs ontbreekt. Niet als geslaagd of definitief effectloos geclassificeerd.

Bij de bridge-opname: personal Chrome disconnected, busy=false, geen childprocessen; systemd active/running, Restart=no. Dit bewijst alleen de toestand op dat moment. Een volledig onderhoudsvenster met voor/na-accounting, graceful shutdown en expliciete beoordeling van de onzekere snapshot blijft nodig. Geen oude browserunit starten en geen nieuwe browsergrant geven.

## Minimale installatiedelta en volgorde

1. Neem na de gecoördineerde voice/core-activatie een verse coherente baseline van alle betrokken containers, compose/config, registryrowhashes en alle niet-terminale admissions. Gebruik de geïnspecteerde readers als bouwstenen. Maak een nieuw installatiepakket met exacte bestanden/hashes en onafhankelijke GO; hergebruik nooit de oude installerbaseline. Beoordeel expliciet de historische Chrome-regel en het browseronderhoudsvenster. De bekende gateway-shutdowngrens geldt: graceful eenmaal, terminal bewijs vereist; timeout/forced kill blokkeert.
2. Behoud gatewayimage/config, actuele corefuncties, gedelegeerde worker, oorspronkelijke provider, projectfiles, catalogusbindings en journals. Voeg aan de ACTUELE `EVA_MCP_PINNING_POLICY.managed_endpoints` uitsluitend `http://eva2-document-task-slot-1-20260913:9000/mcp` → `172.30.0.21` en slot-2 → `172.30.0.22` toe. De nieuwe voorgestelde overlay is exact deze union; alle bestaande owner_networks, blocked_addresses en endpoints blijven staan. Nieuwe runtimeconfig naast de volledige actuele composeketen renderen en vergelijken, niet een oude keten herstarten.
3. Hergebruik desgewenst `phase1/providers.readonly.compose.json`: twee containers, UID10001, restart=no, readonly root, geen hostpoorten, cap_drop ALL, no-new-privileges, beperkte resources. Netwerk `eva2-workspace-connectors`, volume `eva2-workspace-files`. `/workspace`, `/journal`, `/run/eva2` blijven in registratiephase readonly. Slotdirs `phase1/slot-{1,2}/journal` zijn nu leeg, UID10001/mode0700; providerfiles UID10001/mode0400. Netwerkinventaris toont .21/.22 vrij. Vóór toekomstige uitvoering opnieuw controleren; leegte/IPvrijheid zijn momentopnames, geen reservering.
4. Per slot één private registratie via het bestaande registration-clientpatroon onder het geldige owner-mandaat. Eerst afwezigheid aantonen, intent vastleggen, één POST, resultaat bewaren; bij onzeker antwoord niet opnieuw registreren. Werkelijke catalogus ophalen en toetsen op private ownerbinding, precies workspace_create_bound/workspace_observe_bound en unieke native binding. Gebruik `registration_common.py`/`register_once.py` als gereviewde logica met NIEUWE seal/baseline, niet ongewijzigd uitvoeren. Nieuwe registry-ID's registreren voor beperkte rollback; bestaande rijen niet verwijderen of wijzigen.
5. Leid daarna pas echte `tool_configuration_sha256` waarden af uit het werkelijke cataloguscontract; native binding-SHA en catalogus/toolconfig-SHA zijn verschillende zaken. De oude slotvoorstellen hebben null-toolbindings en zijn geen valide pool. Maak `EvaDocumentTaskPool/v1` met uitsluitend `contract` en `slots`, elk slot met `slot_id` en echte unieke 64-hex `tool_configuration_sha256`. Slots heten `document-task-20260913-1` en `document-task-20260913-2`. Voeg de echte tools toe aan BEIDE bestaande runtimecatalogi; niet overschrijven met een pool-only catalogus. Core leest `/run/eva/connectors/mcp/catalog.json`, documentworker `/run/eva/document-project/catalog.json`. Behoud alle bestaande entries en valideer beide effectieve catalogi. Een onnodige wijziging aan het owner-managed-endpointsbestand is niet voorgesteld: native documentuitvoering selecteert de vertrouwde catalogusspec rechtstreeks; een latere flow via authorize_endpoint vereist afzonderlijk passende endpointadmissie.
6. Maak een afzonderlijke effect-phase seal met actuele registrations, catalogi, native genesis/journalhashes, mounts, poolfile en volledige core/workerconfig. Mount dezelfde geldige poolfile readonly in core en documentworker, stel daar `EVA_DOCUMENT_TASK_POOL_FILE` in. Behoud core `EVA_DOCUMENT_GOAL_PLAN_FILE` én `EVA_DOCUMENT_GOAL_DEFINITION_FILE`, en worker `EVA_DOCUMENT_GOAL_PLAN_FILE`. Vervang alleen documentworkerimage door e07 en core door de op dat moment gereviewde actuele kandidaat. Voor de nieuwe providers zijn uitsluitend `/workspace` en het eigen `/journal` in de effectfase writable; `/run/eva2` en rootfs blijven readonly. Geen wijziging aan het oude providerjournal.
7. Verifieer vóór vrijgave runtime-UID-leesbaarheid, beide catalogusbindings, dubbele goalidentity-afwijzing, health en behouden oorspronkelijke projectstatus/resultaten. Een eerste echte eigenaarstaak/effecttest hoort bij die afzonderlijk geautoriseerde activatie, niet bij deze voorbereiding. Slotclaims zijn onomkeerbaar; verbruikte/geannuleerde slots niet naar genesis resetten. Bij rollback alleen nieuw geregistreerde IDs deactiveren na bewijs van hun identiteit; bestaande projecten, journalhistorie en claims behouden.

Legacy mount blijft `/home/administrator/project/.eva2-artifacts/au006-activation-20260913/runtime-reprobe/project` → `/run/eva/document-project` readonly. Huidige plan SHA `6ceb4f56f203c1f4666277831cabe1962c2193df460420c3cd17643d6b950dd1`; definition SHA `a99a48e12f7492e59a4c577a5517ade855bd1e9e12bab8b7d38a7b678049ddd0`; legacy worker-catalog SHA `e1c58728c924da8e254db4531c9ac2a13e78b4cc6af01e0103c85219efaf6d65`. Catalogusuitbreiding verandert zijn hash bewust, met behoud van alle bestaande bindings; plan/definition niet wijzigen.

## Bronverwijzingen en resterende gates

- `eva2/src/eva/providers/document_task_input.py`: exact poolcontract, distinct slots, claimbinding.
- `eva2/src/eva/bootstrap/document_goal_worker.py`: pool plus legacy-selectie en dubbele identity-afwijzing.
- `eva2/src/eva/providers/document_goals.py`: selectie van één echte toolconfig uit runtimecatalogus.
- `eva2/src/eva/providers/mcp_owner_authority.py`: mandaat en afzonderlijke managed-endpointadmissie.
- `eva2/reviews/document-task-input-20260913.md` en `eva2/evidence/product/browser-current-route-audit-2026-09-13.json`.
- Private oude stage: `.eva2-artifacts/document-task-runtime-20260913/phase1-sealed-r1/{read_all_admissions_v2.py,read_registry.py,read_bridge.py,inventory_guard.py,reconcile_existing.py,registration_common.py,register_once.py,verify_providers.py}`.

Nog open: verse coherent sealed baseline na andere activaties; expliciete sluiting/beoordeling van Chrome-history en het gehele onderhoudsvenster; twee echte registraties; daarvan afgeleide catalogus/poolbindings; afzonderlijke gereviewde effect-phase configuratie. De bronimplementatie en bewaarde worker hoeven hiervoor niet opnieuw gebouwd te worden zolang hun exacte bewezen image-identiteiten en configuratievoorwaarden gelden.

## Actuele vervolgcontrole na de privéspraakinstallatie

De nieuwe private stage `/var/lib/eva2/document-pool-phase1-current-20260922`
hergebruikt de registratie- en rollbacklogica met een verse baseline. De
read-only `phase1_preflight.py` slaagt: zes bestaande gateways, 55 tools, vijf
afzonderlijk gecontroleerde terminale gedelegeerde taken, ongewijzigde native
bestanden en vrije nieuwe slotadressen. Het volledige onderhoudsvenster is
hiermee nog niet uitgevoerd. Er zijn geen registraties, servicewissels of
modelcalls uitgevoerd. De historische onzekere Chrome-uitkomst blijft bewaard.

De zeven eerder beoordeelde documentmodules zijn tevens opnieuw verpakt boven
de daadwerkelijk geïnstalleerde core `sha256:d0c28433861b3b51be28f3b9e63d6d506261e7b1ce0506c60add5d42d30763eb`.
Kandidaat: `sha256:8b83127eca41f9aa468a189f252132956709fc047339f79f4d0ac724872b53a8`,
private stage `/var/lib/eva2/document-task-current-core-20260922`. De configuratie,
parentlagen, overige bronbestanden en delegated research/tool-free seals zijn
behouden; runtime-UID-imports slagen. Dit vervangt de oudere documentkandidaten
als voorbereiding, niet de draaiende installatie. Onderhoudsinstaller,
onafhankelijke review, registratie en effectfase blijven open.
