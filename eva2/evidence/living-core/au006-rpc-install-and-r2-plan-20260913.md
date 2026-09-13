# Exacte RPC-installatie en nieuwe r2-proef — plan ter review

Niet uitgevoerd. Bron/evidence `b26ff4ee` is goedgekeurd; dat is geen deploy- of proef-GO. De oorspronkelijke proef blijft mislukt: goal `au006-document-project-20260913` staat op `needs_help`, met één effectreservering, eerste stap expliciet `TOOL_REJECTED`, geen tweede stap, geen native journal en geen doelbestanden. Zijn inputs, events, antwoord en receipts worden niet aangepast.

## Installatie van uitsluitend de RPC-correctie

De huidige gatewaybasis omvat de oorspronkelijke Compose-files, de preserve-resourcesoverride en de exacte AU-006-endpointpolicy. De kandidaat voegt één read-only mount toe: `/var/lib/eva2/workspace-provider/au006-rpc-owner-context/main.py` naar `/app/mcpgateway/main.py`. De voorgestelde override gebruikt `create_host_path=false`.

Na plan-GO wordt uitsluitend de beoordeelde kandidaatbron `ce9a769b1224812fddb1a905cff4025a19411f2760e6ac2abf08d1f3ab3b01fe` op dat root-owned pad geïnstalleerd. De actuele oorspronkelijke bron moet exact `c28f599738d62efd4a1fcdf4d73422cad78a3b135406f4d235c38b62caa8fca9` zijn. Image blijft `daf923c2…`; bestaande toolservicepolicy, bootstrapguard, credentials, visibility, teams en policy veranderen niet. Dry-resolved servicevergelijking bevestigt alleen de nieuwe mainmount.

Vóór recreate: verse live config-/bronhashes, huidige geauthenticeerde metadata/list-toegang, eigen user-emailbinding en actieve DB-adminstatus, exacte geselecteerde tools/catalogushashes en bestaande 53 toolrijen, parent-core/readiness/quiescence en beschermde containers. Bij afwijking stop. Recreate alleen de gateway in het bestaande project en controleer daarna main-/guardhash, policy, readiness en alle bestaande resource-/autoriteitsvelden. Geen herregistratie, catalogusnormalisatie of databasereparatie.

## Nieuwe expliciete doelbinding

R2 is een afzonderlijke hostgebonden proef, geen herstart van de oude operatie:

- Goal-ID: `au006-document-project-20260913-r2`.
- Goal-SHA: `953a7a56e08823509bd8470eb72c4e6dbf894099e0c55bfac4f7a79631851ace`.
- Plan-SHA: `6ceb4f56f203c1f4666277831cabe1962c2193df460420c3cd17643d6b950dd1`.
- Nieuwe bestanden: `/proof-state/eva2-documentproject-concept-20260913-r2.md` en `/proof-state/eva2-documentproject-eindversie-20260913-r2.md`.
- Nieuwe private projectdirectory: `runtime-reprobe/project` in de activatiestage; nog niet aangemaakt.

Dezelfde eigenaar, inhoud, twee instructies, private conversation-projectscope, autoriteit, tools en catalogushashes blijven gelden. De nieuwe operation-IDs zijn aantoonbaar disjunct van beide oude operation-IDs. Alle zes canonical input-/configbestanden zijn offline berekend en gehasht; de volledige inhoud blijft in de private preview. De expliciete sealer staat standaard op read-only controle, gebruikt de vaste preview-SHA en schrijft alleen met `--apply`, exclusief en uid 65532/mode 0400. Een gedeeltelijke directory wordt niet overschreven of automatisch herprobeerd.

## Expliciet ter beoordeling: behoud van het lege journal

Dit plan behoudt dezelfde provider/root-/journalbinding zonder reset of nieuw endpoint. Het journal is nog leeg; de oude gatewayaanroep werd expliciet afgewezen vóór native dispatch. De oude worker blijft uit en de nieuwe worker selecteert uitsluitend r2. Geen oude operation-ID wordt opnieuw aangeboden. Het lege journal wordt niet gewist, opnieuw gebonden of als succesvolle oude ontvangst gepresenteerd.

Deze overgang van ongebruikte genesis naar de nieuwe r2-operaties is een expliciet onderdeel van de gevraagde plan-GO. Als de reviewer dit niet toelaat, of als verse preflight enige journalentry, oude uitvoeronzekerheid, doelbestand of veranderde binding vindt, stopt het plan. Er wordt dan niet stil een nieuwe binding gemaakt of een bestaand doel gereset. Na r2-uitvoering is diezelfde genesis niet meer beschikbaar voor een andere poging.

## Core/worker/provider en begrensde proef

De core gebruikt dezelfde eerder beoordeelde kandidaatimage `2393a1d5…`, dezelfde twee opt-in-instellingen en alleen een andere read-only projectbron. De worker krijgt uitsluitend de nieuwe private projectdirectory; image, scope, credentials, netwerken en loop blijven hetzelfde. Providerconfig blijft exact de eerder beoordeelde twee schrijfbare datamounts, read-only rootfs/config, vaste IP en bestaande instance. De commandlijsten en config-/sourcehashes staan in de JSON.

Na afzonderlijke plan-GO:

1. Bewijs eerst oude goal `needs_help`/reservering 1/expliciete rejection onveranderd, r2-goal afwezig, disjuncte IDs, beide sets paden afwezig, leeg journal en dezelfde inodes. Behoud hashkopieën van alle oude sealed files en failure-evidence. Controleer bestaande quota; geen verhoging of rechtenuitbreiding.
2. Installeer en verifieer de RPC-correctie zoals boven. Materialiseer daarna uitsluitend de nieuwe preview in de nieuwe directory en vergelijk alle zes hashes. Start de eigen provider, controleer native metadata en geselecteerde catalogusbindings; bij afwijking geen rebind of retry.
3. Neem verse core-quiescence en vervang uitsluitend de core met de r2-override. Controleer readiness, juiste owner-/projectscope, drie gesloten acties, bestaande statische catalogus en afwezige raw-tooladmissie. Start alleen de eigen documentworker met de r2-mount. Controleer liveness/geen restart over meerdere polls, nog afwezige r2-goal en leeg journal vóór de gewone vraag.
4. Stuur maximaal één gewone private vraag via dezelfde bestaande ownerroute: “Maak het opnieuw voorbereide documentproject af en laat het eindresultaat zien.” Alleen indien nodig maximaal één volgende statusvraag: “Laat het eindresultaat van het documentproject zien.” Geen gescripte toolkeuze, directe SQL-admit, extra modelproef of operatorretry. Leg alle interne modelpogingen, tooladmissions, tokens, tijd en onbekende kosten eerlijk vast.
5. Succes vereist r2-completed, twee echte onafhankelijke receipts/generaties, exact de nieuwe bestandsbytes en finale inhoud/pad. De oude goal en zijn failed step blijven ongewijzigd. Een onzekere/afgewezen operatie of onzekere HTTP-uitkomst stopt de proef; uitsluitend readback, geen automatische herhaling of reset.
6. Maak een private bestands-/artifactlink pas na inhouds- en toegangscontrole. Een lokaal privé-exportbestand mag alleen exact de geverifieerde finale bytes bevatten en wordt als export onderscheiden van het originele workspacebestand. Geen verzonnen downloadroute, publieke upload of automatische eigenaaracceptatie. Als een bereikbare link niet bewezen is, blijft dat afzonderlijk open.

## Terugweg met behoud van beveiligingsfixen

Bij projectfalen: stop alleen de nieuwe documentworker en provider op hun vastgelegde IDs; herstel alleen de volledige parent-corebasis zonder opt-in. Laat de RPC-correctie, bootstrapguard, endpointpolicy en bestaande gatewayregistratie behouden. Bewaar beide doelstates, alle documenten/receipts/journalgegevens en beide sealed bundles. Geen SQL-/journalreset of DELETE.

Alleen wanneer de gatewayinstallatie zelf afwijkt vóór de proef: de gebonden patch-only terugweg verwijdert uitsluitend de nieuwe mainmount en behoudt de bootstrapguard, pinning/toolservicepatches en huidige endpointpolicy. Dit herintroduceert de bekende eigen-private-toegangsfout en is geen functionele oplossing. Base-only gatewayrecreate blijft ongeldig. Iedere verdere wijziging buiten dit plan vereist opnieuw een concrete beoordeling.
